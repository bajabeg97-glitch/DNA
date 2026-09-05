"""composer_corpus.py — korpus + graditelj korpusa v1 (DNA Composer, 4.69).

Gradi kanonski korpus u score language v1 (composer_tokens) iz tri izvora:

1. **vendor** — 468 fabričkih drum patterna (vendor-max-4.64/dmp-midi, MIT)
   preko postojećeg pattern_library (normalizacija već daje gm/role/steps);
2. **synthetic** — deterministički v0 "composer" (templejti + pattern
   evidencija): kompletni aranžmani (bass+acc+drums, opciono solo) sa
   sekcijama (Intro/Verse/Chorus/...), CC11 krivama i seed reprodukcijom;
3. **engine/user** — pravi .mid iz artifacts-max-4.4x..4.6x (izlazi engine-a
   i korisnički upload fajlovi), kvantizovani na 16-step grid, uz zapis
   program/controller informacija u meta (informativno; generator ih nikad
   ne emituje).

Svaki item mora proći composer_validator 100% (batch report u izlazu).

CLI:  python3.11 -m dna_midi_studio.composer_corpus --out artifacts-max-4.69
Determinističan (seed + sortiranje), stdlib-only.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import random
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

sys.path.insert(0, str(ROOT / "dna_midi_studio"))
from composer_tokens import canonical_item, item_digest, encode  # noqa: E402
import composer_validator as pv  # noqa: E402
import pattern_library as pl  # noqa: E402

# ---------------------------------------------------------------------------
# 1) vendor: dmp-midi pattern moments
# ---------------------------------------------------------------------------

GM = {  # instrument -> (gm, default vel class)
    "BassDrum": (36, 6), "RimShot": (37, 6), "SnareDrum": (38, 6),
    "Clap": (39, 6), "ClosedHiHat": (42, 5), "LowTom": (43, 6),
    "OpenHiHat": (46, 5), "MediumTom": (47, 6), "Cymbal": (49, 6),
    "HighTom": (50, 6), "Tambourine": (54, 5), "Cowbell": (56, 6),
}


def _sig_steps(sig: str) -> tuple[list[int], int]:
    num, den = sig.split("/")
    ns, ds = int(num), int(den)
    if ds == 8:
        return [12, 8], 12          # 12/8 -> 12 steps (triplet eighths)
    if ns == 3:
        return [3, 4], 12           # 3/4 -> 12 sixteenths
    return [4, 4], 16               # 4/4 (i sve ostalo -> 4/4)

_MISSING_GM = {}


def vendor_items() -> list[dict]:
    items = []
    for n, (source, idx, raw) in enumerate(pl.iter_patterns()):
        lib = pl.normalize_pattern(source, idx, raw)
        sig, steps = _sig_steps(lib["signature"])
        nbars = max(1, lib["length"] // steps if lib["length"] % steps == 0
                    else 1)
        acc = lib.get("accentSteps") or ""
        events = []
        for inst, tr in lib["tracks"].items():
            g = GM.get(inst)
            if g is None:
                _MISSING_GM.setdefault(inst, 0)
                _MISSING_GM[inst] += 1
                continue
            gm, vdef = g
            row = tr["steps"]
            for i, ch in enumerate(row):
                if ch == "1":
                    b = i // steps
                    st = i % steps
                    v = 7 if b * steps + st < len(acc) and acc[b * steps + st] == "1" else vdef
                    events.append({"b": b, "st": st, "d": 1, "v": v, "n": gm})
        if not events:
            continue
        items.append({
            "id": f"dmp-{source}-{n:03d}-{lib['digest']}",
            "originKind": "vendor",
            "source": f"{source}:{lib['title']}",
            "license": "MIT (gvellut/dmp_midi — vendor-max-4.64/dmp-midi/NOTICE.md)",
            "bpm": 100,
            "signature": sig,
            "sections": [],
            "tracks": [{"role": "drums", "channel": 9, "events": events}],
            "cc": [],
            "meta": {"patterns": 1},
        })
    return items


# ---------------------------------------------------------------------------
# 2) synthetic: deterministic v0 composer
# ---------------------------------------------------------------------------

CHORDS = {"C": (0, 4, 7), "G": (7, 11, 14), "Am": (9, 12, 16), "F": (5, 9, 12)}
PROG = ["C", "G", "Am", "F"]

STYLES = {
    "rock":   {"bpm": 118, "sig": [4, 4], "structure": [("Intro", 2), ("Verse", 4),
               ("Chorus", 4), ("Verse", 4), ("Chorus", 4), ("Bridge", 2),
               ("Chorus", 4), ("Outro", 2)], "bass": "eighths", "arp": False,
               "energy": {"Intro": 6, "Verse": 9, "Chorus": 12, "Bridge": 7, "Outro": 5}},
    "funk":   {"bpm": 104, "sig": [4, 4], "structure": [("Intro", 2), ("Verse", 4),
               ("Chorus", 4), ("Verse", 4), ("Chorus", 4), ("Outro", 2)],
               "bass": "funky", "arp": True,
               "energy": {"Intro": 6, "Verse": 10, "Chorus": 12, "Outro": 6}},
    "ballad": {"bpm": 72, "sig": [4, 4], "structure": [("Intro", 2), ("Verse", 4),
               ("Chorus", 4), ("Verse", 4), ("Chorus", 4), ("Outro", 2)],
               "bass": "half", "arp": False,
               "energy": {"Intro": 5, "Verse": 7, "Chorus": 9, "Outro": 4}},
    "folk":   {"bpm": 96, "sig": [4, 4], "structure": [("Intro", 2), ("Verse", 4),
               ("Chorus", 4), ("Verse", 4), ("Chorus", 4), ("Outro", 2)],
               "bass": "quarters", "arp": False,
               "energy": {"Intro": 6, "Verse": 8, "Chorus": 10, "Outro": 5}},
    "ballad12": {"bpm": 63, "sig": [12, 8], "structure": [("Intro", 2), ("Verse", 4),
               ("Chorus", 4), ("Outro", 2)], "bass": "half", "arp": False,
               "energy": {"Intro": 5, "Verse": 8, "Chorus": 10, "Outro": 4}},
}


def _patterns_by_sig(sig: list[int]) -> tuple[list[dict], list[dict]]:
    """(full patterns, light kick+hats patterns) for a signature."""
    full, light = [], []
    want = 12 if sig == [12, 8] else 16
    for p in pl.build_library():
        ps, _ = _sig_steps(p["signature"])
        if ps != sig:
            continue
        if p["length"] != want:
            continue
        has = {tr["role"] for tr in p["tracks"].values()}
        if has >= {"kick", "snare", "hats"} and p["parts"] >= 3:
            full.append(p)
        if "kick" in has and "hats" in has and p["parts"] <= 2:
            light.append(p)
    return full, light


def _drum_bar(pool: list[dict], steps: int, acc_mask: list[int], rng: random.Random) -> list[dict]:
    pat = rng.choice(pool)
    evs = []
    for tr in pat["tracks"].values():
        if tr["gm"] not in GM.values() and tr["gm"] not in (36, 38, 42, 46, 49):
            continue
        for i, c in enumerate(tr["steps"]):
            if c == "1":
                st = i % steps
                v = 7 if st in acc_mask else (6 if tr["gm"] in (36, 38, 49) else 5)
                evs.append({"b": 0, "st": st, "d": 1, "v": v, "n": tr["gm"]})
    return sorted(evs, key=lambda e: (e["st"], e["n"]))


def synthetic_items(count: int = 300, seed: int = 469) -> list[dict]:
    rng = random.Random(seed)
    out = []
    styles = list(STYLES)
    for si in range(count):
        stname = styles[si % len(styles)]
        st = STYLES[stname]
        bpm = st["bpm"] + (si % 7) - 3
        sig = list(st["sig"])
        full, light = _patterns_by_sig(sig)
        if not full:
            continue
        steps = 16 if sig != [12, 8] else 12
        # structure bars
        bar_sec: list[tuple[int, str]] = []
        for sec, nbars in st["structure"]:
            for _ in range(nbars):
                bar_sec.append((len(bar_sec), sec))
        sections = [{"name": sec, "bars": sum(1 for _, s in bar_sec if s == sec)}
                    for sec in dict.fromkeys(s for _, s in bar_sec)]
        tr_drums = []
        tr_bass = []
        tr_acc = []
        tr_solo = []
        cc = []
        for b, sec in bar_sec:
            chord = CHORDS[PROG[b % len(PROG)]]
            root = 36 + chord[0] if chord[0] < 12 else chord[0]  # C3 area
            # drums: intro/outro light, chorus full+accent at bar end
            if sec in ("Intro", "Outro") and light:
                pool = light
            else:
                pool = full
            acc_mask = [12, 14] if (sec in ("Chorus", "Bridge") and si % 2 == 0) else []
            tr_drums.extend({"b": b, **e} for e in _drum_bar(pool, steps, acc_mask, rng))
            # bass line per style
            if st["bass"] == "eighths":
                note5 = (chord[1] if b % 2 == 0 else chord[2])
                tr_bass += [{"b": b, "st": 0, "d": 2, "v": 5, "n": 36 + chord[0] - 12},
                            {"b": b, "st": 6, "d": 2, "v": 5, "n": 36 + note5 - 12},
                            {"b": b, "st": 12, "d": 2, "v": 5, "n": 36 + chord[0] - 12}]
            elif st["bass"] == "funky":
                tr_bass += [{"b": b, "st": 0, "d": 2, "v": 6, "n": 36 + chord[0] - 12},
                            {"b": b, "st": 4, "d": 1, "v": 5, "n": 36 + chord[0] - 12},
                            {"b": b, "st": 8, "d": 2, "v": 6, "n": 36 + chord[2] - 12},
                            {"b": b, "st": 14, "d": 1, "v": 5, "n": 36 + chord[1] - 12}]
            elif st["bass"] == "quarters":
                tr_bass += [{"b": b, "st": 0, "d": 4, "v": 5, "n": 36 + chord[0] - 12},
                            {"b": b, "st": 8, "d": 4, "v": 5, "n": 36 + chord[1] - 12}]
            else:  # half
                tr_bass += [{"b": b, "st": 0, "d": 15 if steps == 16 else 11, "v": 4,
                             "n": 36 + chord[0] - 12}]
            # acc pad
            if st["arp"]:
                for k, off in enumerate(chord):
                    tr_acc.append({"b": b, "st": k * 4, "d": 3, "v": 3,
                                   "n": 60 + off})
            else:
                for off in chord:
                    tr_acc.append({"b": b, "st": 0, "d": 15 if steps == 16 else 11,
                                   "v": 3, "n": 55 + off})
            # solo (ballad/folk choruses): short motif from chord tones
            if stname in ("ballad", "folk") and sec == "Chorus":
                for k in range(4):
                    off = chord[(b + k) % len(chord)]
                    tr_solo.append({"b": b, "st": k * 4, "d": 3, "v": 6, "n": 67 + off})
        # build tracks (role/channel), channel roles canonical:
        # drums=9, perc=10(none v0), bass=2, acc=4, solo=6
        trs = []
        if tr_bass:
            trs.append({"role": "bass", "channel": 2, "events": tr_bass})
        if tr_acc:
            trs.append({"role": "acc", "channel": 4, "events": tr_acc})
        if tr_solo:
            trs.append({"role": "solo", "channel": 6, "events": tr_solo})
        if tr_drums:
            trs.append({"role": "drums", "channel": 9, "events": tr_drums})
        # cc curve per section for existing pitched channels (2/4/6)
        live_ch = {tr["channel"] for tr in trs}
        for b, sec in bar_sec:
            val = st["energy"].get(sec, 7)
            for ch in (2, 4, 6):
                if ch in live_ch:
                    cc.append({"ch": ch, "b": b, "st": 0, "v": val})
        item = {
            "id": f"syn-{stname}-{si:03d}",
            "originKind": "synthetic",
            "source": f"composer-v0 template '{stname}' seed {seed}",
            "license": "original-synthetic (CC0, generated deterministically)",
            "bpm": bpm,
            "signature": sig,
            "note": f"v0 curriculum; {len(bar_sec)} bars",
            "sections": sections,
            "tracks": trs,
            "cc": cc,
            "meta": {"style": stname, "seed": seed, "bars": len(bar_sec)},
        }
        out.append(item)
    return out


# ---------------------------------------------------------------------------
# 3) real engine/user midi files from artifacts
# ---------------------------------------------------------------------------

def _tempo_and_sig(f: "object") -> tuple[int, list[int]]:
    # meta_type is the raw SMF meta byte: 0x51 set_tempo, 0x58 time_signature
    bpm = 120
    sig = [4, 4]
    for tr in f.tracks:
        for e in tr.events:
            if e.meta_type == 0x51 and e.data:
                us = int.from_bytes(e.data[:3], "big")
                if us > 0:
                    bpm = max(40, min(220, round(60_000_000 / us)))
            elif e.meta_type == 0x58 and e.data:
                try:
                    num = int(e.data[0])
                    den = 2 ** int(e.data[1])
                    if (num, den) in ((4, 4), (3, 4), (12, 8)):
                        sig = [num, den]
                except Exception:
                    pass
    return bpm, sig


def real_items(limit: int = 24,
               exclude_dirs: tuple[str, ...] = ()) -> list[dict]:
    # generisani direktorijumi (probe/korpus/pesme) se nikad ne ukljucuju
    exclude_dirs = exclude_dirs + ("probes-", "corpus-", "songs-")
    import dna_midi_studio.midi as m
    dirs = sorted(ROOT.glob("artifacts-max-4.4*")) + sorted(ROOT.glob("artifacts-max-4.5*")) + \
        sorted(ROOT.glob("artifacts-max-4.6*"))
    files: dict[str, Path] = {}
    for d in dirs:
        if not d.is_dir():
            continue
        for p in sorted(d.rglob("*.mid")):
            if "REPLACE" in p.name or p.name.startswith("."):
                continue
            if any(part.startswith(ex) if not part.isdigit() else False
                   for part in p.parts for ex in exclude_dirs):
                continue
            digest = hashlib.sha256(p.read_bytes()).hexdigest()
            files.setdefault(digest, p)
    items = []
    skipped = 0
    grid_steps = {(4, 4): 16, (3, 4): 12, (12, 8): 12}
    for digest, p in sorted(files.items(), key=lambda kv: kv[1].as_posix()):
        if len(items) >= limit:
            break
        try:
            f = m.MidiFile.from_bytes(p.read_bytes())
            notes = f.notes()
            if not notes or f.ppq <= 0:
                skipped += 1
                continue
            bpm, sig = _tempo_and_sig(f)
            if tuple(sig) not in grid_steps:
                sig = [4, 4]
            num, den = sig
            steps = grid_steps[(num, den)]
            bar_ticks = f.ppq * 4.0 * num / den     # ticks per bar
            t_step = bar_ticks / steps               # ticks per grid step
            events_by_ch: dict[int, list[dict]] = {}
            programs: dict[int, list[int]] = {}
            extra_cc = 0
            for tr in f.tracks:
                for e in tr.events:
                    st = e.status or 0
                    if e.meta_type is None and st >> 4 == 0xC:
                        ch = e.channel or 0
                        pc = (e.data[0] if e.data else 0)
                        programs.setdefault(ch, []).append(int(pc))
                    elif e.meta_type is None and st >> 4 in (0xB, 0xE, 0xD):
                        extra_cc += 1
            for n in notes:
                if n.start is None or n.end is None or n.pitch is None:
                    continue
                bar = int(n.start / bar_ticks)
                if bar > 127:
                    continue
                in_bar = n.start - bar * bar_ticks
                st = int(round(in_bar / t_step))
                d = max(1, int(round((n.end - n.start) / t_step)))
                if st < 0 or st >= steps:
                    continue
                if st + d > steps:
                    d = steps - st
                ev = {"b": bar, "st": st, "d": d,
                      "v": max(1, min(7, int(round((n.velocity or 64) / 127 * 7)))),
                      "n": int(n.pitch)}
                events_by_ch.setdefault(int(n.channel or 0), []).append(ev)
            if not events_by_ch:
                skipped += 1
                continue
            maxb = max(e["b"] for evs in events_by_ch.values() for e in evs)
            trs = []
            for ch in sorted(events_by_ch):
                if ch in (9, 10):
                    role = "drums" if ch == 9 else "perc"
                else:
                    role = "other"
                trs.append({"role": role, "channel": ch,
                            "events": sorted(events_by_ch[ch],
                                             key=lambda e: (e["b"], e["st"], e["n"]))})
            kind = "user" if "/uploads/" in p.as_posix() else "engine"
            items.append({
                "id": f"real-{digest[:10]}",
                "originKind": kind,
                "source": p.as_posix().replace(str(ROOT) + "/", ""),
                "license": "user's own Pa800 material (repo artifacts; training evidence)",
                "bpm": bpm,
                "signature": sig,
                "note": f"quantized to {steps}-step grid; bars<=127; cc/bend/program in meta only",
                "sections": [],
                "tracks": trs,
                "cc": [],
                "meta": {"programs": {str(k): sorted(set(v)) for k, v in programs.items()},
                         "controllerEvents": extra_cc,
                         "bars": maxb + 1},
            })
        except Exception:
            skipped += 1
    # deterministic: sort by source; ids derived from content, not path order
    items.sort(key=lambda it: (it["source"], it["id"]))
    return items




# ---------------------------------------------------------------------------
# SMF export (SMF0, Pa800-playable) — proof that corpus items render to files
# ---------------------------------------------------------------------------

_VEL_TABLE = {v: max(0, min(127, int(round(127 * v / 7)))) for v in range(8)}


def _jitter_ticks(rng, sigma_ticks: float, step_ticks: int, cap: float = 0.35,
                 max_early: float | None = None) -> int:
    """Ljudski tajming (4.54 dokaz: std ~27.96 ms) — ogranicen da ne predje
    polovinu koraka (kvantizacija ostaje stabilna). max_early (u tick-ovima)
    zabranjuje da nota krene pre sopstvenog takta (st0 ostaje >= bar start)."""
    j = rng.gauss(0.0, sigma_ticks)
    bound = cap * step_ticks
    if j > bound:
        j = bound
    elif j < -bound:
        j = -bound
    if max_early is not None and j < -max_early:
        j = -max_early
    return int(round(j))


def export_smf(item: dict, path: Path, *, humanize: bool = False, seed: int = 0,
               sigma_ms: float = 27.96, cap: float = 0.35) -> Path:
    """Write a canonical score item as SMF0 (ppq 480) — byte-level proof.

    humanize=True: deterministički mikrotajming džiter (ljudska referenca
    4.54, std ~27.96 ms iz 110.313 uzoraka Magenta Groove preko wobblemidi
    profila); cap ograničava džiter da ne promeni kvantizovani korak.
    """
    import random as _random
    import dna_midi_studio.midi as m
    it = canonical_item(item)
    num, den = it["signature"]
    steps = pv.steps_per_bar([num, den])
    bar_ticks = int(480 * 4 * num / den)
    t_step = bar_ticks // steps
    ppq = 480
    bpm = max(40, min(220, int(it.get("bpm", 120))))
    tempo_us = int(60_000_000 / bpm)
    sigma_ticks = sigma_ms * ppq * bpm / 60_000.0
    rng = _random.Random(seed) if humanize else None
    events: list[m.MidiEvent] = []
    order = 0
    events.append(m.meta_event(0, order, 0x51, tempo_us.to_bytes(3, "big"))); order += 1
    den_pow = 3 if num == 12 else 2
    events.append(m.meta_event(0, order, 0x58,
                               bytes([num, den_pow, 24, 8]))); order += 1
    for tr in it["tracks"]:
        ch = int(tr["channel"])
        for e in sorted(tr["events"], key=lambda e: (e["b"], e["st"], e["n"])):
            t_on = e["b"] * bar_ticks + e["st"] * t_step
            if rng is not None:
                t_on += _jitter_ticks(rng, sigma_ticks, t_step, cap,
                                      max_early=e["st"] * t_step)
            t_off = t_on + max(1, e["d"]) * t_step
            vel = _VEL_TABLE.get(int(e["v"]), 100)
            events.append(m.channel_event(t_on, order, 0x90 | ch,
                                          int(e["n"]), vel)); order += 1
            events.append(m.channel_event(t_off, order, 0x80 | ch,
                                          int(e["n"]), 0)); order += 1
    events.sort(key=lambda e: (e.tick, e.order))
    mf = m.MidiFile(format_type=0, ppq=ppq,
                    tracks=[m.MidiTrack(events=events)])
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(mf.to_bytes())
    return path


def smf_matches_item(path: Path, item: dict) -> bool:
    """Parse an exported SMF back and compare (b,st,d,v,n) per channel."""
    import dna_midi_studio.midi as m
    it = canonical_item(item)
    f = m.MidiFile.from_bytes(path.read_bytes())
    num, den = it["signature"]
    steps = pv.steps_per_bar([num, den])
    bar_ticks = f.ppq * 4.0 * num / den
    t_step = bar_ticks / steps
    got: dict[int, list[tuple]] = {}
    for n in f.notes():
        b = int(n.start / bar_ticks)
        st = int(round((n.start - b * bar_ticks) / t_step))
        d = max(1, int(round((n.end - n.start) / t_step)))
        v = max(1, min(7, int(round((n.velocity or 64) / 127 * 7))))
        got.setdefault(int(n.channel or 0), []).append((b, st, d, v, int(n.pitch)))
    for tr in it["tracks"]:
        want = sorted((e["b"], e["st"], e["d"], e["v"], e["n"])
                      for e in tr["events"])
        g = sorted(got.get(int(tr["channel"]), []))
        if g != want:
            return False
    return True

# ---------------------------------------------------------------------------
# assembly + stats + files
# ---------------------------------------------------------------------------

def build(seed: int = 469, synthetic_count: int = 300, real_limit: int = 24) -> dict:
    vendor = vendor_items()
    synthetic = synthetic_items(count=synthetic_count, seed=seed)
    real = real_items(limit=real_limit)
    items = vendor + synthetic + real
    canonical = [canonical_item(it) for it in items]
    # ids unique
    seen = set()
    for it in canonical:
        assert it["id"] not in seen, it["id"]
        seen.add(it["id"])
    report = pv.validate_many(canonical)
    stats = aggregate(canonical)
    stats["validator"] = {"ok": report["ok"], "total": report["total"],
                          "failed": report["failed"]}
    return {"seed": seed, "items": canonical, "stats": stats,
            "missingGm": dict(_MISSING_GM)}


def aggregate(items: list[dict]) -> dict:
    per_origin: dict[str, dict] = {}
    tot_bars = 0
    tot_notes = 0
    tot_tokens = 0
    tot_arrangements = 0
    for it in items:
        o = per_origin.setdefault(it["originKind"], {"items": 0, "bars": 0,
                                                      "notes": 0, "tokens": 0,
                                                      "arrangements": 0})
        o["items"] += 1
        nb = max([e["b"] for tr in it["tracks"] for e in tr["events"]] + [-1]) + 1
        nn = sum(len(tr["events"]) for tr in it["tracks"])
        toks = len(encode(it))
        o["bars"] += nb
        o["notes"] += nn
        o["tokens"] += toks
        if len(it.get("sections") or []) >= 2:
            o["arrangements"] += 1
        tot_bars += nb
        tot_notes += nn
        tot_tokens += toks
        if len(it.get("sections") or []) >= 2:
            tot_arrangements += 1
    return {"items": len(items), "bars": tot_bars, "notes": tot_notes,
            "tokens": tot_tokens, "arrangements": tot_arrangements,
            "moments": tot_bars + sum(1 for it in items if not it["sections"]),
            "perOrigin": per_origin, "vocab": {"size": 380, "cap": 1024}}


def _dump(path: Path, items: list[dict]) -> None:
    with open(path, "w", encoding="utf-8") as fh:
        for it in items:
            fh.write(json.dumps(it, ensure_ascii=False, sort_keys=True) + "\n")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="DNA Composer corpus v1 (4.69)")
    ap.add_argument("--out", default="artifacts-max-4.69", help="output dir")
    ap.add_argument("--seed", type=int, default=469)
    ap.add_argument("--synthetic", type=int, default=300)
    ap.add_argument("--real-limit", type=int, default=24)
    args = ap.parse_args(argv)

    res = build(seed=args.seed, synthetic_count=args.synthetic,
                real_limit=args.real_limit)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    odir = out / "corpus-4.69"
    odir.mkdir(parents=True, exist_ok=True)
    for origin in ("vendor", "synthetic", "engine", "user"):
        sub = [it for it in res["items"] if it["originKind"] == origin]
        if sub:
            _dump(odir / f"corpus-{origin}.jsonl", sub)
    _dump(odir / "corpus-sample-30.jsonl", res["items"][:30])
    (odir / "corpus-stats-4.69.json").write_text(
        json.dumps(res["stats"], indent=1, ensure_ascii=False), encoding="utf-8")
    manifest = {
        "schema": "dna-composer-corpus-manifest",
        "version": "4.69",
        "seed": args.seed,
        "generatedBy": "python3.11 -m dna_midi_studio.composer_corpus",
        "items": [{"id": it["id"], "originKind": it["originKind"],
                   "source": it["source"], "license": it["license"],
                   "bars": max([e["b"] for tr in it["tracks"] for e in tr["events"]] + [-1]) + 1,
                   "digest": item_digest(it)} for it in res["items"]],
        "validator": res["stats"]["validator"],
    }
    (odir / "corpus-manifest-4.69.json").write_text(
        json.dumps(manifest, indent=1, ensure_ascii=False), encoding="utf-8")
    print(json.dumps(res["stats"], indent=1, ensure_ascii=False))
    return 0 if res["stats"]["validator"]["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
