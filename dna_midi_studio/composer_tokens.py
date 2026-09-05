"""composer_tokens.py — score language v1 + tokenizer v1 (DNA Composer, 4.69).

Canonical score item (JSON-serializable "score language"):
    {
      "id": str, "originKind": "engine|vendor|synthetic|user", "source": str,
      "license": str, "bpm": int, "signature": [4,4] | [3,4] | [12,8],
      "note": str|None,
      "sections": [{"name": str, "bars": int}, ...],   # [] when unknown
      "tracks": [ {"role": str, "channel": int,
                   "events": [{"b": bar, "st": step16, "d": durSteps,
                               "v": velClass0..7, "n": gmNote}] } ],
      "cc":     [ {"ch": int, "b": bar, "st": step16, "v": ccClass0..15} ]
    }

Grid: 16 steps per 4/4 bar (12 per 12/8), durations in steps, velocities and
CC11 quantized into classes so the vocabulary stays small and deterministic.

Tokenizer v1: fixed (deterministic, no training) vocabulary < 1024 tokens;
encode(item) -> list[int], decode(tokens) -> canonical item streams.
Roundtrip is exact on canonical event lists.

Deterministic, stdlib-only. Part of the S1 (4.69) corpus/token milestone.
"""

from __future__ import annotations

import json

# ---------------------------------------------------------------------------
# Fixed vocabulary layout (id space, deterministic)
# ---------------------------------------------------------------------------
SPECIALS = {"PAD": 0, "SOS": 1, "EOS": 2}
_T = {"SEC": 375, "TRK": 376, "BAR": 377, "NOTE": 378, "CC": 379}
VOCAB_SIZE = 380  # <= 1024 per plan (S1)
assert VOCAB_SIZE <= 1024

ROLE_IDS = ["drums", "perc", "bass", "acc", "solo", "kick", "snare", "hats", "other", "unknown"]
ROLE_IDX = {r: i for i, r in enumerate(ROLE_IDS)}  # 10 roles, ids 131..140


def role_id(role: str) -> int:
    return ROLE_IDX.get(str(role), ROLE_IDX["unknown"])


def role_name(idx: int) -> str:
    return ROLE_IDS[idx] if 0 <= idx < len(ROLE_IDS) else "unknown"


def _base_offsets() -> dict[str, int]:
    """id offsets for value vocab groups."""
    off = {}
    o = len(SPECIALS)          # 3
    off["SEC"] = o;  o += 16   # 16 section slots
    off["ROLE"] = o; o += len(ROLE_IDS)
    off["ST"] = o;   o += 16
    off["DUR"] = o;  o += 32
    off["VEL"] = o;  o += 8
    off["NOTE"] = o; o += 128
    off["CH"] = o;   o += 16
    off["CCV"] = o;  o += 16
    return off


_OFF = _base_offsets()
_MAX_BAR = 127


def _bar_off() -> int:
    """bar values live right after CCV group up to VOCAB_SIZE specials."""
    return _OFF["CCV"] + 16


def _check(cond: bool, msg: str) -> None:
    if not cond:
        raise ValueError("composer_tokens: " + msg)


def canonical_item(item: dict) -> dict:
    """Deterministic canonical form of a score item (used for roundtrip + digest)."""
    out = {
        "id": str(item.get("id")),
        "originKind": str(item.get("originKind", "engine")),
        "source": str(item.get("source", "")),
        "license": str(item.get("license", "")),
        "bpm": int(item.get("bpm", 120)),
        "signature": [int(x) for x in item.get("signature", [4, 4])],
        "note": str(item.get("note") or ""),
        "sections": [{"name": str(s.get("name", "")), "bars": int(s.get("bars", 1))}
                     for s in item.get("sections", [])],
    }
    trs = []
    for tr in item.get("tracks", []):
        evs = sorted(
            ({"b": int(e["b"]), "st": int(e["st"]), "d": int(e["d"]),
              "v": int(e["v"]), "n": int(e["n"])} for e in tr.get("events", [])),
            key=lambda e: (e["b"], e["st"], e["n"], e["v"]))
        trs.append({"role": str(tr.get("role", "unknown")),
                    "channel": int(tr.get("channel", 0)), "events": evs})
    out["tracks"] = sorted(trs, key=lambda t: (t["channel"], t["role"]))
    cc = sorted(({"ch": int(c["ch"]), "b": int(c["b"]), "st": int(c["st"]), "v": int(c["v"])}
                 for c in item.get("cc", [])), key=lambda c: (c["ch"], c["b"], c["st"]))
    out["cc"] = cc
    return out


