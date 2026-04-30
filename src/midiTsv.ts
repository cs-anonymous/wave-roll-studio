import {
  type MidiData,
  type MidiEvent,
  type MidiKeySignatureEvent,
  type MidiSetTempoEvent,
  type MidiTimeSignatureEvent,
  parseMidi,
  writeMidi,
} from "midi-file";

interface NoteRecord {
  trackId: number;
  t: number;
  dur: number;
  pitch: number;
  vel: number;
  channel: number;
}

interface PedalRecord {
  trackId: number;
  t: number;
  val: number;
  channel: number;
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

const MIN_TICK_SCALE = 2;
const MAX_TICK_SCALE = 20;
const TARGET_TICK_SCALE_MS = 10; // target 1 macro tick ≈ 10ms at dominant tempo
const DEFAULT_MICROSECONDS_PER_BEAT = 500000;
const MIN_SLICE_SECONDS = 10;
const TARGET_SLICE_SECONDS = 15;
const MAX_SLICE_SECONDS = 20;
const MIN_GAP_SECONDS = 0.35;

const PITCH_CLASSES = [
  "C",
  "^C",
  "D",
  "^D",
  "E",
  "F",
  "^F",
  "G",
  "^G",
  "A",
  "^A",
  "B",
] as const;

const NATURAL_PITCH_CLASS: Record<string, number> = {
  C: 0,
  D: 2,
  E: 4,
  F: 5,
  G: 7,
  A: 9,
  B: 11,
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
  const tempos: TsvMeta["tempos"] = [];
  const timeSignatures: TsvMeta["timeSignatures"] = [];
  const keySignatures: TsvMeta["keySignatures"] = [];
  const trackChannels = new Map<number, number>();
  let endTick = 0;

  midi.tracks.forEach((track, trackIndex) => {
    const trackId = trackIndex + 1;
    let tick = 0;
    const openNotes = new Map<string, NoteRecord[]>();

    for (const event of track) {
      tick += event.deltaTime;
      endTick = Math.max(endTick, tick);

      if (event.type === "setTempo") {
        tempos.push({
          tick,
          microsecondsPerBeat: event.microsecondsPerBeat,
        });
      } else if (event.type === "timeSignature") {
        timeSignatures.push({
          tick,
          numerator: event.numerator,
          denominator: event.denominator,
          metronome: event.metronome,
          thirtyseconds: event.thirtyseconds,
        });
      } else if (event.type === "keySignature") {
        keySignatures.push({
          tick,
          key: event.key,
          scale: event.scale,
        });
      } else if (event.type === "noteOn") {
        rememberTrackChannel(trackChannels, trackId, event.channel);
        const key = `${event.channel}:${event.noteNumber}`;
        const queue = openNotes.get(key) ?? [];
        queue.push({
          trackId,
          t: tick,
          dur: 0,
          pitch: event.noteNumber,
          vel: event.velocity,
          channel: event.channel,
        });
        openNotes.set(key, queue);
      } else if (event.type === "noteOff") {
        rememberTrackChannel(trackChannels, trackId, event.channel);
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
      } else if (event.type === "controller" && event.controllerType === 64) {
        rememberTrackChannel(trackChannels, trackId, event.channel);
        pedals.push({
          trackId,
          t: tick,
          val: event.value,
          channel: event.channel,
        });
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

  notes.sort(sortMusicalRecords);
  pedals.sort(sortMusicalRecords);

  const trackIds = Array.from(
    new Set([...notes.map((note) => note.trackId), ...pedals.map((p) => p.trackId)])
  ).sort((a, b) => a - b);
  const tickScale = selectTickScale(tpq, tempos);
  const slices = createSlices(notes, pedals, endTick, tpq, tempos, tickScale);

  const lines = [
    "# midi-tsv v0.1",
    `# source=${source}`,
    "# unit=tick",
    `# tick_scale=${tickScale}`,
    `# tpq=${tpq}`,
    "# pitch=abc-absolute",
    `# voice_map=${trackIds.map((id) => `T${id}:V${id}`).join(",")}`,
  ];

  if (trackChannels.size > 0) {
    lines.push(
      `# track_channel=${Array.from(trackChannels.entries())
        .sort(([a], [b]) => a - b)
        .map(([trackId, channel]) => `T${trackId}:${channel}`)
        .join(",")}`
    );
  }

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
    const localStart = findSliceLocalStart(notes, pedals, slice, slices);

    lines.push(
      `S\t${slice.id}\t${scaleTick(localStart, tickScale)}\t${scaleTick(slice.end, tickScale)}`
    );

    for (const trackId of trackIds) {
      const records = [
        ...notes
          .filter(
            (item) =>
              item.trackId === trackId &&
              item.t >= localStart &&
              (item.t < slice.end || slice.id === slices.length)
          )
          .map((note) => ({
            t: note.t,
            order: 1,
            line: `${midiPitchToAbc(note.pitch)}\t${scaleTick(note.t - localStart, tickScale)}\t${scaleDuration(note.dur, tickScale)}\t${note.vel}`,
          })),
        ...pedals
          .filter(
            (item) =>
              item.trackId === trackId &&
              item.t >= localStart &&
              (item.t < slice.end || slice.id === slices.length)
          )
          .map((pedal) => ({
            t: pedal.t,
            order: 0,
            line: `P\t${scaleTick(pedal.t - localStart, tickScale)}\t${pedal.val}`,
          })),
      ].sort((a, b) => a.t - b.t || a.order - b.order);

      if (records.length === 0) {
        continue;
      }

      lines.push(`T\t${trackId}`);
      for (const record of records) {
        lines.push(record.line);
      }
      lines.push("");
    }
  }

  return `${lines.join("\n").trimEnd()}\n`;
}

export function tsvToMidi(tsv: string): Uint8Array {
  const meta = parseTsvMeta(tsv);
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
        parseNonNegativeInt(fields[2], "slice start", lineIndex) *
        meta.tickScale;
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
      const offTick = onTick + dur;
      const events = ensureTrack(trackEvents, currentTrackId);
      events.push({
        tick: onTick,
        order: 3,
        event: {
          deltaTime: 0,
          type: "noteOn",
          channel,
          noteNumber: pitch,
          velocity: vel,
        },
      });
      events.push({
        tick: offTick,
        order: 2,
        event: {
          deltaTime: 0,
          type: "noteOff",
          channel,
          noteNumber: pitch,
          velocity: 0,
        },
      });
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

  const maxTrackId = Math.max(1, ...trackEvents.keys());
  const tracks: MidiEvent[][] = Array.from({ length: maxTrackId }, () => []);

  for (const tempo of meta.tempos) {
    addTimedEvent(trackEvents, 1, {
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
    addTimedEvent(trackEvents, 1, {
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
    addTimedEvent(trackEvents, 1, {
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

function findSliceLocalStart(
  notes: NoteRecord[],
  pedals: PedalRecord[],
  slice: SliceRecord,
  slices: SliceRecord[]
): number {
  let first = Infinity;
  for (const item of notes) {
    if (item.t >= slice.start && (item.t < slice.end || slice.id === slices.length)) {
      first = Math.min(first, item.t);
    }
  }
  for (const item of pedals) {
    if (item.t >= slice.start && (item.t < slice.end || slice.id === slices.length)) {
      first = Math.min(first, item.t);
    }
  }
  return first === Infinity ? slice.start : first;
}

function isAbcPitch(s: string): boolean {
  return /^[_^=]*[A-Ga-g]['|,]*$/.test(s);
}

function rememberTrackChannel(
  trackChannels: Map<number, number>,
  trackId: number,
  channel: number
): void {
  if (!trackChannels.has(trackId)) {
    trackChannels.set(trackId, channel);
  }
}

function sortMusicalRecords<T extends { trackId: number; t: number }>(
  a: T,
  b: T
): number {
  return a.trackId - b.trackId || a.t - b.t;
}

function selectTickScale(tpq: number, tempos: TsvMeta["tempos"]): number {
  // Pick a tick_scale so that 1 macro-tick ≈ TARGET_TICK_SCALE_MS at the
  // dominant tempo. Fast music (more ticks/second) gets a higher scale.
  // Then round to nearest 5 for clean integer values.
  const microsecondsPerBeat =
    tempos.find((t) => t.tick === 0)?.microsecondsPerBeat ??
    tempos[0]?.microsecondsPerBeat ??
    DEFAULT_MICROSECONDS_PER_BEAT;
  const msPerTick = (microsecondsPerBeat / tpq) / 1000;
  const rawScale = TARGET_TICK_SCALE_MS / msPerTick;
  const rounded = Math.round(rawScale / 5) * 5;
  return clamp(rounded, MIN_TICK_SCALE, MAX_TICK_SCALE);
}

function clamp(v: number, lo: number, hi: number): number {
  return Math.min(hi, Math.max(lo, v));
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
  const msPerTick = (microsecondsPerBeat / tpq) / 1000;

  // Work in macro-ticks so each slice has a roughly constant duration in seconds
  const endMacro = Math.ceil(endTick / tickScale);
  const minMacro = Math.max(1, Math.round(MIN_SLICE_SECONDS / (tickScale * msPerTick / 1000)));
  const targetMacro = Math.max(minMacro, Math.round(TARGET_SLICE_SECONDS / (tickScale * msPerTick / 1000)));
  const maxMacro = Math.max(targetMacro, Math.round(MAX_SLICE_SECONDS / (tickScale * msPerTick / 1000)));
  const minGapMacro = Math.max(1, Math.round(MIN_GAP_SECONDS / (tickScale * msPerTick / 1000)));

  const cutCandidates = findWeakCutCandidatesMacro(notes, pedals, tickScale, minGapMacro);
  const slices: SliceRecord[] = [];
  let startMacro = 0;

  while (endMacro - startMacro > maxMacro) {
    const minCut = startMacro + minMacro;
    const maxCut = Math.min(startMacro + maxMacro, endMacro);
    const targetCut = Math.min(startMacro + targetMacro, maxCut);
    const candidates = cutCandidates.filter(
      (cut) => cut > minCut && cut < maxCut
    );
    const cut =
      candidates.sort(
        (a, b) => Math.abs(a - targetCut) - Math.abs(b - targetCut)
      )[0] ?? targetCut;

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
    .map((note) => ({
      start: toMacro(note.t),
      end: toMacro(note.t + note.dur),
    }))
    .filter((interval) => interval.end >= interval.start);

  const sortedPedals = [...pedals].sort((a, b) => a.t - b.t);
  let pedalDownTick: number | undefined;
  for (const pedal of sortedPedals) {
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

  const eventTicks = [...notes.map((note) => toMacro(note.t)), ...pedals.map((pedal) => toMacro(pedal.t))]
    .sort((a, b) => a - b)
    .filter((tick, index, all) => index === 0 || tick !== all[index - 1]);
  for (let i = 1; i < eventTicks.length; i++) {
    const previousTick = eventTicks[i - 1];
    const nextTick = eventTicks[i];
    if (nextTick - previousTick >= minGapMacro) {
      cuts.push(Math.round((previousTick + nextTick) / 2));
    }
  }

  return cuts.sort((a, b) => a - b).filter((cut, index, all) => index === 0 || cut !== all[index - 1]);
}

function scaleTick(tick: number, tickScale: number): number {
  return Math.round(tick / tickScale);
}

function scaleDuration(duration: number, tickScale: number): number {
  return duration > 0 ? Math.max(1, scaleTick(duration, tickScale)) : 0;
}

function midiPitchToAbc(pitch: number): string {
  const pitchClass = pitch % 12;
  const octave = Math.floor(pitch / 12) - 5;
  const spelled = PITCH_CLASSES[pitchClass];
  const accidental = spelled.startsWith("^") ? "^" : "";
  const letter = accidental ? spelled.slice(1) : spelled;

  if (octave > 0) {
    return `${accidental}${letter.toLowerCase()}${"'".repeat(octave - 1)}`;
  }
  if (octave < 0) {
    return `${accidental}${letter}${",".repeat(-octave)}`;
  }
  return `${accidental}${letter}`;
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
      meta.keySignatures.push({
        tick: tick * meta.tickScale,
        key: keyValue,
        scale,
      });
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

function ensureTrack(
  trackEvents: Map<number, TimedEvent[]>,
  trackId: number
): TimedEvent[] {
  const existing = trackEvents.get(trackId);
  if (existing) {
    return existing;
  }
  const created: TimedEvent[] = [];
  trackEvents.set(trackId, created);
  return created;
}

function addTimedEvent(
  trackEvents: Map<number, TimedEvent[]>,
  trackId: number,
  event: TimedEvent
): void {
  ensureTrack(trackEvents, trackId).push(event);
}

function toDeltaEvents(events: TimedEvent[]): MidiEvent[] {
  let previousTick = 0;
  const sorted = events.sort((a, b) => a.tick - b.tick || a.order - b.order);
  const midiEvents = sorted.map(({ tick, event }) => {
    const deltaTime = tick - previousTick;
    previousTick = tick;
    return { ...event, deltaTime };
  });

  midiEvents.push({
    deltaTime: 0,
    meta: true,
    type: "endOfTrack",
  });

  return midiEvents;
}

function requireFieldCount(
  fields: string[],
  expected: number,
  lineIndex: number
): void {
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

function parseNonNegativeInt(
  value: string,
  label: string,
  lineIndex: number
): number {
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

function formatParseError(
  label: string,
  lineIndex: number,
  message: string
): string {
  const prefix = lineIndex >= 0 ? `Line ${lineIndex + 1}: ` : "";
  return `${prefix}${label} ${message}`;
}
