import * as vscode from "vscode";
import { MidiEditorProvider } from "./midiEditorProvider";
import { isMidiTsvUriPath, midiToTsv, tsvToMidi } from "./midiTsv";

/**
 * Activates the WaveRoll Studio extension.
 * Registers the custom editor provider for MIDI files.
 */
export function activate(context: vscode.ExtensionContext): void {
  const provider = new MidiEditorProvider(context);

  const registration = vscode.window.registerCustomEditorProvider(
    MidiEditorProvider.viewType,
    provider,
    {
      webviewOptions: {
        retainContextWhenHidden: true,
      },
      supportsMultipleEditorsPerDocument: false,
    }
  );

  context.subscriptions.push(registration);
  context.subscriptions.push(
    vscode.commands.registerCommand(
      "wave-roll-piano.exportMidiTsv",
      exportMidiAsTsv
    ),
    vscode.commands.registerCommand(
      "wave-roll-piano.exportTsvMidi",
      exportTsvAsMidi
    )
  );
}

/**
 * Deactivates the extension.
 */
export function deactivate(): void {
  // Cleanup if needed
}

async function exportMidiAsTsv(uri?: vscode.Uri): Promise<void> {
  const inputUri = await resolveInputUri(uri, {
    "MIDI Files": ["mid", "midi"],
  });
  if (!inputUri) {
    return;
  }
  if (!isMidiUriPath(inputUri.path)) {
    vscode.window.showErrorMessage(
      "WaveRoll Studio: expected a .mid or .midi file"
    );
    return;
  }

  const filename = inputUri.path.split("/").pop() ?? "unknown.mid";
  const data = await vscode.workspace.fs.readFile(inputUri);
  const tsv = midiToTsv(data, filename);
  const targetUri = await getUniqueFileUri(
    vscode.Uri.joinPath(inputUri, ".."),
    `${filename}.tsv`
  );

  await vscode.workspace.fs.writeFile(
    targetUri,
    new TextEncoder().encode(tsv)
  );
  vscode.window.showInformationMessage(
    `MIDI-TSV exported: ${vscode.workspace.asRelativePath(targetUri)}`
  );
}

async function exportTsvAsMidi(uri?: vscode.Uri): Promise<void> {
  const inputUri = await resolveInputUri(uri, {
    "MIDI-TSV Files": ["tsv"],
  });
  if (!inputUri) {
    return;
  }
  if (!isMidiTsvUriPath(inputUri.path)) {
    vscode.window.showErrorMessage(
      "WaveRoll Studio: expected a .mid.tsv or .midi.tsv file"
    );
    return;
  }

  const filename = inputUri.path.split("/").pop() ?? "unknown.mid.tsv";
  const data = await vscode.workspace.fs.readFile(inputUri);
  const midiBytes = tsvToMidi(new TextDecoder("utf-8").decode(data));
  const targetUri = await getUniqueFileUri(
    vscode.Uri.joinPath(inputUri, ".."),
    filename.replace(/\.tsv$/i, "")
  );

  await vscode.workspace.fs.writeFile(targetUri, midiBytes);
  vscode.window.showInformationMessage(
    `MIDI exported: ${vscode.workspace.asRelativePath(targetUri)}`
  );
}

async function resolveInputUri(
  uri: vscode.Uri | undefined,
  filters: Record<string, string[]>
): Promise<vscode.Uri | undefined> {
  if (uri) {
    return uri;
  }

  const activeUri = vscode.window.activeTextEditor?.document.uri;
  if (activeUri?.scheme === "file") {
    return activeUri;
  }

  const selected = await vscode.window.showOpenDialog({
    canSelectFiles: true,
    canSelectFolders: false,
    canSelectMany: false,
    filters,
  });
  return selected?.[0];
}

async function getUniqueFileUri(
  directory: vscode.Uri,
  filename: string
): Promise<vscode.Uri> {
  const lastDotIndex = filename.lastIndexOf(".");
  const baseName =
    lastDotIndex > 0 ? filename.slice(0, lastDotIndex) : filename;
  const extension = lastDotIndex > 0 ? filename.slice(lastDotIndex) : "";

  let targetUri = vscode.Uri.joinPath(directory, filename);
  let counter = 0;
  while (await fileExists(targetUri)) {
    counter++;
    targetUri = vscode.Uri.joinPath(
      directory,
      `${baseName}(${counter})${extension}`
    );
  }
  return targetUri;
}

async function fileExists(uri: vscode.Uri): Promise<boolean> {
  try {
    await vscode.workspace.fs.stat(uri);
    return true;
  } catch {
    return false;
  }
}

function isMidiUriPath(path: string): boolean {
  const lower = path.toLowerCase();
  return lower.endsWith(".mid") || lower.endsWith(".midi");
}
