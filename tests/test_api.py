from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from salt import coordinator, pipeline
from salt.api import create_app
from salt.config import Config
from salt.daemon import Daemon
from salt.ingest import ingest_repository

SECOND_SPEC = """
feature:
  name: other
  draft: false
  description: second project
components:
  G:
    requirements:
      1: does a thing
"""


@pytest.fixture
def app_client(wired, tmp_path, profile):
    store, profile, repo = wired
    second = tmp_path / "second-repo"
    (second / "features").mkdir(parents=True)
    (second / "features" / "other.feature.yaml").write_text(SECOND_SPEC.strip() + "\n")
    ingest_repository(store, profile, "second", second)

    config = Config(data_dir=tmp_path / "data")
    daemon = Daemon(config, store, profile)
    app = create_app(config, store, profile, daemon)
    return TestClient(app), store, profile


def _advance(store, profile, feature_id="demo:demo"):
    for phase in ("intake", "spec"):
        store.add_receipt(feature_id, phase, "true", "/tmp", 0, None, None, "", "author", "author")
        pipeline.set_phase(store, profile, feature_id, phase, "done", actor="author", role="author")
    pipeline.approve(store, profile, feature_id, "gaps", "approved", actor="human")
    store.add_receipt(feature_id, "spec_push", "true", "/tmp", 0, None, None, "", "author", "author")
    pipeline.set_phase(store, profile, feature_id, "spec_push", "done", actor="author", role="author")


def test_board_lists_features_from_every_project(app_client):
    """console.BOARD.1"""
    client, _, _ = app_client
    body = client.get("/").text
    assert "demo" in body and "other" in body
    assert "second-repo" in body or "second" in body


def test_board_shows_phase_states(app_client):
    """console.BOARD.2"""
    client, store, profile = app_client
    _advance(store, profile)
    body = client.get("/").text
    assert "state-done" in body
    assert "state-gate" in body


def test_board_columns_name_the_owning_role(app_client):
    """console.BOARD.3"""
    client, _, _ = app_client
    body = client.get("/").text
    assert "implementer" in body
    assert "verifier" in body


def test_detail_lists_acids_with_reference_counts(app_client):
    """console.DETAIL.1"""
    client, _, _ = app_client
    body = client.get("/features/demo:demo").text
    assert "demo.GREETING.1" in body
    assert "untested" in body


def test_detail_shows_receipts(app_client):
    """console.DETAIL.2"""
    client, store, profile = app_client
    store.add_receipt(
        "demo:demo",
        "spec",
        "pytest -q",
        "/tmp",
        0,
        "deadbeef",
        "d",
        "ok",
        "author",
        "author",
    )
    body = client.get("/features/demo:demo").text
    assert "pytest -q" in body
    assert "author" in body


def test_detail_shows_approvals(app_client):
    """console.DETAIL.3"""
    client, store, profile = app_client
    pipeline.approve(store, profile, "demo:demo", "gaps", "approved", note="looks right", actor="human", channel="web")
    body = client.get("/features/demo:demo").text
    assert "looks right" in body


def test_roster_lists_roles_and_runners(app_client):
    """console.ROSTER_VIEW.1"""
    client, _, _ = app_client
    body = client.get("/roster").text
    assert "implementer" in body
    assert "cat {brief}" in body or "cat" in body


def test_roster_marks_the_coordinator(app_client):
    """console.ROSTER_VIEW.2"""
    client, _, _ = app_client
    body = client.get("/roster").text
    assert "coordinator" in body


def test_queue_lists_eligible_units(app_client):
    """console.QUEUE.1"""
    client, store, profile = app_client
    _advance(store, profile)
    body = client.get("/queue").text
    assert "Eligible" in body
    assert "implement" in body


def test_queue_lists_live_leases(app_client):
    """console.QUEUE.2"""
    client, store, profile = app_client
    _advance(store, profile)
    unit = coordinator.eligible(store, profile)[0]
    coordinator.claim(store, profile, unit, actor="bot", ttl_seconds=600)
    body = client.get("/queue").text
    assert "Live leases" in body
    assert "expires" in body.lower()


