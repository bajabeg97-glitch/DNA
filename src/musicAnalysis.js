const MIDI_EXTENSIONS = new Set(['mid', 'midi', 'kar']);
const MAX_ANALYSIS_BYTES = 50 * 1024 * 1024;
const REPAIR_PROFILES = {
  'pa800-safe': { targetVelocity: 96, preservation: 0.86 },
  'stage-ready': { targetVelocity: 100, preservation: 0.82 },
  'cleaner-groove': { targetVelocity: 92, preservation: 0.76 },
  'more-expression': { targetVelocity: 96, preservation: 0.95 },
};

// Key signature detection constants
const KEY_NAMES = ['C', 'C#', 'D', 'D#', 'E', 'F', 'F#', 'G', 'G#', 'A', 'A#', 'B'];
const MAJOR_PATTERNS = [2, 2, 1, 2, 2, 2, 1];
const MINOR_PATTERNS = [2, 1, 2, 2, 1, 2, 2];

// Chord detection
const CHORD_TYPES = {
  major: [0, 4, 7],
  minor: [0, 3, 7],
  diminished: [0, 3, 6],
  augmented: [0, 4, 8],
  seventh: [0, 4, 7, 10],
  minorSeventh: [0, 3, 7, 10],
  majorSeventh: [0, 4, 7, 11],
};

function textBytes(value) {
  return [...value].map((character) => character.charCodeAt(0));
}

const PA800_STYLE_ELEMENTS = [
  ...[1, 2, 3, 4].map((number) => ({ key: `v${number}`, label: `Variation ${number}`, chordVariations: 6 })),
  ...[1, 2, 3].flatMap((number) => [
    { key: `i${number}`, label: `Intro ${number}`, chordVariations: 2 },
    { key: `f${number}`, label: `Fill ${number}`, chordVariations: 2 },
    { key: `e${number}`, label: `Ending ${number}`, chordVariations: 2 },
  ]),
];

export async function analyzeUploadedFile(file) {
  const extension = file.name.split('.').pop()?.toLowerCase() ?? '';

  if (!MIDI_EXTENSIONS.has(extension)) {
    throw new Error('Duboka analiza je trenutno dostupna za MIDI i KAR fajlove. Za PA800 style prvo izvezite SMF sa markerima poput v1cv1, i1cv2 ili f1cv1.');
  }

  if (file.size > MAX_ANALYSIS_BYTES) throw new Error('Fajl je veći od 50 MB limita za brzu analizu.');
  const startedAt = Date.now();
  const result = analyzeMidi(await file.arrayBuffer(), file.name);
  return { ...result, analysisDurationMs: Date.now() - startedAt };
}

export function getRepairPreview(analysis, presetKey = 'pa800-safe', options = {}) {
  const repairProfile = REPAIR_PROFILES[presetKey] ?? REPAIR_PROFILES['pa800-safe'];
  const applyDynamics = options.applyDynamics ?? true;
  const applyTiming = options.applyTiming ?? true;
  const optimizedVelocitySpread = applyDynamics ? Math.max(1, Math.round(analysis.velocitySpread * repairProfile.preservation)) : analysis.velocitySpread;
  const optimizedAverageVelocity = applyDynamics ? Math.round(analysis.averageVelocity * repairProfile.preservation + repairProfile.targetVelocity * (1 - repairProfile.preservation)) : analysis.averageVelocity;
  const optimizedExpressionScore = Math.min(99, Math.max(64, 62 + Math.round(optimizedVelocitySpread * 0.28)));
  const optimizedTimingScore = applyTiming ? Math.min(99, analysis.timingScore + Math.min(20, analysis.timingOutliers)) : analysis.timingScore;
  const optimizedScore = Math.round((optimizedTimingScore + optimizedExpressionScore + Math.min(99, 72 + analysis.channels * 7)) / 3);

  return {
    original: { score: analysis.score, averageVelocity: analysis.averageVelocity, velocitySpread: analysis.velocitySpread, expressionScore: analysis.expressionScore, timingScore: analysis.timingScore },
    optimized: { score: optimizedScore, averageVelocity: optimizedAverageVelocity, velocitySpread: optimizedVelocitySpread, expressionScore: optimizedExpressionScore, timingScore: optimizedTimingScore },
  };
}

function encodeVariableLength(value) {
  const bytes = [value & 0x7f];
  while ((value >>= 7)) bytes.unshift((value & 0x7f) | 0x80);
  return bytes;
}

function snapToGrid(tick, grid) {
  return Math.max(0, Math.round(tick / grid) * grid);
}

