from __future__ import annotations

from salt.ingest import ingest_repository


def test_content_hash_is_recorded(store, profile, repo):
    """ingest.INGEST.1"""
    ingest_repository(store, profile, "demo", repo)
    feature = store.find_feature("demo:demo")
    assert feature["content_hash"]
    assert len(feature["content_hash"]) == 64


def test_reingest_updates_without_duplicating(store, profile, repo):
    """ingest.INGEST.2"""
    ingest_repository(store, profile, "demo", repo)
    first = len(store.acids("demo:demo"))

    spec = repo / "features" / "demo" / "demo.feature.yaml"
    spec.write_text(spec.read_text().replace("2: Trims whitespace from the name", "2: Strips the name"))
    ingest_repository(store, profile, "demo", repo)

    acids = store.acids("demo:demo")
    assert len(acids) == first
    assert any("Strips" in acid["text"] for acid in acids)


def test_stale_features_are_removed(store, profile, repo):
    """ingest.INGEST.3"""
    ingest_repository(store, profile, "demo", repo)
    assert store.feature("demo:demo")

    extra = repo / "features" / "demo" / "other.feature.yaml"
    extra.write_text("feature:\n  name: other\n  draft: false\n")
    ingest_repository(store, profile, "demo", repo)
    assert store.feature("demo:other")

    extra.unlink()
    result = ingest_repository(store, profile, "demo", repo)
    assert store.feature("demo:other") is None
    assert result["removed"] == ["demo:other"]


def test_refs_are_recorded_per_acid(store, profile, repo):
    ingest_repository(store, profile, "demo", repo)
    counts = store.ref_counts("demo:demo")
    assert counts["demo.GREETING.1"]["code"] >= 1
    assert counts["demo.GREETING.1"]["test"] >= 1


def test_phases_are_created_for_every_feature(store, profile, repo):
    ingest_repository(store, profile, "demo", repo)
    states = store.phase_states("demo:demo")
    assert set(states) == set(profile.phase_names)
    assert all(row["state"] == "pending" for row in states.values())