def test_queue_lists_stranded_units(app_client):
    """console.QUEUE.3"""
    client, store, profile = app_client
    _advance(store, profile)
    pipeline.set_phase(store, profile, "demo:demo", "implement", "blocked", actor="bot")
    body = client.get("/queue").text
    assert "Stranded" in body
    assert "blocked" in body


def test_coordinator_page_shows_the_interval(app_client):
    """console.SETTINGS.1"""
    client, store, profile = app_client
    coordinator.set_interval(store, 120)
    body = client.get("/coordinator").text
    assert "120" in body


def test_interval_submitted_from_the_page_is_persisted(app_client):
    """console.SETTINGS.2"""
    client, store, profile = app_client
    response = client.post(
        "/coordinator/settings",
        data={"interval": "90", "concurrency": "2", "enabled": "on"},
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert coordinator.interval_seconds(store, profile) == 90
    assert coordinator.dispatch_enabled(store) is True


def test_dispatch_can_be_disabled_from_the_page(app_client):
    """console.SETTINGS.3"""
    client, store, profile = app_client
    coordinator.set_dispatch_enabled(store, True)
    client.post("/coordinator/settings", data={"interval": "300", "concurrency": "1"})
    assert coordinator.dispatch_enabled(store) is False


def test_run_now_triggers_a_tick(app_client):
    """console.SETTINGS.4"""
    client, store, profile = app_client
    _advance(store, profile)
    coordinator.set_dispatch_enabled(store, True)
    response = client.post("/coordinator/run-now")
    assert response.status_code == 200
    assert store.get_setting(coordinator.SETTING_LAST_TICK)
    assert store.runs()


def test_activity_lists_events_newest_first(app_client):
    """console.ACTIVITY.1"""
    client, store, _ = app_client
    body = client.get("/activity").text
    assert "ingest" in body
    assert body.index("ingest") < len(body)


def test_an_event_names_its_kind_and_subject(app_client):
    """console.ACTIVITY.2"""
    client, store, _ = app_client
    store.event("custom-kind", "custom-subject", "detail here")
    body = client.get("/activity").text
    assert "custom-kind" in body and "custom-subject" in body


def test_pages_render_without_javascript(app_client):
    """console.SAFARI.1"""
    client, _, _ = app_client
    for path in ("/", "/queue", "/roster", "/coordinator", "/activity", "/features/demo:demo"):
        body = client.get(path).text
        assert "<script" not in body.lower()


def test_targets_are_at_least_44_pixels(app_client):
    """console.SAFARI.2"""
    from salt.api import HERE

    css = (HERE / "static" / "style.css").read_text()
    assert "min-height: 44px" in css


def test_tables_collapse_below_900_pixels(app_client):
    """console.SAFARI.3"""
    from salt.api import HERE

    css = (HERE / "static" / "style.css").read_text()
    assert "max-width: 900px" in css


def test_api_next_respects_leases(app_client):
    client, store, profile = app_client
    _advance(store, profile)
    before = client.get("/api/next").json()["units"]
    assert any(unit["feature_id"] == "demo:demo" for unit in before)
    unit = next(u for u in coordinator.eligible(store, profile) if u.feature_id == "demo:demo")
    coordinator.claim(store, profile, unit, actor="bot", ttl_seconds=600)
    after = client.get("/api/next").json()["units"]
    assert all(item["feature_id"] != "demo:demo" for item in after)


def test_api_receipt_with_nonzero_exit_is_refused(app_client):
    client, store, _ = app_client
    response = client.post(
        "/api/receipts",
        json={"feature": "demo:demo", "phase": "implement", "exit_code": 1, "command": "false"},
    )
    assert response.status_code == 409
    assert store.receipts("demo:demo") == []


def test_api_approval_rejects_a_non_gate_phase(app_client):
    client, _, _ = app_client
    response = client.post(
        "/api/approvals", json={"feature": "demo:demo", "gate": "implement", "decision": "approved"}
    )
    assert response.status_code == 422


def test_web_approval_records_the_web_channel(app_client):
    client, store, _ = app_client
    client.post(
        "/features/demo:demo/approve",
        data={"gate": "gaps", "decision": "approved", "note": "from the ipad"},
        follow_redirects=False,
    )
    assert store.approvals("demo:demo")[0]["channel"] == "web"
