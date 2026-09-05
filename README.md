# DNA — Music Optimizer

Professional MIDI and style file optimizer designed for Korg PA800 and other keyboards. Analyze, repair, and export stage-ready performances with advanced music theory algorithms.

> **Special Track Engine 4.57:** echo/terca optimizacija implementirana iz
> v413 spec (ranije: spec bez modula). Echo REPAIR: snap na half-beat grid,
> tiši i kraći od main dela; REBUILD: nesličan echo se uklanja i generiše od
> main dela; terca: dijatonska terca na main tajmingu uz harmony dokaz
> (bez dokaza = PRESERVE); inferencija neimenovanih aux treka. Main se nikada
> ne menja, 0 trigera (kapije). Evidence: `artifacts-max-4.57/` +
> `reports-max-4.57/`; guide `docs/studio-special-track-4.57.md`; status
> `dna-special-track-engine-status-4.57.json`.
> **Special-tracks research 4.56:** istražene bass-slide finese (MIDI 1.0
> mehanizmi: pitch-bend rampa, portamento CC84/65, overlap-legato, keyswitch),
> svetska potraga za echo/terca optimizacijom (`optimize_existing_echo_terca`:
> **0 javnih rešenja**) i unutrašnji gapovi (v413 spec bez modula
> `special_track_engine`; 7 legacy import failova; ai_learning data path).
> Evidence: `reports-max-4.56/`; vodič `docs/finesse-special-tracks-research.md`.
> **Instrument Techniques 4.55:** evidence engine za slap/pop/ghost/palm-mute —
> klasifikuje fabričke note (bas) i udare (ritam gitara) isključivo u postojeći
> rečnik tehnika `complete-instrument-profiles-4.44.json`, koristeći relativne
> pragove samog kanala (accent ≥ Q80, ghost ≤ Q20, gate pragovi); read-only —
> 0 trigera/CC/promena nota (warrant ledger: slap/pop/guitar trigeri ostaju
> SEMANTIC_ONLY dok ne postoji device snimak). Evidence: `artifacts-max-4.55/`
> + `reports-max-4.55/`; guide `docs/instrument-techniques-4.55.md`; roadmap
> `docs/roadmap-ai-session-musician.md`.
> **Groove Engine 4.54:** meri groove činjenice naših bubanj/percussion
> kanala (16-tinski grid-offseti u ms, gustina, dinamika po pitch-u) i poredi
> ih sa ljudskom referencom izvedenom iz wobblemidi rock profila (MIT, autor:
> naučeno sa Magenta Groove MIDI korpusa — pravih bubnjara; 110.313 uzoraka,
> std 27,96 ms; izvorni GCS/HF/archive.org hostovi nedostupni iz sandboxa,
> provenijencija: sha256 u baseline fajlu). Engine meri, ne menja note.
> Evidence: `artifacts-max-4.54/` + `reports-max-4.54/`; guide
> `docs/human-groove-evidence-4.54.md`; status `dna-groove-engine-status-4.54.json`.
> **Korg STY Mapper 4.53:** map + export of Pa800-importable style SMF0 —
> reads Korg markers (`i1cv1`…), maps where each part goes (ch8 bass, 9 drums,
> 10 perc, 11–15 acc; section/CV layout), completes missing per-element
> setup (CC0/32/PC, CC11) only from existing sound evidence — never guessed.
> Evidence: `artifacts-max-4.53/` + `reports-max-4.53/`; research notes
> `docs/korg-sty-research-4.53.md`; guide `docs/studio-sty-mapper-4.53.md`.
> **Mix Engineer 4.52:** gain staging dodan u postojeći projekat —
> CC11 expression automatics scaled x0.55 (-45%) on detected pad layers and the
> percussion channel (notes, velocities and CC7 untouched; gates prove it).
> Evidence: `artifacts-max-4.52/` + `reports-max-4.52/`; mido = independent
> verifier in tests (dev-only). Guide: `docs/studio-mix-4.52.md`; next steps in
> `docs/roadmap-ai-session-musician.md`.
> **Studio Truth Engine 4.51:** the studio flow now *applies*: it never blocks
> when an evidence-backed improvement exists — it fills only empty legal ACC
> slots (strum/comp live-cell chord-tone parts, pad fills), clamps velocity only
> to exact factory-profile ceilings, and proves by gates that pre-existing
> geometry is untouched. Evidence: `artifacts-max-4.51/` (JSON runs on fixture +
> `baseline/reference-style.mid`), tests `reports-max-4.51/02-tests.json`,
> guide `docs/studio-flow-4.51.md` (guide, not proof).
> **Studio Core 4.50 (reconstruction):** design review + consolidation on a single
> arranger contract — `dna_midi_studio/arranger_contract.py` (one factory-velocity,
> channel/polyphony facts, chord-tone classifier), `arranger_planner.py` (per-channel
> instrument-fit + decisions), `studio_flow.py` (analyze→plan→execute→metrics→gates).
> Evidence: `reports-max-4.50/`, guide `docs/studio-flow-4.50.md`, status `max-orchestration-status-4.50.json`.
> **Arranger Pro 4.49:** a real arranger that knows how each instrument plays —
> instrument briefs per channel, Factory-strum **Strumming**, advisory **Best
> Instruments**, **Headroom** (PA800 polyphony budget + dynamics), register fit,
> factory-only dynamics. Module `dna_midi_studio/arranger_pro.py`; e2e artifacts
> in `artifacts-max-4.49/`, evidence `reports-max-4.49/`, guide `docs/arranger-pro-4.49.md`.
> **MAX 4.48:** full MAX plan executed — layout-aware model registry (10/10),
> opt-in executor `dna_midi_studio/max_activation.py`, 13-agent team
> (`agent-team-max-4.48.json`), e2e A/B/C MIDI renders in `artifacts-max-4.48/`,
> evidence in `reports-max-4.48/`. Status: `max-orchestration-status-4.48.json`.
> **Also in this repository:** `dna_midi_studio/` — the full **DNA MIDI Studio Pa800 4.47** Python project (AI arrangement, MAX orchestration, PA800 validation, session fixtures and reports) merged in from the `main` branch. See `dna-build-report.json`, `final-software-completion-4.46.json` and `max-orchestration-status-4.32.json` at the repo root.

