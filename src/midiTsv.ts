import {
  type MidiData,
  type MidiEvent,
  parseMidi,
  writeMidi,
} from "midi-file";

interface NoteRecord {
  t: number;
  dur: number;
  pitch: number;
  vel: number;
  channel: number;
}

interface PedalRecord {
  type: PedalType;
  t: number;
  val: number;
  channel: number;
}

interface MarkerRecord {
  t: number;
  text: string;
}

interface SliceRecord {
  id: number;
  start: number;
  end: number;
}

interface TimedEvent {
  tick: number;
  order: number;
  event: MidiEvent;
}

interface TsvMeta {
  tpq: number;
  tickScale: number;
  version: string;
  tempos: Array<{ tick: number; microsecondsPerBeat: number }>;
  timeSignatures: Array<{
    tick: number;
    numerator: number;
    denominator: number;
    metronome: number;
    thirtyseconds: number;
  }>;
  keySignatures: Array<{ tick: number; key: number; scale: number }>;
  trackChannels: Map<number, number>;
}

type PedalType = "P" | "P1" | "P2" | "P3";

const PEDAL_TO_CC: Record<PedalType, number> = { P: 64, P1: 67, P2: 66, P3: 11 };
const CC_TO_PEDAL = new Map<number, PedalType>([
  [64, "P"],
  [67, "P1"],
  [66, "P2"],
  [11, "P3"],
]);

const MIN_TICK_SCALE = 5;
const MAX_TICK_SCALE = 50;
const TARGET_TICK_SCALE_MS = 20;
const DEFAULT_MICROSECONDS_PER_BEAT = 500000;
const MIN_SLICE_SECONDS = 10;
const TARGET_SLICE_SECONDS = 15;
const MAX_SLICE_SECONDS = 20;
const MIN_GAP_SECONDS = 0.35;
const PEDAL_VALUE_EPSILON = 3;

const NATURAL_PITCH_CLASS: Record<string, number> = {
  C: 0,
  D: 2,
  E: 4,
  F: 5,
  G: 7,
  A: 9,
  B: 11,
};

const NATURAL_BY_CLASS: Record<number, string> = {
  0: "C",
  2: "D",
  4: "E",
  5: "F",
  7: "G",
  9: "A",
  11: "B",
};

const SHARP_BY_CLASS: Record<number, string> = {
  0: "C",
  1: "^C",
  2: "D",
  3: "^D",
  4: "E",
  5: "F",
  6: "^F",
  7: "G",
  8: "^G",
  9: "A",
  10: "^A",
  11: "B",
};

const MAJOR_SCALES: Record<string, number[]> = {
  C: [0, 2, 4, 5, 7, 9, 11],
  G: [0, 2, 4, 6, 7, 9, 11],
  D: [1, 2, 4, 6, 7, 9, 11],
  A: [1, 2, 4, 6, 8, 9, 11],
  E: [1, 3, 4, 6, 8, 9, 11],
  B: [1, 3, 4, 6, 8, 10, 11],
  "F#": [1, 3, 5, 6, 8, 10, 11],
  Db: [0, 1, 3, 5, 6, 8, 10],
  Ab: [0, 1, 3, 5, 7, 8, 10],
  Eb: [0, 2, 3, 5, 7, 8, 10],
  Bb: [0, 2, 3, 5, 7, 9, 10],
  F: [0, 2, 4, 5, 7, 9, 10],
};

const KEY_ACCIDENTALS: Record<string, Record<number, string>> = {
  C: {},
  G: { 6: "^F" },
  D: { 6: "^F", 1: "^C" },
  A: { 6: "^F", 1: "^C", 8: "^G" },
  E: { 6: "^F", 1: "^C", 8: "^G", 3: "^D" },
  B: { 6: "^F", 1: "^C", 8: "^G", 3: "^D", 10: "^A" },
  "F#": { 6: "^F", 1: "^C", 8: "^G", 3: "^D", 10: "^A", 5: "^E" },
  F: { 10: "_B" },
  Bb: { 10: "_B", 3: "_E" },
  Eb: { 10: "_B", 3: "_E", 8: "_A" },
  Ab: { 10: "_B", 3: "_E", 8: "_A", 1: "_D" },
  Db: { 10: "_B", 3: "_E", 8: "_A", 1: "_D", 6: "_G" },
};

