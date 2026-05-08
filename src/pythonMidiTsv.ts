import * as vscode from "vscode";
import * as childProcess from "child_process";
import * as crypto from "crypto";
import * as os from "os";
import * as path from "path";
import { promisify } from "util";

const execFile = promisify(childProcess.execFile);

interface PythonTsvResult {
  tsv: string;
  annotationPath?: string;
}

export async function midiToMeasureTsv(
  context: vscode.ExtensionContext,
  uri: vscode.Uri,
  source: string
): Promise<PythonTsvResult> {
  if (uri.scheme !== "file") {
    throw new Error("Measure-mode conversion requires a local MIDI file");
  }

  const scriptPath = vscode.Uri.joinPath(context.extensionUri, "midi_tsv.py").fsPath;
  const outPath = path.join(
    os.tmpdir(),
    `wave-roll-${process.pid}-${crypto.randomUUID()}.mid.tsv`
  );
  const annotationPath = await findAnnotationForMidi(uri);
  const args = ["midi2tsv", uri.fsPath, "--out", outPath];
  if (annotationPath) {
    args.push("--annotation", annotationPath);
  }

  try {
    await runPython(scriptPath, args);
    const tsv = await vscode.workspace.fs.readFile(vscode.Uri.file(outPath));
    return {
      tsv: new TextDecoder("utf-8").decode(tsv),
      annotationPath,
    };
  } finally {
    await deleteIfExists(vscode.Uri.file(outPath));
  }
}

async function runPython(scriptPath: string, args: string[]): Promise<void> {
  const errors: string[] = [];
  for (const python of getPythonCandidates()) {
    try {
      await execFile(python, [scriptPath, ...args], {
        maxBuffer: 1024 * 1024 * 32,
      });
      return;
    } catch (error) {
      errors.push(formatExecError(python, error));
    }
  }

  throw new Error(errors.join("\n"));
}

function getPythonCandidates(): string[] {
  const configured = vscode.workspace
    .getConfiguration("waveRollPiano")
    .get<string>("pythonPath");
  return [configured, "python3", "python"].filter(
    (value): value is string => typeof value === "string" && value.trim().length > 0
  );
}

async function findAnnotationForMidi(uri: vscode.Uri): Promise<string | undefined> {
  const parsed = path.parse(uri.fsPath);
  const candidates = [
    path.join(parsed.dir, `${parsed.name}_annotations.txt`),
    path.join(parsed.dir, "annotations.txt"),
  ];

  for (const candidate of candidates) {
    if (await fileExists(vscode.Uri.file(candidate))) {
      return candidate;
    }
  }
  return undefined;
}

async function fileExists(uri: vscode.Uri): Promise<boolean> {
  try {
    await vscode.workspace.fs.stat(uri);
    return true;
  } catch {
    return false;
  }
}

async function deleteIfExists(uri: vscode.Uri): Promise<void> {
  try {
    await vscode.workspace.fs.delete(uri);
  } catch {
    // Temporary output is best-effort cleanup.
  }
}

function formatExecError(python: string, error: unknown): string {
  if (error && typeof error === "object") {
    const maybe = error as {
      code?: number | string;
      stderr?: string;
      stdout?: string;
      message?: string;
    };
    const output = (maybe.stderr || maybe.stdout || maybe.message || "").trim();
    const code = maybe.code === undefined ? "" : ` exited with ${maybe.code}:`;
    return `${python}${code} ${output}`.trim();
  }
  return `${python}: ${String(error)}`;
}
