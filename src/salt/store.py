"""SQLite storage.

The database is disposable by design: everything except runs, approvals, events and
settings is rebuildable from `salt ingest`. Nothing personal is stored.

ingest.IDENTITY.1 — the only unbounded free-text columns are spec text, commands,
notes and descriptions. A test enforces that.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

SCHEMA_VERSION = 1

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS project (
  name TEXT PRIMARY KEY,
  repo_path TEXT NOT NULL,
  profile TEXT NOT NULL DEFAULT 'standard'
);

CREATE TABLE IF NOT EXISTS feature (
  id TEXT PRIMARY KEY,
  project TEXT NOT NULL,
  name TEXT NOT NULL,
  file_path TEXT NOT NULL,
  draft INTEGER NOT NULL DEFAULT 0,
  description TEXT,
  content_hash TEXT
);

CREATE TABLE IF NOT EXISTS acid (
  id TEXT PRIMARY KEY,
  feature_id TEXT NOT NULL,
  grp TEXT NOT NULL,
  num TEXT NOT NULL,
  text TEXT NOT NULL,
  state TEXT NOT NULL DEFAULT 'defined'
);

CREATE TABLE IF NOT EXISTS ref (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  acid_id TEXT NOT NULL,
  file_path TEXT NOT NULL,
  line INTEGER NOT NULL,
  kind TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS phase (
  feature_id TEXT NOT NULL,
  name TEXT NOT NULL,
  state TEXT NOT NULL DEFAULT 'pending',
  strands INTEGER NOT NULL DEFAULT 0,
  updated_at TEXT,
  PRIMARY KEY (feature_id, name)
);

CREATE TABLE IF NOT EXISTS receipt (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  feature_id TEXT NOT NULL,
  phase TEXT NOT NULL,
  command TEXT NOT NULL,
  cwd TEXT,
  exit_code INTEGER NOT NULL,
  commit_sha TEXT,
  digest TEXT,
  output_tail TEXT,
  actor TEXT,
  role TEXT,
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS claim (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  feature_id TEXT NOT NULL,
  phase TEXT NOT NULL,
  role TEXT NOT NULL,
  actor TEXT,
  expires_at TEXT NOT NULL,
  released_at TEXT
);

CREATE TABLE IF NOT EXISTS run (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  feature_id TEXT NOT NULL,
  phase TEXT NOT NULL,
  role TEXT,
  actor TEXT,
  command TEXT,
  pid INTEGER,
  started_at TEXT NOT NULL,
  ended_at TEXT,
  exit_code INTEGER,
  log_path TEXT,
  brief_path TEXT
);

CREATE TABLE IF NOT EXISTS approval (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  feature_id TEXT NOT NULL,
  gate TEXT NOT NULL,
  decision TEXT NOT NULL,
  note TEXT,
  actor TEXT,
  channel TEXT NOT NULL,
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS setting (
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS event (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ts TEXT NOT NULL,
  kind TEXT NOT NULL,
  subject TEXT,
  detail TEXT
);

CREATE INDEX IF NOT EXISTS ref_acid ON ref(acid_id);
CREATE INDEX IF NOT EXISTS event_ts ON event(ts);
CREATE INDEX IF NOT EXISTS claim_unit ON claim(feature_id, phase);
"""


def now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def in_seconds(seconds: int) -> str:
    return (datetime.now(UTC) + timedelta(seconds=seconds)).isoformat(
        timespec="seconds"
    )


@dataclass
class Unit:
    feature_id: str
    phase: str
    role: str | None
    project: str = ""