export function isMidiTsvUriPath(path: string): boolean {
  const lower = path.toLowerCase();
  return lower.endsWith(".mid.tsv") || lower.endsWith(".midi.tsv");
}

export function midiToTsv(data: Uint8Array, source = "unknown.mid"): string {
  const midi = parseMidi(data);
  const tpq = midi.header.ticksPerBeat;
  if (!tpq) {
    throw new Error("Only tick-based MIDI files are supported");
  }

  const notes: NoteRecord[] = [];
  const pedals: PedalRecord[] = [];
  const markers: MarkerRecord[] = [];
  const tempos: TsvMeta["tempos"] = [];
  const timeSignatures: TsvMeta["timeSignatures"] = [];
  const keySignatures: TsvMeta["keySignatures"] = [];
  let defaultChannel = 0;
  let endTick = 0;

  midi.tracks.forEach((track) => {
    let tick = 0;
    const openNotes = new Map<string, NoteRecord[]>();

    for (const event of track) {
      tick += event.deltaTime;
      endTick = Math.max(endTick, tick);

      if (event.type === "setTempo") {
        tempos.push({ tick, microsecondsPerBeat: event.microsecondsPerBeat });
      } else if (event.type === "timeSignature") {
        timeSignatures.push({
          tick,
          numerator: event.numerator,
          denominator: event.denominator,
          metronome: event.metronome,
          thirtyseconds: event.thirtyseconds,
        });
      } else if (event.type === "keySignature") {
        keySignatures.push({ tick, key: event.key, scale: event.scale });
      } else if (event.type === "marker" || event.type === "cuePoint") {
        markers.push({ t: tick, text: String((event as any).text ?? "") });
      } else if (event.type === "noteOn" && event.velocity > 0) {
        defaultChannel = event.channel;
        const key = `${event.channel}:${event.noteNumber}`;
        const queue = openNotes.get(key) ?? [];
        queue.push({
          t: tick,
          dur: 0,
          pitch: event.noteNumber,
          vel: event.velocity,
          channel: event.channel,
        });
        openNotes.set(key, queue);
      } else if (event.type === "noteOff" || event.type === "noteOn") {
        defaultChannel = event.channel;
        const key = `${event.channel}:${event.noteNumber}`;
        const queue = openNotes.get(key);
        const note = queue?.shift();
        if (note) {
          note.dur = Math.max(0, tick - note.t);
          notes.push(note);
        }
        if (queue && queue.length === 0) {
          openNotes.delete(key);
        }
      } else if (event.type === "controller") {
        const type = CC_TO_PEDAL.get(event.controllerType);
        if (type) {
          defaultChannel = event.channel;
          pedals.push({ type, t: tick, val: event.value, channel: event.channel });
        }
      }
    }

    for (const queue of openNotes.values()) {
      for (const note of queue) {
        note.dur = Math.max(0, endTick - note.t);
        notes.push(note);
      }
    }
  });

  for (const note of notes) {
    endTick = Math.max(endTick, note.t + note.dur);
  }

  notes.sort(sortTimed);
  pedals.sort(sortTimed);
  const quantizedPedals = quantizePedalEvents(pedals, PEDAL_VALUE_EPSILON);
  markers.sort(sortTimed);
  tempos.sort(sortTimed);

  const tickScale = selectTickScale(tpq, tempos);
  const slices = createSlices(notes, quantizedPedals, endTick, tpq, tempos, tickScale);
  const detectedKey = detectKeyFromNotes(notes);

  const lines = [
    "# midi-tsv v0.2",
    `# source=${source}`,
    "# unit=tick",
    `# tick_scale=${tickScale}`,
    `# tpq=${tpq}`,
    "# pitch=abc-absolute",
    `# detected_key=${detectedKey}`,
    `# channel=${defaultChannel}`,
  ];

  for (const tempo of tempos) {
    lines.push(`# tempo=${scaleTick(tempo.tick, tickScale)},${tempo.microsecondsPerBeat}`);
  }
  for (const sig of timeSignatures) {
    lines.push(
      `# time_signature=${scaleTick(sig.tick, tickScale)},${sig.numerator},${sig.denominator},${sig.metronome},${sig.thirtyseconds}`
    );
  }
  for (const key of keySignatures) {
    lines.push(`# key_signature=${scaleTick(key.tick, tickScale)},${key.key},${key.scale}`);
  }

  lines.push("");

  for (const slice of slices) {
    const localStart = findSliceLocalStart(notes, quantizedPedals, markers, slice, slices);
    const isLast = slice.id === slices.length;
    const sliceNotes = notes.filter(
      (note) => note.t >= localStart && (note.t < slice.end || isLast)
    );
    const sliceKey = detectKeyFromNotes(sliceNotes) || detectedKey;

    lines.push(
      `S${slice.id}\t${scaleTick(localStart, tickScale)}\t${scaleTick(slice.end, tickScale)}`
    );

    const records = [
      ...sliceNotes.map((note) => ({
        t: note.t,
        order: 1,
        line: `${midiPitchToAbcSmart(note.pitch, sliceKey)}${scaleDuration(note.dur, tickScale)}\t${scaleTick(note.t - localStart, tickScale)}\t${note.vel}`,
      })),
      ...quantizedPedals
        .filter((item) => item.t >= localStart && (item.t < slice.end || isLast))
        .map((pedal) => ({
          t: pedal.t,
          order: 0,
          line: `${pedal.type}\t${scaleTick(pedal.t - localStart, tickScale)}\t${pedal.val}`,
        })),
      ...markers
        .filter((item) => item.t >= localStart && (item.t < slice.end || isLast))
        .map((marker) => ({
          t: marker.t,
          order: 2,
          line: `M\t${scaleTick(marker.t - localStart, tickScale)}\t${JSON.stringify(marker.text)}`,
        })),
    ].sort((a, b) => a.t - b.t || a.order - b.order);

    for (const record of records) {
      lines.push(record.line);
    }
    lines.push("");
  }

  return `${lines.join("\n").trimEnd()}\n`;
}

