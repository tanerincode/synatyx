"""scripts/index_project.py: installer, hooks and the debounced push.

Everything runs against a temporary HOME with a fake `security` on PATH. The
real ~/.claude/settings.json and the real Keychain are never touched, and
nothing talks to a network.
"""
from __future__ import annotations

import importlib.util
import io
import json
import os
import stat
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import pytest

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "index_project.py"
URL = "https://context.example.com"


def _load() -> Any:
    spec = importlib.util.spec_from_file_location("index_project_under_test", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


FAKE_SECURITY = """#!/usr/bin/env python3
import os, shlex, sys

STORE = os.environ["FAKE_SECURITY_STORE"]
LOG = open(os.environ["FAKE_SECURITY_LOG"], "a")
LOG.write("argv: " + " ".join(sys.argv[1:]) + "\\n")


def run(words):
    if words[0] == "find-generic-password":
        if not os.path.exists(STORE):
            sys.exit(44)
        print(open(STORE).read())
    elif words[0] == "add-generic-password":
        open(STORE, "w").write(words[words.index("-w") + 1])
    elif words[0] == "delete-generic-password":
        if os.path.exists(STORE):
            os.remove(STORE)


if sys.argv[1:2] == ["-i"]:
    for line in sys.stdin.read().splitlines():
        LOG.write("stdin: " + line + "\\n")
        if line.strip():
            run(shlex.split(line))
else:
    run(sys.argv[1:])
"""


@pytest.fixture
def ix(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Any:
    """The script module, with HOME and `security` fenced into tmp_path."""
    home = tmp_path / "home"
    home.mkdir()
    fakebin = tmp_path / "fakebin"
    fakebin.mkdir()
    sec = fakebin / "security"
    sec.write_text(FAKE_SECURITY)
    sec.chmod(0o755)

    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    monkeypatch.setenv("PATH", f"{fakebin}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setenv("FAKE_SECURITY_LOG", str(tmp_path / "security.log"))
    monkeypatch.setenv("FAKE_SECURITY_STORE", str(tmp_path / "security.store"))
    for name in ("SYNATYX_AUTH_KEY", "SYNATYX_URL", "SYNATYX_USER_ID"):
        monkeypatch.delenv(name, raising=False)

    module = _load()
    monkeypatch.setattr(module, "is_macos", lambda: True)
    module.home = home
    module.security_log = tmp_path / "security.log"
    module.security_store = tmp_path / "security.store"
    return module


def _install(ix: Any, monkeypatch: pytest.MonkeyPatch, key: str = "sekret") -> int:
    monkeypatch.setenv("SYNATYX_AUTH_KEY", key)
    args = ix.build_parser().parse_args(["install", "--url", URL, "--user", "tom"])
    code: int = ix.cmd_install(args)
    monkeypatch.delenv("SYNATYX_AUTH_KEY")
    return code


def _uninstall(ix: Any, *flags: str) -> int:
    code: int = ix.cmd_uninstall(ix.build_parser().parse_args(["uninstall", *flags]))
    return code


def _settings(ix: Any) -> dict[str, Any]:
    data: dict[str, Any] = json.loads(ix.settings_path().read_text())
    return data


def _ours(ix: Any, event: str) -> list[dict[str, Any]]:
    return [
        h
        for group in _settings(ix).get("hooks", {}).get(event, [])
        for h in group["hooks"]
        if "synatyx-index" in h["command"]
    ]


FOREIGN = {
    "theme": "dark",
    "permissions": {"allow": ["Bash(ls)"]},
    "hooks": {
        "SessionStart": [{"hooks": [{"type": "command", "command": "echo hello"}]}],
        "PostToolUse": [
            {"matcher": "Bash", "hooks": [{"type": "command", "command": "audit.sh"}]}
        ],
        "Stop": [{"hooks": [{"type": "command", "command": "notify.sh"}]}],
    },
}


def _write_settings(ix: Any, data: Any) -> None:
    path = ix.settings_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + "\n")


def _git_repo(path: Path) -> Path:
    path.mkdir(parents=True)
    subprocess.run(["git", "init", "-q", str(path)], check=True)
    (path / "main.py").write_text("print('hi')\n")
    return path.resolve()


# ── install ─────────────────────────────────────────────────────────────────

def test_install_stores_key_config_binary_and_both_hooks(ix, monkeypatch):
    assert _install(ix, monkeypatch) == 0

    assert ix.security_store.read_text() == "sekret"
    log = ix.security_log.read_text()
    assert f'stdin: add-generic-password -U -s synatyx -a "{URL}" -w "sekret"' in log
    # the key travels on stdin, so it must not be on any argv
    argv_lines = [ln for ln in log.splitlines() if ln.startswith("argv")]
    assert argv_lines == ["argv: -i"]
    assert not ix.key_file().exists()  # the Keychain took it
    assert ix.env_file().read_text() == f"SYNATYX_URL={URL}\nSYNATYX_USER_ID=tom\n"
    assert stat.S_IMODE(ix.env_file().stat().st_mode) == 0o600
    assert ix.bin_path().is_file() and os.access(ix.bin_path(), os.X_OK)

    (start,) = _ours(ix, "SessionStart")
    assert start["command"].endswith("synatyx-index push --from-hook --background")
    (edit,) = _ours(ix, "PostToolUse")
    assert edit["command"].endswith("synatyx-index push --from-hook --background --debounce 20")
    group = _settings(ix)["hooks"]["PostToolUse"][0]
    assert group["matcher"] == "Edit|Write|MultiEdit"


def test_install_twice_gives_the_same_file(ix, monkeypatch):
    _write_settings(ix, FOREIGN)
    _install(ix, monkeypatch)
    first = ix.settings_path().read_text()
    bak_after_first = ix.settings_path().with_name("settings.json.bak").read_text()

    _install(ix, monkeypatch)

    assert ix.settings_path().read_text() == first
    assert len(_ours(ix, "SessionStart")) == 1
    assert len(_ours(ix, "PostToolUse")) == 1
    # the second run changed nothing, so it must not have overwritten the backup
    assert json.loads(bak_after_first) == FOREIGN
    assert json.loads(ix.settings_path().with_name("settings.json.bak").read_text()) == FOREIGN


def test_install_preserves_foreign_settings_and_hooks(ix, monkeypatch):
    _write_settings(ix, FOREIGN)
    _install(ix, monkeypatch)

    settings = _settings(ix)
    assert settings["theme"] == "dark"
    assert settings["permissions"] == FOREIGN["permissions"]
    assert settings["hooks"]["Stop"] == FOREIGN["hooks"]["Stop"]
    assert settings["hooks"]["SessionStart"][0] == FOREIGN["hooks"]["SessionStart"][0]
    assert settings["hooks"]["PostToolUse"][0] == FOREIGN["hooks"]["PostToolUse"][0]
    assert len(settings["hooks"]["SessionStart"]) == 2
    assert len(settings["hooks"]["PostToolUse"]) == 2


def test_install_keeps_a_backup_of_the_previous_file(ix, monkeypatch):
    _write_settings(ix, FOREIGN)
    before = ix.settings_path().read_text()
    _install(ix, monkeypatch)
    assert ix.settings_path().with_name("settings.json.bak").read_text() == before


@pytest.mark.parametrize("initial", [None, ""])
def test_install_handles_a_missing_or_empty_settings_file(ix, monkeypatch, initial):
    if initial is not None:
        ix.settings_path().parent.mkdir(parents=True)
        ix.settings_path().write_text(initial)
    assert _install(ix, monkeypatch) == 0
    assert len(_ours(ix, "SessionStart")) == 1


def test_install_refuses_to_overwrite_unreadable_settings(ix, monkeypatch):
    ix.settings_path().parent.mkdir(parents=True)
    ix.settings_path().write_text("{not json")
    with pytest.raises(ix.InstallError):
        _install(ix, monkeypatch)
    assert ix.settings_path().read_text() == "{not json"
    assert not ix.env_file().exists()  # nothing was half-installed


def test_install_without_keychain_writes_a_private_key_file(ix, monkeypatch):
    monkeypatch.setattr(ix, "is_macos", lambda: False)
    _install(ix, monkeypatch)
    assert ix.key_file().read_text() == "sekret\n"
    assert stat.S_IMODE(ix.key_file().stat().st_mode) == 0o600
    assert not ix.security_log.exists()


def test_a_key_with_quotes_and_spaces_survives_the_stdin_quoting(ix, monkeypatch):
    awkward = 'p"a\\ss word\'s'
    _install(ix, monkeypatch, key=awkward)
    assert ix.security_store.read_text() == awkward


def test_install_prompts_when_no_env_key(ix, monkeypatch):
    monkeypatch.setattr(ix.getpass, "getpass", lambda prompt="": "typed-key")
    args = ix.build_parser().parse_args(["install", "--url", URL])
    assert ix.cmd_install(args) == 0
    assert ix.security_store.read_text() == "typed-key"


# ── uninstall ───────────────────────────────────────────────────────────────

def test_uninstall_removes_only_ours_and_keeps_the_binary(ix, monkeypatch):
    _write_settings(ix, FOREIGN)
    _install(ix, monkeypatch)
    assert _uninstall(ix) == 0

    assert _settings(ix) == FOREIGN
    assert not ix.env_file().exists()
    assert not ix.security_store.exists()
    assert "argv: delete-generic-password -s synatyx -a " + URL in ix.security_log.read_text()
    assert ix.bin_path().exists()


def test_uninstall_purge_removes_the_binary(ix, monkeypatch):
    _install(ix, monkeypatch)
    _uninstall(ix, "--purge")
    assert not ix.bin_path().exists()


def test_uninstall_on_a_fresh_install_leaves_no_hooks_key(ix, monkeypatch):
    _install(ix, monkeypatch)
    _uninstall(ix)
    assert _settings(ix) == {}


def test_uninstall_when_nothing_was_installed_is_harmless(ix):
    assert _uninstall(ix) == 0
    assert not ix.settings_path().exists()


# ── key lookup ──────────────────────────────────────────────────────────────

def test_key_lookup_order_env_then_keychain_then_file(ix, monkeypatch):
    ix.config_dir().mkdir(parents=True)
    ix.key_file().write_text("from-file\n")
    assert ix.resolve_key(URL) == "from-file"

    ix.security_store.write_text("from-keychain")
    assert ix.resolve_key(URL) == "from-keychain"

    monkeypatch.setenv("SYNATYX_AUTH_KEY", "from-env")
    assert ix.resolve_key(URL) == "from-env"


def test_key_lookup_with_nothing_configured_is_empty(ix):
    assert ix.resolve_key(URL) == ""


def test_url_and_user_come_from_the_env_file(ix, monkeypatch):
    _install(ix, monkeypatch)
    assert ix.resolve_url() == URL
    assert ix.resolve_user() == "tom"
    monkeypatch.setenv("SYNATYX_URL", "http://other:1/")
    assert ix.resolve_url() == "http://other:1"


# ── push, hook mode, debounce ───────────────────────────────────────────────

def _push(ix: Any, monkeypatch: pytest.MonkeyPatch, event: dict[str, Any], *flags: str) -> int:
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(event)))
    args = ix.build_parser().parse_args(["push", "--from-hook", *flags])
    code: int = ix.cmd_push(args)
    return code


