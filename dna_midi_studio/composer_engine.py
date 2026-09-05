"""composer_engine.py — deterministički kompozitor (DNA Composer, 4.70 S2).

Građen isključivo na postojećoj evidenciji projekta:
- struktura pesme: gramatika sekcija (Intro/Verse/Chorus/Bridge/Outro),
- bubnjevi/perc: fabrički drum patterni (vendor-max-4.64/dmp-midi, MIT) —
  svaki izbor se upisuje u scorecard (title + source = sledljivost),
- harmonija: dijatonski progresiji (originalne kompozicije, bez kopiranja),
- ljudski tajming: 4.54 dokaz (std ~27.96 ms, 110.313 uzoraka),
- CC11 oblikovanje po sekcijama (4.52 miks pravila),
- izlaz: kanonski score item (composer_tokens) + SMF0 + scorecard JSON.

Svaka pesma prolazi composer_validator 100%; isti seed -> iste note (byte-level).

CLI:
  python3.11 -m dna_midi_studio.composer_engine --styles rock,funk --seeds 1,2
      --out artifacts-max-4.70
  (--all: svih 10 stilova × 3 seeda = 30 pesama, prihvatanje S2)
"""

from __future__ import annotations

import argparse
import json
import random
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "dna_midi_studio"))

import pattern_library as pl  # noqa: E402
import composer_validator as pv  # noqa: E402
from composer_corpus import export_smf, _sig_steps  # noqa: E402
from composer_tokens import canonical_item, item_digest  # noqa: E402

# ---------------------------------------------------------------------------
# teorija (originalne, jednostavne dijatonske progresije — bez kopiranja)
# ---------------------------------------------------------------------------

CHORDS = {  # ime -> pčevi iznad C (durski/molski trozvuci iz C-dura/A-mola)
    "C": (0, 4, 7), "Dm": (2, 5, 9), "Em": (4, 7, 11), "F": (5, 9, 12),
    "G": (7, 11, 14), "Am": (9, 12, 16),
}

HUMAN_EVIDENCE = ("ljudska referenca 4.54: std ~27.96 ms iz 110.313 uzoraka "
                  "(Magenta Groove preko wobblemidi profila; groove_engine)")