export function tsvToMidi(tsv: string): Uint8Array {
  const meta = parseTsvMeta(tsv);
  return meta.version === "v0.1" ? tsvV1ToMidi(tsv, meta) : tsvV2ToMidi(tsv, meta);
}

function tsvV2ToMidi(tsv: string, meta: TsvMeta): Uint8Array {
  const events: TimedEvent[] = [];
  let currentSliceStart = 0;
  const channel = meta.trackChannels.get(1) ?? 0;

  for (const [lineIndex, rawLine] of tsv.split(/\r?\n/).entries()) {
    const line = rawLine.trim();
    if (!line || line.startsWith("#")) {
      continue;
    }

    const fields = rawLine.split("\t");
    const recordType = fields[0];

    if (isSliceRecord(recordType)) {
      requireFieldCount(fields, 3, lineIndex);
      currentSliceStart =
        parseNonNegativeInt(fields[1], "slice start", lineIndex) * meta.tickScale;
    } else if (isNoteRecord(recordType)) {
      requireFieldCount(fields, 3, lineIndex);
      const [pitchAbc, durMacro] = parseNoteRecord(recordType, lineIndex);
      const localT =
        parseNonNegativeInt(fields[1], "note t", lineIndex) * meta.tickScale;
      const dur = durMacro * meta.tickScale;
      const pitch = abcPitchToMidi(pitchAbc);
      const vel = parseMidiValue(fields[2], "velocity", lineIndex);
      const onTick = currentSliceStart + localT;
      events.push(makeNoteOn(onTick, channel, pitch, vel));
      events.push(makeNoteOff(onTick + dur, channel, pitch));
    } else if (isPedalRecord(recordType)) {
      requireFieldCount(fields, 3, lineIndex);
      const localT =
        parseNonNegativeInt(fields[1], "pedal t", lineIndex) * meta.tickScale;
      const val = parseMidiValue(fields[2], "pedal value", lineIndex);
      events.push({
        tick: currentSliceStart + localT,
        order: 1,
        event: {
          deltaTime: 0,
          type: "controller",
          channel,
          controllerType: PEDAL_TO_CC[recordType],
          value: val,
        },
      });
    } else if (recordType === "M") {
      requireFieldCount(fields, 3, lineIndex);
      const localT =
        parseNonNegativeInt(fields[1], "marker t", lineIndex) * meta.tickScale;
      events.push({
        tick: currentSliceStart + localT,
        order: 0,
        event: {
          deltaTime: 0,
          meta: true,
          type: "marker",
          text: parseMarkerText(fields[2]),
        } as MidiEvent,
      });
    } else {
      throw new Error(`Line ${lineIndex + 1}: unknown record type "${recordType}"`);
    }
  }

  addMetaEvents(events, meta);
  const midi: MidiData = {
    header: { format: 0, numTracks: 1, ticksPerBeat: meta.tpq },
    tracks: [toDeltaEvents(events)],
  };
  return Uint8Array.from(writeMidi(midi, { useByte9ForNoteOff: false }));
}

