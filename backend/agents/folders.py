"""Folders a person can choose for storage, on this server or a paired computer.

Browsing starts from a computer's own places: its home, drives and mounted
disks (in Docker, the mounted folders). Nothing outside them is listed, made or
accepted as a library or incoming folder. Standard library only: this runs on
storage computers too.
"""

from __future__ import annotations

import os
import re
import shutil
import sys
from pathlib import Path

from ..configuration import bind_mounts, in_container

LIMIT = 500
FORBIDDEN = re.compile(r'[\\/:*?"<>|\x00-\x1f]')
RESERVED = re.compile(r"^(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\.|$)", re.I)
# Top-level folders that hold the operating system, not media.
SYSTEM = {
    "proc", "sys", "dev", "run", "boot", "etc", "usr", "bin", "sbin", "lib",
    "lib32", "lib64", "libx32", "var", "snap", "tmp", "opt", "app", "efi",
    "System", "Library", "private", "cores",
}
WINDOWS_SYSTEM = {
    "windows", "program files", "program files (x86)", "programdata",
    "$recycle.bin", "system volume information", "recovery", "perflogs",
    "msocache", "config.msi", "$windows.~bt", "$windows.~ws", "intel",
}
DISKS = {
    "ext2", "ext3", "ext4", "xfs", "btrfs", "zfs", "f2fs", "jfs", "ntfs",
    "ntfs3", "fuseblk", "exfat", "vfat", "hfsplus", "apfs", "nfs", "nfs4",
    "cifs", "smb3", "9p", "virtiofs",
}


class FolderError(ValueError):
    pass


def _system(path: Path) -> bool:
    parts = path.parts
    return len(parts) < 2 or parts[1] in SYSTEM


def _linux_disks(mountinfo: str | None = None) -> list[Path]:
    """Mounted disks and shares, such as /media/me/Films or /mnt/nas."""
    if mountinfo is None:
        try:
            mountinfo = Path("/proc/self/mountinfo").read_text()
        except OSError:
            return []
    found = []
    for line in mountinfo.splitlines():
        head, _, tail = line.partition(" - ")
        fields, kind = head.split(), tail.split()[:1]
        if len(fields) < 5 or not kind or kind[0] not in DISKS:
            continue
        point = Path(re.sub(r"\\([0-7]{3})", lambda m: chr(int(m.group(1), 8)), fields[4]))
        if not _system(point):
            found.append(point)
    return found


def _windows_drives() -> list[Path]:
    import ctypes
    import string

    kernel = ctypes.windll.kernel32
    mask = kernel.GetLogicalDrives()
    drives = []
    for number, letter in enumerate(string.ascii_uppercase):
        root = f"{letter}:\\"
        # Removable and fixed drives; network drives belong to a signed-in user.
        if mask >> number & 1 and kernel.GetDriveTypeW(root) in (2, 3):
            drives.append(Path(root))
    return drives


def starting_points(configured=None, exclude=()) -> list[Path]:
    """Where browsing begins on this computer, most useful first."""
    mounted = (
        [point for _, point in bind_mounts() if not _system(point) and point.is_dir()]
        if os.name != "nt" and not configured and in_container()
        else []
    )
    if configured:
        candidates = [Path(p).expanduser() for p in configured]
    elif os.name == "nt":
        candidates = [Path.home(), *_windows_drives()]
    elif mounted:
        # Docker shows only the folders it mounts, such as /media.
        candidates = mounted
    else:
        candidates = [Path.home()]
        if sys.platform == "darwin":
            volumes = Path("/Volumes")
            if volumes.is_dir():
                candidates += sorted(v for v in volumes.iterdir() if not v.is_symlink())
        else:
            candidates += [Path(p) for p in ("/media", "/mnt", "/srv")]
            candidates += _linux_disks()
    excluded = [Path(p).resolve() for p in exclude if p]
    points: list[Path] = []
    for candidate in candidates:
        try:
            point = candidate.resolve()
        except OSError:
            continue
        if not point.is_dir() or point in points:
            continue
        if os.name != "nt" and point == Path(point.anchor):
            continue  # Never the whole computer.
        if any(point == e or e in point.parents for e in excluded):
            continue  # Sparrow's own state isn't a place for media.
        points.append(point)
    return points


def label(point: Path) -> str:
    if point == Path.home():
        return "Home"
    return str(point)


def within(value: str, points) -> Path:
    """The chosen folder, if it lies inside one of this computer's places."""
    if not value or "\x00" in value:
        raise FolderError("Choose a folder.")
    path = Path(value).expanduser()
    if not path.is_absolute():
        raise FolderError("Choose a folder from the list.")
    resolved = path.resolve(strict=False)
    for point in points:
        if resolved == point or point in resolved.parents:
            return resolved
    raise FolderError(f"{value} isn’t in a place Sparrow can use on this computer.")