STYLES = {
    # ime: bpm, sig, struktura (sekcija, taktovi), bas stil, acc stil,
    #      perc?, solo?, fill?, progresija, energia
    "rock": dict(bpm=118, sig=[4, 4],
                 structure=[("Intro", 2), ("Verse", 4), ("Chorus", 4),
                            ("Verse", 4), ("Chorus", 4), ("Bridge", 2),
                            ("Chorus", 4), ("Outro", 2)],
                 bass="eighths", acc="pad", perc=False, solo=False, fills=True,
                 prog=["C", "G", "Am", "F"], energy={"Bridge": 7}),
    "funk": dict(bpm=104, sig=[4, 4],
                 structure=[("Intro", 2), ("Verse", 4), ("Chorus", 4),
                            ("Verse", 4), ("Chorus", 4), ("Outro", 2)],
                 bass="funky", acc="arp8", perc=True, solo=False, fills=True,
                 prog=["C", "F", "G", "C"], energy={}),
    "pop": dict(bpm=112, sig=[4, 4],
                structure=[("Intro", 2), ("Verse", 4), ("Chorus", 4),
                           ("Verse", 4), ("Chorus", 4), ("Bridge", 2),
                           ("Chorus", 4), ("Outro", 2)],
                bass="eighths", acc="arp8", perc=False, solo=False, fills=True,
                prog=["C", "Am", "F", "G"], energy={}),
    "ballad": dict(bpm=72, sig=[4, 4],
                   structure=[("Intro", 2), ("Verse", 4), ("Chorus", 4),
                              ("Verse", 4), ("Chorus", 4), ("Outro", 2)],
                   bass="half", acc="pad", perc=False, solo=True, fills=False,
                   prog=["C", "G", "Am", "F"], energy={"Chorus": 9}),
    "folk": dict(bpm=96, sig=[4, 4],
                 structure=[("Intro", 2), ("Verse", 4), ("Chorus", 4),
                            ("Verse", 4), ("Chorus", 4), ("Outro", 2)],
                 bass="quarters", acc="pad", perc=False, solo=False, fills=False,
                 prog=["C", "G", "Am", "Em"], energy={}),
    "ballad12": dict(bpm=63, sig=[12, 8],
                     structure=[("Intro", 2), ("Verse", 4), ("Chorus", 4),
                                ("Outro", 2)],
                     bass="half", acc="pad", perc=False, solo=True, fills=False,
                     prog=["Am", "F", "C", "G"], energy={"Chorus": 9}),
    "waltz": dict(bpm=90, sig=[3, 4],
                  structure=[("Intro", 2), ("Verse", 4), ("Chorus", 4),
                             ("Verse", 4), ("Chorus", 4), ("Outro", 2)],
                  bass="waltz", acc="padw", perc=False, solo=False, fills=False,
                  prog=["C", "Am", "F", "G"], energy={}),
    "latin12": dict(bpm=100, sig=[12, 8],
                    structure=[("Intro", 2), ("Verse", 4), ("Chorus", 4),
                               ("Outro", 2)],
                    bass="eighths", acc="arp8", perc=True, solo=False, fills=True,
                    prog=["Am", "F", "G", "Em"], energy={}),
    "disco": dict(bpm=118, sig=[4, 4],
                  structure=[("Intro", 2), ("Verse", 4), ("Chorus", 4),
                             ("Verse", 4), ("Chorus", 4), ("Outro", 2)],
                  bass="eighths", acc="pad", perc=False, solo=False, fills=True,
                  prog=["C", "Am", "Dm", "G"], energy={"Verse": 10}),
    "dance90": dict(bpm=126, sig=[4, 4],
                    structure=[("Intro", 2), ("Verse", 4), ("Chorus", 4),
                               ("Verse", 4), ("Chorus", 4), ("Bridge", 2),
                               ("Chorus", 4), ("Outro", 2)],
                    bass="eighths", acc="arp16", perc=False, solo=False,
                    fills=True, prog=["C", "G", "Am", "F"], energy={}),
}

PERC_GM = {54, 56}  # Tambourine, Cowbell -> ch10 (percussion)
DRUM_KIT = {36, 38, 39, 42, 46, 49}  # kick/snare/clap/hats/crash -> ch9


def _pool(sig: list[int], kind: str) -> list[dict]:
    """Pattern pool iz fabričke evidencije (build_library)."""
    want = 12 if sig == [12, 8] else (12 if sig == [3, 4] else 16)
    full, light, fill = [], [], []
    for pat in pl.build_library():
        ps, _ = _sig_steps(pat["signature"])
        if ps != sig or pat["length"] != want:
            continue
        roles = {tr["role"] for tr in pat["tracks"].values()}
        if kind == "fill":
            if (pat.get("densityPerStep", 0) >= 0.55 or
                    any(w in pat["title"].lower() for w in ("break", "fill", "end"))):
                fill.append(pat)
        elif kind == "full":
            if roles >= {"kick", "snare", "hats"} and pat["parts"] >= 3:
                full.append(pat)
        else:
            if "kick" in roles and "hats" in roles and pat["parts"] <= 2:
                light.append(pat)
    return {"fill": fill, "full": full, "light": light}[kind]


def _drum_bar(pat: dict, steps: int, acc_mask: list[int],
              bar_events: list[dict], perc_events: list[dict], rng) -> None:
    """pattern -> ch9 drums + ch10 perc (per GM); accent na acc_mask koracima."""
    accent = pat.get("accentSteps") or ""
    for tr in pat["tracks"].values():
        gm, role = tr["gm"], tr["role"]
        dest = perc_events if gm in PERC_GM else bar_events
        is_drum = gm in DRUM_KIT
        vdef = 6 if gm in (36, 38, 49) else (5 if gm in (42, 46) else 5)
        for i, c in enumerate(tr["steps"]):
            if c != "1":
                continue
            st = i % steps
            v = 7 if (i < len(accent) and accent[i] == "1") or st in acc_mask \
                else vdef
            if not is_drum and gm not in PERC_GM:
                v = min(v, 5)
            dest.append({"b": 0, "st": st, "d": 1, "v": v, "n": gm})