def test_from_hook_outside_git_does_nothing(ix, monkeypatch, tmp_path):
    spawned: list[Any] = []
    monkeypatch.setattr(ix, "spawn_background", lambda *a: spawned.append(a))
    monkeypatch.setattr(ix, "post", lambda *a, **k: pytest.fail("must not push"))
    not_git = tmp_path / "plain"
    not_git.mkdir()

    assert _push(ix, monkeypatch, {"cwd": str(not_git)}, "--background") == 0
    assert _push(ix, monkeypatch, {"cwd": str(not_git)}) == 0
    assert spawned == []


def test_from_hook_finds_the_repo_from_the_edited_file(ix, monkeypatch, tmp_path):
    repo = _git_repo(tmp_path / "work" / "myrepo")
    (repo / "sub").mkdir()
    spawned: list[Any] = []
    monkeypatch.setattr(ix, "spawn_background", lambda *a: spawned.append(a))

    event = {"cwd": str(tmp_path), "tool_input": {"file_path": str(repo / "sub" / "x.py")}}
    _push(ix, monkeypatch, event, "--background")

    assert spawned == [(repo, 0.0, 0)]


def test_debounce_collapses_rapid_calls_to_one_pending_runner(ix, monkeypatch, tmp_path):
    repo = _git_repo(tmp_path / "myrepo")
    spawned: list[Any] = []
    monkeypatch.setattr(ix, "spawn_background", lambda *a: spawned.append(a))

    for _ in range(6):
        _push(ix, monkeypatch, {"cwd": str(repo)}, "--background", "--debounce", "20")

    # one run now, one runner waiting for the window to end — nothing else
    assert len(spawned) == 2
    assert spawned[0][1] == 0.0
    assert 0 < spawned[1][1] <= 20