function tsvV1ToMidi(tsv: string, meta: TsvMeta): Uint8Array {
  const trackEvents = new Map<number, TimedEvent[]>();
  let currentSliceStart = 0;
  let currentTrackId: number | undefined;

  for (const [lineIndex, rawLine] of tsv.split(/\r?\n/).entries()) {
    const line = rawLine.trim();
    if (!line || line.startsWith("#")) {
      continue;
    }

    const fields = rawLine.split("\t");
    const recordType = fields[0];

    if (recordType === "S") {
      requireFieldCount(fields, 4, lineIndex);
      currentSliceStart =
        parseNonNegativeInt(fields[2], "slice start", lineIndex) * meta.tickScale;
    } else if (recordType === "T") {
      requireFieldCount(fields, 2, lineIndex);
      currentTrackId = parsePositiveInt(fields[1], "track id", lineIndex);
      ensureTrack(trackEvents, currentTrackId);
    } else if (isAbcPitch(recordType)) {
      if (currentTrackId === undefined) {
        throw new Error(`Line ${lineIndex + 1}: note record appears before T record`);
      }
      requireFieldCount(fields, 4, lineIndex);
      const localT =
        parseNonNegativeInt(fields[1], "note t", lineIndex) * meta.tickScale;
      const dur =
        parseNonNegativeInt(fields[2], "note dur", lineIndex) * meta.tickScale;
      const pitch = abcPitchToMidi(fields[0]);
      const vel = parseMidiValue(fields[3], "velocity", lineIndex);
      const channel = meta.trackChannels.get(currentTrackId) ?? 0;
      const onTick = currentSliceStart + localT;
      const events = ensureTrack(trackEvents, currentTrackId);
      events.push(makeNoteOn(onTick, channel, pitch, vel));
      events.push(makeNoteOff(onTick + dur, channel, pitch));
    } else if (recordType === "P") {
      if (currentTrackId === undefined) {
        throw new Error(`Line ${lineIndex + 1}: P record appears before T record`);
      }
      requireFieldCount(fields, 3, lineIndex);
      const localT =
        parseNonNegativeInt(fields[1], "pedal t", lineIndex) * meta.tickScale;
      const val = parseMidiValue(fields[2], "pedal value", lineIndex);
      const channel = meta.trackChannels.get(currentTrackId) ?? 0;
      ensureTrack(trackEvents, currentTrackId).push({
        tick: currentSliceStart + localT,
        order: 1,
        event: {
          deltaTime: 0,
          type: "controller",
          channel,
          controllerType: 64,
          value: val,
        },
      });
    } else {
      throw new Error(`Line ${lineIndex + 1}: unknown record type "${recordType}"`);
    }
  }

  addMetaEvents(ensureTrack(trackEvents, 1), meta);
  const maxTrackId = Math.max(1, ...trackEvents.keys());
  const tracks: MidiEvent[][] = Array.from({ length: maxTrackId }, () => []);
  for (const [trackId, events] of trackEvents.entries()) {
    tracks[trackId - 1] = toDeltaEvents(events);
  }

  const midi: MidiData = {
    header: {
      format: tracks.length > 1 ? 1 : 0,
      numTracks: tracks.length,
      ticksPerBeat: meta.tpq,
    },
    tracks,
  };
  return Uint8Array.from(writeMidi(midi, { useByte9ForNoteOff: false }));
}