export async function createOptimizedMidi(file, presetKey = 'pa800-safe', options = {}) {
  const source = new Uint8Array(await file.arrayBuffer());
  const view = new DataView(source.buffer, source.byteOffset, source.byteLength);
  const repairProfile = REPAIR_PROFILES[presetKey] ?? REPAIR_PROFILES['pa800-safe'];
  const applyDynamics = options.applyDynamics ?? true;
  const applyTiming = options.applyTiming ?? true;
  let offset = 0;
  let repairedNotes = 0;
  let repairedTimingEvents = 0;

  const readText = (length) => {
    if (offset + length > view.byteLength) throw new Error('MIDI fajl je nepotpun ili oštećen.');
    let value = '';
    for (let index = 0; index < length; index += 1) value += String.fromCharCode(view.getUint8(offset + index));
    offset += length;
    return value;
  };
  const readUint16 = () => {
    if (offset + 2 > view.byteLength) throw new Error('MIDI fajl je nepotpun ili oštećen.');
    const value = view.getUint16(offset);
    offset += 2;
    return value;
  };
  const readUint32 = () => {
    if (offset + 4 > view.byteLength) throw new Error('MIDI fajl je nepotpun ili oštećen.');
    const value = view.getUint32(offset);
    offset += 4;
    return value;
  };
  const readVariableLength = (bytes, cursor, end) => {
    let value = 0;
    let count = 0;
    while (cursor.value < end && count < 4) {
      const byte = bytes[cursor.value];
      cursor.value += 1;
      value = (value << 7) | (byte & 0x7f);
      count += 1;
      if ((byte & 0x80) === 0) return value;
    }
    throw new Error('MIDI variable-length događaj nije validan.');
  };

  if (readText(4) !== 'MThd') throw new Error('Fajl nije validan Standard MIDI fajl.');
  const headerLength = readUint32();
  if (headerLength < 6 || offset + headerLength > view.byteLength) throw new Error('MIDI header nije validan.');
  readUint16();
  const trackCount = readUint16();
  const division = readUint16();
  offset += headerLength - 6;
  const outputParts = [source.slice(0, offset)];
  const timingGrid = Math.max(1, Math.round(division / 4));

  for (let trackIndex = 0; trackIndex < trackCount; trackIndex += 1) {
    if (readText(4) !== 'MTrk') throw new Error(`Track ${trackIndex + 1} nije validan.`);
    const trackLength = readUint32();
    const trackEnd = offset + trackLength;
    if (trackEnd > view.byteLength) throw new Error('MIDI track izlazi van granica fajla.');
    const trackBytes = source.slice(offset, trackEnd);
    const cursor = { value: 0 };
    const events = [];
    let tick = 0;
    let order = 0;
    let runningStatus = null;

    while (cursor.value < trackBytes.length) {
      tick += readVariableLength(trackBytes, cursor, trackBytes.length);
      let status = trackBytes[cursor.value];
      if (status < 0x80) {
        if (runningStatus === null) throw new Error('MIDI događaj nema running status.');
        status = runningStatus;
      } else {
        cursor.value += 1;
        if (status < 0xf0) runningStatus = status;
      }

      if (status === 0xff) {
        const metaType = trackBytes[cursor.value];
        cursor.value += 1;
        const length = readVariableLength(trackBytes, cursor, trackBytes.length);
        if (cursor.value + length > trackBytes.length) throw new Error('MIDI meta događaj je nepotpun.');
        events.push({ kind: 'meta', tick, order: order += 1, metaType, data: [...trackBytes.slice(cursor.value, cursor.value + length)] });
        cursor.value += length;
        continue;
      }
      if (status === 0xf0 || status === 0xf7) {
        const length = readVariableLength(trackBytes, cursor, trackBytes.length);
        if (cursor.value + length > trackBytes.length) throw new Error('MIDI sysex događaj je nepotpun.');
        events.push({ kind: 'sysex', tick, order: order += 1, status, data: [...trackBytes.slice(cursor.value, cursor.value + length)] });
        cursor.value += length;
        continue;
      }

      const eventType = status >> 4;
      const dataLength = eventType === 0xc || eventType === 0xd ? 1 : (status >= 0xf0 ? (status === 0xf1 || status === 0xf3 ? 1 : status === 0xf2 ? 2 : 0) : 2);
      if (cursor.value + dataLength > trackBytes.length) throw new Error('MIDI channel događaj je nepotpun.');
      events.push({ kind: status >= 0xf0 ? 'system' : 'channel', tick, order: order += 1, status, data: [...trackBytes.slice(cursor.value, cursor.value + dataLength)] });
      cursor.value += dataLength;
    }

    const openNotes = new Map();
    const notePairs = [];
    for (const event of events) {
      if (event.kind !== 'channel') continue;
      const eventType = event.status >> 4;
      if (eventType !== 0x8 && eventType !== 0x9) continue;
      const noteKey = `${event.status & 0x0f}:${event.data[0]}`;
      if (eventType === 0x9 && event.data[1] > 0) {
        const notes = openNotes.get(noteKey) ?? [];
        notes.push(event);
        openNotes.set(noteKey, notes);
      } else {
        const notes = openNotes.get(noteKey);
        const startEvent = notes?.shift();
        if (startEvent) notePairs.push([startEvent, event]);
      }
      if (eventType === 0x9 && event.data[1] > 0 && applyDynamics) {
        const originalVelocity = event.data[1];
        event.data[1] = Math.max(1, Math.min(127, Math.round(originalVelocity * repairProfile.preservation + repairProfile.targetVelocity * (1 - repairProfile.preservation))));
        if (event.data[1] !== originalVelocity) repairedNotes += 1;
      }
    }

    if (applyTiming) {
      for (const [startEvent, endEvent] of notePairs) {
        const nextStart = snapToGrid(startEvent.tick, timingGrid);
        const nextEnd = Math.max(nextStart + timingGrid, snapToGrid(endEvent.tick, timingGrid));
        if (nextStart !== startEvent.tick) {
          startEvent.tick = nextStart;
          repairedTimingEvents += 1;
        }
        if (nextEnd !== endEvent.tick) {
          endEvent.tick = nextEnd;
          repairedTimingEvents += 1;
        }
      }
    }

    const endOfTrack = events.find((event) => event.kind === 'meta' && event.metaType === 0x2f);
    if (endOfTrack) {
      const lastEventTick = Math.max(...events.filter((event) => event !== endOfTrack).map((event) => event.tick), endOfTrack.tick);
      endOfTrack.tick = Math.max(endOfTrack.tick, lastEventTick);
    }
    events.sort((left, right) => left.tick - right.tick || left.order - right.order);
    const repairedTrack = [];
    let previousTick = 0;
    for (const event of events) {
      repairedTrack.push(...encodeVariableLength(Math.max(0, event.tick - previousTick)));
      previousTick = event.tick;
      if (event.kind === 'meta') repairedTrack.push(0xff, event.metaType, ...encodeVariableLength(event.data.length), ...event.data);
      else if (event.kind === 'sysex') repairedTrack.push(event.status, ...encodeVariableLength(event.data.length), ...event.data);
      else repairedTrack.push(event.status, ...event.data);
    }
    outputParts.push(new Uint8Array([...textBytes('MTrk'), ...[(repairedTrack.length >>> 24) & 0xff, (repairedTrack.length >>> 16) & 0xff, (repairedTrack.length >>> 8) & 0xff, repairedTrack.length & 0xff], ...repairedTrack]));
    offset = trackEnd;
  }

  return { blob: new Blob(outputParts, { type: 'audio/midi' }), repairedNotes, repairedTimingEvents };
}

