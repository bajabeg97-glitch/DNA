"""Session 20 safe bilingual ProducerBrief 2.0.

Free text is converted into a strict, explainable and read-only arrangement
intent.  Optional AI enrichment is metadata-only, consent-gated and limited to
controlled vocabulary; neither local nor cloud paths can emit MIDI, paths,
Bank Select, Program Change or validator decisions.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from hashlib import sha256
import json
import re
import unicodedata
from typing import Any, Callable, Mapping

from .agent_runtime import CloudPolicy, dispatch_optional_cloud


BRIEF_SCHEMA = "dna-premium-producer-brief"
BRIEF_VERSION = "2.0"
GENRES = (
    "pop-folk", "pop", "folk", "rock", "ballad", "dance", "disco",
    "waltz", "polka", "latin", "jazz", "acoustic", "electronic", "unspecified",
)
DENSITIES = ("sparse", "balanced", "full")
SYNCOPATIONS = ("low", "medium", "high")
SPACES = ("dry", "balanced", "open")
TRANSITIONS = ("subtle", "balanced", "dramatic")
SOLO_TREATMENTS = ("preserve", "expression-only", "no-new-layers")
ROLES = ("drums", "percussion", "bass", "guitar", "accompaniment", "riff", "solo", "pad")
ELEMENTS = (
    "i1cv1", "i2cv1", "v1cv1", "v2cv1", "v3cv1", "v4cv1",
    "f1cv1", "f2cv1", "e1cv1", "e2cv1",
)
SECTIONS = ("intro", "verse", "chorus", "bridge", "ending")

_GENRE_PATTERNS = {
    "pop-folk": (r"\bpop[ -]?folk\b", r"\bturbo[ -]?folk\b"),
    "pop": (r"\bpop\b",),
    "folk": (r"\bfolk\b", r"\bnarodn\w*"),
    "rock": (r"\brock\b", r"\brokers\w*"),
    "ballad": (r"\bballad\b", r"\bbalad\w*"),
    "dance": (r"\bdance\b", r"\bplesn\w*"),
    "disco": (r"\bdisco\b",),
    "waltz": (r"\bwaltz\b", r"\bvalcer\w*"),
    "polka": (r"\bpolk\w*",),
    "latin": (r"\blatin\w*", r"\bsalsa\b", r"\brumba\b"),
    "jazz": (r"\bjazz\b",),
    "acoustic": (r"\bacoustic\w*", r"\bakustic\w*"),
    "electronic": (r"\belectronic\w*", r"\belektron\w*"),
}
_ROLE_TERMS = {
    "drums": r"(?:drums?|bubnj\w*)",
    "percussion": r"(?:percussion|perkusij\w*|udaraljk\w*)",
    "bass": r"(?:bass|bas)",
    "guitar": r"(?:guitar\w*|gitar\w*)",
    "accompaniment": r"(?:accompaniment|pratnj\w*)",
    "riff": r"(?:riff\w*)",
    "solo": r"(?:solo|lead|melodij\w*)",
    "pad": r"(?:pad\w*|podlog\w*)",
}
_SECTION_TERMS = {
    "intro": r"(?:intro|uvod)", "verse": r"(?:verse|strof\w*)",
    "chorus": r"(?:chorus|refren\w*)", "bridge": r"(?:bridge|most)",
    "ending": r"(?:ending|outro|kraj)",
}
_INJECTION_PATTERNS = (
    r"\bignore (?:all )?(?:previous|system) instructions?\b",
    r"\bzanemari (?:sve )?(?:prethodne|sistemske) upute\b",
    r"\bsystem prompt\b", r"\bsistemski prompt\b",
    r"\b(?:bypass|skip|disable) (?:the )?validator\b",
    r"\b(?:preskoci|zaobidi|iskljuci) validator\b",
    r"\b(?:write|emit|generate) (?:the )?final midi bytes?\b",
    r"\b(?:pisi|zapisi|generiraj) finaln\w* midi bajt\w*\b",
    r"\bbank select\b", r"\bprogram change\b", r"\bcc ?(?:00|32)\b",
)
_PROTECTED_AI_KEYS = {
    "midi", "midibytes", "bytes", "path", "outputpath", "writepath",
    "bank", "bankmsb", "banklsb", "program", "programchange", "cc00", "cc32",
    "validator", "validatorbypass", "export", "finalmidi",
}


def _canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def _hash(value: Mapping[str, Any]) -> str:
    return sha256(_canonical({key: item for key, item in value.items() if key != "briefHash"})).hexdigest()


def _normalized(text: str) -> str:
    folded = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")
    return re.sub(r"\s+", " ", folded.lower()).strip()


def _language(text: str) -> str:
    value = _normalized(text)
    hr = sum(bool(re.search(pattern, value)) for pattern in (
        r"\bnapravi\b", r"\bsa\b", r"\bbez\b", r"\bstrof\w*", r"\brefren\w*",
        r"\bsuzdrzan\w*", r"\bzivlj\w*", r"\bzakljuc\w*",
    ))
    en = sum(bool(re.search(pattern, value)) for pattern in (
        r"\bmake\b", r"\bwith\b", r"\bwithout\b", r"\bverse\b", r"\bchorus\b",
        r"\bsparse\b", r"\blively\b", r"\block\b",
    ))
    if hr and en:
        return "mixed"
    if hr:
        return "hr"
    if en:
        return "en"
    return "unknown"


def _matches(value: str, patterns: tuple[str, ...] | list[str]) -> list[str]:
    return [match.group(0) for pattern in patterns for match in re.finditer(pattern, value)]


def _field_evidence(field: str, value: Any, matched: str, confidence: float,
                    source: str = "local-rule") -> dict[str, Any]:
    return {"field": field, "value": value, "matchedText": matched,
            "confidence": round(confidence, 4), "source": source}


def _controlled_matches(value: str, choices: Mapping[str, tuple[str, ...]]) -> list[tuple[str, str]]:
    found = []
    for choice, patterns in choices.items():
        for match in _matches(value, patterns):
            found.append((choice, match))
    return found


def _section_energy(value: str, section: str) -> tuple[int | None, str | None]:
    section_pattern = _SECTION_TERMS[section]
    levels = (
        (28, r"(?:very quiet|minimal|sparse|vrlo mirn\w*|minimaln\w*|rijedak\w*)"),
        (35, r"(?:quiet|soft|restrained|calm|mirn\w*|tih\w*|suzdrzan\w*)"),
        (65, r"(?:lively|energetic|zivlj\w*|energic\w*)"),
        (85, r"(?:full|powerful|big|pun\w*|snaz\w*|velik\w*)"),
    )
    section_matches = list(re.finditer(section_pattern, value))
    candidates = []
    for score, adjective in levels:
        for adjective_match in re.finditer(adjective, value):
            for section_match in section_matches:
                if adjective_match.end() <= section_match.start():
                    distance = section_match.start() - adjective_match.end()
                elif section_match.end() <= adjective_match.start():
                    distance = adjective_match.start() - section_match.end()
                else:
                    distance = 0
                if distance <= 24:
                    start = min(adjective_match.start(), section_match.start())
                    end = max(adjective_match.end(), section_match.end())
                    candidates.append((distance, start, score, value[start:end]))
    if candidates:
        _, _, score, match = min(candidates, key=lambda item: (item[0], item[1], -item[2]))
        return score, match
    return None, None


def _genre(value: str) -> tuple[str, list[dict[str, Any]], list[dict[str, Any]]]:
    found = _controlled_matches(value, _GENRE_PATTERNS)
    if any(choice == "pop-folk" for choice, _ in found):
        found = [(choice, match) for choice, match in found if choice not in {"pop", "folk"}]
    unique = list(dict.fromkeys(choice for choice, _ in found))
    evidence = [_field_evidence("genre", choice, match, 0.98) for choice, match in found
                if choice in unique]
    conflicts = []
    if len(unique) > 1:
        conflicts.append({
            "id": "genre-choice", "field": "genre", "code": "MULTIPLE_GENRES",
            "reason": "Multiple primary genres were requested.", "blocking": True,
            "choices": unique, "resolvedChoice": None,
        })
    return (unique[0] if unique else "unspecified"), evidence, conflicts


def _single_choice(value: str, field: str, patterns: Mapping[str, tuple[str, ...]],
                   default: str) -> tuple[str, list[dict[str, Any]], list[dict[str, Any]]]:
    found = _controlled_matches(value, patterns)
    unique = list(dict.fromkeys(choice for choice, _ in found))
    evidence = [_field_evidence(field, choice, match, 0.94) for choice, match in found]
    conflicts = []
    if len(unique) > 1:
        conflicts.append({
            "id": f"{field}-choice", "field": field, "code": f"CONFLICTING_{field.upper()}",
            "reason": f"Conflicting {field} requests require a user choice.",
            "blocking": True, "choices": unique, "resolvedChoice": None,
        })
    return (unique[0] if unique else default), evidence, conflicts


def _roles(value: str) -> tuple[list[str], list[str], list[dict[str, Any]], list[dict[str, Any]]]:
    required, forbidden, evidence = [], [], []
    positive_spans = [match.group("body") for match in re.finditer(
        r"\b(?:with|include|use|add|sa|uz|ukljuci|dodaj)\b(?P<body>.*?)(?=\b(?:but|ali|without|bez)\b|[,.;!?]|$)",
        value,
    )]
    negative_spans = [match.group("body") for match in re.finditer(
        r"\b(?:without|no|bez|exclude|remove|iskljuci|ukloni)\b(?P<body>.*?)(?=\b(?:but|ali|with|sa|use|include|add)\b|[,.;!?]|$)",
        value,
    )]
    for role, term in _ROLE_TERMS.items():
        negative = _matches(value, (
            rf"\b(?:without|no|bez)\s+(?:extra\s+|dodatn\w*\s+)?{term}",
            rf"\b(?:exclude|remove|iskljuci|ukloni)\s+{term}",
        ))
        positive = _matches(value, (
            rf"\b(?:with|include|use|add|sa|uz|ukljuci|dodaj)\s+(?:a\s+|an\s+)?{term}",
            rf"\b{term}\s+(?:is\s+required|required|je\s+obavezan|je\s+obavezna)",
            rf"\bobavezn\w*\s+{term}",
        ))
        positive += [span.strip() for span in positive_spans if re.search(rf"\b{term}\b", span)]
        negative += [span.strip() for span in negative_spans if re.search(rf"\b{term}\b", span)]
        if positive:
            required.append(role)
            evidence.append(_field_evidence("requiredRoles", role, positive[0], 0.96))
        if negative:
            forbidden.append(role)
            evidence.append(_field_evidence("forbiddenRoles", role, negative[0], 0.96))
    required, forbidden = sorted(set(required)), sorted(set(forbidden))
    conflicts = []
    for role in sorted(set(required) & set(forbidden)):
        conflicts.append({
            "id": f"role-{role}", "field": "roles", "code": "ROLE_REQUIRED_AND_FORBIDDEN",
            "reason": f"Role {role} is both required and forbidden.", "blocking": True,
            "choices": [f"require:{role}", f"forbid:{role}"], "resolvedChoice": None,
        })
    return required, forbidden, evidence, conflicts


def _local_parse(text: str) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]], str]:
    value = _normalized(text)
    if any(re.search(pattern, value) for pattern in _INJECTION_PATTERNS):
        raise ValueError("Prompt contains a forbidden system, validator or Bank/Program instruction")
    genre, evidence, conflicts = _genre(value)
    density, items, issues = _single_choice(value, "density", {
        "sparse": (r"\bsparse arrangement\b", r"\bminimal arrangement\b", r"\bsuzdrzan\w* aranzman\w*", r"\brijedak\w* aranzman\w*"),
        "balanced": (r"\bbalanced arrangement\b", r"\buravnotezen\w* aranzman\w*"),
        "full": (r"\bfull arrangement\b", r"\bdense arrangement\b", r"\bpun\w* aranzman\w*", r"\bgust\w* aranzman\w*"),
    }, "balanced")
    evidence += items; conflicts += issues
    syncopation, items, issues = _single_choice(value, "syncopation", {
        "low": (r"\bstraight groove\b", r"\bravan\w* groove\b", r"\bbez sinkop\w*"),
        "medium": (r"\bmoderate syncopation\b", r"\bumjeren\w* sinkop\w*"),
        "high": (r"\bsyncopated\b", r"\bstrong syncopation\b", r"\bsinkopiran(?:i|a|o|e|u)?\b", r"\bjake sinkop\w*"),
    }, "medium")
    evidence += items; conflicts += issues
    space, items, issues = _single_choice(value, "space", {
        "dry": (r"\bdry mix\b", r"\btight space\b", r"\bsuh\w* miks\w*"),
        "balanced": (r"\bbalanced space\b", r"\buravnotezen\w* prostor\w*"),
        "open": (r"\bopen mix\b", r"\bairy\b", r"\bprozrac\w*", r"\botvoren\w* miks\w*"),
    }, "balanced")
    evidence += items; conflicts += issues
    transitions, items, issues = _single_choice(value, "transitions", {
        "subtle": (r"\bsubtle transitions?\b", r"\bsoft fills?\b", r"\bsuptiln\w* prijelaz\w*", r"\bnjezn\w* fill\w*"),
        "balanced": (r"\bbalanced transitions?\b", r"\buravnotezen\w* prijelaz\w*"),
        "dramatic": (r"\bdramatic transitions?\b", r"\bbig fills?\b", r"\bdramaticn\w* prijelaz\w*", r"\bsnazn\w* fill\w*"),
    }, "balanced")
    evidence += items; conflicts += issues

    energy = {"intro": 35, "verse": 45, "chorus": 75, "bridge": 60, "ending": 45}
    overall = _matches(value, (r"\b(?:lively|energetic|upbeat|zivlj\w*|energic\w*)\b",))
    if overall:
        energy = {key: min(100, score + 8) for key, score in energy.items()}
        evidence.append(_field_evidence("energyCurve", "overall:+8", overall[0], 0.88))
    for section in SECTIONS:
        score, match = _section_energy(value, section)
        if score is not None and match:
            energy[section] = score
            evidence.append(_field_evidence(f"energyCurve.{section}", score, match, 0.96))

    required, forbidden, role_evidence, role_conflicts = _roles(value)
    evidence += role_evidence; conflicts += role_conflicts
    solo_treatment = "preserve"
    expression = _matches(value, (r"\bexpressive sol\w*\b", r"\bizrazajn\w* sol\w*\b"))
    no_layers = _matches(value, (r"\bno (?:new|extra) solo(?: layers?)?\b", r"\bbez dodatn\w* sol\w*",))
    delete_original = _matches(value, (r"\b(?:delete|remove|change) (?:the )?original solo\b",
                                       r"\b(?:obrisi|ukloni|promijeni) originaln\w* sol\w*\b"))
    if expression:
        solo_treatment = "expression-only"
        evidence.append(_field_evidence("soloTreatment", solo_treatment, expression[0], 0.96))
    if no_layers:
        if expression:
            conflicts.append({
                "id": "solo-treatment", "field": "soloTreatment", "code": "CONFLICTING_SOLO_TREATMENT",
                "reason": "Expression layers and no-new-layers were both requested.", "blocking": True,
                "choices": ["expression-only", "no-new-layers"], "resolvedChoice": None,
            })
        else:
            solo_treatment = "no-new-layers"
        evidence.append(_field_evidence("soloTreatment", "no-new-layers", no_layers[0], 0.96))
    if delete_original:
        conflicts.append({
            "id": "original-solo-safety", "field": "soloTreatment",
            "code": "ORIGINAL_SOLO_MUTATION_FORBIDDEN",
            "reason": "Automatic changes to original solo notes are forbidden.", "blocking": True,
            "choices": ["preserve-original"], "resolvedChoice": None,
        })

    locked = []
    for match in re.finditer(r"(?:lock|zakljuc\w*)\s+(i[12]cv1|v[1-4]cv1|f[12]cv1|e[12]cv1)", value):
        locked.append(match.group(1))
        evidence.append(_field_evidence("lockedElements", match.group(1), match.group(0), 1.0))
    vague_lock = _matches(value, (r"(?:lock|zakljuc\w*)\s+(?:chorus|refren|verse|strof\w*|intro|uvod)",))
    if vague_lock:
        conflicts.append({
            "id": "section-lock", "field": "lockedElements", "code": "SECTION_LOCK_REQUIRES_ELEMENT",
            "reason": "A section lock must be mapped to an exact Pa800 element.", "blocking": True,
            "choices": list(ELEMENTS), "resolvedChoice": None,
        })

    tolerance = 50
    no_change = _matches(value, (r"\bdo not change\b", r"\bno changes\b", r"\bne mijenjaj\b", r"\bbez promjen\w*"))
    radical = _matches(value, (r"\bradical changes?\b", r"\btransform heavily\b", r"\bvelik\w* promjen\w*", r"\bradikaln\w*"))
    if no_change:
        tolerance = 0
        evidence.append(_field_evidence("transformationTolerance", 0, no_change[0], 0.98))
    if radical:
        if no_change:
            conflicts.append({
                "id": "transformation-tolerance", "field": "transformationTolerance",
                "code": "CONFLICTING_TRANSFORMATION_TOLERANCE",
                "reason": "Both zero and high transformation tolerance were requested.",
                "blocking": True, "choices": [0, 90], "resolvedChoice": None,
            })
        else:
            tolerance = 90
        evidence.append(_field_evidence("transformationTolerance", 90, radical[0], 0.98))

    intent = {
        "genre": genre,
        "energyCurve": [{"section": section, "value": energy[section]} for section in SECTIONS],
        "density": density, "syncopation": syncopation, "space": space,
        "transitions": transitions, "soloTreatment": solo_treatment,
        "requiredRoles": required, "forbiddenRoles": forbidden,
        "lockedElements": sorted(set(locked)), "transformationTolerance": tolerance,
    }
    return intent, evidence, conflicts, _language(text)


def _contains_protected_key(value: Any, key: str = "") -> bool:
    normalized = re.sub(r"[^a-z0-9]", "", key.lower())
    if normalized in _PROTECTED_AI_KEYS:
        return True
    if isinstance(value, Mapping):
        return any(_contains_protected_key(item, str(name)) for name, item in value.items())
    if isinstance(value, (list, tuple)):
        return any(_contains_protected_key(item) for item in value)
    if isinstance(value, (bytes, bytearray, memoryview)):
        return True
    return False


def validate_ai_enrichment(value: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(value, Mapping) or _contains_protected_key(value):
        raise ValueError("AI enrichment contains forbidden MIDI, path, validator or Bank/Program authority")
    allowed = {"genre", "density", "syncopation", "space", "transitions",
               "soloTreatment", "energyHints", "confidence", "explanation"}
    unknown = sorted(set(value) - allowed)
    if unknown:
        raise ValueError("Unknown AI enrichment fields: " + ", ".join(unknown))
    enums = {
        "genre": GENRES, "density": DENSITIES, "syncopation": SYNCOPATIONS,
        "space": SPACES, "transitions": TRANSITIONS, "soloTreatment": SOLO_TREATMENTS,
    }
    for field, choices in enums.items():
        if field in value and value[field] not in choices:
            raise ValueError(f"AI enrichment {field} is outside controlled vocabulary")
    confidence = value.get("confidence", 0.0)
    if isinstance(confidence, bool) or not isinstance(confidence, (int, float)) or not 0 <= confidence <= 1:
        raise ValueError("AI enrichment confidence must be in range 0..1")
    explanation = value.get("explanation", "")
    if not isinstance(explanation, str) or len(explanation) > 1000:
        raise ValueError("AI enrichment explanation is invalid")
    hints = value.get("energyHints", {})
    if not isinstance(hints, Mapping) or not set(hints) <= set(SECTIONS):
        raise ValueError("AI energy hints contain an unknown section")
    if any(isinstance(score, bool) or not isinstance(score, int) or not 0 <= score <= 100
           for score in hints.values()):
        raise ValueError("AI energy hints must be integer values in range 0..100")
    return json.loads(json.dumps(value, ensure_ascii=False))


@dataclass(frozen=True)
class BriefAiPolicy:
    enabled: bool = False
    explicit_consent: bool = False
    metadata_only: bool = True


def _document(text: str, intent: dict[str, Any], evidence: list[dict[str, Any]],
              conflicts: list[dict[str, Any]], language: str, adapter: dict[str, Any],
              extra_assumptions: list[str] | None = None) -> dict[str, Any]:
    assumptions = [
        f"{field} defaulted to {intent[field]}"
        for field in ("genre", "density", "syncopation", "space", "transitions", "soloTreatment")
        if not any(item["field"] == field for item in evidence)
    ]
    assumptions += list(extra_assumptions or [])
    warnings = [item["reason"] for item in conflicts]
    required = any(item["blocking"] and item["resolvedChoice"] is None for item in conflicts)
    summary = (
        f"{intent['genre']} / {intent['density']} / {intent['syncopation']} syncopation; "
        f"{intent['transitions']} transitions; solo={intent['soloTreatment']}"
    )
    result = {
        "schema": BRIEF_SCHEMA, "version": BRIEF_VERSION,
        "sourceText": text, "sourceTextSha256": sha256(text.encode("utf-8")).hexdigest(),
        "language": language, "intent": intent, "evidence": evidence,
        "conflicts": conflicts,
        "approval": {"required": required, "status": "REQUIRED" if required else "NOT_REQUIRED",
                     "approvedBy": None, "resolutions": {}},
        "understanding": {"title": "AI je razumio", "summary": summary,
                          "assumptions": assumptions, "warnings": warnings},
        "adapter": adapter,
        "readyForPlanning": not required,
        "safety": {
            "readOnly": True, "midiBytesAccepted": False, "midiWriterAvailable": False,
            "pathWriteAllowed": False, "bankProgramAuthority": False,
            "validatorBypassAllowed": False, "factoryDynamicsAuthority": True,
            "goldDynamicsAuthority": False, "originalSoloMutationAllowed": False,
        },
    }
    result["briefHash"] = _hash(result)
    validate_producer_brief_v2(result)
    return result


def _merge_enrichment(intent: dict[str, Any], evidence: list[dict[str, Any]],
                      enrichment: Mapping[str, Any]) -> tuple[dict[str, Any], list[dict[str, Any]], list[str]]:
    output = deepcopy(intent)
    additions = list(evidence)
    explicit = {item["field"].split(".", 1)[0] for item in evidence if item["source"] == "local-rule"}
    confidence = float(enrichment.get("confidence", 0.0))
    assumptions = []
    for field in ("genre", "density", "syncopation", "space", "transitions", "soloTreatment"):
        if field in enrichment and field not in explicit and confidence >= 0.5:
            output[field] = enrichment[field]
            additions.append(_field_evidence(field, enrichment[field], "optional-ai-enrichment",
                                             confidence, "optional-ai"))
    if confidence >= 0.5:
        by_section = {item["section"]: item for item in output["energyCurve"]}
        for section, score in enrichment.get("energyHints", {}).items():
            if not any(item["field"] == f"energyCurve.{section}" for item in evidence):
                by_section[section]["value"] = score
                additions.append(_field_evidence(f"energyCurve.{section}", score,
                                                 "optional-ai-enrichment", confidence, "optional-ai"))
    if enrichment.get("explanation"):
        assumptions.append(str(enrichment["explanation"]))
    return output, additions, assumptions


def build_producer_brief(
    text: str,
    policy: BriefAiPolicy | None = None,
    cloud_call: Callable[[Mapping[str, Any]], Any] | None = None,
) -> dict[str, Any]:
    if not isinstance(text, str) or not 1 <= len(text.strip()) <= 2000:
        raise ValueError("Producer intent must contain 1..2000 characters")
    source_text = text.strip()
    intent, evidence, conflicts, language = _local_parse(source_text)
    policy = policy or BriefAiPolicy()
    adapter = {"mode": "local", "cloudRequested": policy.enabled,
               "explicitConsent": policy.explicit_consent,
               "metadataOnly": policy.metadata_only, "responseAccepted": False,
               "fallbackReason": "cloud-disabled" if not policy.enabled else None}
    assumptions: list[str] = []
    if policy.enabled:
        if cloud_call is None:
            raise ValueError("Enabled AI enrichment requires a cloud adapter")
        payload = {
            "schema": "dna-producer-intent-metadata", "version": "1.0",
            "sourceText": source_text, "language": language,
            "textSha256": sha256(source_text.encode("utf-8")).hexdigest(),
            "allowedOutputFields": ["genre", "density", "syncopation", "space",
                                    "transitions", "soloTreatment", "energyHints",
                                    "confidence", "explanation"],
        }
        dispatched = dispatch_optional_cloud(
            CloudPolicy(policy.enabled, policy.explicit_consent, policy.metadata_only),
            payload, cloud_call, lambda _: {},
        )
        adapter["mode"] = dispatched["mode"]
        adapter["fallbackReason"] = None if dispatched["mode"] == "cloud" else dispatched["reason"]
        if dispatched["mode"] == "cloud":
            try:
                enrichment = validate_ai_enrichment(dispatched["result"])
                intent, evidence, assumptions = _merge_enrichment(intent, evidence, enrichment)
                adapter["responseAccepted"] = True
            except ValueError as exc:
                adapter.update({"mode": "local-fallback", "responseAccepted": False,
                                "fallbackReason": f"cloud-response-rejected:{exc}"})
        else:
            assumptions.append("Optional AI was unavailable; local parser result retained.")
    return _document(source_text, intent, evidence, conflicts, language, adapter, assumptions)


def approve_producer_brief(brief: Mapping[str, Any], approved_by: str,
                           resolutions: Mapping[str, Any]) -> dict[str, Any]:
    validate_producer_brief_v2(brief)
    if not isinstance(approved_by, str) or not approved_by.strip():
        raise ValueError("ProducerBrief approval requires an explicit user identity")
    if not isinstance(resolutions, Mapping):
        raise ValueError("ProducerBrief resolutions must be an object")
    output = deepcopy(brief)
    unresolved = [item for item in output["conflicts"] if item["blocking"] and item["resolvedChoice"] is None]
    if set(resolutions) != {item["id"] for item in unresolved}:
        raise ValueError("Every blocking ProducerBrief conflict requires exactly one resolution")
    for conflict in unresolved:
        choice = resolutions[conflict["id"]]
        if choice not in conflict["choices"]:
            raise ValueError(f"Invalid resolution for conflict {conflict['id']}")
        conflict["resolvedChoice"] = choice
        field = conflict["field"]
        if field in {"genre", "density", "syncopation", "space", "transitions",
                     "soloTreatment", "transformationTolerance"}:
            output["intent"][field] = "preserve" if choice == "preserve-original" else choice
        elif field == "roles":
            action, role = choice.split(":", 1)
            output["intent"]["requiredRoles"] = [item for item in output["intent"]["requiredRoles"] if item != role]
            output["intent"]["forbiddenRoles"] = [item for item in output["intent"]["forbiddenRoles"] if item != role]
            output["intent"]["requiredRoles" if action == "require" else "forbiddenRoles"].append(role)
            output["intent"]["requiredRoles"].sort(); output["intent"]["forbiddenRoles"].sort()
        elif field == "lockedElements":
            output["intent"]["lockedElements"] = sorted(set(output["intent"]["lockedElements"] + [choice]))
    output["approval"] = {"required": True, "status": "APPROVED",
                          "approvedBy": approved_by.strip(), "resolutions": dict(resolutions)}
    output["readyForPlanning"] = True
    output["understanding"]["warnings"] = []
    output["briefHash"] = _hash(output)
    validate_producer_brief_v2(output)
    return output


def validate_producer_brief_v2(value: Mapping[str, Any]) -> None:
    root = {"schema", "version", "sourceText", "sourceTextSha256", "language", "intent",
            "evidence", "conflicts", "approval", "understanding", "adapter",
            "readyForPlanning", "safety", "briefHash"}
    if not isinstance(value, Mapping) or set(value) != root:
        raise ValueError("ProducerBrief 2.0 root fields mismatch")
    if value["schema"] != BRIEF_SCHEMA or value["version"] != BRIEF_VERSION:
        raise ValueError("Unsupported ProducerBrief schema/version")
    text = value["sourceText"]
    if not isinstance(text, str) or not 1 <= len(text) <= 2000:
        raise ValueError("ProducerBrief source text is invalid")
    if value["sourceTextSha256"] != sha256(text.encode("utf-8")).hexdigest():
        raise ValueError("ProducerBrief source text hash mismatch")
    if value["language"] not in {"hr", "en", "mixed", "unknown"}:
        raise ValueError("ProducerBrief language is invalid")
    intent_keys = {"genre", "energyCurve", "density", "syncopation", "space", "transitions",
                   "soloTreatment", "requiredRoles", "forbiddenRoles", "lockedElements",
                   "transformationTolerance"}
    intent = value["intent"]
    if not isinstance(intent, Mapping) or set(intent) != intent_keys:
        raise ValueError("ProducerBrief intent fields mismatch")
    enum_fields = {"genre": GENRES, "density": DENSITIES, "syncopation": SYNCOPATIONS,
                   "space": SPACES, "transitions": TRANSITIONS,
                   "soloTreatment": SOLO_TREATMENTS}
    if any(intent[field] not in choices for field, choices in enum_fields.items()):
        raise ValueError("ProducerBrief intent contains a value outside controlled vocabulary")
    curve = intent["energyCurve"]
    if not isinstance(curve, list) or [item.get("section") for item in curve] != list(SECTIONS):
        raise ValueError("ProducerBrief energy curve must cover each controlled section exactly once")
    if any(set(item) != {"section", "value"} or isinstance(item["value"], bool)
           or not isinstance(item["value"], int) or not 0 <= item["value"] <= 100 for item in curve):
        raise ValueError("ProducerBrief energy curve is invalid")
    for field, choices in (("requiredRoles", ROLES), ("forbiddenRoles", ROLES),
                           ("lockedElements", ELEMENTS)):
        items = intent[field]
        if not isinstance(items, list) or len(items) != len(set(items)) or not set(items) <= set(choices):
            raise ValueError(f"ProducerBrief {field} is invalid")
    tolerance = intent["transformationTolerance"]
    if isinstance(tolerance, bool) or not isinstance(tolerance, int) or not 0 <= tolerance <= 100:
        raise ValueError("ProducerBrief transformation tolerance is invalid")
    if any(set(item) != {"field", "value", "matchedText", "confidence", "source"}
           for item in value["evidence"]):
        raise ValueError("ProducerBrief evidence fields mismatch")
    if any(set(item) != {"id", "field", "code", "reason", "blocking", "choices", "resolvedChoice"}
           for item in value["conflicts"]):
        raise ValueError("ProducerBrief conflict fields mismatch")
    approval = value["approval"]
    if set(approval) != {"required", "status", "approvedBy", "resolutions"}:
        raise ValueError("ProducerBrief approval fields mismatch")
    unresolved = any(item["blocking"] and item["resolvedChoice"] is None for item in value["conflicts"])
    if unresolved and (approval["status"] != "REQUIRED" or value["readyForPlanning"] is not False):
        raise ValueError("Unresolved ProducerBrief conflicts must block planning")
    if approval["status"] == "APPROVED" and (not approval["approvedBy"] or unresolved):
        raise ValueError("Approved ProducerBrief is incomplete")
    understanding = value["understanding"]
    if set(understanding) != {"title", "summary", "assumptions", "warnings"}:
        raise ValueError("ProducerBrief understanding fields mismatch")
    if understanding["title"] != "AI je razumio":
        raise ValueError("ProducerBrief understanding title is invalid")
    adapter = value["adapter"]
    if set(adapter) != {"mode", "cloudRequested", "explicitConsent", "metadataOnly",
                        "responseAccepted", "fallbackReason"}:
        raise ValueError("ProducerBrief adapter fields mismatch")
    safety = value["safety"]
    expected_safety = {
        "readOnly": True, "midiBytesAccepted": False, "midiWriterAvailable": False,
        "pathWriteAllowed": False, "bankProgramAuthority": False,
        "validatorBypassAllowed": False, "factoryDynamicsAuthority": True,
        "goldDynamicsAuthority": False, "originalSoloMutationAllowed": False,
    }
    if safety != expected_safety:
        raise ValueError("ProducerBrief safety contract was weakened")
    if value["briefHash"] != _hash(value):
        raise ValueError("ProducerBrief briefHash mismatch")


def execute_producer_brief_api(payload: Mapping[str, Any],
                               cloud_call: Callable[[Mapping[str, Any]], Any] | None = None) -> dict[str, Any]:
    allowed = {"text", "cloudEnabled", "explicitConsent", "metadataOnly"}
    if not isinstance(payload, Mapping) or not set(payload) <= allowed or "text" not in payload:
        raise ValueError("ProducerBrief API accepts only text and explicit cloud policy fields")
    for field, default in (("cloudEnabled", False), ("explicitConsent", False),
                           ("metadataOnly", True)):
        if not isinstance(payload.get(field, default), bool):
            raise ValueError(f"ProducerBrief API field {field} must be boolean")
    policy = BriefAiPolicy(payload.get("cloudEnabled", False),
                           payload.get("explicitConsent", False),
                           payload.get("metadataOnly", True))
    return build_producer_brief(str(payload["text"]), policy, cloud_call)


def execute_producer_brief_gui(payload: Mapping[str, Any],
                               cloud_call: Callable[[Mapping[str, Any]], Any] | None = None) -> dict[str, Any]:
    return execute_producer_brief_api(payload, cloud_call)