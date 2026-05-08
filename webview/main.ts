import { createWaveRollPlayer } from "wave-roll";
import type { AppearanceSettings, MidiExportOptions } from "wave-roll";

// Extended player interface with VS Code integration APIs
interface WaveRollPlayerExtended {
  dispose(): void;
  pianoRollManager?: {
    getPianoRollInstance(): any | null;
  } | null;
  seek?(time: number): void | Promise<void>;
  getState?(): { duration?: number; tempo?: number; currentTime?: number; isPlaying?: boolean };
  applyAppearanceSettings(settings: AppearanceSettings): void;
  onAppearanceChange(
    callback: (settings: AppearanceSettings) => void
  ): () => void;
  onFileAddRequest(callback: () => void): () => void;
  onAudioFileAddRequest(callback: () => void): () => void;
  addFileFromData(data: ArrayBuffer | string, filename: string): Promise<void>;
}

// Declare VS Code API type
declare function acquireVsCodeApi(): {
  postMessage(message: unknown): void;
  getState(): unknown;
  setState(state: unknown): void;
};

// Initialize VS Code API
const vscode = acquireVsCodeApi();

// UI Elements
let loadingContainer: HTMLElement | null;
let errorContainer: HTMLElement | null;
let studioContainer: HTMLElement | null;
let waveRollContainer: HTMLElement | null;
let errorMessage: HTMLElement | null;
let tsvRowsContainer: HTMLElement | null;
let tsvMeta: HTMLElement | null;
let tsvToggle: HTMLButtonElement | null;
let pianoSoundSelect: HTMLSelectElement | null;

// State
let playerInstance: WaveRollPlayerExtended | null = null;
let currentBlobUrl: string | null = null;
let currentMidiBytes: Uint8Array | null = null;
let currentFilename: string | null = null;
let currentTsv: string | null = null;
let appearanceChangeUnsubscribe: (() => void) | null = null;
let pendingSettingsRequest: boolean = false;
let trackRowAdjustObserver: MutationObserver | null = null;
let currentTsvIndex: TsvIndex | null = null;
let activeTsvLine: number | null = null;
let isPlaying = false;
let playbackLoopTimer: number | null = null;
let currentPianoSound: "default" | "salamander" = "default";

// TSV editing mode
let tsvEditMode: "preview" | "edit" = "preview";
let tsvEditor: HTMLTextAreaElement | null = null;

interface TsvRow {
  lineNumber: number;
  raw: string;
  type: "S" | "H" | "T" | "N" | "P" | "M" | "meta" | "blank" | "other";
  absTick?: number;
  durTick?: number;
  trackId?: number;
  pitch?: string;
}

interface TempoPoint {
  tick: number;
  seconds: number;
  microsecondsPerBeat: number;
}

interface TsvIndex {
  rows: TsvRow[];
  notes: TsvRow[];
  tickScale: number;
  tpq: number;
  tempos: TempoPoint[];
  endTick: number;
}

/**
 * Decodes a Base64 string to Uint8Array.
 */
function decodeBase64ToUint8Array(base64: string): Uint8Array {
  const binaryString = atob(base64);
  const bytes = new Uint8Array(binaryString.length);
  for (let i = 0; i < binaryString.length; i++) {
    bytes[i] = binaryString.charCodeAt(i);
  }
  return bytes;
}

/**
 * Creates a Blob URL from MIDI bytes.
 * Remember to call revokeBlobUrl() when done.
 */
function createMidiBlobUrl(midiBytes: Uint8Array): string {
  // Create a new ArrayBuffer copy to ensure compatibility with Blob constructor
  const buffer = new ArrayBuffer(midiBytes.length);
  new Uint8Array(buffer).set(midiBytes);
  const blob = new Blob([buffer], { type: "audio/midi" });
  return URL.createObjectURL(blob);
}

/**
 * Revokes the current Blob URL to free memory.
 */
function revokeBlobUrl(): void {
  if (currentBlobUrl) {
    URL.revokeObjectURL(currentBlobUrl);
    currentBlobUrl = null;
  }
}

/**
 * Applies slight spacing/size tweaks to track rows to better align
 * instrument/eye/volume/notes controls without touching wave-roll code.
 */
