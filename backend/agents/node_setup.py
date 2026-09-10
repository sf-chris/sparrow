"""Native Windows pairing UI and an unprivileged background service."""

import asyncio
import ctypes
import json
import os
import queue
import subprocess
import sys
import threading
from pathlib import Path
from types import SimpleNamespace
from .node_client import configure
from .node_executor import Executor

SERVICE = "SparrowNode"


def command(*args):
    result = subprocess.run(
        [str(a) for a in args],
        capture_output=True,
        text=True,
        timeout=120,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    if result.returncode:
        raise ValueError(
            "A Windows service or folder-permission operation failed. Check that the folders are writable and setup has administrator access."
        )
    return result.stdout


def elevated():
    if os.name != "nt":
        raise ValueError("This setup application is for Windows.")
    return bool(ctypes.windll.shell32.IsUserAnAdmin())


def service_xml(program, data):
    from xml.etree.ElementTree import Element, SubElement, tostring

    root = Element("service")
    for name, value in [
        ("id", SERVICE),
        ("name", "Sparrow storage"),
        (
            "description",
            "Keeps selected media folders available to your Sparrow server.",
        ),
        ("executable", str(program / "SparrowNode.exe")),
        (
            "arguments",
            subprocess.list2cmdline(["--config", str(data / "node.json"), "run"]),
        ),
        ("startmode", "Automatic"),
        ("delayedAutoStart", "true"),
        ("stoptimeout", "35 sec"),
        ("logpath", str(data / "logs")),
    ]:
        SubElement(root, name).text = value
    account = SubElement(root, "serviceaccount")
    SubElement(account, "domain").text = "NT AUTHORITY"
    SubElement(account, "user").text = "LocalService"
    SubElement(root, "onfailure", {"action": "restart", "delay": "30 sec"})
    SubElement(root, "log", {"mode": "roll-by-size"})
    SubElement(root[-1], "sizeThreshold").text = "1024"
    SubElement(root[-1], "keepFiles").text = "3"
    return tostring(root, encoding="unicode")


def setup(values, report, program=None):
    if not elevated():
        raise ValueError(
            "Open Sparrow Node Setup as administrator to install its background service."
        )
    program = Path(program) if program else Path(sys.executable).parent
    data = Path(os.getenv("PROGRAMDATA", "C:/ProgramData")) / SERVICE
    data.mkdir(parents=True, exist_ok=True)
    library = Path(values["library"])
    staging = Path(values["staging"])
    for folder in (library, staging):
        if not folder.is_absolute() or str(folder).startswith("\\\\"):
            raise ValueError(
                "Choose local drive folders. Network shares need their own service account and are not supported by this installer."
            )
        folder.mkdir(parents=True, exist_ok=True)
    wrapper = program / "SparrowService.exe"
    if not wrapper.exists():
        raise ValueError(
            "The service component is missing. Reinstall the complete Sparrow Node package."
        )
    exists = (
        subprocess.run(
            ["sc.exe", "query", SERVICE],
            capture_output=True,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        ).returncode
        == 0
    )
    if exists:
        report("Stopping the existing node while its configuration is updated…")
        subprocess.run(
            [str(wrapper), "stop"],
            capture_output=True,
            timeout=60,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    (program / "SparrowService.xml").write_text(
        service_xml(program, data), encoding="utf8"
    )
    if not exists:
        command(wrapper, "install")
    command("sc.exe", "sidtype", SERVICE, "unrestricted")
    # The service runs as LocalService but only its unique service SID receives
    # access to these folders. Pairing credentials are not readable by Users.
    command(
        "icacls.exe",
        data,
        "/inheritance:r",
        "/grant:r",
        "*S-1-5-18:(OI)(CI)F",
        "*S-1-5-32-544:(OI)(CI)F",
        f"NT SERVICE\\{SERVICE}:(OI)(CI)M",
    )
    for folder in (library, staging):
        command(
            "icacls.exe",
            folder,
            "/grant",
            f"NT SERVICE\\{SERVICE}:(OI)(CI)M",
            "/T",
            "/C",
        )
    downloader = data / "download-setup.json"
    try:
        downloader.write_text(
            json.dumps(
                {
                    "type": values["downloader"],
                    "host": "127.0.0.1",
                    "port": int(values["port"] or 8080),
                    "username": values["username"],
                    "password": values["password"],
                }
            ),
            encoding="utf8",
        )
        report("Checking the selected folders and pairing with Sparrow…")
        asyncio.run(
            configure(
                SimpleNamespace(
                    server=values["server"],
                    code=values["code"],
                    library=str(library),
                    staging=str(staging),
                    config=str(data / "node.json"),
                    node_id=None,
                    downloader_config=str(downloader),
                    allow_private_http=values["private"],
                )
            )
        )
    finally:
        downloader.unlink(missing_ok=True)
    command(wrapper, "start")
    report(
        "Paired. Sparrow storage now runs in the background and starts with Windows. You can close this window."
    )


def main():
    import tkinter as tk
    from tkinter import ttk, filedialog

    root = tk.Tk()
    root.title("Sparrow · Connect storage")
    root.geometry("620x740")
    root.minsize(500, 640)
    root.configure(bg="#17191b")
    style = ttk.Style(root)
    style.theme_use("clam")
    style.configure(
        ".", background="#17191b", foreground="#f4f0e9", font=("Segoe UI", 10)
    )
    style.configure(
        "TEntry", fieldbackground="#272a2d", foreground="#f4f0e9", padding=7
    )
    style.configure("TButton", padding=10)
    style.configure("TCheckbutton", background="#17191b")
    # Keep progress and the primary action visible on shorter/high-DPI screens.
    footer = ttk.Frame(root, padding=(24, 10, 24, 18))
    footer.pack(side="bottom", fill="x")
    body = ttk.Frame(root)
    body.pack(fill="both", expand=True)
    canvas = tk.Canvas(body, bg="#17191b", highlightthickness=0)
    scrollbar = ttk.Scrollbar(body, orient="vertical", command=canvas.yview)
    scrollbar.pack(side="right", fill="y")
    canvas.pack(side="left", fill="both", expand=True)
    canvas.configure(yscrollcommand=scrollbar.set)
    frame = ttk.Frame(canvas, padding=24)
    window = canvas.create_window((0, 0), window=frame, anchor="nw")
    frame.bind(
        "<Configure>", lambda _: canvas.configure(scrollregion=canvas.bbox("all"))
    )
    canvas.bind("<Configure>", lambda e: canvas.itemconfigure(window, width=e.width))
    root.bind(
        "<MouseWheel>", lambda e: canvas.yview_scroll(-int(e.delta / 120), "units")
    )
    ttk.Label(
        frame, text="Give your collection a home.", font=("Segoe UI", 21, "bold")
    ).pack(anchor="w")
    ttk.Label(
        frame,
        text="Pair this Windows machine with your Sparrow server.\nYour files stay in the folders you choose.",
        wraplength=540,
    ).pack(anchor="w", pady=(8, 20))
    fields = {}
    rows = {}
    for key, label, default in [
        ("server", "Sparrow server address", ""),
        ("code", "Pairing code from Storage settings", ""),
        ("library", "Movies and shows folder", ""),
        ("staging", "Separate folder for incoming downloads", ""),
        ("downloader", "Download app (optional)", "none"),
        ("port", "Download app port", "8080"),
        ("username", "Download app username", ""),
        ("password", "Download app password", ""),
    ]:
        row = ttk.Frame(frame)
        row.pack(fill="x", pady=4)
        ttk.Label(row, text=label).pack(anchor="w")
        variable = tk.StringVar(value=default)
        fields[key] = variable
        entry = (
            ttk.Combobox(
                row,
                textvariable=variable,
                values=("none", "qbittorrent", "transmission"),
                state="readonly",
            )
            if key == "downloader"
            else ttk.Entry(
                row, textvariable=variable, show="•" if key == "password" else ""
            )
        )
        entry.pack(side="left", fill="x", expand=True)
        if key in ("library", "staging"):

            def browse(variable=variable):
                chosen = filedialog.askdirectory(parent=root)
                if chosen:
                    variable.set(chosen)

            ttk.Button(row, text="Choose…", command=browse).pack(
                side="right", padx=(8, 0)
            )
    private = tk.BooleanVar(value=False)
    ttk.Checkbutton(
        frame,
        text="This is a trusted private network; allow its HTTP address",
        variable=private,
    ).pack(anchor="w", pady=10)
    status = tk.StringVar(
        value="Setup needs administrator access once to install the background service."
    )
    status_label = ttk.Label(footer, textvariable=status, wraplength=540)
    status_label.pack(anchor="w", pady=8)
    footer.bind(
        "<Configure>",
        lambda e: status_label.configure(wraplength=max(200, e.width - 48)),
    )
    events = queue.Queue()

    def start():
        values = {
            key: value.get().strip() if key != "password" else value.get()
            for key, value in fields.items()
        }
        values["private"] = private.get()
        if values["downloader"] not in ("none", "transmission", "qbittorrent"):
            status.set("Choose none, transmission or qbittorrent for the download app.")
            return
        button.state(["disabled"])

        def work():
            try:
                setup(values, lambda message: events.put(("progress", message)))
            except Exception as exc:
                events.put(
                    (
                        "error",
                        (
                            str(exc)
                            if isinstance(exc, ValueError)
                            else "Pairing failed. Check the address, current pairing code and folder permissions, then try again."
                        ),
                    )
                )
            finally:
                events.put(("done", ""))

        threading.Thread(target=work, daemon=True).start()

    button = ttk.Button(footer, text="Pair storage & start service", command=start)
    button.pack(anchor="e", pady=12)

    def poll():
        while not events.empty():
            kind, message = events.get()
            if kind == "done":
                button.state(["!disabled"])
            else:
                status.set(message)
        root.after(200, poll)

    poll()
    root.mainloop()
