"""assistant_brain.py — milestone 4.68 (P0: AI Brain v1).

Deterministic, stdlib-only "assistant brain" over the Session Pass report.
This is the AI-model layer of the optimizer, with a grounding contract:

1. Every claim the brain emits is derived programmatically from the report
   JSON and carries a `source` json-path tag (provenance).
2. The brain NEVER changes action statuses, never invents numbers, and
   refuses music-quality/generation questions it cannot ground.
3. Output is in Serbian, short, with options.

Design note (docs/ai-model-plan-4.67.md): BrainLLM (Ollama/OpenAI-compatible)
will later sit behind the same `answer()` interface; the eval harness in
test_assistant_brain_v468.py is the golden set both implementations must pass.

CLI: reads JSON from stdin:
    {"text": "zašto je A05 zaključano?", "report": <session pass report>|null}
prints JSON:
    {"intent":..., "reply":..., "claims":[{"text","source"}...], "tool":...}
"""

from __future__ import annotations

import json
import re
import sys
from typing import Any

STATUS_SR = {
    "READY": "spremna za primenu",
    "APPLIED": "primenjeno",
    "SKIPPED": "preskočeno",
    "NEEDS_DECISION": "traži tvoju odluku",
    "LOCKED": "zaključano (nije primenljivo bez device snimka)",
}
ROLE_SR = {
    "bass": "bas", "drums": "bubanj", "percussion": "perkusije",
    "accompaniment": "pratnja", "solo": "solo", "lead": "solo",
}

# --------------------------------------------------------------------------
# NLU v1: intents + parameters (sr/bs tolerant, canonical sinonimi)
# --------------------------------------------------------------------------

HELP_WORDS = ["pomoć", "pomoc", "help", "sta mozes", "šta možeš", "sta radis", "šta radiš",
              "sta sve", "šta sve", "sta mogu", "šta mogu"]
RULES_WORDS = ["pravila", "licenc", "invarijant", "pravil"]
GREET_WORDS = ["zdravo", "cao", "ćao", "cao", "hej", "dobro jutro", "dobar dan", "ej"]
WHAT_NEXT_WORDS = ["sta dalje", "šta dalje", "sta sad", "šta sad", "sta mi preporucujes",
                   "šta mi preporučuješ", "sta preporucujes", "sugestij"]
EXPLAIN_WORDS = ["objasni", "zasto", "zašto", "kako to", "sto je", "što je", "sta znaci",
                 "šta znači", "obrazlozi", "obrazloži", "razlog"]
SUMMARIZE_WORDS = ["sažmi", "sazmi", "ukratko", "rezime", "sta si izmerio", "šta si izmerio",
                   "sta si nasao", "šta si našao", "sta si uradio", "šta si uradio"]
ANALYZE_WORDS = ["analiziraj", "obradi", "analize", "analizu"]
APPLY_WORDS = ["primeni", "primeni", "apply"]
MUSIC_CLAIM_WORDS = ["kako zvuc", "kako zvuč", "da li je bolje", "jel bolje", "je li bolje",
                     "zvuči li", "zvuc", "zvuči", "generisi", "generiši", "napravi pesmu",
                     "komponuj", "sastavi pesmu", "kvalitet zvuka", "svidja", "sviđa"]
MEMORY_WORDS = ["zapamti", "seti se", "sjeti se", "sta sam ti rekao", "šta sam ti rekao",
                "koji fajl", "sta smo radili", "šta smo radili"]
SESS_WORDS = ["nova sesija", "pocni novo", "počni novo", "obrisi sesiju", "obriši sesiju"]

ACTION_RE = re.compile(r"A\d{2}", re.I)
STATUS_RE = re.compile(r"READY|LOCKED|SKIPPED|NEEDS_DECISION|APPLIED", re.I)


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", text.strip().lower())