def item_digest(item: dict) -> str:
    import hashlib
    raw = json.dumps(canonical_item(item), sort_keys=True, ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()[:16]


def section_index(item: dict) -> dict[int, int]:
    """bar -> section idx (0-based)."""
    idx = {}
    b = 0
    for si, s in enumerate(item["sections"]):
        for _ in range(int(s["bars"])):
            idx[b] = si
            b += 1
    return idx


def encode(item: dict) -> list[int]:
    it = canonical_item(item)
    bar_sec = section_index(it)
    bar_base = _bar_off()
    sec_base = _OFF["SEC"]
    role_base = _OFF["ROLE"]
    st_base = _OFF["ST"]
    dur_base = _OFF["DUR"]
    vel_base = _OFF["VEL"]
    note_base = _OFF["NOTE"]
    ch_base = _OFF["CH"]
    ccv_base = _OFF["CCV"]

    toks = [SPECIALS["SOS"]]
    nbars = max([e["b"] for tr in it["tracks"] for e in tr["events"]], default=-1) + 1
    for c in it["cc"]:
        nbars = max(nbars, c["b"] + 1)

    for ti, tr in enumerate(it["tracks"]):
        _check(tr["channel"] in range(16), "channel 0..15")
        _check(0 <= tr["channel"], "channel")
        toks.append(_T["TRK"])
        toks.append(role_base + role_id(tr["role"]))
        toks.append(ch_base + tr["channel"])
        cur_bar = -1
        cur_sec = -1
        # merge cc events of this channel into the track stream
        chan_cc = [c for c in it["cc"] if c["ch"] == tr["channel"]]
        merged = ([("n", e) for e in tr["events"]] +
                  [("c", c) for c in chan_cc])
        merged.sort(key=lambda x: (x[1]["b"], x[1]["st"], 0 if x[0] == "n" else 1))
        for kind, ev in merged:
            if ev["b"] != cur_bar:
                _check(ev["b"] <= _MAX_BAR, "bar index too large (cap 127)")
                cur_bar = ev["b"]
                toks.append(_T["BAR"])
                toks.append(bar_base + cur_bar)
                if cur_bar in bar_sec and bar_sec[cur_bar] != cur_sec:
                    cur_sec = bar_sec[cur_bar]
                    toks.append(_T["SEC"])
                    toks.append(sec_base + cur_sec)
            if kind == "n":
                toks.append(_T["NOTE"])
                toks.append(st_base + int(ev["st"]))
                toks.append(dur_base + int(ev["d"]))
                toks.append(vel_base + int(ev["v"]))
                toks.append(note_base + int(ev["n"]))
            else:
                toks.append(_T["CC"])
                toks.append(st_base + int(ev["st"]))
                toks.append(dur_base + 0)
                toks.append(vel_base + 0)
                toks.append(ccv_base + int(ev["v"]))
        # pad stream: not needed
    if nbars <= 0 and not it["tracks"]:
        pass
    toks.append(SPECIALS["EOS"])
    return toks


def decode(tokens: list[int]) -> tuple[list[dict], list[dict]]:
    """Inverse of encode(): returns (tracks_events, cc_events) canonical lists.

    NOTE: `sections` are arrangement metadata and are NOT part of the
    token roundtrip contract (they are carried verbatim on the item).
    """
    st_base = _OFF["ST"]
    dur_base = _OFF["DUR"]
    vel_base = _OFF["VEL"]
    note_base = _OFF["NOTE"]
    ch_base = _OFF["CH"]
    ccv_base = _OFF["CCV"]
    role_base = _OFF["ROLE"]
    bar_base = _bar_off()

    tracks: list[dict] = []
    cc: list[dict] = []
    i = 0
    n = len(tokens)
    while i < n and tokens[i] in (SPECIALS["PAD"], SPECIALS["SOS"]):
        i += 1
    cur_role = "unknown"
    cur_ch = 0
    cur_b = 0
    events: list[dict] = []
    ccchan: list[dict] = []
    first_trk = True
    while i < n and tokens[i] != SPECIALS["EOS"]:
        t = tokens[i]
        if t == _T["TRK"]:
            if events or ccchan or not first_trk:
                tracks.append({"role": cur_role, "channel": cur_ch,
                               "events": events})
                cc.extend(ccchan)
            i += 1
            _check(i + 1 < n, "TRK needs role+ch")
            cur_role = role_name(tokens[i] - role_base)
            cur_ch = tokens[i + 1] - ch_base
            i += 2
            events = []
            ccchan = []
            first_trk = False
            continue
        if t == _T["BAR"]:
            cur_b = tokens[i + 1] - bar_base
            i += 2
            continue
        if t == _T["SEC"]:
            i += 2  # section markers are metadata; skipped on decode
            continue
        if t == _T["NOTE"] or t == _T["CC"]:
            st = tokens[i + 1] - st_base
            d = tokens[i + 2] - dur_base
            v = tokens[i + 3] - vel_base
            val = tokens[i + 4]
            if t == _T["NOTE"]:
                events.append({"b": cur_b, "st": st, "d": d, "v": v,
                               "n": val - note_base})
            else:
                ccchan.append({"ch": cur_ch, "b": cur_b, "st": st,
                               "v": val - ccv_base})
            i += 5
            continue
        raise ValueError(f"composer_tokens: unexpected token {t} at {i}")
    if events or ccchan or not first_trk:
        tracks.append({"role": cur_role, "channel": cur_ch, "events": events})
        cc.extend(ccchan)
    return tracks, cc


def roundtrip_events(item: dict) -> bool:
    """True when decode(encode(item)) reproduces the canonical event lists."""
    it = canonical_item(item)
    tracks, cc = decode(encode(it))
    want_tracks = sorted(
        ({"role": tr["role"], "channel": tr["channel"],
          "events": sorted(tr["events"],
                           key=lambda e: (e["b"], e["st"], e["n"], e["v"]))}
         for tr in it["tracks"]),
        key=lambda t: (t["channel"], t["role"]))
    want_cc = sorted(it["cc"], key=lambda c: (c["ch"], c["b"], c["st"]))
    return tracks == want_tracks and cc == want_cc


def token_stats(tokens: list[int]) -> dict:
    return {"count": len(tokens), "min": min(tokens), "max": max(tokens),
            "withinVocab": all(0 <= t < VOCAB_SIZE for t in tokens)}