def compose(style_name: str, seed: int) -> dict:
    """Vraća {"item":..., "scorecard":{...}} — validan score item + trag."""
    st = dict(STYLES[style_name])
    rng = random.Random(seed)
    bpm = st["bpm"] + (seed % 5) - 2
    sig = list(st["sig"])
    steps = 16 if sig != [12, 8] else 12
    structure = [tuple(x) for x in st["structure"]]
    bar_sec: list[tuple[int, str]] = []
    for sec, nb in structure:
        for _ in range(nb):
            bar_sec.append((len(bar_sec), sec))
    sections = []
    for sec in dict.fromkeys(s for _, s in bar_sec):
        sections.append({"name": sec, "bars": sum(1 for _, s2 in bar_sec if s2 == sec)})

    full = _pool(sig, "full")
    light = _pool(sig, "light")
    fillp = _pool(sig, "fill")
    if not full:  # stil nema evidencije -> ne komponujemo na praznom
        raise ValueError(f"no 4/4 pattern evidence for {style_name}")

    tr_drums, tr_perc, tr_bass, tr_acc, tr_solo, cc = [], [], [], [], [], []
    drums_used: list[dict] = []
    live_ch: set[int] = set()

    prog = st["prog"]
    for b, sec in bar_sec:
        chord_name = prog[b % len(prog)]
        chord = CHORDS[chord_name]
        root_pc = chord[0]
        # ---- bubnjevi
        is_fill = st["fills"] and sec in ("Chorus", "Bridge", "Verse") and \
            (b + 1) % max(1, [nb for s2, nb in structure if s2 == sec][0]) == 0 \
            and b > 0 and rng.random() < 0.5 and fillp
        if is_fill:
            pat = rng.choice(fillp)
            drums_used.append({"bar": b, "title": pat["title"],
                               "source": pat["source"], "role": "fill"})
        else:
            if sec in ("Intro", "Outro") and light:
                pat = rng.choice(light)
            else:
                pat = rng.choice(full)
            drums_used.append({"bar": b, "title": pat["title"],
                               "source": pat["source"],
                               "role": "light" if sec in ("Intro", "Outro") else "main"})
        acc_mask = [s for s in ([0, 8] if sec in ("Intro", "Outro") else
                     ([12] if sec == "Bridge" else [])) if s < steps]
        _drum_bar(pat, steps, acc_mask, tr_drums, tr_perc, rng)
        # ---- bas (koren na 36+root_pc; kvinta +7)
        root = 36 + root_pc
        fifth = root + 7
        bass_kind = st["bass"]
        if bass_kind == "eighths" and steps == 12:
            # 12/8: korak = osmina; koren na 1., 3. i 5. dob (originalno)
            tr_bass += [{"b": b, "st": 0, "d": 2, "v": 5, "n": root},
                        {"b": b, "st": 4, "d": 2, "v": 5, "n": fifth},
                        {"b": b, "st": 8, "d": 2, "v": 4, "n": root + 12}]
        elif bass_kind == "eighths":
            tr_bass += [{"b": b, "st": 0, "d": 2, "v": 5, "n": root},
                        {"b": b, "st": 6, "d": 2, "v": 5, "n": fifth},
                        {"b": b, "st": 12, "d": 2, "v": 4, "n": root}]
        elif bass_kind == "funky":
            tr_bass += [{"b": b, "st": 0, "d": 2, "v": 6, "n": root},
                        {"b": b, "st": 4, "d": 1, "v": 5, "n": root},
                        {"b": b, "st": 8, "d": 2, "v": 6, "n": fifth},
                        {"b": b, "st": 14, "d": 1, "v": 5, "n": root + 12}]
        elif bass_kind == "quarters":
            tr_bass += [{"b": b, "st": 0, "d": 4, "v": 5, "n": root},
                        {"b": b, "st": 8, "d": 4, "v": 4, "n": fifth}]
        elif bass_kind == "waltz":
            tr_bass += [{"b": b, "st": 0, "d": 4, "v": 5, "n": root},
                        {"b": b, "st": 4, "d": 4, "v": 4, "n": fifth},
                        {"b": b, "st": 8, "d": 4, "v": 4, "n": root}]
        else:  # half
            tr_bass += [{"b": b, "st": 0, "d": steps - 1, "v": 4, "n": root}]
        # ---- acc (trozvuci: 48+pc)
        tones = [48 + pc for pc in chord]
        acc_kind = st["acc"]
        if acc_kind == "pad":
            for off in tones:
                tr_acc.append({"b": b, "st": 0, "d": steps - 1, "v": 3, "n": off})
        elif acc_kind == "padw":  # waltz: akord na 2. i 3. dob (oom-pah)
            for off in tones[:2]:
                tr_acc.append({"b": b, "st": 4, "d": 3, "v": 3, "n": off + 12})
                tr_acc.append({"b": b, "st": 8, "d": 3, "v": 3, "n": off + 12})
        elif acc_kind == "arp8":
            seq = [tones[0], tones[1], tones[2], tones[1]] * 2
            if steps == 12:
                pos = [0, 2, 3, 5, 6, 8, 9, 11]
                for k, n in enumerate(seq):
                    tr_acc.append({"b": b, "st": pos[k], "d": 1, "v": 3, "n": n})
            else:
                for k, n in enumerate(seq):
                    tr_acc.append({"b": b, "st": k * 2, "d": 1, "v": 3, "n": n})
        else:  # arp16
            seq = [tones[0], tones[1], tones[2], tones[1]] * 4
            for k, n in enumerate(seq):
                tr_acc.append({"b": b, "st": k, "d": 1, "v": 3, "n": n})
        # ---- solo (motiv od trozvuka, samo Chorus kad je stil sa solom)
        if st["solo"] and sec == "Chorus":
            intervals = sorted((pc - chord[0]) % 12 for pc in chord)
            base = 72 + root_pc
            motif = [(0, 2), (2, 1), (4, 2), (8, 2), (10, 1), (12, 3)] \
                if steps == 16 else [(0, 2), (2, 1), (4, 2), (6, 2), (8, 1)]
            for k in range(3):
                s_off, d_off = rng.choice(motif)
                if s_off + d_off > steps:
                    continue
                deg = rng.choice(intervals)
                tr_solo.append({"b": b, "st": s_off, "d": d_off, "v": 6,
                                "n": base + deg})
        # ---- CC11 (energija sekcije -> pitched kanali 2/4/6)
        energy = {**{"Intro": 6, "Verse": 8, "Chorus": 11, "Bridge": 7,
                     "Outro": 5}, **st["energy"]}
        val = min(14, max(2, energy.get(sec, 7)))
        for ch in (2, 4, 6):
            cc.append({"ch": ch, "b": b, "st": 0, "v": val})

    # sređivanje po kanalima
    def add(role, ch, evs):
        nonlocal live_ch
        if evs:
            live_ch.add(ch)
            return {"role": role, "channel": ch,
                    "events": sorted(evs, key=lambda e: (e["b"], e["st"], e["n"]))}
        return None

    trs = [t for t in (add("bass", 2, tr_bass), add("acc", 4, tr_acc),
                       add("solo", 6, tr_solo), add("drums", 9, tr_drums),
                       add("perc", 10, tr_perc)) if t]
    cc = [c for c in cc if c["ch"] in live_ch]
    # perc ch10 mora imati track ako cc postoji za ch10 — nema, ok
    item = {
        "id": f"song-{style_name}-s{seed}",
        "originKind": "synthetic",
        "source": f"composer_engine template '{style_name}' seed {seed}",
        "license": "original-synthetic (CC0, deterministicno generisano)",
        "bpm": bpm,
        "signature": sig,
        "sections": sections,
        "tracks": trs,
        "cc": cc,
        "meta": {"style": style_name, "seed": seed,
                 "generator": "composer_engine 4.70"},
    }
    item = canonical_item(item)
    scorecard = {
        "schema": "dna-composer-scorecard",
        "version": "4.70",
        "songId": item["id"], "template": style_name, "seed": seed,
        "bpm": bpm, "signature": sig,
        "structure": [{"section": s["name"], "bars": s["bars"]}
                      for s in sections],
        "chordsPerBar": [prog[b % len(prog)] for b, _ in bar_sec],
        "drums": {"evidence": "vendor-max-4.64/dmp-midi (MIT) fabrički patterni",
                  "choices": drums_used},
        "humanization": {"sigmaMs": 27.96, "evidence": HUMAN_EVIDENCE},
        "roles": [{"role": t["role"], "channel": t["channel"],
                   "notes": len(t["events"])} for t in item["tracks"]],
        "ccSections": {s: st["energy"].get(s, 7) for s in
                       dict.fromkeys(s2 for _, s2 in bar_sec)},
        "validation": pv.validate_item(item),
        "digest": item_digest(item),
    }
    return {"item": item, "scorecard": scorecard}


