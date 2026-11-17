"""The local protocol. SQLite transactions serialize concurrent agents and hooks."""

from __future__ import annotations

import json
import os
import re
import sqlite3
import time
import uuid
from contextlib import contextmanager
from pathlib import Path


def default_home() -> Path:
    return Path(os.environ.get("CHIEF_HOME", "~/.local/share/chief-of-staff")).expanduser().resolve()


class Store:
    def __init__(self, home: Path | str):
        self.home = Path(home).expanduser().resolve()
        self.home.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.path = self.home / "chief.sqlite3"
        with self.db() as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.executescript("""
                CREATE TABLE IF NOT EXISTS sessions (
                    name TEXT PRIMARY KEY, role TEXT NOT NULL, cwd TEXT NOT NULL,
                    task TEXT NOT NULL, model TEXT, claude_id TEXT NOT NULL,
                    state TEXT NOT NULL DEFAULT 'starting', pane TEXT, sidebar TEXT,
                    launched INTEGER NOT NULL DEFAULT 0, demo INTEGER NOT NULL DEFAULT 0,
                    attended INTEGER NOT NULL DEFAULT 0,
                    summary TEXT NOT NULL DEFAULT '', next_step TEXT NOT NULL DEFAULT '',
                    blocker TEXT NOT NULL DEFAULT '', turn INTEGER NOT NULL DEFAULT 0,
                    reported_turn INTEGER NOT NULL DEFAULT -1,
                    created REAL NOT NULL, updated REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS messages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, sender TEXT NOT NULL,
                    recipient TEXT NOT NULL REFERENCES sessions(name), kind TEXT NOT NULL,
                    body TEXT NOT NULL, created REAL NOT NULL, delivered REAL, acked REAL
                );
                CREATE INDEX IF NOT EXISTS mailbox ON messages(recipient, delivered, acked);
                CREATE TABLE IF NOT EXISTS todos (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, session TEXT NOT NULL REFERENCES sessions(name),
                    text TEXT NOT NULL, done INTEGER NOT NULL DEFAULT 0, created REAL NOT NULL
                );
                CREATE UNIQUE INDEX IF NOT EXISTS open_todo ON todos(session, text) WHERE done=0;
            """)
        self.path.chmod(0o600)

    @contextmanager
    def db(self):
        db = sqlite3.connect(self.path, timeout=15, isolation_level=None)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        db.execute("PRAGMA busy_timeout=15000")
        try:
            yield db
        finally:
            db.close()

    def create(self, name, cwd, task="", model=None, demo=False):
        if not re.fullmatch(r"[a-z][a-z0-9-]{0,31}", name):
            raise ValueError(
                "Use a session name with 1–32 lowercase letters, digits, or hyphens; start with a letter."
            )
        cwd = Path(cwd).expanduser().resolve()
        if not cwd.is_dir():
            raise ValueError(f"Working directory does not exist: {cwd}")
        now = time.time()
        try:
            with self.db() as db:
                db.execute(
                    "INSERT INTO sessions(name,role,cwd,task,model,claude_id,demo,created,updated) "
                    "VALUES(?,?,?,?,?,?,?,?,?)",
                    (
                        name,
                        "chief" if name == "chief" else "agent",
                        str(cwd),
                        task,
                        model,
                        str(uuid.uuid4()),
                        int(demo),
                        now,
                        now,
                    ),
                )
        except sqlite3.IntegrityError as exc:
            raise ValueError(f"Session already exists: {name}") from exc
        return self.session(name)

    def session(self, name):
        with self.db() as db:
            row = db.execute("SELECT * FROM sessions WHERE name=?", (name,)).fetchone()
        if row is None:
            raise ValueError(f"Unknown session: {name}")
        return dict(row)

    def sessions(self):
        with self.db() as db:
            return [
                dict(r)
                for r in db.execute(
                    "SELECT s.*, (SELECT count(*) FROM messages m WHERE m.recipient=s.name "
                    "AND m.acked IS NULL) AS unread FROM sessions s ORDER BY role='chief' DESC, created"
                )
            ]

    def update(self, name, **fields):
        allowed = {
            "state",
            "pane",
            "sidebar",
            "launched",
            "summary",
            "next_step",
            "blocker",
            "turn",
            "reported_turn",
            "claude_id",
            "attended",
        }
        if not fields or not fields.keys() <= allowed:
            raise ValueError("Invalid session fields")
        fields["updated"] = time.time()
        with self.db() as db:
            db.execute(
                f"UPDATE sessions SET {','.join(k + '=?' for k in fields)} WHERE name=?",
                (*fields.values(), name),
            )

    def send(self, sender, recipient, body, kind="instruction"):
        if kind not in {"instruction", "probe", "report", "update"}:
            raise ValueError("Unknown message kind")
        if not isinstance(body, str) or not body.strip() or len(body) > 16000:
            raise ValueError("Messages must contain 1–16000 characters")
        self.session(recipient)
        if sender != "user":
            self.session(sender)
        with self.db() as db:
            result = db.execute(
                "INSERT INTO messages(sender,recipient,kind,body,created) VALUES(?,?,?,?,?)",
                (sender, recipient, kind, body.strip(), time.time()),
            )
            return result.lastrowid

    def inbox(self, name, *, pending_only=False, include_acked=False):
        self.session(name)
        predicate = "" if include_acked else " AND acked IS NULL"
        if pending_only:
            predicate += " AND delivered IS NULL"
        with self.db() as db:
            return [
                dict(r)
                for r in db.execute(
                    "SELECT * FROM messages WHERE recipient=?" + predicate + " ORDER BY id LIMIT 200", (name,)
                )
            ]

    def deliver(self, name, replay=False):
        """Claim a bounded batch once; unacknowledged messages replay on session resume."""
        with self.db() as db:
            db.execute("BEGIN IMMEDIATE")
            rows = db.execute(
                "SELECT * FROM messages WHERE recipient=? AND acked IS NULL "
                + ("" if replay else "AND delivered IS NULL ")
                + "ORDER BY id LIMIT 8",
                (name,),
            ).fetchall()
            if rows:
                db.executemany(
                    "UPDATE messages SET delivered=? WHERE id=?", [(time.time(), r["id"]) for r in rows]
                )
            db.execute("COMMIT")
        return [dict(r) for r in rows]

    def ack(self, name, ids):
        if not isinstance(ids, list) or not ids or not all(type(i) is int for i in ids):
            raise ValueError("ids must be a nonempty list of integers")
        with self.db() as db:
            db.executemany(
                "UPDATE messages SET acked=? WHERE recipient=? AND id=? AND acked IS NULL",
                [(time.time(), name, i) for i in ids],
            )

    def report(self, name, summary, next_step="", blocker="", todos=()):
        if not summary.strip() or len(summary) > 1200:
            raise ValueError("A report summary must contain 1–1200 characters")
        if len(next_step) > 600 or len(blocker) > 600:
            raise ValueError("Keep next steps and blockers under 600 characters")
        row = self.session(name)
        for todo in todos:
            self.add_todo(name, todo)
        body = json.dumps({"summary": summary, "next": next_step, "blocker": blocker}, ensure_ascii=False)
        with self.db() as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute(
                "UPDATE sessions SET summary=?,next_step=?,blocker=?,reported_turn=turn,updated=? WHERE name=?",
                (summary, next_step, blocker, time.time(), name),
            )
            if row["role"] != "chief":
                db.execute(
                    "INSERT INTO messages(sender,recipient,kind,body,created) VALUES(?,?,?,?,?)",
                    (name, "chief", "report", body, time.time()),
                )
            db.execute("COMMIT")

    def add_todo(self, session, text):
        self.session(session)
        if not isinstance(text, str) or not text.strip() or len(text) > 300:
            raise ValueError("Action items must contain 1–300 characters")
        with self.db() as db:
            db.execute(
                "INSERT OR IGNORE INTO todos(session,text,created) VALUES(?,?,?)",
                (session, text.strip(), time.time()),
            )

    def todos(self, all_items=False):
        with self.db() as db:
            return [
                dict(r)
                for r in db.execute(
                    "SELECT * FROM todos " + ("" if all_items else "WHERE done=0 ") + "ORDER BY created"
                )
            ]

    def complete_todo(self, todo_id):
        with self.db() as db:
            if not db.execute("UPDATE todos SET done=1 WHERE id=?", (todo_id,)).rowcount:
                raise ValueError(f"Unknown action item: {todo_id}")
