> **Deprecated.** This extension will be removed in a later release. Use the
> installer instead: `python scripts/index_project.py install --url <server>`
> keeps the index fresh from Claude Code sessions with no editor extension. See
> [Automatic indexing](../../docs/code-index.md#automatic-indexing).

# Synatyx Indexer — VS Code / Cursor extension

Keeps the open workspace pushed into a [Synatyx](https://github.com/tanerincode/synatyx)
code/doc index (`ctx_<project>__index`), so AI agents connected to the same
server get exact-symbol code search and `context_pack` code sections — without
anyone running indexing commands.

Works in **VS Code** and **Cursor** (Cursor installs VS Code extensions).

## What it does

- **On startup**: full hash diff against the server, uploads only what changed
- **On save**: debounced re-push (20 s) — the index is fresh moments after you edit
- **Status bar**: `Synatyx ✓` with last-sync details; click for status
- Uses the same `/index/diff` + `/index/files` protocol as
  `scripts/index_project.py` — idempotent, an unchanged repo costs one request
- Auth key lives in the OS keychain (VS Code SecretStorage), never in settings files

## Setup (per developer, once)

1. Install the extension (`.vsix` or marketplace, see below)
2. `Cmd/Ctrl+Shift+P` → **Synatyx: Set Auth Key** → paste the team key
3. Done. Settings (`synatyx.*`) let you override the server URL, user id,
   project slug, and the on-save/on-startup behavior.

## Commands

| Command | Action |
|---|---|
| `Synatyx: Index Project Now` | Force a diff + push |
| `Synatyx: Set Auth Key` | Store the key in the OS keychain |
| `Synatyx: Show Index Status` | Last sync summary |

## Packaging & distribution

```bash
cd extensions/vscode
npx --yes @vscode/vsce package --no-dependencies   # → synatyx-indexer-0.2.0.vsix
```

- **Internal**: share the `.vsix`; devs install via "Extensions: Install from VSIX"
- **Cursor's marketplace**: publish to [Open VSX](https://open-vsx.org) —
  `npx ovsx publish synatyx-indexer-0.2.0.vsix -p <token>`
- **VS Code Marketplace**: `npx vsce publish -p <azure-devops-token>`