function quantizePedalEvents(pedals: PedalRecord[], epsilon: number): PedalRecord[] {
  const lastByType = new Map<PedalType, PedalRecord>();
  const result: PedalRecord[] = [];
  for (const pedal of pedals) {
    const previous = lastByType.get(pedal.type);
    if (previous && Math.abs(pedal.val - previous.val) <= epsilon) {
      continue;
    }
    result.push(pedal);
    lastByType.set(pedal.type, pedal);
  }
  return result;
}

function findSliceLocalStart(
  notes: NoteRecord[],
  pedals: PedalRecord[],
  markers: MarkerRecord[],
  slice: SliceRecord,
  slices: SliceRecord[]
): number {
  let first = Infinity;
  const isLast = slice.id === slices.length;
  for (const item of [...notes, ...pedals, ...markers]) {
    if (item.t >= slice.start && (item.t < slice.end || isLast)) {
      first = Math.min(first, item.t);
    }
  }
  return first === Infinity ? slice.start : first;
}

function selectTickScale(tpq: number, tempos: TsvMeta["tempos"]): number {
  const microsecondsPerBeat =
    tempos.find((t) => t.tick === 0)?.microsecondsPerBeat ??
    tempos[0]?.microsecondsPerBeat ??
    DEFAULT_MICROSECONDS_PER_BEAT;
  const msPerTick = microsecondsPerBeat / tpq / 1000;
  const rawScale = TARGET_TICK_SCALE_MS / msPerTick;
  const rounded = Math.round(rawScale / 5) * 5;
  return clamp(rounded, MIN_TICK_SCALE, MAX_TICK_SCALE);
}

function createSlices(
  notes: NoteRecord[],
  pedals: PedalRecord[],
  endTick: number,
  tpq: number,
  tempos: TsvMeta["tempos"],
  tickScale: number
): SliceRecord[] {
  if (endTick <= 0) {
    return [{ id: 1, start: 0, end: 0 }];
  }

  const microsecondsPerBeat =
    tempos.find((tempo) => tempo.tick === 0)?.microsecondsPerBeat ??
    tempos[0]?.microsecondsPerBeat ??
    DEFAULT_MICROSECONDS_PER_BEAT;
  const msPerTick = microsecondsPerBeat / tpq / 1000;
  const macroSeconds = tickScale * msPerTick / 1000;
  const endMacro = Math.ceil(endTick / tickScale);
  const minMacro = Math.max(1, Math.round(MIN_SLICE_SECONDS / macroSeconds));
  const targetMacro = Math.max(minMacro, Math.round(TARGET_SLICE_SECONDS / macroSeconds));
  const maxMacro = Math.max(targetMacro, Math.round(MAX_SLICE_SECONDS / macroSeconds));
  const minGapMacro = Math.max(1, Math.round(MIN_GAP_SECONDS / macroSeconds));
  const cutCandidates = findWeakCutCandidatesMacro(notes, pedals, tickScale, minGapMacro);
  const slices: SliceRecord[] = [];
  let startMacro = 0;

  while (endMacro - startMacro > maxMacro) {
    const minCut = startMacro + minMacro;
    const maxCut = Math.min(startMacro + maxMacro, endMacro);
    const targetCut = Math.min(startMacro + targetMacro, maxCut);
    const candidates = cutCandidates.filter((cut) => cut > minCut && cut < maxCut);
    const cut =
      candidates.sort((a, b) => Math.abs(a - targetCut) - Math.abs(b - targetCut))[0] ??
      targetCut;
    slices.push({ id: slices.length + 1, start: startMacro * tickScale, end: cut * tickScale });
    startMacro = cut;
  }

  slices.push({ id: slices.length + 1, start: startMacro * tickScale, end: endTick });
  return slices;
}

