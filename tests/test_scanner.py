from __future__ import annotations

import json

from salt import scanner
from salt.scanner import parse_spec, scan_refs, spec_paths


def test_each_spec_file_becomes_a_feature(repo):
    """ingest.SPEC.1"""
    # ingest.SPEC.1
    paths = spec_paths(repo)
    assert len(paths) == 1
    spec = parse_spec(paths[0])
    assert spec.name == "demo"


def test_component_groups_become_acids(repo):
    """ingest.SPEC.2"""
    # ingest.SPEC.2
    spec = parse_spec(spec_paths(repo)[0])
    assert "demo.GREETING.1" in spec.acid_ids
    assert "demo.GREETING.2" in spec.acid_ids


def test_constraint_groups_become_acids(repo):
    """ingest.SPEC.3"""
    # ingest.SPEC.3
    spec = parse_spec(spec_paths(repo)[0])
    assert "demo.SAFE.1" in spec.acid_ids
    kinds = {acid.acid_id(spec.name): acid.kind for acid in spec.acids}
    assert kinds["demo.SAFE.1"] == "constraint"


def test_notes_and_deprecations_parse(tmp_path):
    """ingest.SPEC.4"""
    # ingest.SPEC.4
    path = tmp_path / "features" / "f.feature.yaml"
    path.parent.mkdir(parents=True)
    path.write_text(
        """
feature:
  name: f
  draft: true
  description: x
components:
  G:
    requirements:
      1:
        requirement: something
        note: because
      2:
        requirement: old
        deprecated: true
        replaced_by: ["f.G.1"]
"""
    )
    spec = parse_spec(path)
    assert spec.draft is True
    assert [acid.num for acid in spec.acids] == ["1", "2"]


def test_draft_flag_is_recorded(repo):
    """ingest.SPEC.5"""
    # ingest.SPEC.5
    path = spec_paths(repo)[0]
    path.write_text(path.read_text().replace("draft: false", "draft: true"))
    assert parse_spec(path).draft is True


def test_standalone_comment_line_is_a_reference(repo):
    """ingest.REFS.1"""
    # ingest.REFS.1
    known = {"demo.GREETING.1"}
    refs = scan_refs(repo, known)
    assert any(kind == "code" for _, _, _, kind in refs)


def test_inline_trailing_comment_is_not_a_reference(tmp_path):
    """ingest.REFS.2"""
    # ingest.REFS.2
    (tmp_path / "x.py").write_text("x = 1  # demo.GREETING.1\n")
    assert scan_refs(tmp_path, {"demo.GREETING.1"}) == []


def test_json_acid_array_is_a_reference(tmp_path):
    """ingest.REFS.3"""
    # ingest.REFS.3
    (tmp_path / "graph.json").write_text(
        json.dumps({"_acid": ["demo.GREETING.1 — a note"], "nodes": []})
    )
    refs = scan_refs(tmp_path, {"demo.GREETING.1"})
    assert len(refs) == 1
    assert refs[0][0] == "demo.GREETING.1"


def test_test_paths_are_classified_as_tests(repo):
    """ingest.REFS.4"""
    # ingest.REFS.4
    refs = scan_refs(repo, {"demo.GREETING.1", "demo.GREETING.2"})
    kinds = {path: kind for _, path, _, kind in refs}
    assert kinds["tests/test_app.py"] == "test"
    assert kinds["src/app.py"] == "code"


def test_unknown_acids_are_ignored(tmp_path):
    (tmp_path / "x.py").write_text("# other.FEATURE.9\n")
    assert scan_refs(tmp_path, {"demo.GREETING.1"}) == []


def test_acid_regex_shape():
    assert scanner.ACID_RE.match("demo.GREETING.1").group(1) == "demo.GREETING.1"
    assert scanner.ACID_RE.match("demo.GREETING.1-1").group(1) == "demo.GREETING.1-1"
