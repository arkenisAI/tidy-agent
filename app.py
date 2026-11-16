"""Tidy Agent - a local Downloads & Desktop organizer with a dashboard.

Run:  python app.py              (opens the dashboard window)
      python app.py --background (no window; open http://127.0.0.1:8765 any time)
"""
import argparse
import importlib.util
import json
import mimetypes
import socket
import sys
import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import quote, unquote, urlparse

from organizer.agent import Agent
from organizer.classifier import (CATEGORIES, all_categories, clean_category_name,
                                  custom_categories, ollama_status)
from organizer.config import (APP_DIR, DATA_DIR, DB_PATH, backup_db, import_old_data,
                              load_config, migrate_legacy, save_config)
from organizer.db import DB

STATIC = APP_DIR / "static"
MASCOT_STATES = ("idle", "working", "happy", "waiting", "sleep")
# Accept a few natural names for each dashboard state in file names.
STATE_ALIASES = {
    "idle": "idle", "stand": "idle", "default": "idle", "rest": "idle",
    "working": "working", "work": "working", "busy": "working", "sorting": "working", "run": "working", "rolling": "working", "roll": "working",
    "happy": "happy", "done": "happy", "jump": "happy", "celebrate": "happy", "success": "happy",
    "waiting": "waiting", "wait": "waiting", "review": "waiting", "ask": "waiting", "question": "waiting",
    "sleep": "sleep", "sleeping": "sleep", "nap": "sleep", "paused": "sleep", "pause": "sleep",
}
MASCOT_EXT = (".png", ".webp", ".gif", ".apng", ".jpg", ".jpeg", ".svg")
NOT_MASCOTS = {"options", "sheet", "contact", "contactsheet"}  # e.g. mascot_options.png from Step 1


def discover_mascots():
    """Find mascot sets in static/assets.

    mascot.png / mascot_<state>.png             -> the "default" set
    mascot_<name>.png / mascot_<name>_<state>.png -> a set called <name> (e.g. mascot_boxbot_idle.png)
    """
    assets = STATIC / "assets"
    sets = {}
    if not assets.is_dir():
        return []
    for f in sorted(assets.iterdir(), key=lambda p: (p.suffix.lower() != ".png", p.name.lower())):
        if not f.is_file() or f.suffix.lower() not in MASCOT_EXT:
            continue
        stem = f.stem
        low = stem.lower()
        if low == "mascot":
            name, st = "default", None
        elif low.startswith("mascot_") or low.startswith("mascot-"):
            rest = stem[7:]
            head, _, tail = rest.replace("-", "_").rpartition("_")
            if rest.lower() in STATE_ALIASES:
                name, st = "default", STATE_ALIASES[rest.lower()]
            elif head and tail.lower() in STATE_ALIASES:
                name, st = head, STATE_ALIASES[tail.lower()]
            else:
                name, st = rest, None
        else:
            continue
        key = name.lower()
        if key in NOT_MASCOTS:
            continue
        m = sets.setdefault(key, {
            "id": key,
            "label": "Default" if key == "default" else name.replace("_", " ").replace("-", " ").title(),
            "base": None, "states": {},
        })
        url = "/assets/" + quote(f.name)
        if st:
            m["states"].setdefault(st, url)
        elif not m["base"]:
            m["base"] = url
    return sorted(sets.values(), key=lambda m: (m["id"] != "default", m["label"]))


def port_in_use(port):
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        return s.connect_ex(("127.0.0.1", port)) == 0