function applyTrackRowAdjustments(root: HTMLElement): void {
  const adjust = () => {
    const rows = Array.from(root.querySelectorAll<HTMLElement>("div")).filter(
      (row) => {
        const hasEye = row.querySelector(
          'button[title="Toggle track visibility"]'
        );
        return (
          row.style.display === "flex" &&
          row.style.alignItems === "center" &&
          row.style.gap === "8px" &&
          row.style.padding === "2px 0px" &&
          !!hasEye
        );
      }
    );

    rows.forEach((row) => {
      row.style.gap = "8px";

      const eyeBtn = row.querySelector<HTMLButtonElement>(
        'button[title="Toggle track visibility"]'
      );
      if (eyeBtn) {
        eyeBtn.style.marginLeft = "11px";
        eyeBtn.style.marginRight = "3px";
      }

      const autoBtn = row.querySelector<HTMLButtonElement>(
        'button[title^="Using"]'
      );
      if (autoBtn) {
        autoBtn.style.marginRight = "8px";
      }

      const noteBadge = Array.from(
        row.querySelectorAll<HTMLSpanElement>("span")
      ).find((span) => (span.textContent ?? "").trim().endsWith("notes"));
      if (noteBadge) {
        noteBadge.style.minWidth = "101px";
        noteBadge.style.marginRight = "10px";
      }
    });
  };

  adjust();

  if (trackRowAdjustObserver) {
    trackRowAdjustObserver.disconnect();
  }
  trackRowAdjustObserver = new MutationObserver(() => adjust());
  trackRowAdjustObserver.observe(root, { childList: true, subtree: true });
}

function teardownTrackRowAdjustments(): void {
  if (trackRowAdjustObserver) {
    trackRowAdjustObserver.disconnect();
    trackRowAdjustObserver = null;
  }
}

/**
 * Updates the UI state (loading, error, ready).
 */
function setStatus(
  status: "loading" | "error" | "ready",
  message?: string
): void {
  if (!loadingContainer || !errorContainer || !studioContainer) {
    return;
  }

  loadingContainer.classList.toggle("hidden", status !== "loading");
  errorContainer.classList.toggle("hidden", status !== "error");
  studioContainer.classList.toggle("hidden", status !== "ready");

  if (status === "error" && errorMessage && message) {
    errorMessage.textContent = message;
  }
}

/**
 * Initializes the WaveRoll player with MIDI data.
 */
async function initializeWaveRollPlayer(
  midiBytes: Uint8Array,
  filename: string
): Promise<void> {
  if (!waveRollContainer) {
    throw new Error("WaveRoll container not found");
  }

  // Cleanup previous instance and blob URL
  if (playerInstance) {
    playerInstance.dispose();
    playerInstance = null;
  }
  revokeBlobUrl();

  // Create Blob URL from MIDI bytes
  currentBlobUrl = createMidiBlobUrl(midiBytes);

  // Set piano sound preference before initializing the player
  // The bundled code reads this global to determine which sample URL to use
  (window as Window & { __waveRollPianoSound?: string }).__waveRollPianoSound = currentPianoSound;
  console.log("[WaveRoll] Piano sound set to:", currentPianoSound);

  // Create the WaveRoll player with the Blob URL
  // Multi-file mode enabled (soloMode: false) for file comparison features
  try {
    const playerOptions = {
      // Disable solo mode to enable multi-file comparison features
      soloMode: false,
      // Default highlight to file colors for clearer baseline view
      defaultHighlightMode: "file",
      // Use WebGL for better compatibility in VS Code webview environment
      // Keep light background and hide waveform band (like solo mode styling)
      pianoRoll: {
        rendererPreference: "webgl",
        showWaveformBand: false,
        backgroundColor: 0xffffff,
      },
      // Use custom export handler to save MIDI to original file location
      midiExport: createMidiExportOptions(),
      // Disable drag & drop in VS Code webview; click-to-open only
      allowFileDrop: false,
    } as Parameters<typeof createWaveRollPlayer>[2] & {
      allowFileDrop?: boolean;
    };

    playerInstance = (await createWaveRollPlayer(
      waveRollContainer,
      [
        {
          path: currentBlobUrl,
          name: filename,
        },
      ],
      playerOptions
    )) as unknown as WaveRollPlayerExtended;

    // Align track-row controls (eye/volume/notes) without modifying wave-roll lib code
    applyTrackRowAdjustments(waveRollContainer);

    // Setup file add request callback to use VS Code file dialog
    setupFileAddRequestListener();

    // Request saved appearance settings from extension
    requestSavedSettings();

    // Setup listener to save appearance changes
    setupAppearanceChangeListener();

    // Start playback loop for TSV auto-scroll
    startPlaybackLoop();
  } catch (playerError) {
    console.error("[WaveRoll] createWaveRollPlayer() failed:", playerError);
    throw playerError;
  }
}

/**
 * Switches the piano sound and restarts the player.
 */
