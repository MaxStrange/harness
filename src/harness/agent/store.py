"""Session storage (SH1, SH2, P14): one SQLite database with FTS5 full-text search."""

from __future__ import annotations

import json
import logging
import sqlite3
import threading
import time
import uuid
from dataclasses import dataclass
from pathlib import Path

from harness.model.types import Message

log = logging.getLogger(__name__)

SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    cwd TEXT NOT NULL,
    created REAL NOT NULL,
    updated REAL NOT NULL,
    tasks TEXT
);
CREATE TABLE IF NOT EXISTS projects (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    root_dir TEXT,
    instructions TEXT NOT NULL DEFAULT '',
    created REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    seq INTEGER NOT NULL,
    role TEXT NOT NULL,
    content TEXT NOT NULL,
    payload TEXT NOT NULL,
    created REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS messages_session ON messages(session_id, seq);
CREATE TABLE IF NOT EXISTS skill_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    message_seq INTEGER NOT NULL,
    call_id TEXT NOT NULL,
    skill TEXT NOT NULL,
    ok INTEGER NOT NULL,
    handoff TEXT,
    error TEXT,
    created REAL NOT NULL
);
"""
FTS_SCHEMA = """
CREATE VIRTUAL TABLE IF NOT EXISTS messages_fts USING fts5(content, content='messages', content_rowid='id');
CREATE TRIGGER IF NOT EXISTS messages_ai AFTER INSERT ON messages BEGIN
    INSERT INTO messages_fts(rowid, content) VALUES (new.id, new.content);
END;
CREATE TRIGGER IF NOT EXISTS messages_ad AFTER DELETE ON messages BEGIN
    INSERT INTO messages_fts(messages_fts, rowid, content) VALUES ('delete', old.id, old.content);
