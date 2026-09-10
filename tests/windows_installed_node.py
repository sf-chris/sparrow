"""Windows-only: installed EXE/service, real NTFS media, outbound HTTP and restart."""

import os
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import httpx
from backend.agents.node_setup import setup, command


def main():
    program = Path(sys.argv[1])
    root = Path(os.environ["SPARROW_WINDOWS_FIXTURE"])
    library = root / "library"
    staging = root / "incoming"
    library.mkdir(exist_ok=True)
    staging.mkdir(exist_ok=True)
    subprocess.run(
        [
            os.environ["SPARROW_FFMPEG"],
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            "testsrc2=s=320x240:r=24",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440",
            "-t",
            "12",
            "-c:v",
            "libx264",
            "-preset",
            "ultrafast",
            "-c:a",
            "aac",
            "-y",
            str(library / "Installed Fixture.mp4"),
        ],
        check=True,
    )
    with httpx.Client(
        base_url="http://127.0.0.1:8893",
        timeout=120,
        headers={"X-Sparrow-Request": "1"},
    ) as client:
        for _ in range(40):
            try:
                if client.get("/api/v1/auth/status").status_code == 200:
                    break
            except httpx.HTTPError:
                pass
            time.sleep(1)
        code = (
            (Path(os.environ["SPARROW_DATA_DIR"]) / "owner-setup-code")
            .read_text()
            .strip()
        )
        signed = client.post(
            "/api/v1/auth/bootstrap",
            json={
                "username": "native-owner",
                "name": "Native Owner",
                "password": "fixture-password-123",
                "setup_code": code,
            },
        )
        signed.raise_for_status()
        enrollment = client.post(
            "/api/v1/admin/nodes/enroll", json={"name": "Installed Windows node"}
        )
        enrollment.raise_for_status()
        setup(
            {
                "server": "http://127.0.0.1:8893",
                "code": enrollment.json()["code"],
                "library": str(library),
                "staging": str(staging),
                "downloader": "none",
                "port": "8080",
                "username": "",
                "password": "",
                "private": False,
            },
            print,
            program=program,
        )
        for _ in range(40):
            nodes = client.get("/api/v1/nodes").json()
            remote = next(
                (n for n in nodes if n["id"] != "local" and n["online"]), None
            )
            if remote:
                break
            time.sleep(1)
        assert remote, "The installed service never connected."
        assert remote["capabilities"]["subtitle_model"], "Speech model was not bundled."
        scan = client.post(
            "/api/v1/admin/imports/preview", json={"node_id": remote["id"]}
        )
        scan.raise_for_status()
        candidate = scan.json()["candidates"][0]
        imported = client.post(
            "/api/v1/admin/imports/" + scan.json()["id"] + "/confirm",
            json={
                "selections": [
                    {
                        "id": candidate["id"],
                        "title": "Installed Fixture",
                        "media_type": "movie",
                    }
                ]
            },
        )
        imported.raise_for_status()
        asset = imported.json()["imported"][0]["asset_id"]
        playback = client.post("/api/v1/playback", json={"asset_id": asset})
        playback.raise_for_status()
        session = playback.json()
        stream = client.get(session["url"], headers={"Range": "bytes=-128"})
        assert stream.status_code == 206 and len(stream.content) == 128
        client.put(
            "/api/v1/playback/" + session["id"] + "/progress",
            json={"position": 6, "sequence": 1},
        ).raise_for_status()
        command(program / "SparrowService.exe", "stop")
        command(program / "SparrowService.exe", "start")
        resumed = client.post(
            "/api/v1/playback", json={"asset_id": asset, "force_transcode": True}
        )
        resumed.raise_for_status()
        assert resumed.json()["position"] == 6
        converted = client.get(resumed.json()["url"].replace("index.m3u8", "0.ts"))
        assert converted.status_code == 200, converted.text[:500]
        print(
            "Installed node: pairing, bundled tools/model, authenticated seeking, service restart and resume passed."
        )


if __name__ == "__main__":
    main()
