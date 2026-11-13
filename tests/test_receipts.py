from __future__ import annotations

import pytest

from salt import pipeline
from salt.receipts import git_sha, run_command


def test_receipt_captures_command_cwd_exit_and_sha(git_repo):
    """pipeline.RECEIPT.1"""
    # pipeline.RECEIPT.1
    receipt = run_command("true", cwd=git_repo)
    assert receipt.command == "true"
    assert receipt.cwd == str(git_repo)
    assert receipt.exit_code == 0
    assert receipt.commit_sha and len(receipt.commit_sha) == 40


def test_actor_and_role_are_recorded(store):
    """pipeline.RECEIPT.2"""
    # pipeline.RECEIPT.2
    store.add_receipt(
        "demo:demo", "implement", "pytest -q", "/tmp", 0, "abc", "digest", "tail",
        "implementer-1", "implementer",
    )
    row = store.valid_receipt("demo:demo", "implement")
    assert row["actor"] == "implementer-1"
    assert row["role"] == "implementer"


def test_a_failing_command_cannot_satisfy_a_phase(wired):
    """pipeline.RECEIPT.3"""
    # pipeline.RECEIPT.3
    store, profile, repo = wired
    feature_id = "demo:demo"
    receipt = run_command("false", cwd=repo)
    store.add_receipt(
        feature_id, "spec", receipt.command, receipt.cwd, receipt.exit_code,
        None, receipt.digest, receipt.output_tail, "author", "author",
    )
    assert store.valid_receipt(feature_id, "spec") is None
    with pytest.raises(pipeline.TransitionError):
        pipeline.set_phase(
            store, profile, feature_id, "spec", "done", actor="author", role="author"
        )


def test_digest_is_stable_for_identical_output(tmp_path):
    first = run_command("echo hello", cwd=tmp_path)
    second = run_command("echo hello", cwd=tmp_path)
    assert first.digest == second.digest


def test_git_sha_is_none_outside_a_repository(tmp_path):
    assert git_sha(tmp_path) is None


def test_output_tail_is_bounded(tmp_path):
    receipt = run_command("printf 'x\\n%.0s' $(seq 1 60)", cwd=tmp_path)
    assert len(receipt.output_tail.splitlines()) <= 20
