"""Pair this computer with a real Sparrow server from the one-line command.

Starts Sparrow on a free local port with throwaway state, makes an owner,
asks for a pairing command for this computer's system, runs it exactly as a
person would (sh on Linux/macOS, PowerShell on Windows), waits for the node to
connect, then makes and chooses its folders from Sparrow over the API.

    python tests/node_pairing_check.py [--no-system-python]

It installs a real background service for the current user and removes it
afterwards. Run it on a disposable machine or CI runner.
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from http.cookiejar import CookieJar
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WINDOWS = os.name == "nt"


def free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


class Client:
    def __init__(self, base):
        self.base = base
        self.opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(CookieJar()))

    def call(self, method, path, body=None):
        request = urllib.request.Request(
            self.base + path,
            method=method,
            data=json.dumps(body).encode() if body is not None else None,
            headers={"Content-Type": "application/json", "X-Sparrow-Request": "1"},
        )
        try:
            with self.opener.open(request, timeout=60) as response:
                return response.status, json.loads(response.read() or b"null")
        except urllib.error.HTTPError as error:
            return error.code, json.loads(error.read() or b"null")


def step(text):
    print(f"\n== {text}", flush=True)


def run_command(line, hide_python):
    env = dict(os.environ)
    if hide_python and not WINDOWS:
        # Only the operating system's own tools: exercises Sparrow's own Python.
        env["PATH"] = "/usr/bin:/bin:/usr/sbin:/sbin"
    if WINDOWS:
        shell = shutil.which("powershell") or shutil.which("pwsh")
        command = [shell, "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", line]
    else:
        command = ["sh", "-c", line]
    print("$", line, flush=True)
    return subprocess.run(command, env=env, timeout=900)


def node_dir():
    if WINDOWS:
        return Path(os.environ["LOCALAPPDATA"]) / "SparrowNode"
    if sys.platform == "darwin":
        return Path.home() / "Library/Application Support/SparrowNode"
    if os.geteuid() == 0:
        return Path("/var/lib/sparrow-node")
    return Path(os.getenv("XDG_DATA_HOME", Path.home() / ".local/share")) / "SparrowNode"


def remove_service():
    if WINDOWS:
        subprocess.run(["schtasks", "/End", "/TN", "Sparrow Node"], capture_output=True)
        subprocess.run(["schtasks", "/Delete", "/TN", "Sparrow Node", "/F"], capture_output=True)
    elif sys.platform == "darwin":
        subprocess.run(["launchctl", "bootout", f"gui/{os.getuid()}/com.sparrow.node"], capture_output=True)
        (Path.home() / "Library/LaunchAgents/com.sparrow.node.plist").unlink(missing_ok=True)
    else:
        subprocess.run(["systemctl", "--user", "disable", "--now", "sparrow-node"], capture_output=True)
        subprocess.run(["pkill", "-f", "SparrowNode/app/sparrow-node.py"], capture_output=True)
        subprocess.run(
            ["sh", "-c", "crontab -l 2>/dev/null | grep -v sparrow-node.py | crontab -"],
            capture_output=True,
        )


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--no-system-python", action="store_true",
                        help="Hide this computer's Python so setup fetches its own.")
    args = parser.parse_args()
    state = Path(tempfile.mkdtemp(prefix="sparrow-pairing-"))
    port = free_port()
    base = f"http://127.0.0.1:{port}"
    env = {**os.environ, "SPARROW_DATA_DIR": str(state / "server"),
           "ANTHROPIC_API_KEY": "", "OPENAI_API_KEY": "", "TMDB_API_KEY": ""}
    log = open(state / "server.log", "w")
    server = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "backend.main:app", "--host", "127.0.0.1", "--port", str(port)],
        cwd=ROOT, env=env, stdout=log, stderr=subprocess.STDOUT,
    )
    client = Client(base)
    ok = False
    try:
        step("Starting Sparrow")
        for _ in range(120):
            try:
                if client.call("GET", "/api/v1/auth/status")[0] == 200:
                    break
            except OSError:
                pass
            time.sleep(1)
        else:
            raise SystemExit("Sparrow didn't start.")
        code = (state / "server/owner-setup-code").read_text().strip()
        status, _ = client.call("POST", "/api/v1/auth/bootstrap", {
            "username": "owner", "password": "pairing-check-123", "name": "Owner", "setup_code": code})
        assert status == 200, status

        step("Pairing this computer from one command")
        status, enrollment = client.call("POST", "/api/v1/admin/nodes/enroll", {})
        assert status == 200, enrollment
        kind = "ps1" if WINDOWS else "sh"
        url = f"{base}/api/v1/node/setup/{enrollment['code']}.{kind}"
        line = f"irm {url} | iex" if WINDOWS else f"curl -fsSL {url} | sh"
        started = time.monotonic()
        finished = run_command(line, args.no_system_python)
        print(f"(command finished in {time.monotonic() - started:.0f}s, exit {finished.returncode})")
        node_id = enrollment["node_id"]
        for _ in range(90):
            status, pairing = client.call("GET", f"/api/v1/admin/nodes/pairing/{node_id}")
            if pairing.get("stage", "").startswith("failed:"):
                raise SystemExit(f"Setup reported {pairing['stage']}.")
            if pairing.get("stage") == "running" and (pairing.get("node") or {}).get("online"):
                break
            time.sleep(2)
        else:
            raise SystemExit(f"The node didn't connect: {pairing}")
        node = pairing["node"]
        print(f"Paired as {node['name']!r} on {node['capabilities']['platform']}; folders choosable: "
              f"{node['capabilities'].get('folders')}")
        assert node["capabilities"].get("folders"), node

        step("Choosing its folders from Sparrow")
        status, places = client.call("GET", f"/api/v1/admin/storage/browse?node_id={node_id}")
        assert status == 200 and places["folders"], places
        print("Places:", ", ".join(p["name"] for p in places["folders"]))
        home = next((p for p in places["folders"] if p["name"] == "Home"), places["folders"][0])
        status, made = client.call("POST", "/api/v1/admin/storage/browse/folder",
                                   {"node_id": node_id, "path": home["path"], "name": "SparrowPairingCheck"})
        assert status == 200, made
        parent = made["path"]
        for name in ("Library", "Incoming"):
            status, folder = client.call("POST", "/api/v1/admin/storage/browse/folder",
                                         {"node_id": node_id, "path": parent, "name": name})
            assert status == 200, folder
        sep = "\\" if WINDOWS else "/"
        status, refused = client.call("POST", f"/api/v1/admin/nodes/{node_id}/folders", {
            "libraries": [parent + sep + "Library"], "staging": parent + sep + "Library" + sep + "In"})
        assert status == 422 and "inside Library" in refused["detail"], refused
        status, chosen = client.call("POST", f"/api/v1/admin/nodes/{node_id}/folders", {
            "libraries": [parent + sep + "Library"], "staging": parent + sep + "Incoming"})
        assert status == 200, chosen
        roots = {r["id"]: r for r in chosen["capabilities"]["roots"]}
        assert roots["library"]["available"] and roots["staging"]["available"], roots
        assert Path(parent, "Incoming", ".sparrow-root-id").is_file()
        print("Library:", roots["library"]["label"])
        print("Incoming:", roots["staging"]["label"])
        status, listing = client.call(
            "GET", f"/api/v1/admin/storage/browse?node_id={node_id}&path={urllib.request.quote(parent)}")
        assert [f["name"] for f in listing["folders"]] == ["Incoming", "Library"], listing
        ok = True
        step(f"Passed on {platform.system()} {platform.release()}")
    finally:
        if not ok:
            print("\n-- server log --")
            print((state / "server.log").read_text()[-4000:])
            logs = node_dir() / "logs"
            for path in logs.glob("*") if logs.is_dir() else []:
                print(f"-- {path} --")
                print(path.read_text(errors="replace")[-3000:])
        remove_service()
        server.terminate()
        server.wait(30)
        shutil.rmtree(Path.home() / "SparrowPairingCheck", ignore_errors=True)
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
