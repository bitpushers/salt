from __future__ import annotations

from pathlib import Path

SRC = Path(__file__).parents[1] / "src" / "salt"
ADAPTERS = SRC / "adapters"

HARNESS_NAMES = ("hermes", "openclaw", "claude", "codex", "mission control")
ABSOLUTE_MARKERS = ("/Users/", "C:\\\\Users", "~/src")


def _sources() -> list[Path]:
    return [path for path in SRC.rglob("*.py") if ADAPTERS not in path.parents]


def test_no_module_names_a_harness():
    """pipeline.PORTABILITY.1 — adapters/ is the only place allowed to know a harness."""
    # pipeline.PORTABILITY.1
    offenders = []
    for path in _sources():
        text = path.read_text().lower()
        for name in HARNESS_NAMES:
            if name in text:
                offenders.append(f"{path.name}: {name}")
    assert offenders == []


def test_no_module_hardcodes_a_home_directory():
    """pipeline.PORTABILITY.2"""
    # pipeline.PORTABILITY.2
    offenders = []
    for path in _sources():
        text = path.read_text()
        for marker in ABSOLUTE_MARKERS:
            if marker in text:
                offenders.append(f"{path.name}: {marker}")
    assert offenders == []


def test_there_is_no_dashboard_dependency():
    """Salt is self-contained: it does not import another project's console."""
    offenders = []
    for path in _sources():
        text = path.read_text().lower()
        for name in ("mission_control", "missioncontrol"):
            if name in text:
                offenders.append(f"{path.name}: {name}")
    assert offenders == []
