#!/usr/bin/env python3
"""Push-index a local project into a remote Synatyx code/doc index.

The server can't read your filesystem, so this script does the walking:
hash every indexable file, ask the server what changed (POST /index/diff),
upload only those files (POST /index/files), and prune deletions.
Idempotent and cheap — an unchanged repo is one small diff request.

Stdlib only. Designed for hooks (git post-commit, Claude Code SessionStart,
cron) — always exits 0 when it pushes so it can never break the calling
workflow.

Usage:
    python scripts/index_project.py [--root DIR] [--project NAME] [--force] [--dry-run]
        One-off push of a directory (the original behaviour; env-configured).

    index_project.py install --url URL [--user USER]
        One-time setup per machine: stores the key (macOS Keychain, else a 600
        file), writes ~/.config/synatyx/env, copies this script to
        ~/.local/bin/synatyx-index and adds two Claude Code hooks to
        ~/.claude/settings.json. Idempotent.

    synatyx-index uninstall [--purge]
        Removes our hooks, the env file and the stored key (--purge: the binary too).

    synatyx-index push [--root DIR] [--from-hook] [--debounce SECS] [--background]
        What the hooks run. --from-hook reads the hook JSON on stdin and finds the
        git repo from it (outside a repo it does nothing).

Env:
    SYNATYX_URL       server base URL      (default: env file, else http://localhost:9000)
    SYNATYX_AUTH_KEY  admin key            (else Keychain, else ~/.config/synatyx/key)
    SYNATYX_USER_ID   memory user id       (default: env file, else OS username)
"""
from __future__ import annotations

import argparse
import getpass
import hashlib
import json
import os
import shlex
import shutil
import subprocess
import sys
import time
import urllib.request
from pathlib import Path
from typing import Any

# Mirror of the server-side filters (src/core/index.py) — keep in sync.
EXTENSIONS = {
    ".py", ".js", ".ts", ".tsx", ".jsx", ".go", ".rs", ".java", ".cpp", ".c",
    ".rb", ".php", ".swift", ".kt", ".yml", ".yaml", ".toml", ".json",
    ".md", ".mdx", ".markdown", ".dart",
}
EXCLUDED_DIRS = {
    ".git", "node_modules", "__pycache__", ".venv", "venv", ".tox",
    "dist", "build", "target", ".next", ".mypy_cache", ".pytest_cache",
    ".ruff_cache", ".idea", ".vscode", ".dart_tool", "Pods", ".gradle",
}
EXCLUDED_FILES = {
    "uv.lock", "package-lock.json", "yarn.lock", "poetry.lock",
    ".flutter-plugins", ".flutter-plugins-dependencies", "pubspec.lock",
}
# Generated Dart (build_runner output); matched on the end of the file name.
EXCLUDED_SUFFIXES = (".g.dart", ".freezed.dart", ".gr.dart", ".mocks.dart", ".config.dart")
MAX_FILE_BYTES = 200_000
UPLOAD_BATCH_FILES = 16
UPLOAD_BATCH_BYTES = 800_000

DEFAULT_URL = "http://localhost:9000"
BIN_NAME = "synatyx-index"
KEYCHAIN_SERVICE = "synatyx"
EDIT_MATCHER = "Edit|Write|MultiEdit"
DEBOUNCE_SECONDS = 20
# A pending runner older than this many windows is presumed dead, so a crashed
# runner can never block pushes for good.
PENDING_STALE_WINDOWS = 3
NOINDEX_FILE = ".synatyx-noindex"


def list_files(root: Path) -> list[Path]:
    try:
        out = subprocess.run(
            ["git", "-C", str(root), "ls-files", "--cached", "--others", "--exclude-standard"],
            capture_output=True, text=True, timeout=30,
        )
        if out.returncode == 0:
            return [root / line for line in out.stdout.splitlines() if line.strip()]
    except Exception:
        pass
    found: list[Path] = []
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        dirnames[:] = [d for d in dirnames if d not in EXCLUDED_DIRS]
        found.extend(Path(dirpath) / name for name in filenames)
    return found


def indexable(path: Path) -> bool:
    if path.suffix.lower() not in EXTENSIONS or path.name in EXCLUDED_FILES:
        return False
    if path.name.endswith(EXCLUDED_SUFFIXES):
        return False
    try:
        if not path.is_file() or path.stat().st_size > MAX_FILE_BYTES:
            return False
        with open(path, "rb") as fh:
            return b"\x00" not in fh.read(1024)
    except OSError:
        return False


def post(url: str, key: str, payload: dict) -> dict:
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json", "X-Auth-Key": key},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=300) as resp:
        return json.loads(resp.read().decode("utf-8"))