def export_song(item: dict, out_dir: Path, *, humanize: bool = True,
                seed: int = 0) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    mid = out_dir / f"{item['id']}.mid"
    export_smf(item, mid, humanize=humanize, seed=seed * 7919 + 11)
    return mid


def generate(style_name: str, seed: int, out_dir: Path,
             humanize: bool = True) -> dict:
    res = compose(style_name, seed)
    it = res["item"]
    mid = export_song(it, out_dir, humanize=humanize, seed=seed)
    card = res["scorecard"]
    card["files"] = {"mid": str(mid.relative_to(out_dir))}
    (out_dir / f"{it['id']}.scorecard.json").write_text(
        json.dumps(card, indent=1, ensure_ascii=False), encoding="utf-8")
    return card


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="DNA Composer deterministički "
                                             "kompozitor (4.70)")
    ap.add_argument("--styles", default="")
    ap.add_argument("--seeds", default="1,2,3")
    ap.add_argument("--all", action="store_true",
                    help="svih 10 stilova x 3 seeda (30 pesama)")
    ap.add_argument("--out", default="artifacts-max-4.70")
    ap.add_argument("--no-humanize", action="store_true")
    args = ap.parse_args(argv)

    styles = list(STYLES) if args.all else \
        [s for s in args.styles.split(",") if s in STYLES]
    seeds = [int(x) for x in args.seeds.split(",") if x.strip()]
    out = Path(args.out) / "songs-4.70"
    out.mkdir(parents=True, exist_ok=True)
    cards = []
    fails = []
    for stn in styles:
        for sd in seeds:
            try:
                cards.append(generate(stn, sd, out,
                                      humanize=not args.no_humanize))
            except Exception as exc:  # pragma: no cover
                fails.append({"style": stn, "seed": sd, "error": str(exc)})
    ok = all(c["validation"]["ok"] for c in cards) and not fails
    summary = {
        "schema": "dna-composer-songs-summary",
        "version": "4.70",
        "songs": len(cards),
        "valid": sum(1 for c in cards if c["validation"]["ok"]),
        "humanization": "off" if args.no_humanize else "on (sigma 27.96 ms)",
        "styles": sorted({c["template"] for c in cards}),
        "seeds": seeds,
        "totalBars": sum(sum(s["bars"] for s in c["structure"]) for c in cards),
        "patternChoices": len({(p["title"], p["source"]) for c in cards
                               for p in c["drums"]["choices"]}),
        "failures": fails,
    }
    (out / "songs-4.70-summary.json").write_text(
        json.dumps(summary, indent=1, ensure_ascii=False), encoding="utf-8")
    print(json.dumps(summary, indent=1, ensure_ascii=False))
    return 0 if summary["valid"] == len(cards) and not fails else 1


if __name__ == "__main__":
    sys.exit(main())