def test_a_call_after_the_window_runs_again(ix, monkeypatch, tmp_path):
    repo = _git_repo(tmp_path / "myrepo")
    spawned: list[Any] = []
    monkeypatch.setattr(ix, "spawn_background", lambda *a: spawned.append(a))

    _push(ix, monkeypatch, {"cwd": str(repo)}, "--background", "--debounce", "20")
    last, _ = ix._state_files(repo)
    old = time.time() - 60
    os.utime(last, (old, old))
    _push(ix, monkeypatch, {"cwd": str(repo)}, "--background", "--debounce", "20")

    assert [s[1] for s in spawned] == [0.0, 0.0]


def test_a_dead_pending_runner_does_not_block_forever(ix, monkeypatch, tmp_path):
    repo = _git_repo(tmp_path / "myrepo")
    spawned: list[Any] = []
    monkeypatch.setattr(ix, "spawn_background", lambda *a: spawned.append(a))
    last, pending = ix._state_files(repo)
    ix.cache_dir().mkdir(parents=True)
    last.touch()
    pending.touch()
    old = time.time() - 3600
    os.utime(pending, (old, old))

    _push(ix, monkeypatch, {"cwd": str(repo)}, "--background", "--debounce", "20")

    assert len(spawned) == 1


def test_the_runner_clears_pending_and_pushes_the_whole_repo(ix, monkeypatch, tmp_path):
    repo = _git_repo(tmp_path / "myrepo")
    _install(ix, monkeypatch)
    calls: list[tuple[str, dict[str, Any]]] = []

    def fake_post(url: str, key: str, payload: dict[str, Any]) -> dict[str, Any]:
        calls.append((url, payload))
        assert key == "sekret"
        return {"changed": ["main.py"], "removed": [], "files_indexed": 1}

    monkeypatch.setattr(ix, "post", fake_post)
    _, pending = ix._state_files(repo)
    ix.cache_dir().mkdir(parents=True, exist_ok=True)
    pending.touch()

    args = ix.build_parser().parse_args(
        ["push", "--root", str(repo), "--run-after", "0", "--debounce", "20"]
    )
    assert ix.cmd_push(args) == 0

    assert not pending.exists()
    assert [u for u, _ in calls] == [f"{URL}/index/diff", f"{URL}/index/files"]
    assert calls[0][1]["project"] == "myrepo"
    assert calls[0][1]["user_id"] == "tom"
    assert list(calls[0][1]["files"]) == ["main.py"]


