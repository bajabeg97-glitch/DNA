# NOTICE — vendored third-party material (milestone 4.64)

This directory contains material copied verbatim from third-party open-source
projects, together with provenance, license allow-list checks and integrity
hashes. Policy (standing): MIT/Apache-2.0/BSD/CC0/Unlicense material may be
reused with attribution; GPL/AGPL/LGPL and non-commercial (CC BY-NC-*) material
is study-only and is NOT vendored here.

## dmp_midi — drum-machine pattern corpus (MIT)

- Project: https://github.com/gvellut/dmp_midi
- License file: `LICENSE` (MIT, copyright (c) 2023 Guillaume Vellut)
- Pinned upstream commit: `81f926a4c7045a37dbadc21b9c332d799b1be9bf`
  (master, pushed 2023-03-09)
- Files copied verbatim (no modifications):
  - `LICENSE`
  - `README.md`
  - `input/Patterns_200.json` — 200 drum-machine patterns transcribed from the
    book "200 Drum Machine Patterns" by René-Pierre Bardet
  - `input/Patterns_260.json` — 268 drum-machine patterns transcribed from the
    book "260 Drum Machine Patterns" by René-Pierre Bardet
  - `input/README.md` — upstream notes on the digital transcriptions
- Provenance of the pattern transcriptions (per upstream README):
  - https://github.com/montoyamoraga/drum-machine-patterns (200 book)
  - https://github.com/stephenhandley/DrumMachinePatterns (both books)
- What we built on top (NOT copied): `dna_midi_studio/pattern_library.py`
  (stdlib loader/normalizer/statistics/row-matching over the corpus),
  `artifacts-max-4.64/dmp-library-stats.json` (normalized per-pattern stats),
  `artifacts-max-4.64/dmp-pattern-matches-demo.json` (probes on our own
  baseline files). GM drum key numbers used for statistics follow the public
  General MIDI drum map and the upstream `NOTE_MAPPING` values in
  `dmpmid/cli.py` (BD 36, RS 37, SD 38, CP 39, CH 42, LT 43, OH 46, MT 47,
  CY 49, HT 50, TM 54, CB 56).

## Integrity manifest

See `manifest.json` in this directory (sha256 of every vendored file, computed
at copy time and re-verified by `test_pattern_library_v464.py`).