function findWeakCutCandidatesMacro(
  notes: NoteRecord[],
  pedals: PedalRecord[],
  tickScale: number,
  minGapMacro: number
): number[] {
  const toMacro = (t: number) => Math.round(t / tickScale);
  const intervals = notes
    .map((note) => ({ start: toMacro(note.t), end: toMacro(note.t + note.dur) }))
    .filter((interval) => interval.end >= interval.start);

  let pedalDownTick: number | undefined;
  for (const pedal of [...pedals].sort((a, b) => a.t - b.t)) {
    if (pedal.type !== "P") {
      continue;
    }
    const mt = toMacro(pedal.t);
    if (pedal.val >= 64 && pedalDownTick === undefined) {
      pedalDownTick = mt;
    } else if (pedal.val < 64 && pedalDownTick !== undefined) {
      intervals.push({ start: pedalDownTick, end: mt });
      pedalDownTick = undefined;
    }
  }

  const merged = intervals
    .sort((a, b) => a.start - b.start || a.end - b.end)
    .reduce<Array<{ start: number; end: number }>>((acc, interval) => {
      const previous = acc[acc.length - 1];
      if (!previous || interval.start > previous.end) {
        acc.push({ ...interval });
      } else {
        previous.end = Math.max(previous.end, interval.end);
      }
      return acc;
    }, []);

  const cuts: number[] = [];
  for (let i = 1; i < merged.length; i++) {
    const previousEnd = merged[i - 1].end;
    const nextStart = merged[i].start;
    if (nextStart - previousEnd >= minGapMacro) {
      cuts.push(Math.round((previousEnd + nextStart) / 2));
    }
  }

  return cuts.sort((a, b) => a - b).filter((cut, index, all) => index === 0 || cut !== all[index - 1]);
}

function midiPitchToAbcSmart(pitch: number, key: string): string {
  const pitchClass = pitch % 12;
  const octave = Math.floor(pitch / 12) - 5;
  const spelled =
    KEY_ACCIDENTALS[key]?.[pitchClass] ??
    NATURAL_BY_CLASS[pitchClass] ??
    SHARP_BY_CLASS[pitchClass];
  const accidental = spelled.match(/^[_^=]+/)?.[0] ?? "";
  const letter = spelled.slice(accidental.length);

  if (octave > 0) {
    return `${accidental}${letter.toLowerCase()}${"'".repeat(octave - 1)}`;
  }
  if (octave < 0) {
    return `${accidental}${letter}${",".repeat(-octave)}`;
  }
  return `${accidental}${letter}`;
}

function detectKeyFromNotes(notes: NoteRecord[]): string {
  const pitchClasses = new Set(notes.map((note) => note.pitch % 12));
  let bestKey = "C";
  let bestScore = -1;
  for (const [key, scale] of Object.entries(MAJOR_SCALES)) {
    const scaleSet = new Set(scale);
    let score = 0;
    for (const pitchClass of pitchClasses) {
      if (scaleSet.has(pitchClass)) {
        score++;
      }
    }
    if (score > bestScore) {
      bestScore = score;
      bestKey = key;
    }
  }
  return bestKey;
}