def test_push_without_a_key_exits_zero_and_pushes_nothing(ix, monkeypatch, tmp_path):
    repo = _git_repo(tmp_path / "myrepo")
    monkeypatch.setattr(ix, "post", lambda *a, **k: pytest.fail("no key, no request"))
    args = ix.build_parser().parse_args(["push", "--root", str(repo)])
    assert ix.cmd_push(args) == 0
    assert "no auth key" in (ix.cache_dir() / "index.log").read_text()


def test_push_swallows_server_errors(ix, monkeypatch, tmp_path):
    repo = _git_repo(tmp_path / "myrepo")
    monkeypatch.setenv("SYNATYX_AUTH_KEY", "k")

    def boom(*a: Any, **k: Any) -> Any:
        raise OSError("connection refused")

    monkeypatch.setattr(ix, "post", boom)
    args = ix.build_parser().parse_args(["push", "--root", str(repo)])
    assert ix.cmd_push(args) == 0


def test_background_push_returns_immediately(tmp_path):
    """The real thing, in a subprocess: detach and come back fast."""
    home = tmp_path / "home"
    home.mkdir()
    repo = _git_repo(tmp_path / "repo")
    env = {**os.environ, "HOME": str(home)}
    for name in ("SYNATYX_AUTH_KEY", "SYNATYX_URL"):
        env.pop(name, None)

    started = time.monotonic()
    done = subprocess.run(
        [sys.executable, str(SCRIPT), "push", "--from-hook", "--background"],
        input=json.dumps({"cwd": str(repo)}), text=True, env=env, timeout=10,
        capture_output=True,
    )
    elapsed = time.monotonic() - started

    assert done.returncode == 0
    assert done.stdout == ""  # a SessionStart hook's stdout would reach the model
    assert elapsed < 1.0