## Features

### Core Functionality
- **MIDI Analysis**: Deep analysis of MIDI files including note detection, velocity mapping, timing analysis
- **Chord Detection**: Automatic chord progression detection with support for major, minor, diminished, augmented, and seventh chords
- **Key Detection**: Intelligent key signature detection from pitch class distribution
- **PA800 Style Support**: Full support for Korg PA800 style markers (v1cv1, i1cv2, f2cv1, etc.)
- **Style Coverage Analysis**: Track CV slot coverage for variations, intros, fills, and endings

### Repair & Optimization
- **Timing Correction**: Snap notes to detected grid with customizable sensitivity
- **Dynamics Balancing**: Velocity smoothing with multiple preset profiles
- **Articulation Fix**: Note length optimization for cleaner playback
- **Multiple Presets**: 
  - PA800 Safe (preserve structure)
  - Stage Ready (balanced dynamics)
  - Cleaner Groove (tighter velocity)
  - More Expression (keep the feel)
  - Radio Ready (commercial polish)
  - Live Performance (human feel preserved)

### Export & Sharing
- **MIDI Export**: Download optimized MIDI files
- **Project Sharing**: Share via Web Share API or copy link
- **Preset Management**: Save and load custom repair presets
- **Batch Processing**: Process multiple files simultaneously

### User Interface
- **Dark Theme**: Professional dark UI optimized for studio use
- **Responsive Design**: Works on desktop and mobile devices
- **Real-time Preview**: A/B comparison between original and optimized versions
- **Detailed Metrics**: Score, velocity, timing, and expression analysis

## Technical Specifications

### Supported Formats
- **Input**: MIDI (.mid, .midi, .kar), Audio (.mp3), Korg Style (.sty)
- **Output**: Standard MIDI File (SMF) Format 0/1
- **Max File Size**: 50 MB

### Analysis Metrics
- Note count and distribution
- Channel usage
- Tempo detection
- Resolution (PPQ)
- Average velocity and velocity spread
- Timing drift and outliers
- Expression score
- Key signature detection
- Chord progression tracking

### Repair Algorithms
- Variable-length MIDI encoding
- Running status preservation
- Meta event handling
- SysEx message support
- End-of-track normalization

## Installation

```bash
# Install dependencies
npm install

# Development server
npm run dev

# Production build
npm run build

# Run tests
npm test

# Preview production build
npm run preview
```

## Project Structure

```
/workspace
├── index.html          # Main HTML with meta tags
├── package.json        # Dependencies and scripts
├── public/
│   └── manifest.json   # PWA manifest
├── src/
│   ├── main.jsx        # React application
│   ├── musicAnalysis.js # Core analysis engine
│   └── styles.css      # Styling
└── tests/
    └── midi-stress.test.mjs # Test suite
```

## Usage Example

1. **Upload**: Drag and drop a MIDI file or click to browse
2. **Select Preset**: Choose a repair profile based on your needs
3. **Analyze**: Click "Analyze file" to scan the arrangement
4. **Review**: Compare original vs optimized metrics
5. **Customize**: Toggle timing, dynamics, and articulation fixes
6. **Export**: Download the optimized MIDI file

## API Reference

### `analyzeUploadedFile(file)`
Analyzes an uploaded file and returns detailed metrics.

```javascript
const result = await analyzeUploadedFile(midiFile);
// Returns: { fileName, score, notes, channels, tempo, detectedKey, chordProgression, ... }
```

### `getRepairPreview(analysis, presetKey, options)`
Generates preview metrics for different repair settings.

```javascript
const preview = getRepairPreview(analysis, 'pa800-safe', { 
  applyDynamics: true, 
  applyTiming: true 
});
// Returns: { original: {...}, optimized: {...} }
```

### `createOptimizedMidi(file, presetKey, options)`
Creates an optimized MIDI file blob.

```javascript
const { blob, repairedNotes, repairedTimingEvents } = await createOptimizedMidi(
  midiFile, 
  'stage-ready',
  { applyDynamics: true, applyTiming: true }
);
```

## Testing

The project includes comprehensive stress tests:
- Multi-track MIDI analysis (12,000+ notes)
- PA800 style marker detection
- A/B preview accuracy
- Undo functionality verification
- Timing repair effectiveness
- Export integrity checks
- Error handling for malformed files

Run tests with:
```bash
npm test
```

## Browser Support

- Chrome 90+
- Firefox 88+
- Safari 14+
- Edge 90+

## License

MIT License - See LICENSE file for details.

## Author

Baja Beg - DNA Music Optimizer Team

## Acknowledgments

- Korg PA800 documentation for style format specifications
- Standard MIDI File specification (RP-001)
- Music theory algorithms based on established research