# ── Where things live ────────────────────────────────────────────────────────
# Resolved on every call (never at import) so a test, or a user with an odd
# HOME, gets the paths of the HOME in force right now.

def config_dir() -> Path:
    return Path.home() / ".config" / "synatyx"


def env_file() -> Path:
    return config_dir() / "env"


def key_file() -> Path:
    return config_dir() / "key"


def cache_dir() -> Path:
    return Path.home() / ".cache" / "synatyx"


def bin_path() -> Path:
    return Path.home() / ".local" / "bin" / BIN_NAME


def settings_path() -> Path:
    return Path.home() / ".claude" / "settings.json"


def is_macos() -> bool:
    return sys.platform == "darwin"


def log(message: str) -> None:
    """Background runs have no terminal; failures go to a small log instead."""
    try:
        cache_dir().mkdir(parents=True, exist_ok=True)
        path = cache_dir() / "index.log"
        if path.exists() and path.stat().st_size > 100_000:
            path.unlink()
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} {message}\n")
    except OSError:
        pass


# ── Configuration and the key ────────────────────────────────────────────────

def read_env_file() -> dict[str, str]:
    values: dict[str, str] = {}
    try:
        lines = env_file().read_text(encoding="utf-8").splitlines()
    except OSError:
        return values
    for line in lines:
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            name, _, value = line.partition("=")
            values[name.strip()] = value.strip()
    return values


