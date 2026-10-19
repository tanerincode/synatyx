from __future__ import annotations

import subprocess

import pytest
from starlette.requests import Request

from src.transports.mcp.index_install import (
    DEFAULT_PUBLIC_URL,
    render_install_script,
    resolve_base_url,
)


def _request(headers: dict[str, str] | None = None, host: str = "10.0.0.5:9000") -> Request:
    raw = [(k.lower().encode(), v.encode()) for k, v in (headers or {}).items()]
    raw.append((b"host", host.encode()))
    scope = {
        "type": "http",
        "scheme": "http",
        "method": "GET",
        "path": "/index/install",
        "headers": raw,
        "server": ("10.0.0.5", 9000),
        "query_string": b"",
    }
    return Request(scope)


def test_forwarded_headers_win():
    req = _request({"X-Forwarded-Proto": "https", "X-Forwarded-Host": "memory.example.com"})
    assert resolve_base_url(req, "https://other.example.com") == "https://memory.example.com"


def test_forwarded_host_without_proto_defaults_to_https():
    req = _request({"X-Forwarded-Host": "memory.example.com"})
    assert resolve_base_url(req) == "https://memory.example.com"


def test_forwarded_lists_use_the_first_entry():
    req = _request({"X-Forwarded-Proto": "https, http", "X-Forwarded-Host": "a.example.com, b"})
    assert resolve_base_url(req) == "https://a.example.com"


def test_configured_public_url_beats_the_request_host():
    got = resolve_base_url(_request(), "https://memory.example.com/")
    assert got == "https://memory.example.com"


def test_default_public_url_is_ignored_in_favour_of_the_request():
    assert resolve_base_url(_request(), DEFAULT_PUBLIC_URL) == "http://10.0.0.5:9000"


def test_unsafe_url_is_refused():
    req = _request({"X-Forwarded-Host": "x.com;rm -rf ~"})
    with pytest.raises(ValueError):
        resolve_base_url(req)


def test_script_embeds_the_url_and_the_install_command():
    script = render_install_script("https://memory.example.com")
    assert script.startswith("#!/bin/sh\n")
    assert "URL=https://memory.example.com" in script
    assert '"$URL/index/script"' in script
    assert '.local/bin' in script
    assert 'chmod 755' in script
    assert 'python3 "$DEST/synatyx-index" install --url "$URL"' in script
    assert "SYNATYX_AUTH_KEY" in script


def test_script_passes_sh_syntax_check():
    script = render_install_script("https://memory.example.com")
    proc = subprocess.run(["sh", "-n"], input=script, text=True, capture_output=True)
    assert proc.returncode == 0, proc.stderr


def _run(script: str, env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["/bin/sh", "-s"], input=script, text=True, capture_output=True, env=env, timeout=20
    )


def test_script_fails_loudly_without_the_key(tmp_path):
    proc = _run(render_install_script("https://memory.example.com"),
                {"PATH": "/usr/bin:/bin", "HOME": str(tmp_path)})
    assert proc.returncode != 0
    assert "SYNATYX_AUTH_KEY is not set" in proc.stderr


def test_script_fails_loudly_without_python3(tmp_path):
    proc = _run(render_install_script("https://memory.example.com"),
                {"PATH": str(tmp_path), "HOME": str(tmp_path), "SYNATYX_AUTH_KEY": "k"})
    assert proc.returncode != 0
    assert "python3 is required" in proc.stderr
    assert not (tmp_path / ".local").exists()  # failed before touching the disk


def test_script_downloads_and_runs_install_with_the_key(tmp_path):
    """Run the real script against a stub server that serves a fake indexer."""
    import http.server
    import threading

    seen_keys: list[str | None] = []
    fake_indexer = "import sys\nopen(sys.argv[0] + '.args', 'w').write(' '.join(sys.argv[1:]))\n"

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            seen_keys.append(self.headers.get("X-Auth-Key"))
            body = fake_indexer.encode()
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    httpd = http.server.HTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        url = f"http://127.0.0.1:{httpd.server_address[1]}"
        import os
        proc = _run(render_install_script(url), {
            "PATH": os.environ["PATH"], "HOME": str(tmp_path), "SYNATYX_AUTH_KEY": "sekret",
        })
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=5)

    assert proc.returncode == 0, proc.stderr
    installed = tmp_path / ".local" / "bin" / "synatyx-index"
    assert installed.exists() and installed.stat().st_mode & 0o111
    assert seen_keys == ["sekret"]
    args_file = tmp_path / ".local" / "bin" / "synatyx-index.args"
    assert args_file.read_text() == f"install --url {url}"
