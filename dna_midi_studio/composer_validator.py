"""composer_validator.py — Pa800 validity gate for score items (DNA Composer, 4.69).

Every corpus item and every generated/reconstructed arrangement must pass
100% of these rules before it may be tokenized, trained on, or exported to
a Korg Pa800-compatible SMF.

NOTE: module name differs from the legacy dna_midi_studio/pa800_validator.py
(4.47-era hard validator for Pa800 *Style* SMF0 markers/format, still used by
legacy modules). This module gates the canonical *score language* (S1 corpus
and future generated arrangements).

Rules (v1):
- structure: signature known; section bars sum to max bar count; bars 1..128;
- tracks: channel 0..15; role from the known vocabulary; events sorted per bar;
- drums channels (9/10) for composer-made items: only GM percussion 35..81,
  steps inside the grid; no pitch-bend / program / bank / unverified
  controllers anywhere in the score language;
- pitched channels: notes 0..110 (Pa800 range sanity);
- grid: 16 steps for x/4 signatures, 12 for 3/4 and 12/8; durations >= 1 and
  never cross the bar end (st+d <= steps);
- CC11 (the only CC the composer may emit): value classes 0..15, only on
  channels that have a track;
- bpm in 40..220;
- forbidden emissions: program changes / banks / DNC / keyswitch / slap/pop
  payloads are not representable in the score language; ingested (engine/user)
  files may carry them and record them in item["meta"]["programs"] as
  informational only; composer-made items (vendor|synthetic) must not.

Deterministic, stdlib-only.
"""

from __future__ import annotations

import json

ROLES = ("drums", "perc", "bass", "acc", "solo", "other", "unknown")
SIGNATURES = {(4, 4): 16, (3, 4): 12, (12, 8): 12}
DRUM_CHANNELS = (9, 10)
GM_PERCUSSION = range(35, 82)  # GM drum kit notes 35..81
BPM_MIN, BPM_MAX = 40, 220
MAX_BARS = 128


def steps_per_bar(signature: list[int]) -> int:
    sig = tuple(int(x) for x in signature)
    if sig not in SIGNATURES:
        raise ValueError(f"composer_validator: unsupported signature {sig}")
    return SIGNATURES[sig]


def validate_item(item: dict) -> dict:
    """Returns {"ok": bool, "errors": [str...]}."""
    errors: list[str] = []
    try:
        sig = [int(x) for x in item.get("signature", [4, 4])]
        steps = steps_per_bar(sig)
    except (ValueError, TypeError) as exc:
        return {"ok": False, "errors": [f"signature: {exc}"]}

    bpm = item.get("bpm", 120)
    if not (isinstance(bpm, (int, float)) and BPM_MIN <= bpm <= BPM_MAX):
        errors.append(f"bpm {bpm} outside {BPM_MIN}..{BPM_MAX}")

    sections = item.get("sections", []) or []
    sec_bars = sum(int(s.get("bars", 0)) for s in sections)
    maxb = -1
    for tr in item.get("tracks", []):
        for e in tr.get("events", []):
            maxb = max(maxb, int(e["b"]))
    for c in item.get("cc", []):
        maxb = max(maxb, int(c["b"]))
    nbars = maxb + 1 if maxb >= 0 else 0
    if nbars > MAX_BARS:
        errors.append(f"bars {nbars} > cap {MAX_BARS}")
    if sections and sec_bars != nbars and nbars > 0:
        errors.append(f"section bars {sec_bars} != actual bars {nbars}")

    seen_channels: set[int] = set()
    for tr in item.get("tracks", []):
        role = str(tr.get("role", "unknown"))
        if role not in ROLES:
            errors.append(f"unknown role '{role}'")
        try:
            ch = int(tr["channel"])
        except (KeyError, TypeError, ValueError):
            errors.append(f"track channel missing/invalid: {tr.get('channel')}")
            continue
        if not 0 <= ch <= 15:
            errors.append(f"channel {ch} outside 0..15")
            continue
        seen_channels.add(ch)
        for e in tr.get("events", []):
            try:
                b, st, d, v, n = (int(e["b"]), int(e["st"]), int(e["d"]),
                                  int(e["v"]), int(e["n"]))
            except (KeyError, TypeError, ValueError):
                errors.append(f"malformed event on ch {ch}: {e}")
                continue
            if not 0 <= st < steps:
                errors.append(f"ch{ch}: step {st} outside 0..{steps - 1}")
            if d < 1 or st + d > steps:
                errors.append(f"ch{ch}: duration {d} at st {st} crosses bar end")
            if not 0 <= v <= 7:
                errors.append(f"ch{ch}: velocity class {v} outside 0..7")

    origin = str(item.get("originKind", "engine"))
    is_composer = origin in ("vendor", "synthetic")  # full gate for our output
    if is_composer:
        for tr in item.get("tracks", []):
            ch = int(tr["channel"]) if str(tr.get("channel", "")).isdigit() else -1
            for e in tr.get("events", []):
                try:
                    n = int(e["n"])
                except (KeyError, TypeError, ValueError):
                    continue
                if ch in DRUM_CHANNELS:
                    if n not in GM_PERCUSSION:
                        errors.append(f"ch{ch}: note {n} not in GM percussion 35..81")
                elif not 0 <= n <= 110:
                    errors.append(f"ch{ch}: pitched note {n} outside 0..110")

    for c in item.get("cc", []):
        try:
            ch, v = int(c["ch"]), int(c["v"])
        except (KeyError, TypeError, ValueError):
            errors.append(f"malformed cc event: {c}")
            continue
        if ch not in seen_channels:
            errors.append(f"cc channel {ch} has no track")
        if not 0 <= v <= 15:
            errors.append(f"cc class {v} outside 0..15")

    programs = (item.get("meta") or {}).get("programs") if isinstance(item.get("meta"), dict) else None
    if is_composer and programs:
        errors.append(f"{origin} item must not carry program changes: {programs}")

    return {"ok": not errors, "errors": errors}


def validate_many(items) -> dict:
    """Batch validation summary over an iterable of items."""
    total = 0
    failed = []
    per_origin: dict[str, dict] = {}
    for it in items:
        total += 1
        origin = str(it.get("originKind", "?"))
        stat = per_origin.setdefault(origin, {"items": 0, "failed": 0})
        stat["items"] += 1
        res = validate_item(it)
        if not res["ok"]:
            stat["failed"] += 1
            failed.append({"id": it.get("id"), "origin": origin,
                           "errors": res["errors"]})
    return {"ok": not failed, "total": total, "failed": failed,
            "perOrigin": per_origin}


def validate_item_json(text: str) -> dict:
    return validate_item(json.loads(text))