def make_handler(agent: Agent, db: DB, cfg: dict):
    def mascot_info():
        sets = discover_mascots()
        chosen = cfg.get("mascot") or ""
        ids = {m["id"] for m in sets}
        if chosen != "builtin" and chosen not in ids:
            chosen = sets[0]["id"] if sets else "builtin"
        return {"sets": sets, "selected": chosen}

    def state():
        if cfg.get("backend") == "laya":
            ai = {"online": True, "model_ready": importlib.util.find_spec("laya") is not None, "model": "Laya"}
        else:
            ai = ollama_status(cfg)
        return {
            "stats": db.stats(),
            "mode": cfg["mode"],
            "paused": agent.paused,
            "current": agent.current,
            "next_scan_in": max(0, int(agent.next_scan_at - time.time())),
            "last_done_ago": int(time.time() - agent.last_done_at) if agent.last_done_at else None,
            "ai": ai,
            "backend": cfg.get("backend", "ollama"),
            "ollama_model": cfg["model"],
            "sources": [{"name": s["name"], "path": s["path"]} for s in cfg["sources"]],
            "review": db.by_status("review", limit=100),
            "recent": db.recent(40),
            "categories": all_categories(cfg),
            "builtin_categories": CATEGORIES,
            "custom_categories": custom_categories(cfg),
            "mascot": mascot_info(),
            "data_dir": str(DATA_DIR),
        }

    port = int(cfg["port"])
    hosts = {f"127.0.0.1:{port}", f"localhost:{port}"}

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def _local(self):
            # Host check blocks DNS-rebinding pages; Origin check blocks other websites posting to us.
            if self.headers.get("Host") not in hosts:
                return False
            origin = self.headers.get("Origin")
            return origin is None or origin in {"http://" + h for h in hosts}

        def _send(self, code, body, ctype="application/json"):
            data = body if isinstance(body, bytes) else json.dumps(body).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            if not self._local():
                return self._send(403, {"error": "forbidden"})
            path = urlparse(self.path).path
            if path == "/api/state":
                return self._send(200, state())
            path = unquote(path)
            if path in ("/", "/index.html"):
                path = "/index.html"
            file = (STATIC / path.lstrip("/")).resolve()
            if not str(file).startswith(str(STATIC.resolve())) or not file.is_file():
                return self._send(404, {"error": "not found"})
            ctype = mimetypes.guess_type(str(file))[0] or "application/octet-stream"
            return self._send(200, file.read_bytes(), ctype)

        def do_POST(self):
            # Only accept calls from the dashboard itself (blocks other websites and DNS rebinding).
            if not self._local():
                return self._send(403, {"error": "forbidden"})
            try:
                length = int(self.headers.get("Content-Length") or 0)
                if not 0 <= length <= 64 * 1024:
                    return self._send(413, {"error": "request too large"})
                body = json.loads(self.rfile.read(length) or b"{}")
            except ValueError:
                body = {}
            if not isinstance(body, dict):
                body = {}
            path = urlparse(self.path).path
            tid = body.get("id")
            ok, msg = True, ""
            if path == "/api/scan":
                agent.scan_now()
            elif path == "/api/pause":
                agent.paused = True
            elif path == "/api/resume":
                agent.paused = False
                agent.wake()
            elif path == "/api/mode":
                if body.get("mode") in ("auto", "review"):
                    cfg["mode"] = body["mode"]
                    save_config(cfg)
            elif path == "/api/backend":
                if body.get("backend") in ("ollama", "laya"):
                    cfg["backend"] = body["backend"]
                    save_config(cfg)
                else:
                    ok, msg = False, "Unknown AI backend"
            elif path in ("/api/approve", "/api/set_category") and body.get("category") \
                    and body["category"] not in all_categories(cfg):
                ok, msg = False, "Unknown category"   # never let a request pick a path outside Sorted/
            elif path == "/api/approve":
                ok, msg = agent.approve(tid, body.get("category"))
            elif path == "/api/approve_all":
                msg = f"Moved {agent.approve_all()} files"
            elif path == "/api/reject":
                ok, msg = agent.reject(tid)
            elif path == "/api/retry":
                ok, msg = agent.retry(tid)
            elif path == "/api/undo":
                ok, msg = agent.undo(tid)
            elif path == "/api/open":
                ok, msg = agent.open_location(tid)
            elif path == "/api/set_category":
                ok, msg = agent.set_category(tid, body.get("category"))
            elif path == "/api/refresh":
                ok, msg = agent.refresh()
            elif path == "/api/trash":
                ok, msg = agent.trash(tid)
            elif path == "/api/remove":
                ok, msg = agent.remove_entry(tid)
            elif path == "/api/clear_removed":
                ok, msg = agent.clear_removed()
            elif path == "/api/mascot":
                cfg["mascot"] = str(body.get("id") or "builtin")[:60]
                save_config(cfg)
            elif path == "/api/category_add":
                try:
                    name = clean_category_name(body.get("name"))
                except ValueError as exc:
                    return self._send(200, {"ok": False, "message": str(exc)})
                if name in all_categories(cfg):
                    return self._send(200, {"ok": False, "message": f"{name} already exists"})
                cfg.setdefault("custom_categories", []).append({
                    "name": name,
                    "hint": str(body.get("hint") or "").strip()[:200],
                    "keywords": [k.strip()[:40] for k in str(body.get("keywords") or "").split(",") if k.strip()][:30],
                    "extensions": [e.strip()[:12] for e in str(body.get("extensions") or "").split(",") if e.strip()][:30],
                })
                save_config(cfg)
                return self._send(200, {"ok": True, "message": f"Added {name}", "name": name})
            elif path == "/api/category_remove":
                name = body.get("name")
                before = len(cfg.get("custom_categories", []))
                cfg["custom_categories"] = [c for c in cfg.get("custom_categories", [])
                                            if c.get("name") != name]
                save_config(cfg)
                ok = len(cfg["custom_categories"]) < before
                msg = f"Removed {name}. Files already in that folder stay where they are." if ok else "Not found"
            else:
                return self._send(404, {"error": "unknown action"})
            return self._send(200, {"ok": ok, "message": msg})

    return Handler


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--background", action="store_true", help="run without a window")
    parser.add_argument("--import-from", metavar="OLD_APP_FOLDER",
                        help="copy history + settings from an older Tidy Agent folder, then exit")
    parser.add_argument("--where", action="store_true", help="print where history/settings are stored")
    args = parser.parse_args()

    if args.where:
        print(DATA_DIR)
        return
    if args.import_from:
        print(import_old_data(Path(args.import_from)))
        return

    note = migrate_legacy()
    if note:
        print(f"[data] moved from the old app folder: {note}")
    cfg = load_config()
    port = int(cfg["port"])
    url = f"http://127.0.0.1:{port}"

    if port_in_use(port):  # already running - just show it
        print(f"Tidy Agent is already running at {url}")
        if not args.background:
            webbrowser.open(url)
        return

    backup_db()
    db = DB(DB_PATH)
    print(f"History & settings: {DATA_DIR}")
    agent = Agent(cfg, db, APP_DIR)
    agent.start()
    server = ThreadingHTTPServer(("127.0.0.1", port), make_handler(agent, db, cfg))
    print(f"Tidy Agent running at {url}  (mode: {cfg['mode']}, backend: {cfg.get('backend', 'ollama')})")

    if args.background:
        server.serve_forever()
        return

    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        import webview  # pip install pywebview -> real app window

        webview.create_window("Tidy Agent", url, width=1200, height=820, min_size=(900, 640))
        webview.start()
    except ImportError:
        webbrowser.open(url)
        print("Tip: pip install pywebview to get a native window. Ctrl+C to quit.")
        try:
            while True:
                time.sleep(3600)
        except KeyboardInterrupt:
            pass


if __name__ == "__main__":
    sys.exit(main())