async function switchPianoSound(sound: "default" | "salamander"): Promise<void> {
  if (sound === currentPianoSound) return;
  currentPianoSound = sound;

  // Update selector UI if it exists
  if (pianoSoundSelect) {
    pianoSoundSelect.value = sound;
  }

  // Save setting to extension
  vscode.postMessage({
    type: "save-settings",
    settings: {
      paletteId: "default",
      pianoSound: sound,
    },
  });

  // Restart player with new sound if we have MIDI data
  if (currentMidiBytes && currentFilename) {
    await initializeWaveRollPlayer(currentMidiBytes, currentFilename);
  }
}

/**
 * Creates the piano sound selector and injects it into the TSV panel header.
 */
function createPianoSoundSelector(): void {
  const tsvHeader = document.getElementById("tsv-panel-header");
  if (!tsvHeader || pianoSoundSelect) return;

  const wrapper = document.createElement("div");
  wrapper.style.display = "flex";
  wrapper.style.alignItems = "center";
  wrapper.style.gap = "4px";
  wrapper.style.marginRight = "auto";

  pianoSoundSelect = document.createElement("select");
  pianoSoundSelect.id = "piano-sound-selector";
  pianoSoundSelect.title = "Choose piano sound source";
  pianoSoundSelect.style.cssText = `
    font-size: 10px;
    padding: 2px 4px;
    border: 1px solid var(--ui-border);
    border-radius: 4px;
    background: var(--surface);
    color: var(--text-primary);
    cursor: pointer;
    outline: none;
    height: 20px;
    max-width: 100px;
    min-width: 60px;
  `;
  pianoSoundSelect.addEventListener("mouseenter", () => {
    pianoSoundSelect!.style.borderColor = "var(--accent)";
  });
  pianoSoundSelect.addEventListener("mouseleave", () => {
    pianoSoundSelect!.style.borderColor = "var(--ui-border)";
  });

  const defaultOption = document.createElement("option");
  defaultOption.value = "default";
  defaultOption.textContent = "Default";
  const salamanderOption = document.createElement("option");
  salamanderOption.value = "salamander";
  salamanderOption.textContent = "Salamander C5";

  pianoSoundSelect.appendChild(defaultOption);
  pianoSoundSelect.appendChild(salamanderOption);
  pianoSoundSelect.value = currentPianoSound;

  pianoSoundSelect.addEventListener("change", () => {
    const sound = pianoSoundSelect!.value as "default" | "salamander";
    void switchPianoSound(sound);
  });

  wrapper.appendChild(pianoSoundSelect);
  tsvHeader.appendChild(wrapper);

  // Create edit toggle button
  const editToggle = document.createElement("button");
  editToggle.id = "tsv-edit-toggle";
  editToggle.type = "button";
  editToggle.textContent = "✎";
  editToggle.title = "Edit MIDI-TSV";
  editToggle.setAttribute("aria-label", "Edit MIDI-TSV");
  editToggle.style.cssText = `
    flex: 0 0 22px;
    width: 22px;
    height: 22px;
    display: inline-flex;
    align-items: center;
    justify-content: center;
    border: 1px solid var(--ui-border);
    border-radius: 4px;
    background: var(--surface);
    color: var(--text-primary);
    cursor: pointer;
    font-size: 13px;
  `;
  editToggle.addEventListener("mouseenter", () => {
    editToggle.style.background = "var(--hover-surface)";
  });
  editToggle.addEventListener("mouseleave", () => {
    editToggle.style.background = "var(--surface)";
  });
  editToggle.addEventListener("click", toggleTsvEditMode);
  tsvHeader.appendChild(editToggle);
}

/**
 * Handles messages from the extension host.
 */
function handleMessage(event: MessageEvent): void {
  const message = event.data;

  switch (message.type) {
    case "midi-data":
      handleMidiData(message.data, message.filename, message.tsv);
      break;

    case "settings-loaded":
      // Apply saved appearance settings if available
      if (message.settings && playerInstance) {
        playerInstance.applyAppearanceSettings(message.settings);
      }
      // Restore saved piano sound preference
      if (message.settings?.pianoSound) {
        const sound = message.settings.pianoSound as "default" | "salamander";
        if (sound !== currentPianoSound) {
          currentPianoSound = sound;
          if (pianoSoundSelect) {
            pianoSoundSelect.value = sound;
          }
          // Restart with saved sound if we have MIDI data
          if (currentMidiBytes && currentFilename) {
            void initializeWaveRollPlayer(currentMidiBytes, currentFilename);
          }
        }
      }
      pendingSettingsRequest = false;
      break;

    case "file-added":
      // Handle file added via VS Code file dialog
      handleFileAdded(message.data, message.filename);
      break;

    case "tsv-saved":
      // TSV edit saved successfully
      handleTsvSaved(message.tsv);
      break;
  }
}

/**
 * Handles a file added via VS Code file dialog.
 * Uses the player's addFileFromData API to add the file.
 */
