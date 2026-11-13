from __future__ import annotations

from salt import coordinator, pipeline
from salt.config import Config
from salt.daemon import Daemon


def _to_implement(store, profile):
    for phase in ("intake", "spec"):
        store.add_receipt("demo:demo", phase, "true", "/tmp", 0, None, None, "", "author", "author")
        pipeline.set_phase(store, profile, "demo:demo", phase, "done", actor="author", role="author")
    pipeline.approve(store, profile, "demo:demo", "gaps", "approved", actor="human")
    store.add_receipt("demo:demo", "spec_push", "true", "/tmp", 0, None, None, "", "author", "author")
    pipeline.set_phase(store, profile, "demo:demo", "spec_push", "done", actor="author", role="author")


def make_daemon(store, profile, tmp_path) -> Daemon:
    config = Config(data_dir=tmp_path / "data")
    return Daemon(config, store, profile)


def test_dispatch_is_off_by_default(wired, tmp_path):
    store, profile, _ = wired
    _to_implement(store, profile)
    daemon = make_daemon(store, profile, tmp_path)
    result = daemon.tick()
    assert result["enabled"] is False
    assert result["dispatched"] == []
    assert store.runs() == []


def test_a_tick_writes_a_brief_and_starts_the_runner(wired, tmp_path):
    store, profile, _ = wired
    _to_implement(store, profile)
    coordinator.set_dispatch_enabled(store, True)
    daemon = make_daemon(store, profile, tmp_path)
    result = daemon.tick()

    assert len(result["dispatched"]) == 1
    run = store.runs()[0]
    assert run["phase"] == "implement"
    assert run["role"] == "implementer"
    brief = tmp_path / "data" / "runs" / ""
    assert run["brief_path"] and run["brief_path"].endswith(".brief.md")
    text = open(run["brief_path"]).read()
    assert "implementer" in text
    assert "demo.GREETING.1" in text
    assert "salt prove" in text

    child = daemon.children[run["pid"]]
    child.wait(timeout=20)
    finished = daemon.reap()
    assert finished and finished[0]["exit_code"] == 0


def test_a_clean_run_does_not_advance_the_phase(wired, tmp_path):
    """A run exiting zero is not evidence; only a receipt is."""
    store, profile, _ = wired
    _to_implement(store, profile)
    coordinator.set_dispatch_enabled(store, True)
    daemon = make_daemon(store, profile, tmp_path)
    daemon.tick()
    run = store.runs()[0]
    daemon.children[run["pid"]].wait(timeout=20)
    daemon.reap()
    assert store.phase_state("demo:demo", "implement") == "started"
    assert store.valid_receipt("demo:demo", "implement") is None


def test_an_expired_lease_is_reaped_and_counted(wired, tmp_path):
    store, profile, _ = wired
    _to_implement(store, profile)
    coordinator.set_dispatch_enabled(store, True)
    daemon = make_daemon(store, profile, tmp_path)
    daemon.tick()
    with store.connect() as conn:
        conn.execute("UPDATE claim SET expires_at = '2000-01-01T00:00:00+00:00'")
    result = daemon.tick()
    assert result["expired"]
    assert store.phase_states("demo:demo")["implement"]["strands"] == 1


def test_concurrency_blocks_a_second_dispatch_while_one_is_live(wired, tmp_path):
    store, profile, _ = wired
    _to_implement(store, profile)
    coordinator.set_dispatch_enabled(store, True)
    coordinator.set_concurrency(store, 1)
    # A live run for this role: the coordinator must not double up on it.
    store.add_run(
        "demo:demo", "implement", "implementer", "coordinator:implementer",
        "sleep 1", __import__("os").getpid(), None, None,
    )
    daemon = make_daemon(store, profile, tmp_path)
    result = daemon.tick()
    assert result["dispatched"] == []
    assert result["skipped"]


def test_manual_tick_records_the_time(wired, tmp_path):
    store, profile, _ = wired
    daemon = make_daemon(store, profile, tmp_path)
    daemon.tick()
    assert store.get_setting(coordinator.SETTING_LAST_TICK)
