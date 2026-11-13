from __future__ import annotations

from salt.store import SCHEMA_VERSION, Store

ALLOWED_FREE_TEXT = {
    "value",  # settings
    "text",  # spec requirement text
    "command",  # a receipt's command
    "cwd",
    "digest",
    "output_tail",
    "note",
    "description",
    "file_path",
    "repo_path",
    "subject",
    "detail",
    "kind",
    "name",
    "project",
    "actor",
    "role",
    "grp",
    "num",
    "state",
    "id",
    "profile",
    "phase",
    "gate",
    "decision",
    "channel",
    "expires_at",
    "released_at",
    "started_at",
    "ended_at",
    "created_at",
    "updated_at",
    "ts",
    "content_hash",
    "commit_sha",
    "log_path",
    "brief_path",
    "key",
    "acid_id",
    "feature_id",
}


def test_schema_version_is_recorded(store: Store):
    with store.connect() as conn:
        row = conn.execute(
            "SELECT value FROM meta WHERE key = 'schema_version'"
        ).fetchone()
    assert row["value"] == str(SCHEMA_VERSION)


def test_migrating_twice_is_a_no_op(store: Store):
    path = store.path
    again = Store(path)
    with again.connect() as conn:
        tables = {
            row["name"]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            ).fetchall()
        }
    assert {"phase", "receipt", "claim", "run", "approval", "setting", "event"} <= tables


def test_settings_round_trip(store: Store):
    store.set_setting("coordinator.interval_seconds", "120")
    assert store.get_setting("coordinator.interval_seconds") == "120"
    store.set_setting("coordinator.interval_seconds", "60")
    assert store.get_setting("coordinator.interval_seconds") == "60"
    assert store.get_setting("absent", "fallback") == "fallback"


def test_events_are_append_only_and_ordered(store: Store):
    store.event("ingest", "demo", "first")
    store.event("phase", "demo:GREETING", "second")
    events = store.events()
    assert [event["kind"] for event in events] == ["phase", "ingest"]


def test_no_unbounded_free_text_columns(store: Store):
    """ingest.IDENTITY.1 — nothing here may accumulate another project's user data."""
    # ingest.IDENTITY.1
    unexpected = set()
    with store.connect() as conn:
        tables = [
            row["name"]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            ).fetchall()
        ]
        for table in tables:
            for column in conn.execute(f"PRAGMA table_info({table})").fetchall():
                if column["type"].upper().startswith("TEXT"):
                    if column["name"] not in ALLOWED_FREE_TEXT:
                        unexpected.add(f"{table}.{column['name']}")
    assert unexpected == set()


def test_no_identity_columns(store: Store):
    """ingest.IDENTITY.2 — no email, token or credential column exists."""
    # ingest.IDENTITY.2
    with store.connect() as conn:
        tables = [
            row["name"]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            ).fetchall()
        ]
        names = set()
        for table in tables:
            for column in conn.execute(f"PRAGMA table_info({table})").fetchall():
                names.add(column["name"].lower())
    assert not {"email", "token", "password", "secret", "api_key"} & names