async function handleFileAdded(
  base64Data: string,
  filename: string
): Promise<void> {
  if (!playerInstance) {
    console.error("[WaveRoll] Cannot add file: player not initialized");
    return;
  }

  try {
    await playerInstance.addFileFromData(base64Data, filename);
  } catch (error) {
    console.error("[WaveRoll] Error adding file:", error);
    const errorMsg =
      error instanceof Error ? error.message : "Failed to add file";
    vscode.postMessage({
      type: "error",
      message: errorMsg,
    });
  }
}

/**
 * Toggles TSV panel between preview and edit mode.
 */
function toggleTsvEditMode(): void {
  if (tsvEditMode === "preview") {
    tsvEditMode = "edit";
    renderTsvEditor();
  } else {
    tsvEditMode = "preview";
    tsvRowsContainer?.style.removeProperty("display");
    if (currentTsv) {
      renderTsvPanel(currentTsv);
    }
  }
  // Update toggle button text
  const editToggle = document.getElementById("tsv-edit-toggle");
  if (editToggle) {
    editToggle.textContent = tsvEditMode === "preview" ? "✎" : "⇄";
    editToggle.title = tsvEditMode === "preview" ? "Edit MIDI-TSV" : "Preview MIDI-TSV";
  }
}

/**
 * Renders TSV as an editable textarea with line numbers.
 */
function renderTsvEditor(): void {
  if (!tsvRowsContainer || !currentTsv) return;

  tsvRowsContainer.textContent = "";
  tsvRowsContainer.style.display = "flex";

  const lineNumbers = document.createElement("div");
  lineNumbers.id = "tsv-editor-gutter";

  const lineCount = currentTsv.split(/\r?\n/).length;
  for (let i = 1; i <= lineCount; i++) {
    const span = document.createElement("span");
    span.className = "tsv-editor-line-num";
    span.textContent = String(i);
    lineNumbers.appendChild(span);
  }

  tsvEditor = document.createElement("textarea");
  tsvEditor.id = "tsv-editor";
  tsvEditor.value = currentTsv;
  tsvEditor.spellcheck = false;
  tsvEditor.autocapitalize = "off";
  tsvEditor.autocomplete = "off";

  tsvRowsContainer.appendChild(lineNumbers);
  tsvRowsContainer.appendChild(tsvEditor);
  tsvEditor.focus();

  // Sync gutter scroll with textarea
  tsvEditor.addEventListener("scroll", () => {
    lineNumbers.scrollTop = tsvEditor!.scrollTop;
  });
}

/**
 * Saves edited TSV by converting back to MIDI and writing to disk.
 */
function saveTsvEdit(): void {
  if (!tsvEditor) return;

  const editedTsv = tsvEditor.value;
  vscode.postMessage({
    type: "tsv-edit",
    tsv: editedTsv,
  });
}

/**
 * Handles TSV save confirmation from extension.
 */
function handleTsvSaved(newTsv: string): void {
  currentTsv = newTsv;
  // Switch back to preview mode
  tsvEditMode = "preview";
  tsvRowsContainer?.style.removeProperty("display");
  renderTsvPanel(newTsv);
  const editToggle = document.getElementById("tsv-edit-toggle");
  if (editToggle) {
    editToggle.textContent = "✎";
    editToggle.title = "Edit MIDI-TSV";
  }
}

/**
 * Request saved appearance settings from extension.
 */
function requestSavedSettings(): void {
  pendingSettingsRequest = true;
  vscode.postMessage({ type: "get-settings" });
}

/**
 * Save appearance settings to extension.
 */
function saveAppearanceSettings(settings: AppearanceSettings): void {
  vscode.postMessage({
    type: "save-settings",
    settings,
  });
}

/**
 * Subscribe to appearance changes and save them.
 */
function setupAppearanceChangeListener(): void {
  if (!playerInstance) return;

  // Unsubscribe previous listener if exists
  if (appearanceChangeUnsubscribe) {
    appearanceChangeUnsubscribe();
    appearanceChangeUnsubscribe = null;
  }

  // Subscribe to appearance changes
  appearanceChangeUnsubscribe = playerInstance.onAppearanceChange(
    (settings) => {
      // Don't save if we're still loading initial settings
      if (pendingSettingsRequest) return;

      saveAppearanceSettings(settings);
    }
  );
}

