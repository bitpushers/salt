from __future__ import annotations

import pytest

from salt import pipeline


def _pass_phase(store, profile, feature_id, phase, role=None):
    store.add_receipt(
        feature_id, phase, "true", "/tmp", 0, "sha", "digest", "", role or "bot", role
    )
    return pipeline.set_phase(
        store, profile, feature_id, phase, "done", actor=role or "bot", role=role
    )


def test_done_requires_a_receipt(wired):
    """pipeline.PHASE.1"""
    store, profile, _ = wired
    with pytest.raises(pipeline.TransitionError, match="no passing receipt"):
        pipeline.set_phase(
            store, profile, "demo:demo", "spec", "done", actor="a", role="author"
        )


def test_phases_finish_in_order(wired):
    """pipeline.PHASE.2"""
    store, profile, _ = wired
    _pass_phase(store, profile, "demo:demo", "intake", "author")
    _pass_phase(store, profile, "demo:demo", "spec", "author")
    pipeline.approve(store, profile, "demo:demo", "gaps", "approved", actor="human")
    _pass_phase(store, profile, "demo:demo", "spec_push", "author")
    with pytest.raises(pipeline.TransitionError, match="cannot be done while"):
        _pass_phase(store, profile, "demo:demo", "tests", "implementer")


def test_blocked_clears_only_with_a_new_receipt(wired):
    """pipeline.PHASE.3"""
    store, profile, _ = wired
    pipeline.set_phase(store, profile, "demo:demo", "intake", "blocked", actor="a")
    assert store.phase_state("demo:demo", "intake") == "blocked"
    with pytest.raises(pipeline.TransitionError):
        pipeline.set_phase(
            store, profile, "demo:demo", "intake", "done", actor="a", role="author"
        )
    _pass_phase(store, profile, "demo:demo", "intake", "author")
    assert store.phase_state("demo:demo", "intake") == "done"


def test_a_role_cannot_move_a_phase_it_does_not_own(wired):
    """pipeline.PHASE.4"""
    store, profile, _ = wired
    store.add_receipt("demo:demo", "implement", "true", "/tmp", 0, None, None, "", "x", "verifier")
    with pytest.raises(pipeline.RoleError):
        pipeline.set_phase(
            store, profile, "demo:demo", "implement", "done", actor="v", role="verifier"
        )


def test_a_gate_advances_only_on_an_approval(wired):
    """pipeline.GATE.1"""
    store, profile, _ = wired
    store.add_receipt("demo:demo", "gaps", "true", "/tmp", 0, None, None, "", "bot", None)
    with pytest.raises(pipeline.TransitionError, match="gate"):
        pipeline.set_phase(
            store, profile, "demo:demo", "gaps", "done", actor="bot"
        )
    pipeline.approve(store, profile, "demo:demo", "gaps", "approved", actor="human", channel="web")
    assert store.phase_state("demo:demo", "gaps") == "done"


def test_approval_records_its_channel(wired):
    """pipeline.GATE.2"""
    store, profile, _ = wired
    pipeline.approve(store, profile, "demo:demo", "gaps", "approved", actor="human", channel="cli")
    pipeline.approve(store, profile, "demo:demo", "review", "approved", actor="human", channel="web")
    channels = {row["gate"]: row["channel"] for row in store.approvals("demo:demo")}
    assert channels == {"gaps": "cli", "review": "web"}


def test_exactly_one_coordinator_role(tmp_path):
    """pipeline.ROSTER.1"""
    bad = tmp_path / "bad.toml"
    bad.write_text(
        '[roster]\ncoordinator = "a"\n'
        '[[role]]\nname = "a"\ncoordinator = true\n'
        '[[role]]\nname = "b"\ncoordinator = true\n'
        '[[phase]]\nname = "p"\nowner = "a"\n'
    )
    with pytest.raises(pipeline.ProfileError, match="exactly one coordinator"):
        pipeline.load_profile(str(bad))


def test_every_role_declares_a_runner(profile):
    """pipeline.ROSTER.2"""
    assert profile.roles
    assert all(role.runner for role in profile.roles)
    assert profile.coordinator_role() is not None


def test_runner_accepts_a_brief_substitution(profile):
    """pipeline.ROSTER.3"""
    template = profile.roles[0].runner
    assert "{brief}" in template
    assert "/tmp/brief.md" in template.replace("{brief}", "/tmp/brief.md")


def test_the_shipped_profile_has_the_ten_phases(profile):
    assert profile.phase_names == [
        "intake", "spec", "gaps", "spec_push", "implement",
        "tests", "verify", "commit", "sync", "review",
    ]
    assert profile.gates == ["gaps", "review"]


def test_unknown_phase_is_refused(wired):
    store, profile, _ = wired
    with pytest.raises(pipeline.TransitionError, match="unknown phase"):
        pipeline.set_phase(store, profile, "demo:demo", "nope", "started", actor="a")


def test_acid_coverage_counts_tests(wired):
    store, profile, _ = wired
    coverage = pipeline.acid_coverage(store, "demo:demo")
    assert coverage["total"] == 3
    assert coverage["with_tests"] == 2
    assert coverage["untested"] == 1