def understand(text: str) -> dict[str, Any]:
    """Intent + parameters from raw user text (deterministic rules)."""
    t = _norm(text)
    if not t:
        return {"intent": "empty", "params": {}, "confidence": "high"}
    if any(w in t for w in HELP_WORDS):
        return {"intent": "help", "params": {}, "confidence": "high"}
    if any(w in t for w in RULES_WORDS):
        return {"intent": "rules", "params": {}, "confidence": "high"}
    if any(w in t for w in GREET_WORDS) and len(t) < 30:
        return {"intent": "greet", "params": {}, "confidence": "high"}
    if any(w in t for w in MUSIC_CLAIM_WORDS):
        return {"intent": "refuse_music_claim", "params": {}, "confidence": "high"}
    if any(w in t for w in SESS_WORDS):
        return {"intent": "session_new", "params": {}, "confidence": "high"}
    actions = ACTION_RE.findall(t)
    if any(w in t for w in SUMMARIZE_WORDS):
        return {"intent": "summarize", "params": {}, "confidence": "high"}
    if any(w in t for w in ANALYZE_WORDS):
        pr = _preset_in_text(t)
        return {"intent": "analyze", "params": {"preset": pr},
                "confidence": "medium" if pr else "low"}
    if any(w in t for w in APPLY_WORDS):
        return {"intent": "apply", "params": {"actions": actions},
                "confidence": "medium" if actions else "low"}
    if any(w in t for w in EXPLAIN_WORDS) or actions or ("zakljucan" in t or "zaključan" in t):
        params: dict[str, Any] = {"actions": actions}
        status = None
        m = STATUS_RE.search(t)
        if m:
            status = m.group(0).upper()
        params["status"] = status
        if any(w in t for w in WHAT_NEXT_WORDS):
            return {"intent": "what_next", "params": params, "confidence": "high"}
        return {"intent": "explain", "params": params, "confidence": "high"}
    if any(w in t for w in WHAT_NEXT_WORDS):
        return {"intent": "what_next", "params": {}, "confidence": "high"}
    if any(w in t for w in MEMORY_WORDS):
        return {"intent": "memory", "params": {}, "confidence": "high"}
    return {"intent": "unknown", "params": {}, "confidence": "low"}


def _preset_in_text(t: str) -> str | None:
    for pid in ("reference-style", "fixture", "session35", "song19-01"):
        if pid in t or pid.split("-")[0] in t:
            return pid
    return None


# --------------------------------------------------------------------------
# Grounded claims (NLG): svaka tvrdnja nosi izvor u izveštaju
# --------------------------------------------------------------------------

def claims_from_report(report: dict[str, Any] | None) -> list[dict[str, str]]:
    if not report:
        return []
    claims: list[dict[str, str]] = []
    f = report.get("fileFacts") or {}
    if "markerCount" in f:
        claims.append({
            "text": f"Fajl ima {f['markerCount']} Korg markera"
                    + (f" ({f.get('elementsInStyle')} elemenata stila)" if f.get("elementsInStyle") else "")
                    + f" i {len(f.get('channels') or [])} kanala sa notama (SMF {f.get('format')}, {f.get('ppq')} ppq).",
            "source": "report.fileFacts"})
    patterns = report.get("perRolePatterns") or {}
    for ch in sorted(patterns, key=int):
        b = patterns[ch]
        role = ROLE_SR.get(b.get("role", ""), b.get("role", ""))
        vel = (b.get("velocity") or {}).get("q50")
        claims.append({
            "text": f"{role.capitalize()} (kanal {ch}): {b.get('noteCount')} nota, "
                    f"{b.get('densityNotesPerBar')} nota/taktu"
                    + (f", velocity q50 = {vel}" if vel is not None else "") + ".",
            "source": f"report.perRolePatterns.{ch}"})
    for g in report.get("grooveVsHuman") or []:
        claims.append({
            "text": f"Kanal {g.get('channel')} ({g.get('role')}): tajming std = {g.get('stdMs')} ms, "
                    f"{round((g.get('exactOnGridShare') or 0) * 100)}% nota tačno na gridu "
                    f"(ljudska referenca ≈ 28 ms).",
            "source": "report.grooveVsHuman"})
    for a in report.get("actions") or []:
        st = a.get("status", "?")
        txt = f"{a.get('id')} — {st}: {STATUS_SR.get(st, st.lower())}"
        reason = a.get("reason") or a.get("effect")
        if reason:
            txt += f" ({reason})"
        if a.get("artifact") and st == "APPLIED":
            txt += f" → artefakt {a.get('artifact')}"
        claims.append({"text": txt + ".", "source": f"report.actions[{a.get('id')}]"})
    mix = (report.get("mixPlan") or {}).get("targets")
    if mix:
        targets = ", ".join(f"ch{k}→{v}" for k, v in sorted(mix.items()))
        claims.append({"text": f"CC11 plan za miks: {targets}.", "source": "report.mixPlan.targets"})
    pr = report.get("patternRecognition") or {}
    if pr:
        cm = pr.get("corpusMatches") or {}
        titles = sorted({t for m in cm.values() for t in (m.get("titles") or [])})
        um = pr.get("userReferenceMatches") or {}
        files = sorted({u for m in um.values() for u in (m.get("userFiles") or [])})
        parts = [f"analizirano {pr.get('barsAnalyzed')} taktova (ch9/10)"]
        if titles:
            parts.append(f"{len(cm)} redova tačno kao korpus (npr. {', '.join(titles[:3])})")
        else:
            parts.append("nema tačnih poklapanja sa 468 korpus obrazaca")
        if files:
            parts.append(f"poklapanja sa tvojim referencama: {', '.join(files)}")
        claims.append({"text": "Prepoznavanje obrazaca: " + "; ".join(parts) + ".",
                       "source": "report.patternRecognition"})
    return claims


