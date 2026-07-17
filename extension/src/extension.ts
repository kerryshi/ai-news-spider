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
  statusItem.backgroundColor = undefined; // clear any stale warning; not an alarm yet
  statusItem.tooltip = "Click to refresh & open the latest AI signal digest";
}

/**
 * The engine's collect-staleness verdict (`health` in `top --json`). The comparison
 * lives in the engine so it is pytest-covered and every surface renders one verdict;
 * the extension only paints it.
 */
type Health = { stale?: boolean; reason?: string; age_minutes?: number | null };

/**
 * Unknown health counts as stale. A verdict we can't read (unreachable Jetson,
 * unparseable output, or an engine too old to send `health`) is exactly the state the
 * 2026-07-05 outage sat in for 74.5h — a quiet badge there is the bug, not the fix.
 */
function isStale(health: Health | undefined): boolean {
  return !health || health.stale !== false;
}

function staleLabel(health: Health | undefined): string {
  if (health?.reason === "stale" && typeof health.age_minutes === "number") {
    const m = health.age_minutes;
    return m < 120 ? `last collect ${m.toFixed(0)} min ago` : `last collect ${(m / 60).toFixed(1)} h ago`;
  }
  return "last collect unknown — collector unreachable?";
}

/** Paint the badge as a warning. Pair every call with a digest banner: the badge alone
 * is half the warning, and the digest is what actually gets read. */
function staleStatus(health: Health | undefined, suffix = "") {
  statusItem.text = `$(warning) AI Signal — stale`;
  statusItem.backgroundColor = new vscode.ThemeColor("statusBarItem.warningBackground");
  const md = new vscode.MarkdownString(
    `**AI Signal — collection may have stopped**\n\n${mdEscape(staleLabel(health))}${suffix}`
  );
  md.isTrusted = false;
  statusItem.tooltip = md;
}

// Must match engine/digest.py's _stale_banner headline verbatim — it doubles as the
// "already bannered" marker, so drift here would double-banner the digest. Pinned from
// the engine side by test_collect_health.py::test_extension_banner_marker_matches_engine.
const STALE_MARKER = "Collection may have stopped";
// Detection must match the banner's exact blockquote structure, not the bare phrase: a
// scraped title/summary containing the words would otherwise suppress a real warning
// (reviewed defect, run 2026-07-15_1936 round 2). Both banner emitters (engine + this
// file) start the line with this prefix.
const STALE_BANNER_PREFIX = `> 🚨 **${STALE_MARKER}**`;

/** True only when a line IS the banner — content merely mentioning the phrase doesn't count. */
function hasStaleBanner(md: string): boolean {
  return md.split(/\r?\n/).some((line) => line.startsWith(STALE_BANNER_PREFIX));
}

/** Why the digest is untrustworthy, in the banner's voice. */
function staleWhy(health: Health | undefined): string {
  if (health?.reason === "stale" && typeof health.age_minutes === "number") {
    const m = health.age_minutes;
    const ago = m < 120 ? `${m.toFixed(0)} min` : `${(m / 60).toFixed(1)} h`;
    return `last successful collect was ${ago} ago`;
  }
  return "the last successful collect is unknown — the collector did not answer";
}

/**
 * Prepend the staleness banner to a digest the engine did not banner itself. The engine
 * banners whenever it *has* a verdict; this covers the cases it cannot speak to — an
 * unreachable collector, unreadable output, or an engine too old to send `health` at
 * all. Idempotent via STALE_MARKER, so a run of failed refreshes can't stack banners.
 */
function withStaleBanner(md: string, health: Health | undefined): string {
  if (hasStaleBanner(md)) return md; // the engine already said it
  return (
    `> 🚨 **${STALE_MARKER}** — ${staleWhy(health)} (threshold: 25 min). The items below ` +
    `are a frozen snapshot, not a quiet news day. Check the collector before trusting ` +
    `this digest.\n\n${md}`
  );
}

/**
 * Banner the *cached* digest on a failed fetch. Without this the badge warns while the
 * open preview still reads as a normal digest — which is the 2026-07-05 split exactly:
 * the digest looked like a quiet news day for 74.5h. Warn on the surface being read.
 */
