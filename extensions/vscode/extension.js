// Synatyx Indexer — keeps the workspace pushed into a Synatyx code/doc index.
// Plain JS, zero dependencies: talks to the server's /index/diff + /index/files
// endpoints (same protocol as scripts/index_project.py).
"use strict";

const vscode = require("vscode");
const crypto = require("crypto");
const cp = require("child_process");
const fs = require("fs");
const os = require("os");
const path = require("path");

// Mirror of the server-side filters (src/core/index.py) — keep in sync.
const EXTENSIONS = new Set([
  ".py", ".js", ".ts", ".tsx", ".jsx", ".go", ".rs", ".java", ".cpp", ".c",
  ".rb", ".php", ".swift", ".kt", ".yml", ".yaml", ".toml", ".json",
  ".md", ".mdx", ".markdown",
]);
const EXCLUDED_DIRS = new Set([
  ".git", "node_modules", "__pycache__", ".venv", "venv", ".tox",
  "dist", "build", "target", ".next", ".mypy_cache", ".pytest_cache",
  ".ruff_cache", ".idea", ".vscode",
]);
const EXCLUDED_FILES = new Set(["uv.lock", "package-lock.json", "yarn.lock", "poetry.lock"]);
const MAX_FILE_BYTES = 200_000;
const UPLOAD_BATCH_FILES = 16;
const UPLOAD_BATCH_BYTES = 800_000;
const SAVE_DEBOUNCE_MS = 20_000;
const STARTUP_DELAY_MS = 8_000;

const KEY_SECRET = "synatyx.authKey";

let statusBar;
let output;
let secrets;
let saveTimer = null;
let running = false;
let lastSummary = "not indexed yet";

function cfg() {
  const c = vscode.workspace.getConfiguration("synatyx");
  const folder = vscode.workspace.workspaceFolders?.[0];
  return {
    url: (c.get("url") || "").replace(/\/+$/, ""),
    userId: c.get("userId") || os.userInfo().username,
    project: c.get("project") || (folder ? folder.name : ""),
    indexOnSave: c.get("indexOnSave"),
    indexOnStartup: c.get("indexOnStartup"),
    root: folder ? folder.uri.fsPath : null,
  };
}

function setStatus(text, tooltip) {
  statusBar.text = text;
  statusBar.tooltip = tooltip || "Synatyx Indexer — click for status";
  statusBar.show();
}

function listFiles(root) {
  try {
    const out = cp.execFileSync(
      "git", ["-C", root, "ls-files", "--cached", "--others", "--exclude-standard"],
      { encoding: "utf-8", timeout: 30_000, maxBuffer: 32 * 1024 * 1024 },
    );
    return out.split("\n").filter(Boolean).map((line) => path.join(root, line));
  } catch {
    const found = [];
    const walk = (dir) => {
      let entries;
      try { entries = fs.readdirSync(dir, { withFileTypes: true }); } catch { return; }
      for (const e of entries) {
        if (e.isDirectory()) {
          if (!EXCLUDED_DIRS.has(e.name) && !e.name.startsWith(".")) walk(path.join(dir, e.name));
        } else if (e.isFile()) {
          found.push(path.join(dir, e.name));
        }
      }
    };
    walk(root);
    return found;
  }
}

function indexable(file) {
  if (!EXTENSIONS.has(path.extname(file).toLowerCase())) return false;
  if (EXCLUDED_FILES.has(path.basename(file))) return false;
  try {
    const st = fs.statSync(file);
    if (!st.isFile() || st.size > MAX_FILE_BYTES) return false;
    const fd = fs.openSync(file, "r");
    const buf = Buffer.alloc(1024);
    const n = fs.readSync(fd, buf, 0, 1024, 0);
    fs.closeSync(fd);
    return !buf.subarray(0, n).includes(0);
  } catch {
    return false;
  }
}

async function post(base, key, route, payload) {
  const resp = await fetch(base + route, {
    method: "POST",
    headers: { "Content-Type": "application/json", "X-Auth-Key": key },
    body: JSON.stringify(payload),
  });
  if (!resp.ok) throw new Error(`${route} → HTTP ${resp.status}`);
  return resp.json();
}

