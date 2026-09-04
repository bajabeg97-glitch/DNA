# Session 14 - Korg Pa800 physical device test

This directory is a prepared test kit. Its presence does not certify a device.

## Import procedure

1. Copy both `.mid` files to USB media readable by the Pa800.
2. Open Style Record and create a new Style.
3. Choose Import SMF, keep SHIFT pressed and choose Execute.
4. Use Initialize for a new Style.
5. Confirm original Key/Chord C Major, Track Type and NTT.
6. Test the functional Style first, then the polyphony stress Style.
7. Save to USER/FAVORITE, reload it and repeat the transition test.
8. Add at least one image and one audio evidence file beside the result JSON.
9. Fill `device-result-template.json`, save it under a new name and run verification.

The polyphony file reaches the project's simultaneous MIDI-note limits. It is
not a claim about oscillator consumption: a Pa800 Sound may consume more than
one hardware voice per MIDI note, which is why listening on the device is mandatory.

## Required observations

- [ ] usbMediaReadable
- [ ] shiftExecuteImportCompleted
- [ ] allMarkersImported
- [ ] channelsNineThroughSixteenCorrect
- [ ] trackTypesConfirmed
- [ ] nttConfirmed
- [ ] allStyleTransitionsAudible
- [ ] noStuckOrMissingNotes
- [ ] normalStyleNoUnacceptableVoiceStealing
- [ ] polyphonyStressNoUnacceptableVoiceStealing
- [ ] soloOriginalPreserved
- [ ] delayRoutingCorrect
- [ ] rxDncOnlyOnConfirmedSounds
- [ ] noDigitalClipping
- [ ] headroomAcceptable
- [ ] savedToUserOrFavorite
- [ ] reloadMatchesSavedStyle