def suggest_next(report: dict[str, Any] | None) -> list[str]:
    if not report:
        return ["Priloži MIDI fajl ili izaberi demo iz menija (npr. „analiziraj reference-style”)."]
    acts = report.get("actions") or []
    ready = [a["id"] for a in acts if a.get("status") == "READY"]
    dec = [a["id"] for a in acts if a.get("status") == "NEEDS_DECISION"]
    applied = [a["id"] for a in acts if a.get("status") == "APPLIED"]
    out: list[str] = []
    if applied:
        out.append(f"Primenjeno: {', '.join(applied)} — preuzmi artefakte i proveri na klavijaturi.")
    if ready:
        out.append(f"READY akcije čekaju tvoju potvrdu: {', '.join(ready)} — reci „primeni sve” ili označi u planu.")
    if dec:
        out.append(f"Za odluku: {', '.join(dec)} — pitaj me „objasni {dec[0]}” pre nego što odlučiš.")
    locked = [a["id"] for a in acts if a.get("status") == "LOCKED"]
    if locked:
        out.append(f"{', '.join(locked)} je zaključano do device snimka sa Pa800 — to ne mogu da zaobiđem.")
    if not out:
        out.append("Trenutno nema READY akcija; sledeći korak je analiza drugog fajla.")
    return out


def _reply_for(report: dict[str, Any] | None, claims: list[dict[str, str]],
               suggest: bool = False) -> str:
    if not report:
        return "Još nema analize u ovoj sesiji. Priloži MIDI fajl (📎 ili drag & drop) ili reci „analiziraj reference-style”."
    lines = [c["text"] for c in claims]
    if suggest:
        lines += ["Predlog: " + s for s in suggest_next(report)]
    return "\n".join(lines)


# --------------------------------------------------------------------------
# answer(): glavni ulaz (BrainV1). LLM adapter kasnije iza istog interfejsa.
# --------------------------------------------------------------------------

