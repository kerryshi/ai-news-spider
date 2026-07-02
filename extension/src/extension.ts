import * as vscode from "vscode";
import { spawn } from "child_process";
import * as fs from "fs";
import * as os from "os";
import * as path from "path";

let output: vscode.OutputChannel;
let statusItem: vscode.StatusBarItem;
let refreshTimer: NodeJS.Timeout | undefined;
let busy = false;

export function activate(context: vscode.ExtensionContext) {
  output = vscode.window.createOutputChannel("AI Signal");

  statusItem = vscode.window.createStatusBarItem(vscode.StatusBarAlignment.Left, 100);
  // Clicking the badge refetches fresh + opens the digest (was: open the cached
  // file, which could be a day stale). "Open last digest" still opens the cache.
  statusItem.command = "aiSignal.top";
  idleStatus();
  statusItem.show();

  context.subscriptions.push(
    output,
    statusItem,
    vscode.commands.registerCommand("aiSignal.top", () => showTop()),
    vscode.commands.registerCommand("aiSignal.topByTopic", () => showTopByTopic()),
    vscode.commands.registerCommand("aiSignal.collectNow", () => collectNow()),
    vscode.commands.registerCommand("aiSignal.openLatest", () => openLatest()),
    vscode.commands.registerCommand("aiSignal.openWebView", () => openWebView()),
    vscode.workspace.onDidChangeConfiguration((e) => {
      if (e.affectsConfiguration("aiSignal.refreshMinutes")) applyRefresh();
    })
  );

  applyRefresh();
  // Lazy mode: surface the signal automatically when the editor opens.
  if (cfg().get<boolean>("topOnStartup")) {
    setTimeout(() => showTop(undefined, cfg().get<boolean>("openOnStartup") ?? true), 2500);
  }
}

export function deactivate() {
  if (refreshTimer) clearInterval(refreshTimer);
}

function cfg() {
  return vscode.workspace.getConfiguration("aiSignal");
}

function idleStatus() {
  statusItem.text = "$(radar) AI Signal";
  statusItem.tooltip = "Click to refresh & open the latest AI signal digest";
}

function sshTarget(): string | undefined {
  const host = cfg().get<string>("sshHost") || "192.168.55.1";
  const user = cfg().get<string>("sshUser");
  if (!user) {
    vscode.window
      .showErrorMessage("AI Signal: set 'aiSignal.sshUser' to the Jetson's username.", "Open Settings")
      .then((c) => c && vscode.commands.executeCommand("workbench.action.openSettings", "aiSignal.sshUser"));
    return undefined;
  }
  return `${user}@${host}`;
}

