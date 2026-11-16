"""SQLite task log. Every file the agent touches is one row."""
import sqlite3
import threading
import time

ACTIVE = ("scheduled", "doing", "review")
IGNORED = ("skipped", "undone")

_COLUMNS = {
    "status", "category", "dest", "method", "confidence", "reason", "error",
    "not_before", "attempts", "done_at", "content_hash", "src", "fingerprint",
}
REMOVED = ("gone", "deleted")

# Columns added after the first release. Old databases get them added in place,
# so existing history is kept as is.
_ADDED_COLUMNS = {"content_hash": "TEXT"}

SCHEMA = """
CREATE TABLE IF NOT EXISTS tasks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    src TEXT NOT NULL,
    name TEXT NOT NULL,
    source_name TEXT,
    dest_root TEXT,
    fingerprint TEXT,
    status TEXT NOT NULL,
    category TEXT,
    dest TEXT,
    method TEXT,
    confidence REAL,
    reason TEXT,
    error TEXT,
    attempts INTEGER DEFAULT 0,
    not_before REAL DEFAULT 0,
    created REAL,
    updated REAL,
    done_at REAL
);
CREATE INDEX IF NOT EXISTS idx_status ON tasks(status);
CREATE INDEX IF NOT EXISTS idx_src ON tasks(src);
CREATE INDEX IF NOT EXISTS idx_fp ON tasks(fingerprint);
CREATE INDEX IF NOT EXISTS idx_dest ON tasks(dest);
"""