def write_private(path: Path, text: str) -> None:
    """Write a file that only the owner can read, from the first byte."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(text)
    os.chmod(path, 0o600)


def keychain_get(url: str) -> str:
    if not is_macos():
        return ""
    try:
        out = subprocess.run(
            ["security", "find-generic-password", "-s", KEYCHAIN_SERVICE, "-a", url, "-w"],
            capture_output=True, text=True, timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    return out.stdout.strip() if out.returncode == 0 else ""


def _security_quote(value: str) -> str:
    """Quote one word for `security -i`, whose parser reads double quotes."""
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def keychain_set(url: str, key: str) -> bool:
    """Store the key. It goes in on stdin (`security -i`), never on argv, so it
    cannot be read from the process list."""
    if not is_macos():
        return False
    command = (
        f"add-generic-password -U -s {KEYCHAIN_SERVICE} "
        f"-a {_security_quote(url)} -w {_security_quote(key)}\n"
    )
    try:
        out = subprocess.run(
            ["security", "-i"], input=command,
            capture_output=True, text=True, timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return out.returncode == 0


def keychain_delete(url: str) -> None:
    if not is_macos():
        return
    try:
        subprocess.run(
            ["security", "delete-generic-password", "-s", KEYCHAIN_SERVICE, "-a", url],
            capture_output=True, text=True, timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        pass


def resolve_url() -> str:
    url = os.getenv("SYNATYX_URL") or read_env_file().get("SYNATYX_URL") or DEFAULT_URL
    return url.rstrip("/")


def resolve_user() -> str:
    configured = os.getenv("SYNATYX_USER_ID") or read_env_file().get("SYNATYX_USER_ID")
    return configured or getpass.getuser()


def resolve_key(url: str) -> str:
    """The auth key: env var first, then the Keychain, then the key file."""
    key = os.getenv("SYNATYX_AUTH_KEY", "")
    if key:
        return key
    key = keychain_get(url)
    if key:
        return key
    try:
        return key_file().read_text(encoding="utf-8").strip()
    except OSError:
        return ""


# ── The push itself ──────────────────────────────────────────────────────────

def sync(root: Path, project: str, base: str, key: str, user_id: str,
         force: bool = False, dry_run: bool = False) -> None:
    files = [f for f in list_files(root) if indexable(f)]
    manifest: dict[str, str] = {}
    for f in files:
        try:
            rel = str(f.resolve().relative_to(root))
            manifest[rel] = hashlib.sha256(f.read_bytes()).hexdigest()[:16]
        except (OSError, ValueError):
            continue

    diff = post(f"{base}/index/diff", key, {
        "user_id": user_id, "project": project, "files": manifest,
    })
    changed = list(manifest) if force else diff.get("changed", [])
    removed = diff.get("removed", [])
    print(f"[synatyx-index] {project}: {len(manifest)} files — "
          f"{len(changed)} to upload, {len(removed)} to prune, "
          f"{diff.get('unchanged', 0)} unchanged")

    if dry_run or (not changed and not removed):
        return

    totals = {"files_indexed": 0, "files_failed": 0, "chunks_upserted": 0, "chunks_pruned": 0}
    batch: list[dict] = []
    batch_bytes = 0
    pending_prune = list(removed)

    def flush() -> None:
        nonlocal batch, batch_bytes, pending_prune
        if not batch and not pending_prune:
            return
        result = post(f"{base}/index/files", key, {
            "user_id": user_id, "project": project,
            "files": batch, "prune": pending_prune, "force": force,
        })
        for k in totals:
            totals[k] += result.get(k, 0)
        batch, batch_bytes, pending_prune = [], 0, []

    for rel in changed:
        try:
            content = (root / rel).read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        batch.append({"path": rel, "content": content})
        batch_bytes += len(content)
        if len(batch) >= UPLOAD_BATCH_FILES or batch_bytes >= UPLOAD_BATCH_BYTES:
            flush()
    flush()

    print(f"[synatyx-index] done: {totals['files_indexed']} indexed, "
          f"{totals['chunks_upserted']} chunks, {totals['chunks_pruned']} pruned"
          + (f", {totals['files_failed']} FAILED" if totals["files_failed"] else ""))


def project_name(root: Path) -> str:
    """The project a checkout belongs to.

    A git worktree is another checkout of the same repository, so it files under
    the main working tree's folder name, not its own (otherwise every agent
    worktree would create a junk index). Falls back to the folder name.
    """
    try:
        out = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "--path-format=absolute", "--git-common-dir"],
            capture_output=True, text=True, timeout=10,
        )
        common = Path(out.stdout.strip())
        if out.returncode == 0 and common.name == ".git" and common.parent.name:
            return common.parent.name
    except (OSError, subprocess.SubprocessError):
        pass
    return root.name


def indexing_disabled(root: Path | None = None) -> bool:
    """The opt-out: SYNATYX_INDEX=0, or a .synatyx-noindex file in the repo root."""
    if os.getenv("SYNATYX_INDEX", "").strip() == "0":
        return True
    return root is not None and (root / NOINDEX_FILE).exists()


def git_toplevel(start: Path) -> Path | None:
    if not start.is_dir():
        start = start.parent
    try:
        out = subprocess.run(
            ["git", "-C", str(start), "rev-parse", "--show-toplevel"],
            capture_output=True, text=True, timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    top = out.stdout.strip()
    return Path(top).resolve() if out.returncode == 0 and top else None


def root_from_hook(raw: str) -> Path | None:
    """The git repo a Claude Code hook event happened in, or None."""
    try:
        event = json.loads(raw) if raw.strip() else {}
    except ValueError:
        event = {}
    if not isinstance(event, dict):
        event = {}
    tool_input = event.get("tool_input")
    file_path = tool_input.get("file_path") if isinstance(tool_input, dict) else None
    start = Path(file_path).parent if isinstance(file_path, str) and file_path else None
    if start is None or not start.exists():
        start = Path(str(event.get("cwd") or os.getcwd()))
    return git_toplevel(start)


# ── Debounce ─────────────────────────────────────────────────────────────────
# Per repo: <id>.last (mtime = when the last push started) and <id>.pending
# (exists while one delayed runner is waiting). A call inside the window starts
# no work if a runner is already waiting; otherwise it becomes that runner.

def _state_files(root: Path) -> tuple[Path, Path]:
    ident = hashlib.sha1(str(root).encode("utf-8")).hexdigest()[:16]
    return cache_dir() / f"{ident}.last", cache_dir() / f"{ident}.pending"


def plan_debounce(root: Path, window: float) -> float | None:
    """Seconds to wait before pushing, or None when a push is already covered."""
    last, pending = _state_files(root)
    cache_dir().mkdir(parents=True, exist_ok=True)
    now = time.time()
    try:
        age = now - last.stat().st_mtime
    except OSError:
        age = window + 1
    if age >= window and not pending.exists():
        last.touch()
        return 0.0
    try:
        if now - pending.stat().st_mtime > window * PENDING_STALE_WINDOWS:
            pending.unlink()
    except OSError:
        pass
    try:
        os.close(os.open(pending, os.O_CREAT | os.O_EXCL | os.O_WRONLY))
    except FileExistsError:
        return None
    return max(window - age, 0.0)


def finish_pending(root: Path) -> None:
    last, pending = _state_files(root)
    try:
        pending.unlink()
    except OSError:
        pass
    last.touch()


def spawn_background(root: Path, delay: float, debounce: float) -> None:
    """Run the push in a detached session, so the caller returns at once."""
    cmd = [sys.executable, os.path.abspath(__file__), "push", "--root", str(root),
           "--run-after", str(delay)]
    if debounce:
        cmd += ["--debounce", str(debounce)]
    subprocess.Popen(
        cmd, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL, start_new_session=True, close_fds=True,
    )


def run_push(root: Path) -> None:
    if indexing_disabled(root):
        return
    base = resolve_url()
    key = resolve_key(base)
    if not key:
        log(f"{root}: no auth key (run install, or set SYNATYX_AUTH_KEY)")
        return
    sync(root, project_name(root), base, key, resolve_user())


def cmd_push(args: argparse.Namespace) -> int:
    if args.run_after is not None:  # the detached runner
        try:
            time.sleep(max(args.run_after, 0.0))
            root = Path(args.root).resolve()
            if args.debounce:
                finish_pending(root)
            run_push(root)
        except Exception as exc:
            log(f"push failed: {exc}")
        return 0

    try:
        if indexing_disabled():
            return 0
        if args.from_hook:
            root = root_from_hook(sys.stdin.read())
            if root is None:
                return 0  # not in a git repo: nothing to index
        else:
            root = Path(args.root).resolve()
        if indexing_disabled(root):
            return 0

        delay = 0.0
        if args.debounce:
            planned = plan_debounce(root, args.debounce)
            if planned is None:
                return 0
            delay = planned

        if args.background:
            spawn_background(root, delay, args.debounce)
            return 0

        time.sleep(delay)
        if args.debounce:
            finish_pending(root)
        run_push(root)
    except Exception as exc:
        log(f"push failed: {exc}")
    return 0


# ── Install / uninstall ──────────────────────────────────────────────────────

class InstallError(Exception):
    pass


def _is_ours(hook: Any) -> bool:
    return isinstance(hook, dict) and BIN_NAME in str(hook.get("command", ""))


def _hook_commands() -> dict[str, tuple[str | None, str]]:
    exe = shlex.quote(str(bin_path()))
    return {
        "SessionStart": (None, f"{exe} push --from-hook --background"),
        "PostToolUse": (
            EDIT_MATCHER,
            f"{exe} push --from-hook --background --debounce {DEBOUNCE_SECONDS}",
        ),
    }


def merge_hooks(settings: dict[str, Any]) -> dict[str, Any]:
    """Add our two hooks, replacing any earlier copy of them in place.

    Everything that is not ours is left exactly as found.
    """
    hooks = settings.setdefault("hooks", {})
    if not isinstance(hooks, dict):
        raise InstallError("settings.json: 'hooks' is not an object")
    for event, (matcher, command) in _hook_commands().items():
        groups = hooks.setdefault(event, [])
        if not isinstance(groups, list):
            raise InstallError(f"settings.json: hooks.{event} is not a list")
        ours = {"type": "command", "command": command}
        placed = False
        for group in list(groups):
            inner = group.get("hooks") if isinstance(group, dict) else None
            if not isinstance(inner, list) or not any(_is_ours(h) for h in inner):
                continue
            kept = [h for h in inner if not _is_ours(h)]
            if not placed:
                position = next(i for i, h in enumerate(inner) if _is_ours(h))
                kept.insert(min(position, len(kept)), ours)
                if matcher:
                    group["matcher"] = matcher
                placed = True
            group["hooks"] = kept
            if not kept:
                groups.remove(group)
        if not placed:
            group_new: dict[str, Any] = {"hooks": [ours]}
            if matcher:
                group_new = {"matcher": matcher, **group_new}
            groups.append(group_new)
    return settings


def strip_hooks(settings: dict[str, Any]) -> dict[str, Any]:
    hooks = settings.get("hooks")
    if not isinstance(hooks, dict):
        return settings
    for event in list(hooks):
        groups = hooks[event]
        if not isinstance(groups, list):
            continue
        for group in list(groups):
            inner = group.get("hooks") if isinstance(group, dict) else None
            if not isinstance(inner, list) or not any(_is_ours(h) for h in inner):
                continue
            group["hooks"] = [h for h in inner if not _is_ours(h)]
            if not group["hooks"]:
                groups.remove(group)
        if not groups:
            del hooks[event]
    if not hooks:
        del settings["hooks"]
    return settings


def load_settings() -> dict[str, Any]:
    path = settings_path()
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return {}
    if not raw.strip():
        return {}
    try:
        data = json.loads(raw)
    except ValueError as exc:
        raise InstallError(f"{path} is not valid JSON ({exc}); fix it and run again") from exc
    if not isinstance(data, dict):
        raise InstallError(f"{path} is not a JSON object")
    return data


def save_settings(settings: dict[str, Any]) -> bool:
    """Atomic write, previous file kept as .bak. False when nothing changed."""
    path = settings_path()
    text = json.dumps(settings, indent=2, ensure_ascii=False) + "\n"
    if path.exists() and path.read_text(encoding="utf-8") == text:
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        shutil.copy2(path, path.with_name(path.name + ".bak"))
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)
    return True


def cmd_install(args: argparse.Namespace) -> int:
    url = args.url.rstrip("/")
    user = args.user or getpass.getuser()
    key = os.getenv("SYNATYX_AUTH_KEY") or getpass.getpass("Synatyx auth key: ").strip()
    if not key:
        print("[synatyx-index] no key given", file=sys.stderr)
        return 1

    settings = load_settings()  # fail before changing anything if it is unreadable

    if keychain_set(url, key):
        where = "macOS Keychain"
        if key_file().exists():
            key_file().unlink()
    else:
        write_private(key_file(), key + "\n")
        where = str(key_file())
    write_private(env_file(), f"SYNATYX_URL={url}\nSYNATYX_USER_ID={user}\n")

    target = bin_path()
    if Path(__file__).resolve() != target.resolve():
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(__file__, target)
        target.chmod(0o755)

    changed = save_settings(merge_hooks(settings))
    print(f"[synatyx-index] installed: key in {where}, config {env_file()}, "
          f"binary {target}, hooks {'updated' if changed else 'already in place'} "
          f"in {settings_path()}")
    if str(target.parent) not in os.getenv("PATH", "").split(os.pathsep):
        print(f"[synatyx-index] note: {target.parent} is not on your PATH "
              f"(the hooks call {target} directly, so this only matters for typing it)")
    return 0


def cmd_uninstall(args: argparse.Namespace) -> int:
    url = read_env_file().get("SYNATYX_URL") or resolve_url()
    if settings_path().exists():
        save_settings(strip_hooks(load_settings()))
    keychain_delete(url)
    for path in (env_file(), key_file()):
        path.unlink(missing_ok=True)
    if args.purge:
        bin_path().unlink(missing_ok=True)
    print("[synatyx-index] uninstalled" + (" (binary removed)" if args.purge else ""))
    return 0


# ── Entry point ──────────────────────────────────────────────────────────────

SUBCOMMANDS = {"install", "uninstall", "push"}


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawTextHelpFormatter
    )
    sub = ap.add_subparsers(dest="command")

    inst = sub.add_parser("install", help="one-time setup: key, config, binary, Claude Code hooks")
    inst.add_argument("--url", required=True, help="Synatyx server base URL")
    inst.add_argument("--user", default=None, help="memory user id (default: OS username)")

    un = sub.add_parser("uninstall", help="remove our hooks, config and stored key")
    un.add_argument("--purge", action="store_true", help="also remove ~/.local/bin/synatyx-index")

    push = sub.add_parser("push", help="push the repo to the index (what the hooks run)")
    push.add_argument("--root", default=".", help="project root (default: cwd)")
    push.add_argument("--from-hook", action="store_true", help="read the hook JSON on stdin")
    push.add_argument("--debounce", type=float, default=0, metavar="SECS")
    push.add_argument("--background", action="store_true", help="detach and return at once")
    push.add_argument("--run-after", type=float, default=None, help=argparse.SUPPRESS)
    return ap


def main() -> int:
    if len(sys.argv) > 1 and sys.argv[1] in SUBCOMMANDS:
        args = build_parser().parse_args()
        if args.command == "install":
            return cmd_install(args)
        if args.command == "uninstall":
            return cmd_uninstall(args)
        return cmd_push(args)

    # No subcommand: the original one-off push, configured by env.
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawTextHelpFormatter
    )
    ap.add_argument("--root", default=".", help="project root (default: cwd)")
    ap.add_argument("--project", default=None, help="project slug (default: repo folder name)")
    ap.add_argument("--force", action="store_true", help="re-upload everything")
    ap.add_argument("--dry-run", action="store_true", help="show the diff, upload nothing")
    args = ap.parse_args()

    base = resolve_url()
    root = Path(args.root).resolve()
    sync(root, args.project or project_name(root), base, resolve_key(base), resolve_user(),
         force=args.force, dry_run=args.dry_run)
    return 0


if __name__ == "__main__":
    try:
        code = main()
    except InstallError as exc:
        print(f"[synatyx-index] {exc}", file=sys.stderr)
        code = 1
    except Exception as exc:
        print(f"[synatyx-index] skipped: {exc}", file=sys.stderr)
        # install/uninstall are run by a person and must report failure; every
        # other path is a hook and must never break the calling workflow.
        code = 1 if sys.argv[1:2] in (["install"], ["uninstall"]) else 0
    sys.exit(code)
