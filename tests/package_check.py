"""Exercise the actual Linux image with bundled media tools and persistent data."""

import json
import os
import subprocess
import time
import httpx

name = os.getenv("SPARROW_TEST_CONTAINER", "sparrow-check")
base = os.getenv("SPARROW_TEST_URL", "http://127.0.0.1:8892")


def inside(*args):
    return subprocess.check_output(["docker", "exec", name, *args], text=True).strip()


def main():
    with httpx.Client(
        base_url=base, timeout=120, headers={"X-Sparrow-Request": "1"}
    ) as browser:
        for _ in range(40):
            try:
                if browser.get("/api/v1/auth/status").status_code == 200:
                    break
            except httpx.HTTPError:
                pass
            time.sleep(1)
        status = browser.get("/api/v1/auth/status").json()
        if status["needs_setup"]:
            code = inside("cat", "/data/owner-setup-code")
            response = browser.post(
                "/api/v1/auth/bootstrap",
                json={
                    "username": "package-owner",
                    "password": "package-fixture-password",
                    "name": "Package Owner",
                    "setup_code": code,
                },
            )
        else:
            response = browser.post(
                "/api/v1/auth/login",
                json={
                    "username": "package-owner",
                    "password": "package-fixture-password",
                },
            )
        assert response.status_code == 200, response.text
        setup = browser.patch(
            "/api/v1/admin/onboarding",
            json={"mode": "library", "step": "storage", "deferred": True},
        )
        assert setup.status_code == 200 and not setup.json()["complete"]
        response = browser.patch(
            "/api/v1/admin/config",
            json={"library_dir": "/data/library", "staging_dir": "/data/incoming"},
        )
        assert response.status_code == 200, response.text
        inside(
            "python",
            "-c",
            "from pathlib import Path;Path('/data/library').mkdir(exist_ok=True);Path('/data/incoming').mkdir(exist_ok=True)",
        )
        inside(
            "/opt/sparrow/bin/ffmpeg",
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
            "-movflags",
            "+faststart",
            "-y",
            "/data/library/Package Fixture.mp4",
        )
        scan = browser.post("/api/v1/admin/imports/preview", json={"node_id": "local"})
        assert scan.status_code == 200, scan.text
        candidate = scan.json()["candidates"][0]
        imported = browser.post(
            "/api/v1/admin/imports/" + scan.json()["id"] + "/confirm",
            json={
                "selections": [
                    {
                        "id": candidate["id"],
                        "title": "Package Fixture",
                        "media_type": "movie",
                    }
                ]
            },
        )
        assert imported.status_code == 200, imported.text
        asset = imported.json()["imported"][0]["asset_id"]
        started = browser.post("/api/v1/playback", json={"asset_id": asset})
        assert started.status_code == 200, started.text
        session = started.json()
        stream = browser.get(session["url"], headers={"Range": "bytes=0-127"})
        assert stream.status_code == 206 and len(stream.content) == 128
        fallback = browser.post(
            "/api/v1/playback", json={"asset_id": asset, "force_transcode": True}
        ).json()
        segment = browser.get(fallback["url"].replace("index.m3u8", "0.ts"))
        assert segment.status_code == 200, segment.text[:200]
        saved = browser.put(
            "/api/v1/playback/" + session["id"] + "/progress",
            json={"position": 5, "sequence": 1},
        )
        # The newer converted session owns progress.
        assert saved.json()["saved"] is False
        browser.put(
            "/api/v1/playback/" + fallback["id"] + "/progress",
            json={"position": 5, "sequence": 1},
        )
        subprocess.run(
            ["docker", "restart", name], check=True, stdout=subprocess.DEVNULL
        )
        for _ in range(40):
            try:
                if browser.get("/api/v1/auth/status").status_code == 200:
                    break
            except httpx.HTTPError:
                pass
            time.sleep(1)
        resumed = browser.post("/api/v1/playback", json={"asset_id": asset})
        assert resumed.status_code == 200, resumed.text
        assert resumed.json()["position"] == 5
        setup = browser.get("/api/v1/admin/onboarding").json()
        assert setup["mode"] == "library" and setup["step"] == "storage"
        assert setup["deferred"] and not setup["complete"]
        finished = browser.post("/api/v1/admin/onboarding/finish")
        assert finished.status_code == 200 and finished.json()["complete"]
        assert "javascript" in browser.get("/sw.js").headers["content-type"]
        assert browser.get("/manifest.webmanifest").json()["display"] == "standalone"
        nodes = browser.get("/api/v1/nodes").json()
        assert (
            nodes[0]["capabilities"]["probe"] and nodes[0]["capabilities"]["transcode"]
        )
        inside(
            "python",
            "-c",
            "from faster_whisper import WhisperModel;WhisperModel('/opt/sparrow/models/whisper-base',device='cpu',compute_type='int8',local_files_only=True)",
        )
        print(
            json.dumps(
                {
                    "container": name,
                    "import": True,
                    "direct_range": True,
                    "converted_segment": True,
                    "restart_resume": True,
                    "onboarding_resume": True,
                    "pwa_assets": True,
                    "bundled_speech_model": True,
                }
            )
        )


if __name__ == "__main__":
    main()