def _hidden(entry: os.DirEntry, parent: Path) -> bool:
    name = entry.name
    if name.startswith((".", "$")) or name == "lost+found":
        return True
    if os.name == "nt":
        if parent == Path(parent.anchor) and name.lower() in WINDOWS_SYSTEM:
            return True
        try:
            # Hidden or system folders.
            return bool(entry.stat(follow_symlinks=False).st_file_attributes & 0x6)
        except (OSError, AttributeError):
            return False
    return False


def _access(path: Path) -> dict:
    return {
        "readable": os.access(path, os.R_OK | os.X_OK),
        "writable": os.access(path, os.W_OK),
    }


def browse(value: str, points) -> dict:
    """One folder's subfolders, or this computer's places when value is empty."""
    if not value:
        rows = []
        for point in points:
            row = {"name": label(point), "path": str(point), **_access(point)}
            try:
                row["free_bytes"] = shutil.disk_usage(point).free
            except OSError:
                pass
            rows.append(row)
        return {"path": "", "parent": None, "crumbs": [], "folders": rows, "more": 0}
    folder = within(value, points)
    if not folder.is_dir():
        raise FolderError("That folder isn’t there any more.")
    point = max(
        (p for p in points if folder == p or p in folder.parents),
        key=lambda p: len(p.parts),
    )
    crumbs = [{"name": label(point), "path": str(point)}]
    current = point
    for part in folder.parts[len(point.parts) :]:
        current = current / part
        crumbs.append({"name": part, "path": str(current)})
    try:
        entries = sorted(os.scandir(folder), key=lambda e: e.name.lower())
        here = folder.stat().st_dev
    except PermissionError:
        raise FolderError("Sparrow can’t open this folder.") from None
    rows, more = [], 0
    for entry in entries:
        try:
            if entry.is_symlink() or not entry.is_dir(follow_symlinks=False):
                continue
        except OSError:
            continue
        if _hidden(entry, folder):
            continue
        if len(rows) >= LIMIT:
            more += 1
            continue
        path = Path(entry.path)
        row = {"name": entry.name, "path": str(path), **_access(path)}
        try:
            if entry.stat(follow_symlinks=False).st_dev != here:
                row["free_bytes"] = shutil.disk_usage(path).free  # Another disk.
        except OSError:
            pass
        rows.append(row)
    usage = None
    try:
        usage = shutil.disk_usage(folder).free
    except OSError:
        pass
    return {
        "path": str(folder),
        "parent": str(folder.parent) if folder != point else "",
        "crumbs": crumbs,
        "folders": rows,
        "more": more,
        **_access(folder),
        **({"free_bytes": usage} if usage is not None else {}),
    }


def name_problem(name: str) -> str:
    """Why a new folder name won't work, in words; empty when it's fine."""
    if not name:
        return "Name the folder."
    if len(name) > 100:
        return "Use a shorter name."
    if FORBIDDEN.search(name):
        return 'Folder names can’t contain / \\ : * ? " < > |'
    if name.startswith("."):
        return "Folder names can’t start with a dot."
    if name.endswith((" ", ".")):
        return "Folder names can’t end with a space or a dot."
    if RESERVED.match(name):
        return f"{name} is reserved on Windows."
    return ""


def make_folder(parent: str, name: str, points) -> str:
    name = (name or "").strip()
    problem = name_problem(name)
    if problem:
        raise FolderError(problem)
    base = within(parent, points)
    if not base.is_dir():
        raise FolderError("That folder isn’t there any more.")
    target = base / name
    if target.exists():
        raise FolderError(f"There’s already a folder called {name} here.")
    try:
        target.mkdir()
        if os.name != "nt":
            target.chmod(0o775)  # The household's media group may manage it too.
    except PermissionError:
        raise FolderError("Sparrow can’t create a folder here.") from None
    except OSError as exc:
        raise FolderError(f"Sparrow couldn’t create {name}: {exc.strerror or exc}.") from None
    return str(target)


def overlap(named) -> str:
    """Why chosen folders can't work together, in words; empty when they can.

    named is [(label, path)], libraries first and incoming last.
    """
    cleaned = [(n, Path(p)) for n, p in named if p]
    for number, (first_name, first) in enumerate(cleaned):
        for second_name, second in cleaned[number + 1 :]:
            if first == second:
                return f"{first_name} and {second_name} can’t be the same folder."
            inner, outer = (
                (second_name, first_name) if first in second.parents
                else (first_name, second_name) if second in first.parents
                else (None, None)
            )
            if not inner:
                continue
            if inner == "Incoming":
                return (
                    f"Incoming can’t be inside {outer}: downloads in progress "
                    "would look like part of your collection."
                )
            if outer == "Incoming":
                return (
                    f"{inner} can’t be inside Incoming: finished titles would "
                    "be treated as downloads."
                )
            return f"{inner} can’t be inside {outer}."
    return ""


def labelled(libraries, staging):
    names = ["Library" if n == 0 else f"Library {n + 1}" for n in range(len(libraries))]
    return [*zip(names, libraries), ("Incoming", staging)]