function renderTsvPanel(tsv: string): void {
  if (!tsvRowsContainer) {
    return;
  }

  currentTsvIndex = parseTsvIndex(tsv);
  activeTsvLine = null;
  tsvRowsContainer.textContent = "";

  if (tsvMeta) {
    tsvMeta.textContent = `${currentTsvIndex.notes.length} notes · ${currentTsvIndex.rows.length} lines`;
  }

  for (const row of currentTsvIndex.rows) {
    const rowElement = document.createElement("div");
    rowElement.className = `tsv-row tsv-row-${row.type}`;
    rowElement.dataset.line = String(row.lineNumber);
    rowElement.dataset.type = row.type;
    rowElement.innerHTML = `<span class="tsv-line-number">${row.lineNumber}</span><span class="tsv-line-text"></span>`;
    const textElement = rowElement.querySelector<HTMLElement>(".tsv-line-text");
    if (textElement) {
      textElement.textContent = row.raw || " ";
    }

    if (row.absTick !== undefined) {
      rowElement.addEventListener("click", () => {
        highlightTsvLine(row.lineNumber, false);
        if (!isPlaying) {
          seekPlayerToTick(row.absTick ?? 0);
        }
      });
    }

    tsvRowsContainer.appendChild(rowElement);
  }
}

function parseTsvIndex(tsv: string): TsvIndex {
  const rawLines = tsv.split(/\r?\n/);
  const rows: TsvRow[] = [];
  let tickScale = 1;
  let tpq = 480;
  let currentSliceStart = 0;
  let currentTrackId: number | undefined;
  const tempoEvents: Array<{ tick: number; microsecondsPerBeat: number }> = [];
  let endTick = 0;

  rawLines.forEach((raw, index) => {
    const lineNumber = index + 1;
    const trimmed = raw.trim();

    if (!trimmed) {
      rows.push({ lineNumber, raw, type: "blank" });
      return;
    }

    if (trimmed.startsWith("#")) {
      const body = trimmed.slice(1).trim();
      const separatorIndex = body.indexOf("=");
      if (separatorIndex !== -1) {
        const key = body.slice(0, separatorIndex);
        const value = body.slice(separatorIndex + 1);
        if (key === "tick_scale") {
          tickScale = parsePositiveNumber(value, tickScale);
        } else if (key === "tpq") {
          tpq = parsePositiveNumber(value, tpq);
        } else if (key === "tempo") {
          const [tick, microsecondsPerBeat] = value
            .split(",")
            .map((part) => Number(part));
          if (Number.isFinite(tick) && Number.isFinite(microsecondsPerBeat)) {
            tempoEvents.push({
              tick: tick * tickScale,
              microsecondsPerBeat,
            });
          }
        }
      }
      rows.push({ lineNumber, raw, type: "meta" });
      return;
    }

    const fields = raw.split("\t");
    // Support both S (segment) and M (measure) slice types
    if (/^[SM]\d+$/.test(fields[0]) && fields.length >= 3) {
      currentSliceStart = Number(fields[1]) * tickScale;
      endTick = Math.max(endTick, Number(fields[2]) * tickScale);
      rows.push({
        lineNumber,
        raw,
        type: "S",
        absTick: currentSliceStart,
      });
      return;
    }

    if (/^H\d+$/.test(fields[0]) && fields.length >= 3) {
      const phraseStart = Number(fields[1]) * tickScale;
      endTick = Math.max(endTick, Number(fields[2]) * tickScale);
      rows.push({
        lineNumber,
        raw,
        type: "H",
        absTick: phraseStart,
      });
      return;
    }

    if ((fields[0] === "S" || fields[0] === "M") && fields.length >= 4) {
      currentSliceStart = Number(fields[2]) * tickScale;
      endTick = Math.max(endTick, Number(fields[3]) * tickScale);
      rows.push({
        lineNumber,
        raw,
        type: "S",
        absTick: currentSliceStart,
      });
      return;
    }

    if (fields[0] === "T" && fields.length >= 2) {
      currentTrackId = Number(fields[1]);
      rows.push({ lineNumber, raw, type: "T", trackId: currentTrackId });
      return;
    }

    const v2Note = parseMidiTsvV2Note(fields[0]);
    if (v2Note && fields.length >= 3) {
      const absTick = currentSliceStart + Number(fields[1]) * tickScale;
      const durTick = v2Note.dur * tickScale;
      endTick = Math.max(endTick, absTick + durTick);
      rows.push({
        lineNumber,
        raw,
        type: "N",
        absTick,
        durTick,
        trackId: currentTrackId,
        pitch: v2Note.pitch,
      });
      return;
    }

    if (isAbcPitch(fields[0]) && fields.length >= 4) {
      const absTick = currentSliceStart + Number(fields[1]) * tickScale;
      const durTick = Number(fields[2]) * tickScale;
      endTick = Math.max(endTick, absTick + durTick);
      rows.push({
        lineNumber,
        raw,
        type: "N",
        absTick,
        durTick,
        trackId: currentTrackId,
        pitch: fields[0],
      });
      return;
    }

    if (/^P[123]?$/.test(fields[0]) && fields.length >= 3) {
      const absTick = currentSliceStart + Number(fields[1]) * tickScale;
      rows.push({
        lineNumber,
        raw,
        type: "P",
        absTick,
        trackId: currentTrackId,
      });
      return;
    }

    if (fields[0] === "M" && fields.length >= 3) {
      const absTick = currentSliceStart + Number(fields[1]) * tickScale;
      rows.push({
        lineNumber,
        raw,
        type: "M",
        absTick,
        trackId: currentTrackId,
      });
      return;
    }

    rows.push({ lineNumber, raw, type: "other" });
  });

  const tempos = buildTempoMap(tempoEvents, tpq);
  return {
    rows,
    notes: rows.filter((row) => row.type === "N" && row.absTick !== undefined),
    tickScale,
    tpq,
    tempos,
    endTick,
  };
}