# ── project name in worktrees, opt-out ──────────────────────────────────────

def _commit(repo: Path) -> None:
    git = ["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@t"]
    subprocess.run([*git, "add", "."], check=True)
    subprocess.run([*git, "commit", "-qm", "init"], check=True)


def test_a_worktree_files_under_the_main_repo_name_but_pushes_its_own_files(
    ix, monkeypatch, tmp_path
):
    repo = _git_repo(tmp_path / "myrepo")
    _commit(repo)
    wt = tmp_path / "myrepo" / ".claude" / "worktrees" / "myrepo-w1"
    subprocess.run(
        ["git", "-C", str(repo), "worktree", "add", "-q", "-b", "wip", str(wt)], check=True
    )
    (wt / "only_here.py").write_text("x = 1\n")
    assert ix.project_name(wt.resolve()) == "myrepo"
    assert ix.project_name(repo) == "myrepo"

    _install(ix, monkeypatch)
    calls: list[dict[str, Any]] = []

    def fake_post(url: str, key: str, payload: dict[str, Any]) -> dict[str, Any]:
        calls.append(payload)
        return {"changed": [], "removed": []}

    monkeypatch.setattr(ix, "post", fake_post)
    ix.run_push(wt.resolve())

    assert calls[0]["project"] == "myrepo"
    assert "only_here.py" in calls[0]["files"]  # the worktree's own files


def test_project_name_falls_back_to_the_folder_name_outside_git(ix, tmp_path):
    plain = tmp_path / "just-a-folder"
    plain.mkdir()
    assert ix.project_name(plain) == "just-a-folder"


def test_noindex_file_makes_push_do_nothing(ix, monkeypatch, tmp_path):
    repo = _git_repo(tmp_path / "myrepo")
    (repo / ".synatyx-noindex").write_text("")
    spawned: list[Any] = []
    monkeypatch.setattr(ix, "spawn_background", lambda *a: spawned.append(a))
    monkeypatch.setattr(ix, "post", lambda *a, **k: pytest.fail("opted out"))
    monkeypatch.setenv("SYNATYX_AUTH_KEY", "k")

    assert _push(ix, monkeypatch, {"cwd": str(repo)}, "--background") == 0
    assert ix.cmd_push(ix.build_parser().parse_args(["push", "--root", str(repo)])) == 0
    ix.run_push(repo)  # even a runner that was already spawned stands down
    assert spawned == []