class Store:
    def __init__(self, path: Path | str):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as conn:
            conn.executescript(SCHEMA)
            conn.execute(
                "INSERT OR IGNORE INTO meta(key, value) VALUES('schema_version', ?)",
                (str(SCHEMA_VERSION),),
            )

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    # -- settings ---------------------------------------------------------
    def get_setting(self, key: str, default: str | None = None) -> str | None:
        with self.connect() as conn:
            row = conn.execute("SELECT value FROM setting WHERE key = ?", (key,)).fetchone()
        return row["value"] if row else default

    def set_setting(self, key: str, value: str) -> None:
        with self.connect() as conn:
            conn.execute(
                "INSERT INTO setting(key, value) VALUES(?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (key, value),
            )

    # -- events -----------------------------------------------------------
    def event(self, kind: str, subject: str = "", detail: str = "") -> None:
        with self.connect() as conn:
            conn.execute(
                "INSERT INTO event(ts, kind, subject, detail) VALUES(?, ?, ?, ?)",
                (now(), kind, subject, detail),
            )

    def events(self, limit: int = 200, since: str | None = None) -> list[sqlite3.Row]:
        query = "SELECT * FROM event"
        params: list = []
        if since:
            query += " WHERE ts > ?"
            params.append(since)
        query += " ORDER BY id DESC LIMIT ?"
        params.append(limit)
        with self.connect() as conn:
            return conn.execute(query, params).fetchall()

    # -- projects and features -------------------------------------------
    def upsert_project(self, name: str, repo_path: str, profile: str) -> None:
        with self.connect() as conn:
            conn.execute(
                "INSERT INTO project(name, repo_path, profile) VALUES(?, ?, ?) "
                "ON CONFLICT(name) DO UPDATE SET repo_path = excluded.repo_path, "
                "profile = excluded.profile",
                (name, repo_path, profile),
            )

    def projects(self) -> list[sqlite3.Row]:
        with self.connect() as conn:
            return conn.execute("SELECT * FROM project ORDER BY name").fetchall()

    def project(self, name: str) -> sqlite3.Row | None:
        with self.connect() as conn:
            return conn.execute("SELECT * FROM project WHERE name = ?", (name,)).fetchone()

    def features(self, project: str | None = None) -> list[sqlite3.Row]:
        query = "SELECT * FROM feature"
        params: list = []
        if project:
            query += " WHERE project = ?"
            params.append(project)
        query += " ORDER BY project, name"
        with self.connect() as conn:
            return conn.execute(query, params).fetchall()

    def feature(self, feature_id: str) -> sqlite3.Row | None:
        with self.connect() as conn:
            return conn.execute(
                "SELECT * FROM feature WHERE id = ?", (feature_id,)
            ).fetchone()

    def find_feature(self, text: str) -> sqlite3.Row | None:
        """Accept `feature`, `project:feature`, or the stored id."""
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM feature WHERE id = ?", (text,)).fetchone()
            if row:
                return row
            if ":" in text:
                project, name = text.split(":", 1)
                return conn.execute(
                    "SELECT * FROM feature WHERE project = ? AND name = ?",
                    (project, name),
                ).fetchone()
            return conn.execute(
                "SELECT * FROM feature WHERE name = ? ORDER BY project LIMIT 1", (text,)
            ).fetchone()

    def upsert_feature(
        self,
        feature_id: str,
        project: str,
        name: str,
        file_path: str,
        draft: bool,
        description: str | None,
        content_hash: str | None,
    ) -> None:
        with self.connect() as conn:
            conn.execute(
                "INSERT INTO feature(id, project, name, file_path, draft, description, "
                "content_hash) VALUES(?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(id) DO UPDATE SET project = excluded.project, "
                "name = excluded.name, file_path = excluded.file_path, "
                "draft = excluded.draft, description = excluded.description, "
                "content_hash = excluded.content_hash",
                (
                    feature_id,
                    project,
                    name,
                    file_path,
                    int(bool(draft)),
                    description,
                    content_hash,
                ),
            )

    def delete_features_not_in(self, project: str, keep_ids: list[str]) -> list[str]:
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT id FROM feature WHERE project = ?", (project,)
            ).fetchall()
            gone = [r["id"] for r in rows if r["id"] not in keep_ids]
            for feature_id in gone:
                conn.execute("DELETE FROM feature WHERE id = ?", (feature_id,))
                conn.execute("DELETE FROM acid WHERE feature_id = ?", (feature_id,))
                conn.execute("DELETE FROM phase WHERE feature_id = ?", (feature_id,))
                conn.execute(
                    "DELETE FROM ref WHERE acid_id IN "
                    "(SELECT id FROM acid WHERE feature_id = ?)",
                    (feature_id,),
                )
            return gone

    # -- acids and refs ---------------------------------------------------
    def upsert_acid(self, acid_id: str, feature_id: str, grp: str, num: str, text: str) -> None:
        with self.connect() as conn:
            conn.execute(
                "INSERT INTO acid(id, feature_id, grp, num, text) VALUES(?, ?, ?, ?, ?) "
                "ON CONFLICT(id) DO UPDATE SET feature_id = excluded.feature_id, "
                "grp = excluded.grp, num = excluded.num, text = excluded.text",
                (acid_id, feature_id, grp, num, text),
            )

    def acids(self, feature_id: str) -> list[sqlite3.Row]:
        with self.connect() as conn:
            return conn.execute(
                "SELECT * FROM acid WHERE feature_id = ? "
                "ORDER BY grp, CAST(num AS INTEGER), num",
                (feature_id,),
            ).fetchall()

    def acid(self, acid_id: str) -> sqlite3.Row | None:
        with self.connect() as conn:
            return conn.execute("SELECT * FROM acid WHERE id = ?", (acid_id,)).fetchone()

    def set_acid_state(self, acid_id: str, state: str) -> None:
        with self.connect() as conn:
            conn.execute("UPDATE acid SET state = ? WHERE id = ?", (state, acid_id))

    def prune_acids(self, feature_id: str, keep_ids: list[str]) -> None:
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT id FROM acid WHERE feature_id = ?", (feature_id,)
            ).fetchall()
            for row in rows:
                if row["id"] not in keep_ids:
                    conn.execute("DELETE FROM acid WHERE id = ?", (row["id"],))
                    conn.execute("DELETE FROM ref WHERE acid_id = ?", (row["id"],))

    def replace_refs(self, project: str, refs: list[tuple[str, str, int, str]]) -> None:
        """refs are (acid_id, file_path, line, kind)."""
        with self.connect() as conn:
            conn.execute(
                "DELETE FROM ref WHERE acid_id IN "
                "(SELECT id FROM acid WHERE feature_id IN "
                "(SELECT id FROM feature WHERE project = ?))",
                (project,),
            )
            conn.executemany(
                "INSERT INTO ref(acid_id, file_path, line, kind) VALUES(?, ?, ?, ?)",
                refs,
            )

    def ref_counts(self, feature_id: str) -> dict[str, dict[str, int]]:
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT r.acid_id, r.kind, COUNT(*) AS n FROM ref r "
                "JOIN acid a ON a.id = r.acid_id WHERE a.feature_id = ? "
                "GROUP BY r.acid_id, r.kind",
                (feature_id,),
            ).fetchall()
        counts: dict[str, dict[str, int]] = {}
        for row in rows:
            counts.setdefault(row["acid_id"], {"code": 0, "test": 0})[row["kind"]] = row["n"]
        return counts

    # -- phases -----------------------------------------------------------
    def ensure_phases(self, feature_id: str, phase_names: list[str]) -> None:
        with self.connect() as conn:
            for name in phase_names:
                conn.execute(
                    "INSERT OR IGNORE INTO phase(feature_id, name, state, strands, "
                    "updated_at) VALUES(?, ?, 'pending', 0, ?)",
                    (feature_id, name, now()),
                )

    def phase_states(self, feature_id: str) -> dict[str, sqlite3.Row]:
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM phase WHERE feature_id = ?", (feature_id,)
            ).fetchall()
        return {row["name"]: row for row in rows}

    def phase_state(self, feature_id: str, phase: str) -> str | None:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT state FROM phase WHERE feature_id = ? AND name = ?",
                (feature_id, phase),
            ).fetchone()
        return row["state"] if row else None

    def set_phase_state(self, feature_id: str, phase: str, state: str) -> None:
        with self.connect() as conn:
            conn.execute(
                "UPDATE phase SET state = ?, updated_at = ? WHERE feature_id = ? AND name = ?",
                (state, now(), feature_id, phase),
            )
            conn.execute(
                "INSERT OR IGNORE INTO phase(feature_id, name, state, strands, updated_at) "
                "VALUES(?, ?, ?, 0, ?)",
                (feature_id, phase, state, now()),
            )

    def add_strand(self, feature_id: str, phase: str) -> int:
        with self.connect() as conn:
            conn.execute(
                "UPDATE phase SET strands = strands + 1, updated_at = ? "
                "WHERE feature_id = ? AND name = ?",
                (now(), feature_id, phase),
            )
            row = conn.execute(
                "SELECT strands FROM phase WHERE feature_id = ? AND name = ?",
                (feature_id, phase),
            ).fetchone()
        return int(row["strands"]) if row else 0

    def phases_for_project(self, project: str) -> list[sqlite3.Row]:
        with self.connect() as conn:
            return conn.execute(
                "SELECT p.* FROM phase p JOIN feature f ON f.id = p.feature_id "
                "WHERE f.project = ? ORDER BY p.feature_id",
                (project,),
            ).fetchall()

    # -- receipts ---------------------------------------------------------
    def add_receipt(
        self,
        feature_id: str,
        phase: str,
        command: str,
        cwd: str | None,
        exit_code: int,
        commit_sha: str | None,
        digest: str | None,
        output_tail: str | None,
        actor: str | None,
        role: str | None,
    ) -> int:
        with self.connect() as conn:
            cur = conn.execute(
                "INSERT INTO receipt(feature_id, phase, command, cwd, exit_code, "
                "commit_sha, digest, output_tail, actor, role, created_at) "
                "VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    feature_id,
                    phase,
                    command,
                    cwd,
                    exit_code,
                    commit_sha,
                    digest,
                    output_tail,
                    actor,
                    role,
                    now(),
                ),
            )
            return int(cur.lastrowid or 0)

    def valid_receipt(self, feature_id: str, phase: str) -> sqlite3.Row | None:
        with self.connect() as conn:
            return conn.execute(
                "SELECT * FROM receipt WHERE feature_id = ? AND phase = ? "
                "AND exit_code = 0 ORDER BY id DESC LIMIT 1",
                (feature_id, phase),
            ).fetchone()

    def receipts(self, feature_id: str, limit: int = 50) -> list[sqlite3.Row]:
        with self.connect() as conn:
            return conn.execute(
                "SELECT * FROM receipt WHERE feature_id = ? ORDER BY id DESC LIMIT ?",
                (feature_id, limit),
            ).fetchall()

    def latest_receipt(self, feature_id: str, phase: str) -> sqlite3.Row | None:
        with self.connect() as conn:
            return conn.execute(
                "SELECT * FROM receipt WHERE feature_id = ? AND phase = ? "
                "ORDER BY id DESC LIMIT 1",
                (feature_id, phase),
            ).fetchone()

    # -- approvals --------------------------------------------------------
    def add_approval(
        self, feature_id: str, gate: str, decision: str, note: str, actor: str, channel: str
    ) -> None:
        with self.connect() as conn:
            conn.execute(
                "INSERT INTO approval(feature_id, gate, decision, note, actor, channel, "
                "created_at) VALUES(?, ?, ?, ?, ?, ?, ?)",
                (feature_id, gate, decision, note, actor, channel, now()),
            )

    def approvals(self, feature_id: str) -> list[sqlite3.Row]:
        with self.connect() as conn:
            return conn.execute(
                "SELECT * FROM approval WHERE feature_id = ? ORDER BY id DESC",
                (feature_id,),
            ).fetchall()

    def approved(self, feature_id: str, gate: str) -> bool:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT decision FROM approval WHERE feature_id = ? AND gate = ? "
                "ORDER BY id DESC LIMIT 1",
                (feature_id, gate),
            ).fetchone()
        return bool(row and row["decision"] == "approved")

    # -- claims -----------------------------------------------------------
    def live_claim(self, feature_id: str, phase: str) -> sqlite3.Row | None:
        with self.connect() as conn:
            return conn.execute(
                "SELECT * FROM claim WHERE feature_id = ? AND phase = ? "
                "AND released_at IS NULL AND expires_at > ? ORDER BY id DESC LIMIT 1",
                (feature_id, phase, now()),
            ).fetchone()

    def add_claim(
        self, feature_id: str, phase: str, role: str, actor: str | None, ttl_seconds: int
    ) -> int:
        with self.connect() as conn:
            cur = conn.execute(
                "INSERT INTO claim(feature_id, phase, role, actor, expires_at) "
                "VALUES(?, ?, ?, ?, ?)",
                (feature_id, phase, role, actor, in_seconds(ttl_seconds)),
            )
            return int(cur.lastrowid or 0)

    def release_claim(self, claim_id: int) -> None:
        with self.connect() as conn:
            conn.execute(
                "UPDATE claim SET released_at = ? WHERE id = ?", (now(), claim_id)
            )

    def live_claims(self) -> list[sqlite3.Row]:
        with self.connect() as conn:
            return conn.execute(
                "SELECT c.*, f.name AS feature_name, f.project AS project FROM claim c "
                "JOIN feature f ON f.id = c.feature_id "
                "WHERE c.released_at IS NULL AND c.expires_at > ? ORDER BY c.expires_at",
                (now(),),
            ).fetchall()

    def expire_claims(self) -> list[sqlite3.Row]:
        """Release claims whose lease has lapsed and return them."""
        expired_at = now()
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM claim WHERE released_at IS NULL AND expires_at <= ?",
                (expired_at,),
            ).fetchall()
            for row in rows:
                conn.execute(
                    "UPDATE claim SET released_at = ? WHERE id = ?", (expired_at, row["id"])
                )
        return rows

    # -- runs -------------------------------------------------------------
    def add_run(
        self,
        feature_id: str,
        phase: str,
        role: str | None,
        actor: str | None,
        command: str | None,
        pid: int | None,
        log_path: str | None,
        brief_path: str | None,
    ) -> int:
        with self.connect() as conn:
            cur = conn.execute(
                "INSERT INTO run(feature_id, phase, role, actor, command, pid, started_at, "
                "log_path, brief_path) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    feature_id,
                    phase,
                    role,
                    actor,
                    command,
                    pid,
                    now(),
                    log_path,
                    brief_path,
                ),
            )
            return int(cur.lastrowid or 0)

    def finish_run(self, run_id: int, exit_code: int) -> None:
        with self.connect() as conn:
            conn.execute(
                "UPDATE run SET exit_code = ?, ended_at = ? WHERE id = ?",
                (exit_code, now(), run_id),
            )

    def open_runs(self) -> list[sqlite3.Row]:
        with self.connect() as conn:
            return conn.execute(
                "SELECT * FROM run WHERE exit_code IS NULL AND pid IS NOT NULL"
            ).fetchall()

    def runs(self, limit: int = 50) -> list[sqlite3.Row]:
        with self.connect() as conn:
            return conn.execute(
                "SELECT r.*, f.name AS feature_name, f.project AS project FROM run r "
                "JOIN feature f ON f.id = r.feature_id ORDER BY r.id DESC LIMIT ?",
                (limit,),
            ).fetchall()

    def running_count(self, role: str | None = None) -> int:
        query = "SELECT COUNT(*) AS n FROM run WHERE exit_code IS NULL AND pid IS NOT NULL"
        params: list = []
        if role:
            query += " AND role = ?"
            params.append(role)
        with self.connect() as conn:
            return int(conn.execute(query, params).fetchone()["n"])
