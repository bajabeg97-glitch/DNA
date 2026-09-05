"""Independent verification of MIDI candidates and reproducibility evidence.

This module deliberately consumes bytes and serialized manifests only.  It does
not import or trust planner decisions, optimizer verdicts or apply-result flags.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from hashlib import sha256
from typing import Any, Callable, Mapping, Sequence

from .midi import MidiEvent, MidiFile, MidiFormatError


_PROTECTED_CC = {0, 32, 98, 99, 100, 101}


@dataclass(frozen=True)
class AuthorizedNoteAddition:
    track: int
    channel: int
    start_tick: int
    end_tick: int
    pitch_min: int
    pitch_max: int
    reason: str

    def __post_init__(self) -> None:
        if self.track < 0 or not 0 <= self.channel <= 15:
            raise ValueError("Authorized note addition has invalid track/channel")
        if self.start_tick < 0 or self.end_tick <= self.start_tick:
            raise ValueError("Authorized note addition has invalid time window")
        if not 0 <= self.pitch_min <= self.pitch_max <= 127 or not self.reason.strip():
            raise ValueError("Authorized note addition requires pitch range and reason")

    def allows(self, note: tuple[int, int, int, int, int, int]) -> bool:
        track, channel, pitch, start, end, _velocity = note
        return (track == self.track and channel == self.channel
                and self.start_tick <= start < self.end_tick
                and self.pitch_min <= pitch <= self.pitch_max and end > start
                and bool(self.reason.strip()))


@dataclass(frozen=True)
class VerificationPolicy:
    authorized_note_additions: tuple[AuthorizedNoteAddition, ...] = ()
    pa800_style_contract: bool = False
    require_idempotency: bool = True


@dataclass(frozen=True)
class VerificationReport:
    passed: bool
    issues: tuple[str, ...]
    checks: Mapping[str, bool]
    source_hash: str
    candidate_hash: str

    def to_dict(self) -> dict[str, Any]:
        return {"schema": "dna-independent-verification", "version": "1.0",
                "passed": self.passed, "issues": list(self.issues), "checks": dict(self.checks),
                "sourceHash": self.source_hash, "candidateHash": self.candidate_hash,
                "verdictSource": "independent-byte-parser"}


def _note_fingerprints(midi: MidiFile) -> Counter[tuple[int, int, int, int, int, int]]:
    return Counter((n.track, n.channel, n.pitch, n.start, n.end, n.velocity) for n in midi.notes())


def _protected_event(event: MidiEvent) -> bool:
    return (event.kind in {"meta", "sysex"} or event.command == 0xC0
            or (event.command == 0xB0 and len(event.data) == 2 and event.data[0] in _PROTECTED_CC))


def _event_fingerprints(midi: MidiFile) -> Counter[tuple[Any, ...]]:
    return Counter((track, e.tick, e.kind, e.status, bytes(e.data), e.meta_type)
                   for track, midi_track in enumerate(midi.tracks)
                   for e in midi_track.events if _protected_event(e))


def _pa800_issues(midi: MidiFile) -> list[str]:
    issues = []
    if midi.format_type != 0 or len(midi.tracks) != 1 or midi.ppq != 480:
        issues.append("Pa800 Style requires SMF0, one track and PPQ 480")
    events = midi.tracks[0].events if midi.tracks else []
    for event in events:
        if event.channel is not None and not 8 <= event.channel <= 15:
            issues.append("Pa800 Style channel lies outside 9-16")
            break
    markers = [event for event in events if event.kind == "meta" and event.meta_type == 0x06]
    if not markers:
        issues.append("Pa800 Style requires marker events")
    for marker in markers:
        try:
            name = marker.data.decode("ascii")
        except UnicodeDecodeError:
            issues.append("Pa800 marker is not ASCII")
            continue
        if name != name.lower():
            issues.append("Pa800 marker must be lowercase")
        same_tick = [event for event in events if event.tick == marker.tick]
        controllers = {(event.channel, event.data[0]) for event in same_tick
                       if event.command == 0xB0 and len(event.data) == 2}
        programs = {event.channel for event in same_tick if event.command == 0xC0}
        channels = {channel for channel, cc in controllers if cc == 0}
        for channel in channels:
            required = {(channel, 0), (channel, 32), (channel, 11)}
            if not required <= controllers or channel not in programs:
                issues.append(f"Marker {name} lacks CC00/CC32/PC/CC11 setup")
                break
    return issues


def verify_candidate(source_bytes: bytes, candidate_bytes: bytes, manifest: Mapping[str, Any],
                     policy: VerificationPolicy = VerificationPolicy(),
                     rerun: Callable[[], bytes] | None = None,
                     worker_runs: Mapping[int, bytes] | None = None,
                     journal: Mapping[str, Any] | None = None) -> VerificationReport:
    issues: list[str] = []
    checks: dict[str, bool] = {}
    source_hash, candidate_hash = sha256(source_bytes).hexdigest(), sha256(candidate_bytes).hexdigest()
    try:
        source, candidate = MidiFile.from_bytes(source_bytes), MidiFile.from_bytes(candidate_bytes)
        checks["sourceParsed"] = checks["candidateParsed"] = True
    except MidiFormatError as exc:
        return VerificationReport(False, (f"MIDI parse failed: {exc}",), {"sourceParsed": False,
                                  "candidateParsed": False}, source_hash, candidate_hash)

    checks["manifestInputHash"] = manifest.get("inputHash") == source_hash
    checks["manifestOutputHash"] = manifest.get("outputHash") == candidate_hash
    if not checks["manifestInputHash"]:
        issues.append("Manifest input hash does not match source bytes")
    if not checks["manifestOutputHash"]:
        issues.append("Manifest output hash does not match candidate bytes")
    stages = manifest.get("stages", [])
    final_stage_hash = stages[-1].get("manifest", {}).get("outputHash") if stages else candidate_hash
    checks["finalStageHash"] = final_stage_hash == candidate_hash
    if not checks["finalStageHash"]:
        issues.append("Final stage manifest does not describe candidate MIDI")

    try:
        source_notes, candidate_notes = _note_fingerprints(source), _note_fingerprints(candidate)
        missing = source_notes - candidate_notes
        additions = candidate_notes - source_notes
        checks["originalNotesPreserved"] = not missing
        if missing:
            issues.append(f"Protected original note diff detected: {sum(missing.values())} missing/changed")
        unauthorized = []
        for note, count in additions.items():
            if not any(rule.allows(note) for rule in policy.authorized_note_additions):
                unauthorized.extend([note] * count)
        checks["noteAdditionsAuthorized"] = not unauthorized
        if unauthorized:
            issues.append(f"Unauthorized note additions: {len(unauthorized)}")
    except MidiFormatError as exc:
        checks["originalNotesPreserved"] = False
        checks["noteAdditionsAuthorized"] = False
        issues.append(f"Independent note pairing failed: {exc}")

    source_events, candidate_events = _event_fingerprints(source), _event_fingerprints(candidate)
    protected_diff = source_events - candidate_events
    checks["protectedEventsPreserved"] = not protected_diff
    if protected_diff:
        issues.append(f"Protected SysEx/meta/Bank/Program/RPN/NRPN diff: {sum(protected_diff.values())}")

    round_trip = candidate.to_bytes() == candidate_bytes
    checks["candidateRoundTripStable"] = round_trip
    if not round_trip:
        issues.append("Candidate is not byte-stable after independent parse/write")

    if policy.pa800_style_contract:
        contract_issues = _pa800_issues(candidate)
        checks["pa800Contract"] = not contract_issues
        issues.extend(contract_issues)
    else:
        checks["pa800Contract"] = True

    if journal is not None:
        checks["journalCommitted"] = journal.get("status") == "COMMITTED"
        checks["journalOutputHash"] = journal.get("outputHash") == candidate_hash
        if not checks["journalCommitted"] or not checks["journalOutputHash"]:
            issues.append("Atomic journal does not authorize this candidate")
    else:
        checks["journalCommitted"] = checks["journalOutputHash"] = True

    if policy.require_idempotency:
        checks["idempotent"] = rerun is not None and rerun() == candidate_bytes
        if not checks["idempotent"]:
            issues.append("Idempotency proof is missing or failed")
    else:
        checks["idempotent"] = True

    if worker_runs is not None:
        checks["workerDeterminism"] = bool(worker_runs) and all(value == candidate_bytes for value in worker_runs.values())
        if not checks["workerDeterminism"]:
            issues.append("Worker-count determinism failed")
    else:
        checks["workerDeterminism"] = True
    return VerificationReport(not issues and all(checks.values()), tuple(issues), checks,
                              source_hash, candidate_hash)