def answer(text: str, report: dict[str, Any] | None,
           history: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    u = understand(text)
    intent = u["intent"]
    claims = claims_from_report(report)
    params = u["params"]

    if intent == "empty":
        return {"intent": intent, "reply": "Kako mogu da pomognem?", "claims": [], "tool": None}
    if intent == "greet":
        return {"intent": intent,
                "reply": "Zdravo! Ja sam DNA Optimizer asistent. Analiziram tvoj MIDI, objašnjavam plan "
                         "i primenjujem samo ono što potvrdiš — ništa ne izmišljam.",
                "claims": [], "tool": None}
    if intent == "help":
        return {"intent": intent,
                "reply": "Mogu: (1) „analiziraj X” — X je demo ili priložen fajl; (2) „objasni A01/A05” — "
                         "zašto je akcija u nekom statusu; (3) „šta dalje” — predlog sledećeg koraka; "
                         "(4) „sažmi” — rezime izveštaja; (5) „koja su pravila” — invarijante.",
                "claims": [], "tool": None}
    if intent == "rules":
        return {"intent": intent,
                "reply": "Invarijante: original se nikad ne menja (samo novi artefakti); primenjuju se samo "
                         "READY akcije; LOCKED/NEEDS_DECISION ne mogu da se primene; FACTORY velocity je "
                         "autoritet; DNC/slap/pop trigeri su zaključani do device snimka.",
                "claims": [], "tool": None}
    if intent == "refuse_music_claim":
        return {"intent": intent,
                "reply": "To ne mogu da tvrdim — nemam slušni model ni pravo da procenjujem „kako zvuči” ili da "
                         "generišem muziku. Mogu samo da izmerim (analiza) i primenim dokazane optimizacije "
                         "(READY akcije) koje ti onda čuješ na klavijaturi.",
                "claims": [], "tool": None}
    if intent == "session_new":
        return {"intent": intent, "reply": "__NEW_SESSION__", "claims": [], "tool": None}
    if intent == "analyze":
        if params.get("preset"):
            return {"intent": intent, "reply": "",
                    "claims": [],
                    "tool": {"type": "analyzePreset", "presetId": params["preset"]}}
        return {"intent": intent,
                "reply": "Za analizu: priloži MIDI fajl (📎 / drag & drop) ili reci npr. „analiziraj fixture”.",
                "claims": [], "tool": None}
    if intent == "apply":
        if params.get("actions"):
            return {"intent": intent, "reply": "",
                    "claims": [],
                    "tool": {"type": "applyActions", "actions": params["actions"]}}
        return {"intent": intent,
                "reply": "„Primeni” + koje akcije: npr. „primeni A01 A02” ili „primeni sve” (sve READY).",
                "claims": [], "tool": None}

    if intent == "memory":
        if not history:
            return {"intent": intent, "reply": "Ova sesija još nema istoriju.", "claims": [], "tool": None}
        names = [h for h in history if h.get("kind") == "plan"]
        tail = history[-3:]
        lines = [f"{h.get('role')}: {str(h.get('text'))[:90]}" for h in tail]
        return {"intent": intent,
                "reply": "Iz ove sesije pamtim (poslednje):\n" + "\n".join(lines)
                         + (f"\nAnalizirani fajlovi: {len(names)}." if names else ""),
                "claims": [], "tool": None}

    # intents that need a report
    if intent in ("explain", "summarize", "what_next") and not report:
        return {"intent": intent,
                "reply": "Još nema analize u ovoj sesiji. Prvo reci „analiziraj "
                         "reference-style” ili priloži fajl — onda mogu da objasnim "
                         "i predložim sledeći korak.",
                "claims": [], "tool": None}

    if intent == "explain":
        target = params.get("actions") or []
        status = params.get("status")
        if target:
            needle = target[0].upper()
            sel = [c for c in claims
                   if c["source"].startswith("report.actions") and needle in c["text"]]
            if not sel:
                sel = [c for c in claims if c["source"].startswith("report.actions")]
        elif status:
            sel = [c for c in claims
                   if c["source"].startswith("report.actions") and status in c["text"]]
        else:
            sel = [c for c in claims if c["source"].startswith("report.actions")]
        return {"intent": intent,
                "reply": _reply_for(report, sel, suggest=False),
                "claims": [{"text": c["text"], "source": c["source"]} for c in sel],
                "tool": None}
    if intent == "summarize":
        return {"intent": intent, "reply": _reply_for(report, claims[:8], suggest=False),
                "claims": [{"text": c["text"], "source": c["source"]} for c in claims[:8]],
                "tool": None}
    if intent == "what_next":
        return {"intent": intent, "reply": _reply_for(report, [], suggest=True),
                "claims": [], "tool": None}
    return {"intent": intent,
            "reply": "Ne razumem najbolje. Mogu: „analiziraj X”, „objasni A05”, „šta dalje”, „sažmi”, "
                     "„koja su pravila”, „pomoć”.",
            "claims": [], "tool": None}


def _main() -> int:
    payload = json.load(sys.stdin)
    text = str(payload.get("text") or "")
    report = payload.get("report")
    history = payload.get("history") or []
    out = answer(text, report, history)
    sys.stdout.write(json.dumps(out, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