function shq(s: string): string {
  return `'${s.replace(/'/g, `'\\''`)}'`;
}

function remoteCommand(engineArgs: string): string {
  const dir = cfg().get<string>("remoteDir") || "~/ai-signal";
  const py = cfg().get<string>("remotePython") || "venv/bin/python";
  return `cd ${dir} && ${py} -m engine.cli ${engineArgs}`;
}

function runRemote(engineArgs: string, title: string): Thenable<{ stdout: string; code: number | null }> {
  const target = sshTarget();
  if (!target) return Promise.resolve({ stdout: "", code: 1 });
  busy = true;
  statusItem.text = "$(sync~spin) AI Signal";
  const remoteCmd = remoteCommand(engineArgs);

  return vscode.window.withProgress(
    { location: vscode.ProgressLocation.Window, title },
    () =>
      new Promise<{ stdout: string; code: number | null }>((resolve) => {
        output.appendLine(`$ ssh ${target} ${remoteCmd}`);
        const proc = spawn("ssh", ["-o", "BatchMode=yes", "-o", "ConnectTimeout=8", target, remoteCmd], {});
        let stdout = "";
        proc.stdout.on("data", (d) => (stdout += d.toString()));
        proc.stderr.on("data", (d) => output.appendLine(d.toString().trimEnd()));
        proc.on("error", (err) => {
          vscode.window.showErrorMessage(`AI Signal: ssh failed — ${err.message}`);
          busy = false;
          resolve({ stdout: "", code: 1 });
        });
        proc.on("close", (code) => {
          busy = false;
          resolve({ stdout, code });
        });
      })
  );
}

/**
 * Write the digest atomically (write a sibling temp file, then rename over the
 * target). An open markdown preview watches this file; a plain writeFileSync can
 * be observed mid-write and render a truncated digest. Rename is atomic on Win/
 * POSIX, so the preview only ever sees a complete file and live-updates cleanly.
 */
function writeDigest(file: string, md: string) {
  const tmp = `${file}.tmp`;
  fs.writeFileSync(tmp, md, "utf-8");
  fs.renameSync(tmp, file);
}

async function showTop(query?: string, openPreview = true) {
  if (busy) return;
  const n = cfg().get<number>("defaultTopN") ?? 20;
  const since = cfg().get<number>("sinceHours") ?? 72;
  let args = `top --json --n ${n} --since ${since}h`;
  if (query) args += ` --query ${shq(query)}`;

  const { stdout, code } = await runRemote(args, "AI Signal: ranking…");
  if (code !== 0) {
    idleStatus();
    if (openPreview)
      vscode.window.showErrorMessage(`AI Signal: failed (exit ${code}). See the "AI Signal" output.`);
    return;
  }
  try {
    const result = JSON.parse(stdout);
    const items = result.items ?? [];
    updateStatus(items, query);
    // Always refresh the on-disk digest so an already-open preview live-updates,
    // even on silent (timer / post-collect) refreshes. Previously the file was only
    // rewritten when openPreview was true, so background refreshes left the preview
    // stale. Only steal focus / open the preview when explicitly asked.
    const file = path.join(os.tmpdir(), "ai-signal-latest.md");
    if (result.digest_markdown) {
      writeDigest(file, result.digest_markdown);
    }
    // Feed the local web view (scripts/serve.py): persist the full ranked result so
    // the server renders fresh HTML on its next auto-refresh. Written on every
    // Top / Collect now / timer refresh, so "Collect now" updates the open web page
    // hands-free. Atomic write so the server never reads a half-written file.
    writeDigest(path.join(os.tmpdir(), "ai-signal-latest.json"), JSON.stringify({ items, query: query ?? "" }));
    if (openPreview) {
      vscode.commands.executeCommand("markdown.showPreview", vscode.Uri.file(file));
    }
  } catch {
    idleStatus();
    output.appendLine("top: remote output was not valid JSON — digest not refreshed.");
    if (openPreview) vscode.window.showErrorMessage("AI Signal: couldn't parse remote output.");
  }
}

/** Glanceable status bar: count + top headline in the tooltip. */
function updateStatus(items: any[], query?: string) {
  const n = items.length;
  statusItem.text = n ? `$(radar) AI Signal $(arrow-up) ${n}` : "$(radar) AI Signal";
  const md = new vscode.MarkdownString(
    `**AI Signal** — top ${n}${query ? ` · _${query}_` : ""}\n\n` +
      items
        .slice(0, 8)
        .map((it, i) => `${i + 1}. ${it.title?.slice(0, 70) ?? ""}  \`${(it.score ?? 0).toFixed(2)}\``)
        .join("\n") +
      `\n\n_Click to open the full digest._`
  );
  md.isTrusted = true;
  statusItem.tooltip = md;
}

async function showTopByTopic() {
  const query = await vscode.window.showInputBox({
    prompt: "Topic to focus the ranking on",
    placeHolder: "e.g. agent frameworks, quantization, world models",
  });
  if (query && query.trim()) showTop(query.trim());
}

async function collectNow() {
  if (busy) return;
  const { stdout, code } = await runRemote("collect", "AI Signal: collecting on the Jetson…");
  if (code !== 0) {
    // A failed collect must never look like success — without this toast the badge
    // just returns to idle and the next digest is silently stale.
    idleStatus();
    vscode.window.showErrorMessage(`AI Signal: collect failed (exit ${code}). See the "AI Signal" output.`);
    return;
  }
  let msg = "AI Signal: collection complete.";
  try {
    const s = JSON.parse(stdout.trim().split("\n").pop() || "{}");
    if (s.items != null) msg = `AI Signal: corpus ${s.items} items (+${s.enriched ?? 0} new). Refreshing…`;
  } catch {
    // Exit 0 but unparseable stats: the run succeeded — say so, but leave a trace.
    output.appendLine("collect: run succeeded but its stats line was not valid JSON (see output above).");
  }
  vscode.window.showInformationMessage(msg);
  showTop(undefined, false); // refresh the badge after collecting
}

function openLatest() {
  const file = path.join(os.tmpdir(), "ai-signal-latest.md");
  if (fs.existsSync(file)) {
    vscode.commands.executeCommand("markdown.showPreview", vscode.Uri.file(file));
  } else {
    showTop();
  }
}

/**
 * Open the local web view in the default browser. The page is served by
 * `scripts/serve.py` on the desktop (start it once: `.venv/Scripts/python.exe
 * scripts/serve.py`). The extension only feeds it data; it does not start the
 * server. If the server isn't running the browser will show a connection error.
 */
function openWebView() {
  const port = cfg().get<number>("webPort") ?? 8765;
  vscode.env.openExternal(vscode.Uri.parse(`http://127.0.0.1:${port}`));
}

function applyRefresh() {
  if (refreshTimer) {
    clearInterval(refreshTimer);
    refreshTimer = undefined;
  }
  const mins = cfg().get<number>("refreshMinutes") ?? 0;
  if (mins > 0) {
    refreshTimer = setInterval(() => showTop(undefined, false), mins * 60_000);
    output?.appendLine(`Auto-refresh badge every ${mins} min.`);
  }
}
