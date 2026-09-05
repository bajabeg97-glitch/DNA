#!/usr/bin/env node
// GUI chain 4.59: prove evidence from the GUI (musicAnalysis.js) onward.
// Reads real MIDI files through the actual GUI analyzer entry point
// (analyzeUploadedFile — same code path the React app uses), writes the
// GUI-level analysis JSON into artifacts-max-4.59/, then the Python role
// pattern engine continues the chain on the same inputs.
import { readFileSync, mkdirSync, writeFileSync } from 'node:fs';
import { analyzeUploadedFile } from './src/musicAnalysis.js';

const inputs = [
  ['baseline/reference-style.mid', 'reference-style'],
  ['artifacts-max-4.51/arranged-4.51-fixture.mid', 'arranged-4.51-fixture'],
  ['session19-benchmark/song19-01.mid', 'song19-01'],
  ['session19-benchmark/song19-02.mid', 'song19-02'],
  ['session35-partial-preview.mid', 'session35-partial-preview'],
];

mkdirSync('artifacts-max-4.59', { recursive: true });
const runReport = { schema: 'dna-gui-chain-run', version: '4.59', date: '2026-09-05', analyses: [] };

for (const [path, stem] of inputs) {
  const bytes = readFileSync(path);
  const file = { name: path.split('/').pop(), size: bytes.byteLength,
                 arrayBuffer: async () => bytes.buffer.slice(bytes.byteOffset, bytes.byteOffset + bytes.byteLength) };
  const analysis = await analyzeUploadedFile(file);
  const outFile = `artifacts-max-4.59/gui-analysis-${stem}.json`;
  writeFileSync(outFile, JSON.stringify(analysis, null, 2));
  runReport.analyses.push({
    file: path, artifact: outFile, notes: analysis.notes, tracks: analysis.tracks,
    channels: analysis.channels, tempo: analysis.tempo, resolution: analysis.resolution,
    detectedKey: analysis.detectedKey, keyConfidence: analysis.keyConfidence,
    styleMarkers: analysis.styleMarkers?.length ?? 0,
    trackNames: analysis.trackNames, score: analysis.score,
    guiOk: true,
  });
  console.log(`GUI OK ${path} -> notes=${analysis.notes} channels=${analysis.channels} key=${analysis.detectedKey} markers=${analysis.styleMarkers?.length ?? 0} score=${analysis.score}`);
}
writeFileSync('artifacts-max-4.59/gui-run.json', JSON.stringify(runReport, null, 2));
console.log('gui-run.json written');