async function runIndex(trigger) {
  if (running) return;
  const { url, userId, project, root } = cfg();
  if (!root || !url || !project) return;
  const key = await secrets.get(KEY_SECRET);
  if (!key) {
    setStatus("$(database) Synatyx: no key", "Run “Synatyx: Set Auth Key” to enable indexing");
    return;
  }

  running = true;
  setStatus("$(sync~spin) Synatyx: indexing…");
  try {
    const manifest = {};
    for (const f of listFiles(root)) {
      if (!indexable(f)) continue;
      const rel = path.relative(root, f);
      if (rel.startsWith("..")) continue;
      manifest[rel.split(path.sep).join("/")] =
        crypto.createHash("sha256").update(fs.readFileSync(f)).digest("hex").slice(0, 16);
    }

    const diff = await post(url, key, "/index/diff", { user_id: userId, project, files: manifest });
    const changed = diff.changed || [];
    const removed = diff.removed || [];
    output.appendLine(
      `[${new Date().toISOString()}] ${trigger}: ${Object.keys(manifest).length} files — ` +
      `${changed.length} to upload, ${removed.length} to prune, ${diff.unchanged} unchanged`,
    );

    let indexed = 0, chunks = 0;
    let batch = [], batchBytes = 0, prune = removed;
    const flush = async () => {
      if (!batch.length && !prune.length) return;
      const res = await post(url, key, "/index/files", {
        user_id: userId, project, files: batch, prune,
      });
      indexed += res.files_indexed || 0;
      chunks += res.chunks_upserted || 0;
      batch = []; batchBytes = 0; prune = [];
    };
    for (const rel of changed) {
      let content;
      try { content = fs.readFileSync(path.join(root, rel), "utf-8"); } catch { continue; }
      batch.push({ path: rel, content });
      batchBytes += content.length;
      if (batch.length >= UPLOAD_BATCH_FILES || batchBytes >= UPLOAD_BATCH_BYTES) await flush();
    }
    await flush();

    lastSummary = `${Object.keys(manifest).length} files tracked · ` +
      `${indexed} pushed, ${chunks} chunks (${new Date().toLocaleTimeString()})`;
    setStatus("$(database) Synatyx ✓", lastSummary);
  } catch (err) {
    lastSummary = `last run failed: ${err.message}`;
    output.appendLine(`[synatyx] ${err.message}`);
    setStatus("$(database) Synatyx ⚠", lastSummary);
  } finally {
    running = false;
  }
}

function activate(context) {
  secrets = context.secrets;
  output = vscode.window.createOutputChannel("Synatyx Indexer");
  statusBar = vscode.window.createStatusBarItem(vscode.StatusBarAlignment.Right, 90);
  statusBar.command = "synatyx.status";
  setStatus("$(database) Synatyx");

  context.subscriptions.push(
    statusBar,
    output,
    vscode.commands.registerCommand("synatyx.indexNow", () => runIndex("manual")),
    vscode.commands.registerCommand("synatyx.setKey", async () => {
      const key = await vscode.window.showInputBox({
        prompt: "Synatyx auth key (stored in the OS keychain via VS Code SecretStorage)",
        password: true, ignoreFocusOut: true,
      });
      if (key) {
        await secrets.store(KEY_SECRET, key.trim());
        vscode.window.showInformationMessage("Synatyx key saved.");
        runIndex("key-set");
      }
    }),
    vscode.commands.registerCommand("synatyx.status", () => {
      const { url, project, userId } = cfg();
      vscode.window.showInformationMessage(
        `Synatyx — ${project} @ ${url} (user ${userId}): ${lastSummary}`,
      );
    }),
    vscode.workspace.onDidSaveTextDocument(() => {
      if (!cfg().indexOnSave) return;
      clearTimeout(saveTimer);
      saveTimer = setTimeout(() => runIndex("on-save"), SAVE_DEBOUNCE_MS);
    }),
  );

  if (cfg().indexOnStartup) {
    setTimeout(() => runIndex("startup"), STARTUP_DELAY_MS);
  }
}

function deactivate() {
  clearTimeout(saveTimer);
}

module.exports = { activate, deactivate };
