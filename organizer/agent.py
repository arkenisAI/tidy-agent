"""The background worker: scans folders on a schedule, classifies, moves, undoes."""
import os
import shutil
import sys
import threading
import time
import traceback
from pathlib import Path

from .classifier import LLMError, classify

HIDDEN_ATTRS = 0x2 | 0x4  # Windows FILE_ATTRIBUTE_HIDDEN | FILE_ATTRIBUTE_SYSTEM
UF_HIDDEN = 0x8000        # macOS chflags hidden
MAX_ATTEMPTS = 3


def quick_hash(path: Path) -> str:
    """Fast content fingerprint: size + first and last 1 MB. Same file -> same hash, even renamed."""
    import hashlib

    try:
        size = path.stat().st_size
        h = hashlib.sha1(str(size).encode())
        with open(path, "rb") as fh:
            h.update(fh.read(1 << 20))
            if size > 2 << 20:
                fh.seek(-(1 << 20), os.SEEK_END)
                h.update(fh.read(1 << 20))
        return h.hexdigest()
    except OSError:
        return ""


def _norm(p) -> str:
    return os.path.normcase(os.path.abspath(str(p)))


def send_to_trash(path: Path) -> None:
    """Move a file to the Recycle Bin / Trash (recoverable), never a permanent delete."""
    try:
        from send2trash import send2trash  # pip install send2trash (in requirements.txt)

        send2trash(str(path))
        return
    except ImportError:
        pass
    import subprocess

    if sys.platform == "win32":
        import ctypes
        from ctypes import wintypes

        class SHFILEOPSTRUCTW(ctypes.Structure):
            _fields_ = [("hwnd", wintypes.HWND), ("wFunc", wintypes.UINT),
                        ("pFrom", wintypes.LPCWSTR), ("pTo", wintypes.LPCWSTR),
                        ("fFlags", ctypes.c_uint16), ("fAnyOperationsAborted", wintypes.BOOL),
                        ("hNameMappings", ctypes.c_void_p), ("lpszProgressTitle", wintypes.LPCWSTR)]

        FO_DELETE, FOF_ALLOWUNDO, FOF_NOCONFIRMATION, FOF_SILENT, FOF_NOERRORUI = 3, 0x40, 0x10, 0x4, 0x400
        op = SHFILEOPSTRUCTW(None, FO_DELETE, str(path) + "\0", None,
                             FOF_ALLOWUNDO | FOF_NOCONFIRMATION | FOF_SILENT | FOF_NOERRORUI,
                             False, None, None)
        rc = ctypes.windll.shell32.SHFileOperationW(ctypes.byref(op))
        if rc or op.fAnyOperationsAborted:
            raise OSError(f"Windows couldn't move it to the Recycle Bin (code {rc})")
    elif sys.platform == "darwin":
        escaped = str(path).replace("\\", "\\\\").replace('"', '\\"')
        subprocess.run(["osascript", "-e", f'tell application "Finder" to delete POSIX file "{escaped}"'],
                       check=True, capture_output=True)
    else:
        subprocess.run(["gio", "trash", str(path)], check=True, capture_output=True)


def unique_path(path: Path) -> Path:
    if not path.exists():
        return path
    i = 1
    while True:
        candidate = path.with_name(f"{path.stem} ({i}){path.suffix}")
        if not candidate.exists():
            return candidate
        i += 1