function buildTempoMap(
  tempoEvents: Array<{ tick: number; microsecondsPerBeat: number }>,
  tpq: number
): TempoPoint[] {
  const sorted = [...tempoEvents]
    .sort((a, b) => a.tick - b.tick)
    .filter((event, index, all) => index === 0 || event.tick !== all[index - 1].tick);

  if (sorted.length === 0 || sorted[0].tick !== 0) {
    sorted.unshift({ tick: 0, microsecondsPerBeat: 500000 });
  }

  let seconds = 0;
  let previous = sorted[0];
  const points: TempoPoint[] = [
    {
      tick: previous.tick,
      seconds: 0,
      microsecondsPerBeat: previous.microsecondsPerBeat,
    },
  ];

  for (let i = 1; i < sorted.length; i++) {
    const event = sorted[i];
    seconds += ticksToSeconds(
      event.tick - previous.tick,
      previous.microsecondsPerBeat,
      tpq
    );
    points.push({
      tick: event.tick,
      seconds,
      microsecondsPerBeat: event.microsecondsPerBeat,
    });
    previous = event;
  }

  return points;
}

function parsePositiveNumber(value: string, fallback: number): number {
  const parsed = Number(value);
  return Number.isFinite(parsed) && parsed > 0 ? parsed : fallback;
}

