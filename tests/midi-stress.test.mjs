import assert from 'node:assert/strict';
import test from 'node:test';
import { analyzeUploadedFile, createOptimizedMidi, getRepairPreview } from '../src/musicAnalysis.js';

const TRACK_COUNT = 6;
const NOTES_PER_TRACK = 2000;
const EXPECTED_NOTES = TRACK_COUNT * NOTES_PER_TRACK;

function encodeVariableLength(value) {
  const bytes = [value & 0x7f];
  while ((value >>= 7)) bytes.unshift((value & 0x7f) | 0x80);
  return bytes;
}

function textBytes(value) {
  return [...value].map((character) => character.charCodeAt(0));
}

function concatBytes(...parts) {
  return Uint8Array.from(parts.flat());
}

function uint16(value) {
  return [(value >> 8) & 0xff, value & 0xff];
}

function uint32(value) {
  return [(value >> 24) & 0xff, (value >> 16) & 0xff, (value >> 8) & 0xff, value & 0xff];
}

function makeTrack(trackIndex) {
  const events = [];
  if (trackIndex === 0) {
    events.push([0, 0xff, 0x51, 0x03, 0x07, 0xa1, 0x20]);
    for (const marker of ['v1cv1', 'v1cv2', 'v4cv6', 'i1cv1', 'f2cv2', 'e3cv1']) {
      events.push([0, 0xff, 0x06, marker.length, ...textBytes(marker)]);
    }
  }
  const trackName = `Track${trackIndex + 1}`;
  events.push([0, 0xff, 0x03, trackName.length, ...textBytes(trackName)]);
  for (let noteIndex = 0; noteIndex < NOTES_PER_TRACK; noteIndex += 1) {
    const note = 36 + ((noteIndex + trackIndex * 5) % 48);
    const velocity = 30 + ((noteIndex * 17 + trackIndex * 13) % 94);
    const channel = trackIndex % 4;
    events.push([0, 0x90 | channel, note, velocity]);
    events.push([48, 0x80 | channel, note, 64]);
  }
  events.push([0, 0xff, 0x2f, 0]);
  const body = events.flatMap(([delta, ...event]) => [...encodeVariableLength(delta), ...event]);
  return [...textBytes('MTrk'), ...uint32(body.length), ...body];
}

function makeStressMidi() {
  const header = [...textBytes('MThd'), ...uint32(6), ...uint16(1), ...uint16(TRACK_COUNT), ...uint16(480)];
  return concatBytes(header, ...Array.from({ length: TRACK_COUNT }, (_, index) => makeTrack(index)));
}

function makeFile(bytes, name) {
  return { name, size: bytes.byteLength, arrayBuffer: async () => bytes.buffer.slice(bytes.byteOffset, bytes.byteOffset + bytes.byteLength) };
}

const stressBytes = makeStressMidi();

test('analyzes a multi-track PA800-style stress MIDI', async () => {
  const result = await analyzeUploadedFile(makeFile(stressBytes, 'live-set-stress.mid'));

  assert.equal(result.notes, EXPECTED_NOTES);
  assert.equal(result.tracks, TRACK_COUNT);
  assert.equal(result.channels, 4);
  assert.equal(result.tempo, 120);
  assert.equal(result.resolution, 480);
  assert.equal(result.styleMarkers.length, 6);
  assert.deepEqual(result.styleMarkers, ['v1cv1', 'v1cv2', 'v4cv6', 'i1cv1', 'f2cv2', 'e3cv1']);
  assert.equal(result.styleCoverage.coveredSlots, 6);
  assert.equal(result.styleCoverage.totalSlots, 42);
  assert.ok(result.timingDrift > 0);
  assert.ok(result.timingOutliers > 0);
  assert.ok(result.analysisDurationMs >= 0);
  assert.ok(result.score >= 60 && result.score <= 99);
});

test('A/B preview reports original and optimized metrics', async () => {
  const analysis = await analyzeUploadedFile(makeFile(stressBytes, 'preview.mid'));
  const preview = getRepairPreview(analysis, 'cleaner-groove');

  assert.equal(preview.original.score, analysis.score);
  assert.equal(preview.original.averageVelocity, analysis.averageVelocity);
  assert.equal(preview.optimized.velocitySpread < preview.original.velocitySpread, true);
  assert.ok(preview.optimized.score >= 64 && preview.optimized.score <= 99);
});

test('undoing dynamics leaves the original MIDI bytes unchanged', async () => {
  const file = makeFile(stressBytes, 'undo-dynamics.mid');
  const original = new Uint8Array(await file.arrayBuffer());
  const preview = getRepairPreview(await analyzeUploadedFile(file), 'cleaner-groove', { applyDynamics: false });
  const result = await createOptimizedMidi(file, 'cleaner-groove', { applyDynamics: false });
  const output = new Uint8Array(await result.blob.arrayBuffer());

  assert.deepEqual(preview.optimized, preview.original);
  assert.equal(result.repairedNotes, 0);
  assert.deepEqual([...output], [...original]);
});

test('repairs and exports the full stress MIDI without changing its size', async () => {
  const file = makeFile(stressBytes, 'live-set-stress.mid');
  const optimized = await createOptimizedMidi(file, 'cleaner-groove');
  const optimizedBytes = new Uint8Array(await optimized.blob.arrayBuffer());

  assert.ok(optimized.repairedNotes > 0 && optimized.repairedNotes <= EXPECTED_NOTES);
  assert.equal(optimizedBytes.byteLength, stressBytes.byteLength);
  assert.deepEqual([...optimizedBytes.slice(0, 14)], [...stressBytes.slice(0, 14)]);
  assert.deepEqual([...optimizedBytes.slice(-4)], [...stressBytes.slice(-4)]);
  assert.notDeepEqual([...optimizedBytes], [...stressBytes]);
});

test('all repair profiles produce a valid, distinct velocity result', async () => {
  const file = makeFile(stressBytes, 'profiles.mid');
  const profiles = ['pa800-safe', 'stage-ready', 'cleaner-groove', 'more-expression'];
  const outputs = await Promise.all(profiles.map((profile) => createOptimizedMidi(file, profile)));
  const snapshots = await Promise.all(outputs.map(async ({ blob }) => new Uint8Array(await blob.arrayBuffer())));
  const firstNoteStatusOffset = stressBytes.findIndex((byte, index) => byte === 0x90 && stressBytes[index + 1] === 36);
  const firstNoteVelocityOffset = firstNoteStatusOffset + 2;
  const velocityValues = snapshots.map((output) => output[firstNoteVelocityOffset]);

  assert.ok(firstNoteStatusOffset > 0);
  assert.ok(outputs.every(({ repairedNotes }) => repairedNotes > 0 && repairedNotes <= EXPECTED_NOTES));
  assert.equal(new Set(velocityValues).size, profiles.length);
  assert.ok(velocityValues.every((velocity) => velocity >= 1 && velocity <= 127));
});

test('rejects unsupported, oversized and malformed uploads with user-facing errors', async () => {
  await assert.rejects(() => analyzeUploadedFile(makeFile(stressBytes, 'style.sty')), /STY|markerima/);
  await assert.rejects(() => analyzeUploadedFile({ name: 'oversized.mid', size: 50 * 1024 * 1024 + 1, arrayBuffer: async () => stressBytes.buffer }), /50 MB/);
  await assert.rejects(() => analyzeUploadedFile(makeFile(Uint8Array.from([1, 2, 3]), 'broken.mid')), /MIDI|validan|nepotpun/);
});