function isPa800Marker(marker) {
  const match = marker.match(/^([vife])(\d)cv(\d)$/);
  if (!match) return false;
  const elementNumber = Number(match[2]);
  const chordVariation = Number(match[3]);
  const maxElementNumber = match[1] === 'v' ? 4 : 3;
  const maxChordVariation = match[1] === 'v' ? 6 : 2;
  return elementNumber <= maxElementNumber && chordVariation <= maxChordVariation;
}

function buildStyleCoverage(markers) {
  const elements = PA800_STYLE_ELEMENTS.map((element) => {
    const elementMarkers = markers.filter((marker) => marker.startsWith(`${element.key}cv`));
    return { ...element, found: elementMarkers.length, markers: elementMarkers };
  });
  const totalSlots = elements.reduce((sum, element) => sum + element.chordVariations, 0);
  const coveredSlots = elements.reduce((sum, element) => sum + element.found, 0);
  return { elements, totalSlots, coveredSlots };
}

function analyzeMidi(buffer, fileName) {
  const view = new DataView(buffer);
  let offset = 0;

  const readText = (length) => {
    if (offset + length > view.byteLength) throw new Error('MIDI fajl je nepotpun ili oštećen.');
    let value = '';
    for (let index = 0; index < length; index += 1) value += String.fromCharCode(view.getUint8(offset + index));
    offset += length;
    return value;
  };
  const readUint16 = () => {
    if (offset + 2 > view.byteLength) throw new Error('MIDI fajl je nepotpun ili oštećen.');
    const value = view.getUint16(offset);
    offset += 2;
    return value;
  };
  const readUint32 = () => {
    if (offset + 4 > view.byteLength) throw new Error('MIDI fajl je nepotpun ili oštećen.');
    const value = view.getUint32(offset);
    offset += 4;
    return value;
  };
  const readVariableLength = (end) => {
    let value = 0;
    let count = 0;
    while (offset < end && count < 4) {
      const byte = view.getUint8(offset);
      offset += 1;
      value = (value << 7) | (byte & 0x7f);
      count += 1;
      if ((byte & 0x80) === 0) return value;
    }
    throw new Error('MIDI variable-length događaj nije validan.');
  };

  if (readText(4) !== 'MThd') throw new Error('Fajl nije validan Standard MIDI fajl.');
  const headerLength = readUint32();
  if (headerLength < 6 || offset + headerLength > view.byteLength) throw new Error('MIDI header nije validan.');
  const format = readUint16();
  const trackCount = readUint16();
  const division = readUint16();
  offset += headerLength - 6;

  if (division & 0x8000) throw new Error('SMPTE MIDI timing trenutno nije podržan.');
  if (trackCount === 0) throw new Error('MIDI fajl nema trackove.');

  let totalNotes = 0;
  let totalControllers = 0;
  let maxTick = 0;
  let tempo = 120;
  let trackNames = 0;
  let timingDistanceTotal = 0;
  let timingOutliers = 0;
  const styleMarkers = [];
  let velocityTotal = 0;
  let velocityLowest = 127;
  let velocityHighest = 0;
  const channels = new Set();
  const openNotes = new Map();
  const durations = [];
  const pitchClasses = new Array(12).fill(0);
  const timingGrid = Math.max(1, Math.round(division / 4));

  for (let trackIndex = 0; trackIndex < trackCount; trackIndex += 1) {
    if (readText(4) !== 'MTrk') throw new Error(`Track ${trackIndex + 1} nije validan.`);
    const trackLength = readUint32();
    const trackEnd = offset + trackLength;
    if (trackEnd > view.byteLength) throw new Error('MIDI track izlazi van granica fajla.');

    let tick = 0;
    let runningStatus = null;
    while (offset < trackEnd) {
      tick += readVariableLength(trackEnd);
      maxTick = Math.max(maxTick, tick);
      let status = view.getUint8(offset);
      if (status < 0x80) {
        if (runningStatus === null) throw new Error('MIDI događaj nema running status.');
        status = runningStatus;
      } else {
        offset += 1;
        if (status < 0xf0) runningStatus = status;
      }

      if (status === 0xff) {
        const metaType = view.getUint8(offset);
        offset += 1;
        const length = readVariableLength(trackEnd);
        if (offset + length > trackEnd) throw new Error('MIDI meta događaj je nepotpun.');
        if (metaType === 0x51 && length === 3) tempo = Math.round(60000000 / ((view.getUint8(offset) << 16) | (view.getUint8(offset + 1) << 8) | view.getUint8(offset + 2)));
        if (metaType === 0x03 && length > 0) trackNames += 1;
        if ((metaType === 0x01 || metaType === 0x06) && length > 0) {
          let marker = '';
          for (let markerIndex = 0; markerIndex < length; markerIndex += 1) marker += String.fromCharCode(view.getUint8(offset + markerIndex));
          const normalizedMarker = marker.trim().toLowerCase();
          if (isPa800Marker(normalizedMarker)) styleMarkers.push(normalizedMarker);
        }
        offset += length;
        if (metaType === 0x2f) break;
        continue;
      }

      if (status === 0xf0 || status === 0xf7) {
        offset += readVariableLength(trackEnd);
        continue;
      }

      const eventType = status >> 4;
      const channel = status & 0x0f;
      channels.add(channel);
      const firstData = view.getUint8(offset);
      const secondData = eventType === 0xc || eventType === 0xd ? null : view.getUint8(offset + 1);
      offset += secondData === null ? 1 : 2;

      if (eventType === 0x9 && secondData > 0) {
        totalNotes += 1;
        velocityTotal += secondData;
        velocityLowest = Math.min(velocityLowest, secondData);
        velocityHighest = Math.max(velocityHighest, secondData);
        pitchClasses[firstData % 12] += 1;
        const remainder = tick % timingGrid;
        const timingDistance = Math.min(remainder, timingGrid - remainder);
        timingDistanceTotal += timingDistance;
        if (timingDistance > timingGrid / 3) timingOutliers += 1;
        const noteKey = `${channel}:${firstData}`;
        const notes = openNotes.get(noteKey) ?? [];
        notes.push({ tick, velocity: secondData });
        openNotes.set(noteKey, notes);
      } else if (eventType === 0x8 || (eventType === 0x9 && secondData === 0)) {
        const noteKey = `${channel}:${firstData}`;
        const notes = openNotes.get(noteKey);
        if (notes?.length) {
          const note = notes.shift();
          durations.push(Math.max(0, tick - note.tick));
        }
      } else if (eventType === 0xb) {
        totalControllers += 1;
      }
    }
    offset = trackEnd;
  }

  if (totalNotes === 0) throw new Error('MIDI fajl ne sadrži note za analizu.');
  const averageVelocity = Math.round(velocityTotal / totalNotes);
  const velocitySpread = velocityHighest - velocityLowest;
  const averageDuration = durations.length ? Math.round(durations.reduce((sum, duration) => sum + duration, 0) / durations.length) : 0;
  const topPitchClass = pitchClasses.indexOf(Math.max(...pitchClasses));
  const timingDrift = Math.round(timingDistanceTotal / totalNotes);
  const timingScore = Math.min(99, Math.max(60, 100 - Math.round((timingDrift / timingGrid) * 100)));
  const expressionScore = Math.min(99, Math.max(64, 62 + Math.round(velocitySpread * 0.28)));
  const score = Math.round((timingScore + expressionScore + Math.min(99, 72 + channels.size * 7)) / 3);

  // Detect key signature from pitch class distribution
  let detectedKey = null;
  let keyConfidence = 0;
  for (let root = 0; root < 12; root++) {
    let majorFit = 0;
    let minorFit = 0;
    for (let i = 0; i < 7; i++) {
      const majorIndex = (root + MAJOR_PATTERNS.slice(0, i).reduce((a, b) => a + b, 0)) % 12;
      const minorIndex = (root + MINOR_PATTERNS.slice(0, i).reduce((a, b) => a + b, 0)) % 12;
      majorFit += pitchClasses[majorIndex];
      minorFit += pitchClasses[minorIndex];
    }
    if (majorFit > keyConfidence) {
      keyConfidence = majorFit;
      detectedKey = `${KEY_NAMES[root]} major`;
    }
    if (minorFit > keyConfidence) {
      keyConfidence = minorFit;
      detectedKey = `${KEY_NAMES[root]} minor`;
    }
  }

  // Detect chord progressions
  const chordProgression = [];
  const windowSize = division * 4; // One beat window
  for (let tick = 0; tick < maxTick; tick += windowSize) {
    const activePitches = new Set();
    for (const [noteKey, notes] of openNotes.entries()) {
      const [, pitch] = noteKey.split(':').map(Number);
      const noteStart = notes.find(n => n.tick <= tick && n.tick + (durations[notes.indexOf(n)] || 0) > tick);
      if (noteStart) activePitches.add(pitch % 12);
    }
    if (activePitches.size >= 3) {
      const pitches = Array.from(activePitches).sort((a, b) => a - b);
      for (let root = 0; root < 12; root++) {
        for (const [chordName, intervals] of Object.entries(CHORD_TYPES)) {
          const transposedIntervals = intervals.map(i => (root + i) % 12);
          if (transposedIntervals.every(i => pitches.includes(i))) {
            chordProgression.push({ tick, chord: `${KEY_NAMES[root]} ${chordName.replace(/([A-Z])/g, ' $1').trim()}` });
            break;
          }
        }
        if (chordProgression[chordProgression.length - 1]?.tick === tick) break;
      }
    }
  }

  return {
    fileName,
    format: 'MIDI',
    formatLabel: `Format ${format} · ${trackCount} trackova`,
    score,
    notes: totalNotes,
    tracks: trackCount,
    channels: channels.size,
    tempo,
    resolution: division,
    averageVelocity,
    velocitySpread,
    averageDuration,
    totalControllers,
    trackNames,
    topPitchClass,
    detectedKey,
    keyConfidence,
    chordProgression: chordProgression.slice(0, 50), // Limit to first 50 chords
    timingScore,
    timingGrid,
    timingDrift,
    timingOutliers,
    expressionScore,
    styleMarkers,
    styleCoverage: buildStyleCoverage(styleMarkers),
  };
}