def test_synatyx_index_zero_makes_push_do_nothing(ix, monkeypatch, tmp_path):
    repo = _git_repo(tmp_path / "myrepo")
    spawned: list[Any] = []
    monkeypatch.setattr(ix, "spawn_background", lambda *a: spawned.append(a))
    monkeypatch.setattr(ix, "post", lambda *a, **k: pytest.fail("opted out"))
    monkeypatch.setenv("SYNATYX_AUTH_KEY", "k")
    monkeypatch.setenv("SYNATYX_INDEX", "0")

    assert _push(ix, monkeypatch, {"cwd": str(repo)}, "--background") == 0
    ix.run_push(repo)
    assert spawned == []


# ── the original, subcommand-less behaviour ─────────────────────────────────

def test_legacy_invocation_still_pushes_by_env(ix, monkeypatch, tmp_path, capsys):
    repo = _git_repo(tmp_path / "legacy-app")
    monkeypatch.setenv("SYNATYX_URL", "http://stub:9000")
    monkeypatch.setenv("SYNATYX_AUTH_KEY", "legacy-key")
    monkeypatch.setenv("SYNATYX_USER_ID", "someone")
    calls: list[tuple[str, str, dict[str, Any]]] = []

    def fake_post(url: str, key: str, payload: dict[str, Any]) -> dict[str, Any]:
        calls.append((url, key, payload))
        return {"changed": ["main.py"], "removed": [], "unchanged": 0, "files_indexed": 1}

    monkeypatch.setattr(ix, "post", fake_post)
    monkeypatch.setattr(sys, "argv", ["index_project.py", "--root", str(repo)])

    assert ix.main() == 0

    assert [c[0] for c in calls] == ["http://stub:9000/index/diff", "http://stub:9000/index/files"]
    assert calls[0][1] == "legacy-key"
    assert calls[0][2]["project"] == "legacy-app"
    assert calls[0][2]["user_id"] == "someone"
    assert calls[1][2]["files"] == [{"path": "main.py", "content": "print('hi')\n"}]
    assert "[synatyx-index] done" in capsys.readouterr().out


def test_legacy_dry_run_uploads_nothing(ix, monkeypatch, tmp_path):
    repo = _git_repo(tmp_path / "app")
    monkeypatch.setenv("SYNATYX_AUTH_KEY", "k")
    calls: list[str] = []
    monkeypatch.setattr(
        ix, "post", lambda url, key, payload: calls.append(url) or {"changed": ["main.py"]}
    )
    monkeypatch.setattr(sys, "argv", ["index_project.py", "--root", str(repo), "--dry-run"])
    assert ix.main() == 0
    assert calls == ["http://localhost:9000/index/diff"]


# ── file filters ────────────────────────────────────────────────────────────

def test_flutter_tree_keeps_source_and_drops_generated_and_build_files(ix, tmp_path):
    app = tmp_path / "flutter_app"
    kept = ["lib/main.dart", "lib/src/widget.dart", "pubspec.yaml", "analysis_options.yaml"]
    dropped = [
        "lib/model.g.dart", "lib/model.freezed.dart", "lib/router.gr.dart",
        "test/api.mocks.dart", "lib/injection.config.dart",
        "pubspec.lock", ".flutter-plugins", ".flutter-plugins-dependencies",
        ".dart_tool/package_config.json", "ios/Pods/Pods.xcodeproj/x.json",
        "android/.gradle/cache.json", "build/app/outputs/app.json",
    ]
    for rel in kept + dropped:
        path = app / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("x: 1\n" if rel.endswith((".yaml", ".json")) else "void main() {}\n")

    found = sorted(str(p.relative_to(app)) for p in ix.list_files(app) if ix.indexable(p))

    assert found == sorted(kept)