function bannerCachedDigest(health: Health | undefined) {
  const file = path.join(os.tmpdir(), "ai-signal-latest.md");
  try {
    if (!fs.existsSync(file)) return; // no cached digest = nothing misleading on screen
    const md = fs.readFileSync(file, "utf-8");
    const bannered = withStaleBanner(md, health);
    if (bannered !== md) writeDigest(file, bannered);
  } catch (err) {
    output.appendLine(`could not banner the cached digest: ${err}`);
  }
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

function shqRemotePath(p: string): string {
  // Single-quote a remote path so it can't inject shell metacharacters, while still
  // letting the remote shell expand a leading ~ / ~user (which quoting would suppress).
  // Anything that isn't a clean `~`/`~user` prefix followed by `/...` is quoted whole.
  const m = /^(~[a-zA-Z0-9_-]*)(\/.*)?$/.exec(p);
  if (m) return m[1] + (m[2] ? "/" + shq(m[2].slice(1)) : "");
  return shq(p);
}

function mdEscape(s: string): string {
  // Neutralize markdown link/HTML syntax so a remote-scraped title can't inject a
  // `[x](command:...)` link or a raw HTML tag into the status-bar tooltip.
  return s.replace(/[\\`<>[\]()]/g, "\\$&");
}

function remoteCommand(engineArgs: string): string {
  const dir = shqRemotePath(cfg().get<string>("remoteDir") || "~/ai-signal");
  const py = shqRemotePath(cfg().get<string>("remotePython") || "venv/bin/python");
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
  // Coerce to positive integers before they reach the remote shell command, so a
  // non-numeric workspace override can't smuggle anything into the argument string.
  const n = Math.max(1, Math.trunc(Number(cfg().get<number>("defaultTopN") ?? 20)) || 20);
  const since = Math.max(1, Math.trunc(Number(cfg().get<number>("sinceHours") ?? 72)) || 72);
  let args = `top --json --n ${n} --since ${since}h`;
  if (query) args += ` --query ${shq(query)}`;

  const { stdout, code } = await runRemote(args, "AI Signal: ranking…");
  if (code !== 0) {
    // An unreachable collector is unknown health, not idle: going quiet here is what
    // let the ICS outage hide for 74.5h. Badge stays a warning until a fetch succeeds,
    // and the cached digest gets the same warning — the badge alone is half of it.
    staleStatus(undefined, "\n\nThe last fetch failed — the digest below may be frozen.");
    bannerCachedDigest(undefined);
    if (openPreview)
      vscode.window.showErrorMessage(`AI Signal: failed (exit ${code}). See the "AI Signal" output.`);
    return;
  }
  try {
    const result = JSON.parse(stdout);
    const items = result.items ?? [];
    updateStatus(items, query, result.health);
    // Always refresh the on-disk digest so an already-open preview live-updates,
    // even on silent (timer / post-collect) refreshes. Previously the file was only
    // rewritten when openPreview was true, so background refreshes left the preview
    // stale. Only steal focus / open the preview when explicitly asked.
    const file = path.join(os.tmpdir(), "ai-signal-latest.md");
    if (result.digest_markdown) {
      // withStaleBanner is a no-op when the engine already bannered (it knows it's
      // stale) or when health is fresh; it only fills the gap left by an engine too
      // old to send a verdict — whose silence must not read as "fresh".
      const md = isStale(result.health)
        ? withStaleBanner(result.digest_markdown, result.health)
        : result.digest_markdown;
      writeDigest(file, md);
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
    // Unparseable output = no verdict = unknown health. Same reasoning as the exit-code
    // path above: never let an unverified digest wear a healthy badge, and warn on the
    // cached digest too since it is the surface that actually gets read.
    staleStatus(undefined, "\n\nThe last fetch was unreadable — the digest below may be frozen.");
    bannerCachedDigest(undefined);
    output.appendLine("top: remote output was not valid JSON — digest not refreshed.");
    if (openPreview) vscode.window.showErrorMessage("AI Signal: couldn't parse remote output.");
  }
}

/** Glanceable status bar: count + top headline in the tooltip. */
function updateStatus(items: any[], query?: string, health?: Health) {
  if (isStale(health)) {
    staleStatus(health, `\n\nShowing ${items.length} item(s) from the last good collect.`);
    return;
  }
  const n = items.length;
  statusItem.text = n ? `$(radar) AI Signal $(arrow-up) ${n}` : "$(radar) AI Signal";
  statusItem.backgroundColor = undefined; // collection is healthy — drop any warning
  const md = new vscode.MarkdownString(
    `**AI Signal** — top ${n}${query ? ` · _${mdEscape(query)}_` : ""}\n\n` +
      items
        .slice(0, 8)
        .map((it, i) => `${i + 1}. ${mdEscape(it.title?.slice(0, 70) ?? "")}  \`${(it.score ?? 0).toFixed(2)}\``)
        .join("\n") +
      `\n\n_Click to open the full digest._`
  );
  // Deliberately NOT trusted: the tooltip contains remote-scraped titles, and an
  // untrusted MarkdownString renders `command:` links inert (no code-execution sink).
  md.isTrusted = false;
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
    // A failed collect must never look like success. idleStatus() would actively erase
    // an established stale warning and leave the badge neutral until the next refresh —
    // a failed collect is evidence *for* staleness, so warn instead of clearing.
    staleStatus(undefined, "\n\nThe last collect failed — the digest below may be frozen.");
    bannerCachedDigest(undefined);
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

// Behavioral test hooks — consumed by tests/test_collect_health.py through node with a
// stubbed `vscode` module. Not part of the extension's runtime surface.
export const __test = { STALE_MARKER, STALE_BANNER_PREFIX, hasStaleBanner, withStaleBanner };
