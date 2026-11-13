from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from salt import pipeline
from salt.store import Store

SPEC = """
feature:
  name: demo
  product: demo
  draft: false
  description: |
    A demo feature.

components:
  GREETING:
    name: Greeting
    description: Says hello.
    requirements:
      1: Returns a greeting for a name
      2: Trims whitespace from the name

constraints:
  SAFE:
    name: Safety
    description: Does not crash on empty input.
    requirements:
      1: Returns an empty greeting for an empty name
"""

APP = '''
# demo.GREETING.1 — returns a greeting
def greet(name):
    return f"hello {name}".strip()
'''

TEST_FILE = '''
# demo.GREETING.1
def test_greets():
    assert True


# demo.GREETING.2
def test_trims():
    assert True
'''


@pytest.fixture
def store(tmp_path) -> Store:
    return Store(tmp_path / "salt.sqlite")


@pytest.fixture
def profile() -> pipeline.Profile:
    return pipeline.load_profile("standard")


@pytest.fixture
def repo(tmp_path) -> Path:
    root = tmp_path / "demo-repo"
    (root / "features" / "demo").mkdir(parents=True)
    (root / "src").mkdir()
    (root / "tests").mkdir()
    (root / "features" / "demo" / "demo.feature.yaml").write_text(SPEC.strip() + "\n")
    (root / "src" / "app.py").write_text(APP)
    (root / "tests" / "test_app.py").write_text(TEST_FILE)
    return root


@pytest.fixture
def git_repo(repo) -> Path:
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "t@example.com"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "T"], cwd=repo, check=True)
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-qm", "init"], cwd=repo, check=True)
    return repo


@pytest.fixture
def wired(store, profile, repo):
    """A store with the demo project ingested, ready for phase work."""
    from salt.ingest import ingest_repository

    ingest_repository(store, profile, "demo", repo)
    return store, profile, repo