function abcPitchToMidi(pitch: string): number {
  const match = pitch.match(/^([_^=]*)([A-Ga-g])([',]*)$/);
  if (!match) {
    throw new Error(`Invalid ABC pitch "${pitch}"`);
  }

  const [, accidentals, letterRaw, suffix] = match;
  const letter = letterRaw.toUpperCase();
  let midi = (letterRaw === letter ? 60 : 72) + NATURAL_PITCH_CLASS[letter];

  for (const char of accidentals) {
    if (char === "^") {
      midi += 1;
    } else if (char === "_") {
      midi -= 1;
    }
  }
  for (const char of suffix) {
    midi += char === "'" ? 12 : -12;
  }

  if (midi < 0 || midi > 127) {
    throw new Error(`ABC pitch "${pitch}" is outside MIDI range`);
  }
  return midi;
}

function parseTsvMeta(tsv: string): TsvMeta {
  const meta: TsvMeta = {
    tpq: 480,
    tickScale: 1,
    version: "v0.2",
    tempos: [],
    timeSignatures: [],
    keySignatures: [],
    trackChannels: new Map(),
  };

  for (const rawLine of tsv.split(/\r?\n/)) {
    const line = rawLine.trim();
    if (!line.startsWith("#")) {
      continue;
    }
    const versionMatch = line.match(/^#\s*midi-tsv\s+(v\d+\.\d+)/);
    if (versionMatch) {
      meta.version = versionMatch[1];
      continue;
    }
    const body = line.slice(1).trim();
    const separatorIndex = body.indexOf("=");
    if (separatorIndex === -1) {
      continue;
    }
    const key = body.slice(0, separatorIndex);
    const value = body.slice(separatorIndex + 1);

    if (key === "tpq") {
      meta.tpq = parsePositiveInt(value, "tpq", -1);
    } else if (key === "tick_scale") {
      meta.tickScale = parsePositiveInt(value, "tick_scale", -1);
    } else if (key === "channel") {
      meta.trackChannels.set(1, parseMidiValue(value, "channel", -1));
    } else if (key === "tempo") {
      const [tick, microsecondsPerBeat] = parseCsvInts(value, 2, "tempo");
      meta.tempos.push({ tick: tick * meta.tickScale, microsecondsPerBeat });
    } else if (key === "time_signature") {
      const [tick, numerator, denominator, metronome, thirtyseconds] =
        parseCsvInts(value, 5, "time_signature");
      meta.timeSignatures.push({
        tick: tick * meta.tickScale,
        numerator,
        denominator,
        metronome,
        thirtyseconds,
      });
    } else if (key === "key_signature") {
      const [tick, keyValue, scale] = parseCsvInts(value, 3, "key_signature");
      meta.keySignatures.push({ tick: tick * meta.tickScale, key: keyValue, scale });
    } else if (key === "track_channel") {
      for (const item of value.split(",")) {
        const match = item.match(/^T(\d+):(\d+)$/);
        if (match) {
          meta.trackChannels.set(Number(match[1]), Number(match[2]));
        }
      }
    }
  }

  return meta;
}

function addMetaEvents(events: TimedEvent[], meta: TsvMeta): void {
  for (const tempo of meta.tempos) {
    events.push({
      tick: tempo.tick,
      order: 0,
      event: {
        deltaTime: 0,
        meta: true,
        type: "setTempo",
        microsecondsPerBeat: tempo.microsecondsPerBeat,
      },
    });
  }
  for (const sig of meta.timeSignatures) {
    events.push({
      tick: sig.tick,
      order: 0,
      event: {
        deltaTime: 0,
        meta: true,
        type: "timeSignature",
        numerator: sig.numerator,
        denominator: sig.denominator,
        metronome: sig.metronome,
        thirtyseconds: sig.thirtyseconds,
      },
    });
  }
  for (const key of meta.keySignatures) {
    events.push({
      tick: key.tick,
      order: 0,
      event: {
        deltaTime: 0,
        meta: true,
        type: "keySignature",
        key: key.key,
        scale: key.scale,
      },
    });
  }
}

function makeNoteOn(tick: number, channel: number, pitch: number, velocity: number): TimedEvent {
  return {
    tick,
    order: 3,
    event: { deltaTime: 0, type: "noteOn", channel, noteNumber: pitch, velocity },
  };
}

function makeNoteOff(tick: number, channel: number, pitch: number): TimedEvent {
  return {
    tick,
    order: 2,
    event: { deltaTime: 0, type: "noteOff", channel, noteNumber: pitch, velocity: 0 },
  };
}

function ensureTrack(trackEvents: Map<number, TimedEvent[]>, trackId: number): TimedEvent[] {
  const existing = trackEvents.get(trackId);
  if (existing) {
    return existing;
  }
  const created: TimedEvent[] = [];
  trackEvents.set(trackId, created);
  return created;
}

function toDeltaEvents(events: TimedEvent[]): MidiEvent[] {
  let previousTick = 0;
  const sorted = events.sort((a, b) => a.tick - b.tick || a.order - b.order);
  const midiEvents = sorted.map(({ tick, event }) => {
    const deltaTime = tick - previousTick;
    previousTick = tick;
    return { ...event, deltaTime };
  });
  midiEvents.push({ deltaTime: 0, meta: true, type: "endOfTrack" });
  return midiEvents;
}

function sortTimed<T extends { t?: number; tick?: number }>(a: T, b: T): number {
  return (a.t ?? a.tick ?? 0) - (b.t ?? b.tick ?? 0);
}

function isSliceRecord(s: string): boolean {
  return /^S\d+$/.test(s);
}

function isPedalRecord(s: string): s is PedalType {
  return s === "P" || s === "P1" || s === "P2" || s === "P3";
}

function isAbcPitch(s: string): boolean {
  return /^[_^=]*[A-Ga-g]['|,]*$/.test(s);
}

function isNoteRecord(s: string): boolean {
  return /^[_^=]*[A-Ga-g]['|,]*\d+$/.test(s);
}

function parseNoteRecord(s: string, lineIndex: number): [string, number] {
  const match = s.match(/^([_^=]*[A-Ga-g]['|,]*)(\d+)$/);
  if (!match) {
    throw new Error(`Line ${lineIndex + 1}: invalid note record "${s}"`);
  }
  return [match[1], parseNonNegativeInt(match[2], "note dur", lineIndex)];
}

function parseMarkerText(value: string): string {
  try {
    const parsed = JSON.parse(value);
    return typeof parsed === "string" ? parsed : String(parsed);
  } catch {
    return value;
  }
}

function scaleTick(tick: number, tickScale: number): number {
  return Math.round(tick / tickScale);
}

function scaleDuration(duration: number, tickScale: number): number {
  return duration > 0 ? Math.max(1, scaleTick(duration, tickScale)) : 0;
}

function clamp(v: number, lo: number, hi: number): number {
  return Math.min(hi, Math.max(lo, v));
}

function requireFieldCount(fields: string[], expected: number, lineIndex: number): void {
  if (fields.length !== expected) {
    throw new Error(
      `Line ${lineIndex + 1}: expected ${expected} fields, got ${fields.length}`
    );
  }
}

function parsePositiveInt(value: string, label: string, lineIndex: number): number {
  const parsed = parseInteger(value, label, lineIndex);
  if (parsed <= 0) {
    throw new Error(formatParseError(label, lineIndex, "must be positive"));
  }
  return parsed;
}

function parseNonNegativeInt(value: string, label: string, lineIndex: number): number {
  const parsed = parseInteger(value, label, lineIndex);
  if (parsed < 0) {
    throw new Error(formatParseError(label, lineIndex, "must be non-negative"));
  }
  return parsed;
}

function parseMidiValue(value: string, label: string, lineIndex: number): number {
  const parsed = parseInteger(value, label, lineIndex);
  if (parsed < 0 || parsed > 127) {
    throw new Error(formatParseError(label, lineIndex, "must be 0-127"));
  }
  return parsed;
}

function parseInteger(value: string, label: string, lineIndex: number): number {
  if (!/^-?\d+$/.test(value)) {
    throw new Error(formatParseError(label, lineIndex, "must be an integer"));
  }
  return Number(value);
}

function parseCsvInts(value: string, expected: number, label: string): number[] {
  const fields = value.split(",");
  if (fields.length !== expected) {
    throw new Error(`# ${label}: expected ${expected} values`);
  }
  return fields.map((field) => parseInteger(field, label, -1));
}

function formatParseError(label: string, lineIndex: number, message: string): string {
  const prefix = lineIndex >= 0 ? `Line ${lineIndex + 1}: ` : "";
  return `${prefix}${label} ${message}`;
}