class DB:
    def __init__(self, path):
        self.conn = sqlite3.connect(str(path), check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.lock = threading.Lock()
        with self.lock:
            self.conn.executescript(SCHEMA)
            have = {r[1] for r in self.conn.execute("PRAGMA table_info(tasks)")}
            for col, ctype in _ADDED_COLUMNS.items():
                if col not in have:
                    self.conn.execute(f"ALTER TABLE tasks ADD COLUMN {col} {ctype}")
            self.conn.execute("CREATE INDEX IF NOT EXISTS idx_hash ON tasks(content_hash)")
            self.conn.commit()

    def _q(self, sql, args=()):
        with self.lock:
            cur = self.conn.execute(sql, args)
            self.conn.commit()
            return cur

    def _all(self, sql, args=()):
        with self.lock:
            return [dict(r) for r in self.conn.execute(sql, args).fetchall()]

    def _one(self, sql, args=()):
        rows = self._all(sql, args)
        return rows[0] if rows else None

    # --- writes ---
    def add(self, src, name, source_name, dest_root, fingerprint, not_before):
        now = time.time()
        cur = self._q(
            "INSERT INTO tasks (src, name, source_name, dest_root, fingerprint, status,"
            " not_before, created, updated) VALUES (?,?,?,?,?, 'scheduled', ?, ?, ?)",
            (src, name, source_name, dest_root, fingerprint, not_before, now, now),
        )
        return cur.lastrowid

    def update(self, task_id, **fields):
        fields = {k: v for k, v in fields.items() if k in _COLUMNS}
        if not fields:
            return
        fields["updated"] = time.time()
        cols = ", ".join(f"{k}=?" for k in fields)
        self._q(f"UPDATE tasks SET {cols} WHERE id=?", (*fields.values(), task_id))

    def add_found(self, path, name, source_name, dest_root, category, content_hash, mtime):
        """Record a file that was already sitting in a Sorted folder (sorted earlier or by hand)."""
        self._q(
            "INSERT INTO tasks (src, name, source_name, dest_root, status, category, dest, method,"
            " confidence, reason, content_hash, created, updated, done_at)"
            " VALUES (?,?,?,?, 'done', ?, ?, 'found', 1.0, 'Already in the Sorted folder', ?, ?, ?, ?)",
            (path, name, source_name, dest_root, category, path, content_hash, mtime, mtime, mtime),
        )

    def reset_doing(self):
        """After a crash, anything stuck in 'doing' goes back to the queue."""
        self._q("UPDATE tasks SET status='scheduled' WHERE status='doing'")

    # --- reads ---
    def get(self, task_id):
        return self._one("SELECT * FROM tasks WHERE id=?", (task_id,))

    def next_ready(self, now):
        return self._one(
            "SELECT * FROM tasks WHERE status='scheduled' AND not_before<=?"
            " ORDER BY not_before LIMIT 1",
            (now,),
        )

    def has_active(self, src):
        q = f"SELECT 1 FROM tasks WHERE src=? AND status IN ({','.join('?' * len(ACTIVE))}) LIMIT 1"
        return self._one(q, (src, *ACTIVE)) is not None

    def is_ignored(self, fingerprint):
        q = f"SELECT 1 FROM tasks WHERE fingerprint=? AND status IN ({','.join('?' * len(IGNORED))}) LIMIT 1"
        return self._one(q, (fingerprint, *IGNORED)) is not None

    def done_rows(self):
        return self._all("SELECT id, dest, content_hash, category, method FROM tasks WHERE status='done'")

    def waiting_rows(self):
        return self._all("SELECT id, src FROM tasks WHERE status IN ('scheduled','review','failed')")

    def delete(self, task_id):
        self._q("DELETE FROM tasks WHERE id=?", (task_id,))

    def clear_removed(self):
        return self._q(f"DELETE FROM tasks WHERE status IN ({','.join('?' * len(REMOVED))})",
                       REMOVED).rowcount

    def known_dests(self):
        with self.lock:
            return {r[0] for r in self.conn.execute(
                "SELECT dest FROM tasks WHERE status='done' AND dest IS NOT NULL")}

    def remembered(self, content_hash, exclude_id=None):
        """A decision already made for an identical file (same content)."""
        if not content_hash:
            return None
        return self._one(
            "SELECT category, dest, method FROM tasks WHERE content_hash=? AND id!=?"
            " AND status='done' AND category IS NOT NULL AND method!='fallback'"
            " ORDER BY (method='found'), updated DESC LIMIT 1",
            (content_hash, exclude_id or -1),
        )

    def by_status(self, status, limit=200):
        return self._all(
            "SELECT * FROM tasks WHERE status=? ORDER BY updated DESC LIMIT ?", (status, limit)
        )

    def recent(self, limit=40):
        return self._all("SELECT * FROM tasks WHERE method IS NULL OR method!='found'"
                         " ORDER BY updated DESC LIMIT ?", (limit,))

    def stats(self):
        counts = {r["status"]: r["n"] for r in self._all(
            "SELECT status, COUNT(*) AS n FROM tasks GROUP BY status")}
        start_of_day = time.mktime(time.localtime()[:3] + (0, 0, 0, 0, 0, -1))
        today = self._one(
            "SELECT COUNT(*) AS n FROM tasks WHERE status='done' AND method!='found' AND done_at>=?",
            (start_of_day,),
        )["n"]
        ai = self._one("SELECT COUNT(*) AS n FROM tasks WHERE status='done' AND method='ai'")["n"]
        found = self._one("SELECT COUNT(*) AS n FROM tasks WHERE status='done' AND method='found'")["n"]
        remembered = self._one("SELECT COUNT(*) AS n FROM tasks WHERE status='done' AND method='memory'")["n"]
        # "found" files were already sorted before the agent saw them: shown separately,
        # not counted as the agent's own work.
        return {
            "total": sum(counts.values()) - found,
            "done": counts.get("done", 0) - found,
            "doing": counts.get("doing", 0),
            "scheduled": counts.get("scheduled", 0),
            "review": counts.get("review", 0),
            "failed": counts.get("failed", 0),
            "skipped": counts.get("skipped", 0) + counts.get("undone", 0),
            "removed": counts.get("gone", 0) + counts.get("deleted", 0),
            "done_today": today,
            "done_by_ai": ai,
            "found": found,
            "remembered": remembered,
        }