class Agent:
    def __init__(self, cfg, db, app_dir: Path):
        self.cfg = cfg
        self.db = db
        self.app_dir = app_dir.resolve()
        self.paused = False
        self.next_scan_at = time.time() + 3
        self.last_scan_at = 0.0
        self.last_done_at = 0.0
        self.current = None
        self._wake = threading.Event()
        self._move_lock = threading.Lock()

    # ---------- lifecycle ----------
    def start(self):
        self.db.reset_doing()
        threading.Thread(target=self._loop, name="tidy-agent", daemon=True).start()

    def _sorted_files(self):
        """Every file currently inside the Sorted folders -> (path, category, source, root)."""
        present = {}
        for source in self.cfg["sources"]:
            root = Path(source["dest_root"])
            if not root.is_dir():
                continue
            for dirpath, dirnames, filenames in os.walk(root):
                rel = Path(dirpath).relative_to(root)
                if len(rel.parts) >= 3:  # categories are at most 2 levels deep
                    dirnames[:] = []
                dirnames[:] = [d for d in dirnames if not d.startswith(".")]
                if not rel.parts:
                    continue  # loose files directly in Sorted/ have no category
                category = "/".join(rel.parts[:2])
                for name in filenames:
                    low = name.lower()
                    if low.startswith((".", "~$")) or low in {"desktop.ini", "thumbs.db", ".ds_store"}:
                        continue
                    full = str((Path(dirpath) / name).resolve())
                    present[_norm(full)] = (full, name, category, source["name"], str(root))
        return present

    def sync_with_disk(self):
        """Make the history match the folders as they are right now.

        - files already in Sorted that the history doesn't know (sorted by hand) -> added as "found"
        - a sorted file you moved to another Sorted sub-folder -> its entry follows it
        - a sorted file you deleted or moved out of Sorted -> marked "gone"
        - a sorted file you dragged back into Downloads/Desktop -> left alone, not re-sorted
        - a queued/waiting file you deleted or moved -> marked "gone"
        """
        result = {"added": 0, "moved": 0, "gone": 0, "kept_out": 0}
        with self._move_lock:
            present = self._sorted_files()
            missing, known = [], set()
            for row in self.db.done_rows():
                key = _norm(row["dest"]) if row["dest"] else None
                if key and key in present:
                    known.add(key)
                else:
                    missing.append(row)
            by_hash = {}
            for row in missing:
                if row["content_hash"]:
                    by_hash.setdefault(row["content_hash"], []).append(row)

            for key, (full, name, category, source_name, root) in present.items():
                if key in known:
                    continue
                path = Path(full)
                h = quick_hash(path)
                if by_hash.get(h):  # same content as a file that vanished -> it was moved
                    row = by_hash[h].pop(0)
                    missing.remove(row)
                    self.db.update(row["id"], dest=full, category=category,
                                   reason=f"You moved it to {category}")
                    result["moved"] += 1
                    continue
                try:
                    mtime = path.stat().st_mtime
                except OSError:
                    continue
                self.db.add_found(full, name, source_name, root, category, h, mtime)
                result["added"] += 1

            if any(r["content_hash"] for r in missing):
                # Did you take some of them back out into Downloads/Desktop? Then respect that.
                wanted = {r["content_hash"]: r for r in missing if r["content_hash"]}
                for source in self.cfg["sources"]:
                    folder = Path(source["path"])
                    try:
                        entries = [e for e in os.scandir(folder) if e.is_file(follow_symlinks=False)]
                    except OSError:
                        continue
                    for entry in entries:
                        h = quick_hash(Path(entry.path))
                        row = wanted.pop(h, None)
                        if not row:
                            continue
                        st = entry.stat()
                        src = str(Path(entry.path).resolve())
                        self.db.update(row["id"], status="undone", src=src,
                                       fingerprint=f"{src}|{st.st_size}|{int(st.st_mtime)}",
                                       reason="You took it out of Sorted - I'll leave it there")
                        missing.remove(row)
                        result["kept_out"] += 1

            for row in missing:
                self.db.update(row["id"], status="gone", reason="Deleted or moved out of Sorted")
                result["gone"] += 1

            for row in self.db.waiting_rows():
                if not Path(row["src"]).exists():
                    self.db.update(row["id"], status="gone", reason="Deleted or moved before sorting")
                    result["gone"] += 1
        if any(result.values()):
            print(f"[sync] {result}")
        return result

    def refresh(self):
        """Dashboard 'Refresh': re-read the folders now, then look for new files."""
        result = self.sync_with_disk()
        self.scan(sync=False)
        self.wake()
        parts = []
        if result["added"]:
            parts.append(f"{result['added']} already-sorted found")
        if result["moved"]:
            parts.append(f"{result['moved']} moved by you")
        if result["gone"]:
            parts.append(f"{result['gone']} no longer there")
        if result["kept_out"]:
            parts.append(f"{result['kept_out']} taken out of Sorted")
        return True, ("Up to date: " + ", ".join(parts)) if parts else "Everything is up to date"

    def wake(self):
        self._wake.set()

    def _loop(self):
        while True:
            try:
                if not self.paused:
                    if time.time() >= self.next_scan_at:
                        self.scan()
                    task = self.db.next_ready(time.time())
                    if task:
                        self.process(task)
                        continue
            except Exception:
                traceback.print_exc()
            self._wake.wait(1.0)
            self._wake.clear()

    # ---------- scanning ----------
    def _ignored(self, entry) -> bool:
        low = entry.name.lower()
        if low in {n.lower() for n in self.cfg["ignore_names"]}:
            return True
        if low.startswith((".", "~$")):
            return True
        if os.path.splitext(low)[1] in {e.lower() for e in self.cfg["ignore_extensions"]}:
            return True
        try:
            st = entry.stat(follow_symlinks=False)
            if getattr(st, "st_file_attributes", 0) & HIDDEN_ATTRS:  # Windows hidden/system
                return True
            if getattr(st, "st_flags", 0) & UF_HIDDEN:  # macOS "hidden" flag
                return True
        except OSError:
            return True
        return False

    def scan(self, sync=True):
        if sync:
            try:
                self.sync_with_disk()
            except Exception:
                traceback.print_exc()
        now = time.time()
        self.last_scan_at = now
        self.next_scan_at = now + max(1, float(self.cfg["scan_interval_minutes"])) * 60
        settle = float(self.cfg["settle_seconds"])
        added = 0
        for source in self.cfg["sources"]:
            folder = Path(source["path"])
            if not folder.is_dir():
                continue
            try:
                entries = list(os.scandir(folder))
            except OSError:
                continue
            for entry in entries:
                # Top-level files only. Folders (including "Sorted") are never touched.
                if not entry.is_file(follow_symlinks=False) or self._ignored(entry):
                    continue
                src = str(Path(entry.path).resolve())
                if src.startswith(str(self.app_dir)):
                    continue
                st = entry.stat()
                fingerprint = f"{src}|{st.st_size}|{int(st.st_mtime)}"
                if self.db.has_active(src) or self.db.is_ignored(fingerprint):
                    continue
                self.db.add(src, entry.name, source["name"], source["dest_root"],
                            fingerprint, max(now, st.st_mtime + settle))
                added += 1
        return added

    def scan_now(self):
        self.next_scan_at = 0
        self.wake()

    # ---------- processing ----------
    def process(self, task):
        tid = task["id"]
        src = Path(task["src"])
        if not src.exists():
            self.db.update(tid, status="gone", reason="File was moved or deleted before sorting")
            return
        settle = float(self.cfg["settle_seconds"])
        mtime = src.stat().st_mtime
        if time.time() - mtime < settle:  # still being written - check again later
            self.db.update(tid, status="scheduled", not_before=mtime + settle)
            return

        self.db.update(tid, status="doing")
        self.current = task["name"]
        try:
            content_hash = quick_hash(src)
            self.db.update(tid, content_hash=content_hash)
            memory = self.db.remembered(content_hash, exclude_id=tid)
            try:
                if memory:
                    # Identical file was sorted before - reuse that decision, no AI call.
                    category, confidence, method = memory["category"], 1.0, "memory"
                    reason = f"Same file is already in {memory['category']}"
                else:
                    category, confidence, method, reason = classify(src, self.cfg)
            except LLMError as exc:
                attempts = (task["attempts"] or 0) + 1
                if attempts < MAX_ATTEMPTS:
                    self.db.update(tid, status="scheduled", attempts=attempts,
                                   not_before=time.time() + 120, error=str(exc))
                    return
                category, confidence, method = "Other", 0.0, "fallback"
                if src.suffix.lower() in {".pdf", ".doc", ".docx", ".txt", ".md", ".rtf", ".odt"}:
                    category = "Documents/Other"
                reason = "AI was unavailable - please check this one"

            dest = Path(task["dest_root"]) / category / src.name
            self.db.update(tid, category=category, confidence=confidence, method=method,
                           reason=reason, dest=str(dest), error=None, attempts=0)
            auto = self.cfg["mode"] == "auto"
            if auto and (method in ("rule", "memory") or confidence >= float(self.cfg["confidence_threshold"])):
                self._move(tid, from_auto=True)
            else:
                self.db.update(tid, status="review")
        finally:
            self.current = None

    def _move(self, tid, from_auto=False):
        with self._move_lock:
            task = self.db.get(tid)
            if not task or not task["dest"]:
                return False, "Nothing to move"
            src = Path(task["src"])
            if not src.exists():
                self.db.update(tid, status="gone", reason="File no longer exists")
                return False, "File no longer exists"
            dest = unique_path(Path(task["dest"]))
            try:
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(src), str(dest))
            except PermissionError:
                msg = "File is open in another program"
                attempts = (task["attempts"] or 0) + 1
                if from_auto and attempts < MAX_ATTEMPTS:
                    self.db.update(tid, status="scheduled", attempts=attempts,
                                   not_before=time.time() + 300, error=msg)
                else:
                    self.db.update(tid, status="failed", error=msg + " - close it and press Retry")
                return False, msg
            except OSError as exc:
                self.db.update(tid, status="failed", error=str(exc))
                return False, str(exc)
            self.db.update(tid, status="done", dest=str(dest), done_at=time.time(), error=None)
            self.last_done_at = time.time()
            return True, str(dest)

    # ---------- user actions ----------
    def approve(self, tid, category=None):
        task = self.db.get(tid)
        if not task or task["status"] not in ("review", "failed"):
            return False, "Not waiting for approval"
        if category:
            self.db.update(tid, category=category,
                           dest=str(Path(task["dest_root"]) / category / task["name"]))
        return self._move(tid)

    def approve_all(self):
        moved = 0
        for task in self.db.by_status("review", limit=1000):
            ok, _ = self._move(task["id"])
            moved += ok
        return moved

    def set_category(self, tid, category):
        """Remember the folder you picked in the review list (survives refreshes and restarts)."""
        task = self.db.get(tid)
        if not task or task["status"] not in ("review", "failed"):
            return False, "Not waiting for approval"
        if not category:
            return False, "Pick a category"
        self.db.update(tid, category=category, method="you", confidence=1.0,
                       reason="Folder picked by you",
                       dest=str(Path(task["dest_root"]) / category / task["name"]))
        return True, ""

    def _inside_watched(self, path: Path) -> bool:
        p = _norm(path)
        for s in self.cfg["sources"]:
            for root in (s["path"], s["dest_root"]):
                r = _norm(root)
                if p == r or p.startswith(r.rstrip("\\/") + os.sep):
                    return True
        return False

    def trash(self, tid):
        """Move the file to the Recycle Bin / Trash and mark the entry as deleted."""
        with self._move_lock:
            task = self.db.get(tid)
            if not task:
                return False, "Unknown file"
            if task["status"] == "doing":
                return False, "I'm working on that file right now - try again in a moment"
            path = Path(task["dest"] if task["status"] == "done" else task["src"])
            if not path.exists():
                self.db.update(tid, status="gone", reason="File was already gone")
                return False, "That file isn't there anymore"
            if not self._inside_watched(path):
                return False, "I only delete files inside the folders I watch"
            try:
                send_to_trash(path)
            except Exception as exc:
                return False, f"Couldn't delete it: {exc}"
            self.db.update(tid, status="deleted", reason="Moved to the Trash by you")
            return True, "Moved to the Trash"

    def remove_entry(self, tid):
        """Forget a gone/deleted/failed entry. Files are not touched."""
        task = self.db.get(tid)
        if not task:
            return False, "Unknown entry"
        if task["status"] not in ("gone", "deleted", "failed"):
            return False, "Only removed, deleted or failed entries can be cleared"
        self.db.delete(tid)
        return True, "Removed from the list"

    def clear_removed(self):
        n = self.db.clear_removed()
        return True, f"Cleared {n} entr{'y' if n == 1 else 'ies'}"

    def reject(self, tid):
        self.db.update(tid, status="skipped", reason="You chose to leave this file where it is")
        return True, "Skipped"

    def retry(self, tid):
        self.db.update(tid, status="scheduled", not_before=0, attempts=0, error=None)
        self.wake()
        return True, "Queued"

    def undo(self, tid):
        with self._move_lock:
            task = self.db.get(tid)
            if not task or task["status"] != "done":
                return False, "Only finished moves can be undone"
            if task["method"] == "found":
                return False, "This file was already in the Sorted folder - nothing to undo"
            current = Path(task["dest"])
            if not current.exists():
                return False, "The moved file isn't there anymore"
            back = unique_path(Path(task["src"]))
            try:
                back.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(current), str(back))
            except OSError as exc:
                return False, str(exc)
            self.db.update(tid, status="undone", reason="Moved back by you")
            return True, str(back)

    def open_location(self, tid):
        task = self.db.get(tid)
        if not task:
            return False, "Unknown task"
        path = Path(task["dest"] if task["status"] == "done" else task["src"])
        import subprocess

        try:
            if sys.platform == "win32":
                if path.exists():
                    subprocess.Popen(["explorer", "/select,", str(path)])
                else:
                    os.startfile(str(path.parent))  # noqa
            elif sys.platform == "darwin":
                # Reveal in Finder
                subprocess.Popen(["open", "-R", str(path)] if path.exists() else ["open", str(path.parent)])
            else:
                subprocess.Popen(["xdg-open", str(path.parent)])
        except OSError as exc:
            return False, str(exc)
        return True, str(path)
