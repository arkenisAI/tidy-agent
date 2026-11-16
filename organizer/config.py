"""Settings and data location for Tidy Agent.

History (tasks.db) and settings (config.json) live in a per-user folder OUTSIDE the
app folder, so unzipping a new version anywhere never loses them:

    Windows: %LOCALAPPDATA%\\TidyAgent
    macOS:   ~/Library/Application Support/TidyAgent
    Linux:   ~/.local/share/tidy-agent
    (override with the TIDY_DATA_DIR environment variable)

Older versions kept them in <app>/data - those are copied over automatically.
"""
import json
import os
import shutil
import sqlite3
import sys
import time
from pathlib import Path

APP_DIR = Path(__file__).resolve().parent.parent
LEGACY_DATA_DIR = APP_DIR / "data"


def user_data_dir() -> Path:
    if os.environ.get("TIDY_DATA_DIR"):
        return Path(os.environ["TIDY_DATA_DIR"]).expanduser()
    if sys.platform == "win32":
        base = Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local")
        return base / "TidyAgent"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "TidyAgent"
    return Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share") / "tidy-agent"


DATA_DIR = user_data_dir()
CONFIG_PATH = DATA_DIR / "config.json"
DB_PATH = DATA_DIR / "tasks.db"
BACKUP_DIR = DATA_DIR / "backups"


def _copy_sqlite(src: Path, dst: Path) -> None:
    """Consistent copy of a SQLite file (safe even if it was mid-write)."""
    dst.parent.mkdir(parents=True, exist_ok=True)
    s = sqlite3.connect(str(src))
    d = sqlite3.connect(str(dst))
    try:
        s.backup(d)
    finally:
        d.close()
        s.close()


def _db_row_count(path: Path) -> int:
    if not path.exists():
        return 0
    try:
        con = sqlite3.connect(str(path))
        try:
            return con.execute("SELECT COUNT(*) FROM tasks").fetchone()[0]
        finally:
            con.close()
    except sqlite3.Error:
        return 0


def import_old_data(old_dir: Path) -> str:
    """Bring history + settings from an older install (its data/ folder or the app folder itself).

    Only fills in what's missing: an existing, non-empty history is never overwritten
    (it is backed up and kept as is)."""
    old_dir = Path(old_dir).expanduser().resolve()
    if (old_dir / "data" / "tasks.db").exists():
        old_dir = old_dir / "data"
    old_db, old_cfg = old_dir / "tasks.db", old_dir / "config.json"
    if not old_db.exists() and not old_cfg.exists():
        return f"No Tidy Agent data found in {old_dir}"
    if old_dir == DATA_DIR.resolve():
        return "That is already the current data folder"
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    notes = []
    if old_db.exists():
        if _db_row_count(DB_PATH) == 0:
            _copy_sqlite(old_db, DB_PATH)
            notes.append(f"history imported ({_db_row_count(DB_PATH)} files)")
        else:
            notes.append("history NOT imported - this install already has its own history "
                         f"({_db_row_count(DB_PATH)} files). Old file left untouched at {old_db}")
    if old_cfg.exists() and not CONFIG_PATH.exists():
        shutil.copy2(old_cfg, CONFIG_PATH)
        notes.append("settings imported")
    return "; ".join(notes) or "nothing new to import"


def migrate_legacy() -> str:
    """First start of this version: pick up <app>/data from the old layout automatically."""
    if LEGACY_DATA_DIR.resolve() == DATA_DIR.resolve():
        return ""
    if (LEGACY_DATA_DIR / "tasks.db").exists() or (LEGACY_DATA_DIR / "config.json").exists():
        if _db_row_count(DB_PATH) == 0 or not CONFIG_PATH.exists():
            return import_old_data(LEGACY_DATA_DIR)
    return ""


def backup_db(keep: int = 7) -> None:
    """One backup per day of tasks.db, newest `keep` kept."""
    if not DB_PATH.exists():
        return
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    target = BACKUP_DIR / f"tasks-{time.strftime('%Y%m%d')}.db"
    if not target.exists():
        _copy_sqlite(DB_PATH, target)
    for old in sorted(BACKUP_DIR.glob("tasks-*.db"))[:-keep]:
        try:
            old.unlink()
        except OSError:
            pass

# Registry names for the real Desktop / Downloads locations (handles OneDrive redirection).
_SHELL_IDS = {
    "Desktop": "Desktop",
    "Downloads": "{374DE290-123F-4565-9164-39C4925E467B}",
}


def known_folder(name: str) -> Path:
    if sys.platform == "win32":
        try:
            import winreg

            key = winreg.OpenKey(
                winreg.HKEY_CURRENT_USER,
                r"Software\Microsoft\Windows\CurrentVersion\Explorer\User Shell Folders",
            )
            value, _ = winreg.QueryValueEx(key, _SHELL_IDS[name])
            path = Path(os.path.expandvars(value))
            if path.exists():
                return path
        except OSError:
            pass
    return Path.home() / name


def default_config() -> dict:
    downloads = known_folder("Downloads")
    desktop = known_folder("Desktop")
    return {
        # "ollama" (local LLM) or "laya" (small decision model, pip install laya)
        "backend": "ollama",
        "model": "qwen3.5:4b",
        "ollama_url": "http://127.0.0.1:11434",
        "num_ctx": 4096,
        "port": 8765,
        # "review": the agent proposes, you approve.  "auto": it moves files itself.
        "mode": "review",
        "scan_interval_minutes": 10,
        # Wait this long after a file was last modified (finishes downloads, saves).
        "settle_seconds": 60,
        # In auto mode, AI guesses below this confidence still go to review.
        "confidence_threshold": 0.6,
        "sources": [
            {"name": "Downloads", "path": str(downloads), "dest_root": str(downloads / "Sorted")},
            {"name": "Desktop", "path": str(desktop), "dest_root": str(desktop / "Sorted")},
        ],
        "ignore_extensions": [
            ".lnk", ".url", ".ini", ".crdownload", ".part", ".partial",
            ".download", ".tmp", ".opdownload", ".!ut",
            ".webloc", ".alias", ".localized",  # macOS shortcuts / system files
        ],
        "ignore_names": ["desktop.ini", "thumbs.db", ".ds_store", ".localized"],
        # Your own folders. keywords/extensions are optional instant rules; hint helps the AI.
        # e.g. {"name": "Work/Clients", "hint": "client briefs and quotes",
        #       "keywords": ["teklif", "quote"], "extensions": []}
        "custom_categories": [],
        # Mascot set picked in the dashboard ("" = first one found, "builtin" = the drawn robot)
        "mascot": "",
    }


def load_config() -> dict:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    cfg = default_config()
    if CONFIG_PATH.exists():
        try:
            saved = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
            cfg.update(saved)
        except (OSError, ValueError) as exc:
            print(f"[config] could not read {CONFIG_PATH}: {exc} - using defaults")
    save_config(cfg)
    return cfg


def save_config(cfg: dict) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    CONFIG_PATH.write_text(json.dumps(cfg, indent=2, ensure_ascii=False), encoding="utf-8")
