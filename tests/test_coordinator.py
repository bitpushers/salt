from __future__ import annotations

import pytest

from salt import coordinator, pipeline


def _unit(store, profile, phase="implement"):
    feature = store.find_feature("demo:demo")
    definition = profile.phase(phase)
    return coordinator.Unit(
        feature_id=feature["id"],
        project="demo",
        feature_name="demo",
        phase=phase,
        role=definition.owner,
    )


def _clear_to_implement(store, profile):
    for phase in ("intake", "spec"):
        store.add_receipt("demo:demo", phase, "true", "/tmp", 0, None, None, "", "author", "author")
        pipeline.set_phase(store, profile, "demo:demo", phase, "done", actor="author", role="author")
    pipeline.approve(store, profile, "demo:demo", "gaps", "approved", actor="human")
    store.add_receipt("demo:demo", "spec_push", "true", "/tmp", 0, None, None, "", "author", "author")
    pipeline.set_phase(store, profile, "demo:demo", "spec_push", "done", actor="author", role="author")


def test_an_eligible_unit_appears_once_gates_pass(wired):
    """console.QUEUE.1"""
    # console.QUEUE.1
    store, profile, _ = wired
    _clear_to_implement(store, profile)
    units = coordinator.eligible(store, profile)
    assert [(unit.phase, unit.role) for unit in units] == [("implement", "implementer")]


def _force_expiry(store):
    with store.connect() as conn:
        conn.execute("UPDATE claim SET expires_at = '2000-01-01T00:00:00+00:00'")


def test_a_live_lease_hides_the_unit_and_expiry_resurfaces_it(wired):
    """pipeline.CLAIM.1"""
    # pipeline.CLAIM.1
    store, profile, _ = wired
    _clear_to_implement(store, profile)
    unit = _unit(store, profile)
    coordinator.claim(store, profile, unit, actor="bot", ttl_seconds=600)
    assert coordinator.eligible(store, profile) == []

    _force_expiry(store)
    result = coordinator.expire(store)
    assert result and result[0]["phase"] == "implement"
    assert coordinator.eligible(store, profile)


def test_only_one_live_claim_per_unit(wired):
    """pipeline.CLAIM.2"""
    # pipeline.CLAIM.2
    store, profile, _ = wired
    _clear_to_implement(store, profile)
    unit = _unit(store, profile)
    coordinator.claim(store, profile, unit, actor="a", ttl_seconds=600)
    with pytest.raises(pipeline.TransitionError, match="already claimed"):
        coordinator.claim(store, profile, unit, actor="b", ttl_seconds=600)


def test_a_claim_records_role_and_actor(wired):
    """pipeline.CLAIM.3"""
    # pipeline.CLAIM.3
    store, profile, _ = wired
    _clear_to_implement(store, profile)
    coordinator.claim(store, profile, _unit(store, profile), actor="impl-1", ttl_seconds=600)
    claim = store.live_claims()[0]
    assert claim["role"] == "implementer"
    assert claim["actor"] == "impl-1"


def test_a_gate_is_never_dispatchable(wired):
    """pipeline.GATE.3"""
    # pipeline.GATE.3
    store, profile, _ = wired
    for phase in ("intake", "spec"):
        store.add_receipt("demo:demo", phase, "true", "/tmp", 0, None, None, "", "author", "author")
        pipeline.set_phase(store, profile, "demo:demo", phase, "done", actor="author", role="author")
    units = coordinator.eligible(store, profile)
    assert all(unit.phase != "gaps" for unit in units)
    waiting = coordinator.waiting(store, profile)
    assert waiting and waiting[0].phase == "gaps"


def test_a_gate_cannot_be_claimed(wired):
    store, profile, _ = wired
    definition = profile.phase("gaps")
    unit = coordinator.Unit("demo:demo", "demo", "demo", "gaps", definition.owner)
    with pytest.raises(pipeline.TransitionError, match="gate"):
        coordinator.claim(store, profile, unit, actor="bot", ttl_seconds=60)


def test_strands_are_counted_and_surface(wired):
    store, profile, _ = wired
    _clear_to_implement(store, profile)
    coordinator.claim(store, profile, _unit(store, profile), actor="a", ttl_seconds=600)
    for _ in range(3):
        _force_expiry(store)
        coordinator.expire(store)
        if coordinator.eligible(store, profile):
            coordinator.claim(store, profile, _unit(store, profile), actor="a", ttl_seconds=600)
    stranded = coordinator.stranded(store, profile)
    assert any(unit.phase == "implement" for unit in stranded)


def test_settings_have_defaults_and_setters(store, profile):
    assert coordinator.interval_seconds(store, profile) == profile.interval_seconds
    assert coordinator.dispatch_enabled(store) is False
    coordinator.set_interval(store, 90)
    coordinator.set_concurrency(store, 3)
    coordinator.set_dispatch_enabled(store, True)
    assert coordinator.interval_seconds(store, profile) == 90
    assert coordinator.concurrency(store, profile) == 3
    assert coordinator.dispatch_enabled(store) is True


def test_interval_has_a_floor(store, profile):
    coordinator.set_interval(store, 1)
    assert coordinator.interval_seconds(store, profile) >= 15