END;
"""


@dataclass
class SessionRecord:
    id: str
    title: str
    cwd: str
    created: float
    updated: float
    project_id: str | None = None


@dataclass
class ProjectRecord:
    id: str
    name: str
    root_dir: str | None
    instructions: str
    created: float


@dataclass
class SearchHit:
    session_id: str
    title: str
    snippet: str
    role: str
    seq: int


@dataclass
class SkillEvent:
    message_seq: int
    call_id: str
    skill: str
    ok: bool
    handoff: dict | None
    error: str | None


class SessionStore:
    """Thread-safe (one connection guarded by a lock; the UI and the agent thread share it)."""

    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self.path), check_same_thread=False, isolation_level=None)
        self._conn.row_factory = sqlite3.Row
        self._lock = threading.RLock()
        with self._lock:
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA foreign_keys=ON")
            self._conn.executescript(SCHEMA)
            self._migrate()
            self.fts = self._init_fts()

    def _migrate(self) -> None:
        columns = {row["name"] for row in self._conn.execute("PRAGMA table_info(sessions)")}
        if "project_id" not in columns:
            self._conn.execute("ALTER TABLE sessions ADD COLUMN project_id TEXT")

    def _init_fts(self) -> bool:
        try:
            self._conn.executescript(FTS_SCHEMA)
            return True
        except sqlite3.OperationalError as exc:
            log.warning("FTS5 unavailable (%s); search falls back to LIKE", exc)
            return False

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    # -- sessions ----------------------------------------------------------

    def create_session(
        self, cwd: str, title: str = "New session", project_id: str | None = None
    ) -> SessionRecord:
        now = time.time()
        record = SessionRecord(uuid.uuid4().hex[:12], title, cwd, now, now, project_id)
        with self._lock:
            self._conn.execute(
                "INSERT INTO sessions(id, title, cwd, created, updated, project_id) "
                "VALUES (?,?,?,?,?,?)",
                (record.id, title, cwd, now, now, project_id),
            )
        return record

    def set_session_project(self, session_id: str, project_id: str | None) -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE sessions SET project_id=? WHERE id=?", (project_id, session_id)
            )

    # -- projects ----------------------------------------------------------

    def create_project(
        self, name: str, root_dir: str | None = None, instructions: str = ""
    ) -> ProjectRecord:
        record = ProjectRecord(
            uuid.uuid4().hex[:12], name, root_dir or None, instructions, time.time()
        )
        with self._lock:
            self._conn.execute(
                "INSERT INTO projects(id, name, root_dir, instructions, created) VALUES (?,?,?,?,?)",
                (record.id, record.name, record.root_dir, record.instructions, record.created),
            )
        return record

    def get_project(self, project_id: str | None) -> ProjectRecord | None:
        if project_id is None:
            return None
        with self._lock:
            row = self._conn.execute("SELECT * FROM projects WHERE id=?", (project_id,)).fetchone()
        return _project(row) if row else None

    def list_projects(self) -> list[ProjectRecord]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM projects ORDER BY name COLLATE NOCASE"
            ).fetchall()
        return [_project(r) for r in rows]

    def update_project(
        self,
        project_id: str,
        *,
        name: str | None = None,
        root_dir: str | None = None,
        instructions: str | None = None,
    ) -> None:
        with self._lock:
            if name is not None:
                self._conn.execute("UPDATE projects SET name=? WHERE id=?", (name, project_id))
            if root_dir is not None:
                self._conn.execute(
                    "UPDATE projects SET root_dir=? WHERE id=?", (root_dir or None, project_id)
                )
            if instructions is not None:
                self._conn.execute(
                    "UPDATE projects SET instructions=? WHERE id=?", (instructions, project_id)
                )

    def delete_project(self, project_id: str) -> None:
        """Remove the project; its sessions stay, unassigned."""
        with self._lock:
            self._conn.execute(
                "UPDATE sessions SET project_id=NULL WHERE project_id=?", (project_id,)
            )
            self._conn.execute("DELETE FROM projects WHERE id=?", (project_id,))

    def get_session(self, session_id: str) -> SessionRecord | None:
        with self._lock:
            row = self._conn.execute("SELECT * FROM sessions WHERE id=?", (session_id,)).fetchone()
        return _record(row) if row else None

    def list_sessions(self, limit: int = 200) -> list[SessionRecord]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM sessions ORDER BY updated DESC LIMIT ?", (limit,)
            ).fetchall()
        return [_record(r) for r in rows]

    def rename_session(self, session_id: str, title: str) -> None:
        with self._lock:
            self._conn.execute("UPDATE sessions SET title=? WHERE id=?", (title, session_id))

    def set_cwd(self, session_id: str, cwd: str) -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE sessions SET cwd=?, updated=? WHERE id=?", (cwd, time.time(), session_id)
            )

    def delete_session(self, session_id: str) -> None:
        with self._lock:
            self._conn.execute("DELETE FROM messages WHERE session_id=?", (session_id,))
            self._conn.execute("DELETE FROM skill_events WHERE session_id=?", (session_id,))
            self._conn.execute("DELETE FROM sessions WHERE id=?", (session_id,))

    def delete_all(self) -> int:
        """Factory reset: remove every session and message. Returns how many sessions went."""
        with self._lock:
            count = self._conn.execute("SELECT COUNT(*) FROM sessions").fetchone()[0]
            self._conn.execute("DELETE FROM messages")
            self._conn.execute("DELETE FROM skill_events")
            self._conn.execute("DELETE FROM sessions")
            self._conn.execute("DELETE FROM projects")
            self._conn.execute("VACUUM")
        return count

    def save_tasks(self, session_id: str, tasks_json: str) -> None:
        with self._lock:
            self._conn.execute("UPDATE sessions SET tasks=? WHERE id=?", (tasks_json, session_id))

    def load_tasks(self, session_id: str) -> str | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT tasks FROM sessions WHERE id=?", (session_id,)
            ).fetchone()
        return row["tasks"] if row else None

    # -- messages ----------------------------------------------------------

    def append_message(self, session_id: str, message: Message) -> int:
        """Append and return the message's sequence number within the session."""
        with self._lock:
            row = self._conn.execute(
                "SELECT COALESCE(MAX(seq), -1) + 1 AS n FROM messages WHERE session_id=?",
                (session_id,),
            ).fetchone()
            seq = row["n"]
            now = time.time()
            self._conn.execute(
                "INSERT INTO messages(session_id, seq, role, content, payload, created) VALUES (?,?,?,?,?,?)",
                (session_id, seq, message.role, message.content, message.to_json(), now),
            )
            self._conn.execute("UPDATE sessions SET updated=? WHERE id=?", (now, session_id))
        return seq

    def replace_messages(self, session_id: str, messages: list[Message]) -> None:
        """Used after compaction: the stored transcript becomes the compacted one."""
        with self._lock:
            self._conn.execute("BEGIN")
            try:
                self._conn.execute("DELETE FROM messages WHERE session_id=?", (session_id,))
                now = time.time()
                for seq, message in enumerate(messages):
                    self._conn.execute(
                        "INSERT INTO messages(session_id, seq, role, content, payload, created) VALUES (?,?,?,?,?,?)",
                        (session_id, seq, message.role, message.content, message.to_json(), now),
                    )
                self._conn.execute("COMMIT")
            except Exception:
                self._conn.execute("ROLLBACK")
                raise

    def load_messages(self, session_id: str) -> list[Message]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT payload FROM messages WHERE session_id=? ORDER BY seq", (session_id,)
            ).fetchall()
        return [Message.from_json(r["payload"]) for r in rows]

    def message_count(self, session_id: str) -> int:
        with self._lock:
            return self._conn.execute(
                "SELECT COUNT(*) FROM messages WHERE session_id=?", (session_id,)
            ).fetchone()[0]

    # -- skill events ------------------------------------------------------

    def record_skill_event(
        self,
        session_id: str,
        message_seq: int,
        call_id: str,
        skill: str,
        ok: bool,
        handoff: dict | None,
        error: str | None,
    ) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO skill_events(session_id, message_seq, call_id, skill, ok, handoff, error, created) VALUES (?,?,?,?,?,?,?,?)",
                (
                    session_id,
                    message_seq,
                    call_id,
                    skill,
                    int(ok),
                    json.dumps(handoff) if handoff else None,
                    error,
                    time.time(),
                ),
            )

    def skill_events(self, session_id: str) -> list[SkillEvent]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM skill_events WHERE session_id=? ORDER BY id", (session_id,)
            ).fetchall()
        return [
            SkillEvent(
                r["message_seq"],
                r["call_id"],
                r["skill"],
                bool(r["ok"]),
                json.loads(r["handoff"]) if r["handoff"] else None,
                r["error"],
            )
            for r in rows
        ]

    # -- search ------------------------------------------------------------

    def search(self, query: str, limit: int = 50) -> list[SearchHit]:
        query = query.strip()
        if not query:
            return []
        with self._lock:
            if self.fts:
                try:
                    rows = self._conn.execute(
                        "SELECT m.session_id, s.title, m.role, m.seq, snippet(messages_fts, 0, '[', ']', '...', 12) AS snip "
                        "FROM messages_fts JOIN messages m ON m.id = messages_fts.rowid JOIN sessions s ON s.id = m.session_id "
                        "WHERE messages_fts MATCH ? AND m.role IN ('user', 'assistant') ORDER BY rank LIMIT ?",
                        (_fts_query(query), limit),
                    ).fetchall()
                    return [
                        SearchHit(r["session_id"], r["title"], r["snip"], r["role"], r["seq"])
                        for r in rows
                    ]
                except sqlite3.OperationalError as exc:
                    log.info("FTS query failed (%s); falling back to LIKE", exc)
            rows = self._conn.execute(
                "SELECT m.session_id, s.title, m.role, m.seq, substr(m.content, 1, 160) AS snip FROM messages m JOIN sessions s ON s.id = m.session_id "
                "WHERE m.content LIKE ? AND m.role IN ('user', 'assistant') ORDER BY m.created DESC LIMIT ?",
                (f"%{query}%", limit),
            ).fetchall()
        return [
            SearchHit(r["session_id"], r["title"], r["snip"], r["role"], r["seq"]) for r in rows
        ]


def _fts_query(text: str) -> str:
    """Quote each word so punctuation in the user's query cannot break FTS syntax."""
    words = [w.replace('"', '""') for w in text.split()]
    return " ".join(f'"{w}"' for w in words)


def _record(row: sqlite3.Row) -> SessionRecord:
    return SessionRecord(
        row["id"], row["title"], row["cwd"], row["created"], row["updated"], row["project_id"]
    )


def _project(row: sqlite3.Row) -> ProjectRecord:
    return ProjectRecord(
        row["id"], row["name"], row["root_dir"], row["instructions"], row["created"]
    )
