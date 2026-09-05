# Real bad-MIDI corpus evidence

Source corpus: user-provided Midi.zip (4,726 MIDI/KAR entries discovered during forensic scoring).
The release does not redistribute the full user corpus.

Deep 10-file verification after safe orphan cleanup:
- 4 structurally clean and eligible for musical reconstruction.
- 6 blocked by NON_POSITIVE_DURATION patterns.
- one blocked file also has true ORPHAN_NOTE_OFF events.

4.45 policy intentionally refuses to invent duration or shift drum same-tick events merely to make a file parse.