function parseMidiTsvV2Note(value: string): { pitch: string; dur: number } | null {
  const match = value.match(/^([_^=]*[A-Ga-g]['|,]*):?(\d+)$/);
  if (!match) {
    return null;
  }
  return { pitch: match[1], dur: Number(match[2]) };
}

function tickToSeconds(tick: number): number {
  if (!currentTsvIndex) {
    return 0;
  }

  const tempo = findTempoPoint(tick);
  return (
    tempo.seconds +
    ticksToSeconds(tick - tempo.tick, tempo.microsecondsPerBeat, currentTsvIndex.tpq)
  );
}

function findTempoPoint(tick: number): TempoPoint {
  const tempos = currentTsvIndex?.tempos;
  if (!tempos || tempos.length === 0) {
    return { tick: 0, seconds: 0, microsecondsPerBeat: 500000 };
  }

  let selected = tempos[0];
  for (const tempo of tempos) {
    if (tempo.tick <= tick) {
      selected = tempo;
    } else {
      break;
    }
  }
  return selected;
}

function ticksToSeconds(
  ticks: number,
  microsecondsPerBeat: number,
  tpq: number
): number {
  return (ticks * microsecondsPerBeat) / tpq / 1_000_000;
}

function seekPlayerToTick(tick: number): void {
  if (!playerInstance?.seek) {
    return;
  }

  void playerInstance.seek(tickToSeconds(tick));
}

function highlightTsvLine(lineNumber: number, shouldScroll: boolean): void {
  if (!tsvRowsContainer || activeTsvLine === lineNumber) {
    return;
  }

  if (activeTsvLine !== null) {
    tsvRowsContainer
      .querySelector(`[data-line="${activeTsvLine}"]`)
      ?.classList.remove("active");
  }

  activeTsvLine = lineNumber;
  const row = tsvRowsContainer.querySelector<HTMLElement>(
    `[data-line="${lineNumber}"]`
  );
  row?.classList.add("active");
  if (row && shouldScroll) {
    row.scrollIntoView({ block: "nearest" });
  }
}

function startPlaybackLoop(): void {
  stopPlaybackLoop();
  const poll = () => {
    if (!playerInstance?.getState || !currentTsvIndex) {
      isPlaying = false;
      return;
    }
    const state = playerInstance.getState();
    isPlaying = !!state.isPlaying;
    if (isPlaying && tsvRowsContainer) {
      // Use playhead ratio within duration, map to TSV note range
      const duration = state.duration ?? 0;
      const currentTime = state.currentTime ?? 0;
      const ratio = duration > 0 ? clamp(currentTime / duration, 0, 1) : 0;

      let lastNoteEndTick = 0;
      for (const n of currentTsvIndex.notes) {
        const end = (n.absTick ?? 0) + (n.durTick ?? 0);
        if (end > lastNoteEndTick) lastNoteEndTick = end;
      }
      const currentTick = Math.round(ratio * lastNoteEndTick);

      const notes = currentTsvIndex.notes.filter(
        (n) => n.trackId === 0 || n.trackId === undefined
      );
      const activeNotes = notes.filter((n) => {
        const start = n.absTick ?? 0;
        const end = start + (n.durTick ?? 0);
        return currentTick >= start && currentTick <= end;
      });
      if (activeNotes.length > 0) {
        highlightTsvLine(activeNotes[0].lineNumber, true);
      }
    } else if (!isPlaying && tsvRowsContainer) {
      // When playback stops, clear the active line
      if (activeTsvLine !== null) {
        tsvRowsContainer
          .querySelector(`[data-line="${activeTsvLine}"]`)
          ?.classList.remove("active");
        activeTsvLine = null;
      }
    }
    playbackLoopTimer = window.setTimeout(poll, 100);
  };
  poll();
}

function stopPlaybackLoop(): void {
  if (playbackLoopTimer) {
    clearTimeout(playbackLoopTimer);
    playbackLoopTimer = null;
  }
  isPlaying = false;
}

function clamp(value: number, min: number, max: number): number {
  return Math.min(max, Math.max(min, value));
}

function isAbcPitch(s: string): boolean {
  return /^[_^=]*[A-Ga-g]['|,]*$/.test(s);
}

// State for file add request listeners
let fileAddRequestUnsubscribe: (() => void) | null = null;
let audioFileAddRequestUnsubscribe: (() => void) | null = null;

/**
 * Setup file add request listeners.
 * When user clicks "Add MIDI Files" or "Add Audio File" button,
 * send message to VS Code extension to open native file dialog.
 */
function setupFileAddRequestListener(): void {
  if (!playerInstance) {
    return;
  }

  // Unsubscribe previous listeners if exists
  if (fileAddRequestUnsubscribe) {
    fileAddRequestUnsubscribe();
    fileAddRequestUnsubscribe = null;
  }
  if (audioFileAddRequestUnsubscribe) {
    audioFileAddRequestUnsubscribe();
    audioFileAddRequestUnsubscribe = null;
  }

  // Subscribe to MIDI file add requests
  fileAddRequestUnsubscribe = playerInstance.onFileAddRequest(() => {
    // Request VS Code to show MIDI file dialog
    vscode.postMessage({ type: "add-midi-files" });
  });

  // Subscribe to audio file add requests
  audioFileAddRequestUnsubscribe = playerInstance.onAudioFileAddRequest(() => {
    // Request VS Code to show audio file dialog
    vscode.postMessage({ type: "add-audio-file" });
  });
}

/**
 * Converts a Blob to Base64 string.
 */
async function blobToBase64(blob: Blob): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onloadend = () => {
      const dataUrl = reader.result as string;
      // Remove the data URL prefix (e.g., "data:audio/midi;base64,")
      const base64 = dataUrl.split(",")[1];
      resolve(base64);
    };
    reader.onerror = reject;
    reader.readAsDataURL(blob);
  });
}

/**
 * Creates MIDI export options for VS Code extension integration.
 * Uses custom mode to send the exported MIDI to the extension for saving.
 */
function createMidiExportOptions(): MidiExportOptions {
  return {
    mode: "custom",
    onExport: async (blob: Blob, filename: string) => {
      // Convert blob to base64 for sending via postMessage
      const base64Data = await blobToBase64(blob);

      // Send to extension for saving to original file location
      vscode.postMessage({
        type: "export-midi",
        data: base64Data,
        filename,
      });
    },
  };
}

/**
 * Waits for container layout to be ready with valid dimensions.
 * In VS Code webview, the container may not have accurate dimensions immediately
 * after being shown, so we poll until we get a stable width > 0.
 */
async function waitForContainerLayout(
  container: HTMLElement,
  timeoutMs: number = 2000
): Promise<void> {
  const startTime = Date.now();
  const minWidth = 100; // Minimum expected width in pixels

  return new Promise<void>((resolve, reject) => {
    const checkLayout = () => {
      const now = Date.now();
      const elapsed = now - startTime;

      if (elapsed > timeoutMs) {
        reject(
          new Error(
            `Container layout timeout: width=${container.clientWidth}px after ${timeoutMs}ms`
          )
        );
        return;
      }

      const width = container.clientWidth;
      const height = container.clientHeight;

      // Check if container has valid dimensions
      if (width >= minWidth && height > 0) {
        // Double-check with one more frame to ensure stability
        requestAnimationFrame(() => {
          const stableWidth = container.clientWidth;
          const stableHeight = container.clientHeight;
          if (
            stableWidth >= minWidth &&
            stableHeight > 0 &&
            Math.abs(stableWidth - width) < 10
          ) {
            // Dimensions are stable, proceed
            resolve();
          } else {
            // Dimensions changed, check again
            checkLayout();
          }
        });
      } else {
        // Not ready yet, check again after a short delay
        setTimeout(checkLayout, 16); // ~60fps polling
      }
    };

    // Start checking after initial animation frame
    requestAnimationFrame(() => {
      requestAnimationFrame(() => {
        checkLayout();
      });
    });
  });
}

/**
 * Processes received MIDI data.
 */
async function handleMidiData(
  base64Data: string,
  filename: string,
  tsv?: string
): Promise<void> {
  try {
    if (tsv) {
      renderTsvPanel(tsv);
    }

    // Show the container before initializing (so it has dimensions)
    setStatus("ready");

    // Wait for container layout to be ready with valid dimensions
    // This is critical in VS Code webview where layout calculation may be delayed
    if (waveRollContainer) {
      await waitForContainerLayout(waveRollContainer);
    }

    // Decode base64 to bytes
    const midiBytes = decodeBase64ToUint8Array(base64Data);
    currentMidiBytes = midiBytes;
    currentFilename = filename;
    currentTsv = tsv || null;

    // Initialize the WaveRoll player
    await initializeWaveRollPlayer(midiBytes, filename);
  } catch (error) {
    console.error("[WaveRoll] Error in handleMidiData:", error);
    const errorMsg =
      error instanceof Error ? error.message : "Failed to load MIDI file";
    setStatus("error", errorMsg);

    // Notify extension host about the error
    vscode.postMessage({
      type: "error",
      message: errorMsg,
    });
  }
}

/**
 * Initializes the webview when DOM is ready.
 */
function initialize(): void {
  // Get UI elements
  loadingContainer = document.getElementById("loading-container");
  errorContainer = document.getElementById("error-container");
  studioContainer = document.getElementById("studio-container");
  waveRollContainer = document.getElementById("wave-roll-container");
  errorMessage = document.getElementById("error-message");
  tsvRowsContainer = document.getElementById("tsv-rows");
  tsvMeta = document.getElementById("tsv-meta");
  tsvToggle = document.getElementById("tsv-toggle") as HTMLButtonElement | null;

  // Listen for messages from extension
  window.addEventListener("message", handleMessage);
  tsvToggle?.addEventListener("click", toggleTsvPanel);

  // Cmd/Ctrl+S to save TSV edits
  document.addEventListener("keydown", (e) => {
    if ((e.metaKey || e.ctrlKey) && e.key === "s") {
      e.preventDefault();
      if (tsvEditMode === "edit") {
        saveTsvEdit();
      }
    }
  });

  // Create piano sound selector in the TSV panel header
  createPianoSoundSelector();

  // Cleanup on page unload
  window.addEventListener("beforeunload", () => {
    if (appearanceChangeUnsubscribe) {
      appearanceChangeUnsubscribe();
      appearanceChangeUnsubscribe = null;
    }
    if (fileAddRequestUnsubscribe) {
      fileAddRequestUnsubscribe();
      fileAddRequestUnsubscribe = null;
    }
    if (audioFileAddRequestUnsubscribe) {
      audioFileAddRequestUnsubscribe();
      audioFileAddRequestUnsubscribe = null;
    }
    stopPlaybackLoop();
    if (playerInstance) {
      playerInstance.dispose();
      playerInstance = null;
    }
    teardownTrackRowAdjustments();
    revokeBlobUrl();
  });

  // Notify extension that webview is ready
  vscode.postMessage({ type: "ready" });
}

function toggleTsvPanel(): void {
  if (!studioContainer || !tsvToggle) {
    return;
  }

  const collapsed = studioContainer.classList.toggle("tsv-collapsed");
  tsvToggle.textContent = collapsed ? "‹" : "›";
  tsvToggle.title = collapsed
    ? "Expand MIDI-TSV panel"
    : "Collapse MIDI-TSV panel";
  tsvToggle.setAttribute(
    "aria-label",
    collapsed ? "Expand MIDI-TSV panel" : "Collapse MIDI-TSV panel"
  );
}

// Initialize when DOM is loaded
if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", initialize);
} else {
  initialize();
}
