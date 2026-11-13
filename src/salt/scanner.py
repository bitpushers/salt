"""Spec parsing and ACID reference scanning.

ingest.SPEC.* — `features/**/*.feature.yaml` becomes features and ACIDs.
ingest.REFS.* — an ACID counts as a reference only when it starts a standalone comment
line (an ACID appended to code after the code is not detected). JSON files may list ACIDs
in a top-level `_acid` array instead.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml

ACID_RE = re.compile(r"\b([a-z][a-z0-9_]*\.[A-Z][A-Z0-9_]*\.[0-9]+(?:-[0-9]+)?)\b")

COMMENT_PREFIXES = ("#", "//", "/*", "*", "--", "<!--", ";")

SKIP_DIRS = {
    ".git",
    ".venv",
    "venv",
    "node_modules",
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    "dist",
    "build",
    ".eggs",
    "runs",
    ".agents",
}

TEXT_SUFFIXES = {
    ".py", ".js", ".jsx", ".ts", ".tsx", ".json", ".md", ".html", ".css", ".yml",
    ".yaml", ".toml", ".cfg", ".ini", ".sh", ".bash", ".zsh", ".rs", ".go", ".java",
    ".kt", ".c", ".h", ".cpp", ".hpp", ".rb", ".php", ".swift", ".sql", ".txt",
}
ACID_COMMENT_SUFFIXES = TEXT_SUFFIXES - {".md", ".html", ".css", ".txt"}


@dataclass
class ParsedAcid:
    group: str
    num: str
    text: str
    kind: str  # component | constraint

    def acid_id(self, feature_name: str) -> str:
        return f"{feature_name}.{self.group}.{self.num}"


@dataclass
class ParsedFeature:
    name: str
    draft: bool
    description: str
    acids: list[ParsedAcid] = field(default_factory=list)
    content_hash: str = ""

    @property
    def acid_ids(self) -> list[str]:
        return [acid.acid_id(self.name) for acid in self.acids]


def _requirement_text(value: object) -> str:
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, dict):
        text = value.get("requirement") or value.get("text") or ""
        return str(text).strip()
    return str(value).strip()


def spec_paths(repo: Path) -> list[Path]:
    features_dir = repo / "features"
    if not features_dir.is_dir():
        return []
    return sorted(p for p in features_dir.rglob("*.feature.yaml") if p.is_file())


# ingest.SPEC.1
# ingest.SPEC.2
# ingest.SPEC.3
# ingest.SPEC.4
# ingest.SPEC.5
def parse_spec(path: Path) -> ParsedFeature:
    raw_bytes = path.read_bytes()
    data = yaml.safe_load(raw_bytes.decode("utf-8")) or {}
    meta = data.get("feature") or {}
    name = str(meta.get("name") or path.name.replace(".feature.yaml", ""))
    parsed = ParsedFeature(
        name=name,
        draft=bool(meta.get("draft", False)),
        description=str(meta.get("description") or "").strip(),
        content_hash=hashlib.sha256(raw_bytes).hexdigest(),
    )
    for section, kind in (("components", "component"), ("constraints", "constraint")):
        groups = data.get(section) or {}
        if not isinstance(groups, dict):
            continue
        for group_name, body in groups.items():
            if not isinstance(body, dict):
                continue
            requirements = body.get("requirements") or {}
            if not isinstance(requirements, dict):
                continue
            for num, value in requirements.items():
                text = _requirement_text(value)
                if not text:
                    continue
                parsed.acids.append(
                    ParsedAcid(group=str(group_name), num=str(num), text=text, kind=kind)
                )
    return parsed


# ingest.REFS.4
def _is_test_path(relative: Path) -> bool:
    parts = {part.lower() for part in relative.parts}
    if "tests" in parts or "test" in parts:
        return True
    name = relative.name.lower()
    return name.startswith("test_") or name.endswith("_test.py") or ".test." in name


# ingest.REFS.1
# ingest.REFS.2
def _acid_comments(line: str) -> list[str]:
    stripped = line.lstrip()
    if not stripped.startswith(COMMENT_PREFIXES):
        return []
    return [m.group(1) for m in ACID_RE.finditer(stripped)]


# ingest.REFS.3
def _json_acids(text: str) -> list[str]:
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return []
    if not isinstance(data, dict):
        return []
    entries = data.get("_acid")
    if not isinstance(entries, list):
        return []
    found: list[str] = []
    for entry in entries:
        found.extend(m.group(1) for m in ACID_RE.finditer(str(entry)))
    return found


def scan_refs(repo: Path, known_acids: set[str]) -> list[tuple[str, str, int, str]]:
    """Return (acid_id, file_path, line, kind) for every citation of a known ACID."""
    refs: list[tuple[str, str, int, str]] = []
    for path in sorted(repo.rglob("*")):
        if not path.is_file() or path.suffix.lower() not in TEXT_SUFFIXES:
            continue
        relative = path.relative_to(repo)
        if any(part in SKIP_DIRS for part in relative.parts):
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        kind = "test" if _is_test_path(relative) else "code"
        if path.suffix.lower() == ".json":
            for acid_id in _json_acids(text):
                if acid_id in known_acids:
                    refs.append((acid_id, str(relative), 1, kind))
            continue
        if path.suffix.lower() not in ACID_COMMENT_SUFFIXES:
            continue
        for number, line in enumerate(text.splitlines(), start=1):
            for acid_id in _acid_comments(line):
                if acid_id in known_acids:
                    refs.append((acid_id, str(relative), number, kind))
    return refs
