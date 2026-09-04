const MIDI_EXTENSIONS = new Set(['mid', 'midi', 'kar']);

export async function analyzeUploadedFile(file) {
  const extension = file.name.split('.').pop()?.toLowerCase() ?? '';

  if (!MIDI_EXTENSIONS.has(extension)) {
    throw new Error('Duboka analiza je trenutno dostupna za MIDI i KAR fajlove. STY i MP3 adapter dolazi u sledećoj fazi.');
  }

  return analyzeMidi(await file.arrayBuffer(), file.name);
}

export async function createOptimizedMidi(file) {
  const source = new Uint8Array(await file.arrayBuffer());
  const output = source.slice();
  const view = new DataView(source.buffer, source.byteOffset, source.byteLength);
  let offset = 0;
  let repairedNotes = 0;

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
  readUint16();
  const trackCount = readUint16();
  readUint16();
  offset += headerLength - 6;

  for (let trackIndex = 0; trackIndex < trackCount; trackIndex += 1) {
    if (readText(4) !== 'MTrk') throw new Error(`Track ${trackIndex + 1} nije validan.`);
    const trackLength = readUint32();
    const trackEnd = offset + trackLength;
    if (trackEnd > view.byteLength) throw new Error('MIDI track izlazi van granica fajla.');

    let runningStatus = null;
    while (offset < trackEnd) {
      readVariableLength(trackEnd);
      let status = view.getUint8(offset);
      if (status < 0x80) {
        if (runningStatus === null) throw new Error('MIDI događaj nema running status.');
        status = runningStatus;
      } else {
        offset += 1;
        if (status < 0xf0) runningStatus = status;
      }

      if (status === 0xff) {
        offset += 1;
        const length = readVariableLength(trackEnd);
        offset += length;
        continue;
      }
      if (status === 0xf0 || status === 0xf7) {
        offset += readVariableLength(trackEnd);
        continue;
      }

      const eventType = status >> 4;
      const dataOffset = offset;
      const secondOffset = dataOffset + 1;
      offset += eventType === 0xc || eventType === 0xd ? 1 : 2;
      if (eventType === 0x9 && view.getUint8(secondOffset) > 0) {
        output[secondOffset] = Math.max(1, Math.min(127, Math.round(view.getUint8(secondOffset) * 0.86 + 96 * 0.14)));
        repairedNotes += 1;
      }
    }
    offset = trackEnd;
  }

  return { blob: new Blob([output], { type: 'audio/midi' }), repairedNotes };
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
  let velocityTotal = 0;
  let velocityLowest = 127;
  let velocityHighest = 0;
  const channels = new Set();
  const openNotes = new Map();
  const durations = [];
  const pitchClasses = new Array(12).fill(0);

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
  const timingScore = Math.min(99, Math.max(72, 96 - Math.round((totalNotes - durations.length) / Math.max(1, totalNotes) * 18)));
  const expressionScore = Math.min(99, Math.max(64, 62 + Math.round(velocitySpread * 0.28)));
  const score = Math.round((timingScore + expressionScore + Math.min(99, 72 + channels.size * 7)) / 3);

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
    timingScore,
    expressionScore,
  };
}
