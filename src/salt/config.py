"""Configuration and paths.

Defaults must work with no config file at all: a fresh install that never writes
`config.toml` still has a data directory, a port, and no projects.

pipeline.PORTABILITY.2 — nothing here may hardcode an absolute home directory.
"""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

DEFAULT_PORT = 9131
DEFAULT_PROFILE = "standard"


class ConfigError(Exception):
    """A configuration file exists but cannot be used."""


def _xdg(env: str, fallback: str) -> Path:
    base = os.environ.get(env)
    if base:
        return Path(base)
    return Path.home() / fallback


def default_data_dir() -> Path:
    return _xdg("XDG_DATA_HOME", ".local/share") / "salt"


def config_dir() -> Path:
    return _xdg("XDG_CONFIG_HOME", ".config") / "salt"


def config_path() -> Path:
    return config_dir() / "config.toml"


@dataclass
class Project:
    name: str
    repo_path: str
    profile: str = DEFAULT_PROFILE


@dataclass
class Config:
    data_dir: Path = field(default_factory=default_data_dir)
    port: int = DEFAULT_PORT
    host: str = "127.0.0.1"
    profile: str = DEFAULT_PROFILE
    projects: list[Project] = field(default_factory=list)

    @property
    def db_path(self) -> Path:
        return self.data_dir / "salt.sqlite"

    @property
    def runs_dir(self) -> Path:
        return self.data_dir / "runs"

    def project(self, name: str) -> Project | None:
        for project in self.projects:
            if project.name == name:
                return project
        return None

    def add_project(self, project: Project) -> Project:
        existing = self.project(project.name)
        if existing:
            existing.repo_path = project.repo_path
            existing.profile = project.profile
            return existing
        self.projects.append(project)
        return project


def load(path: Path | None = None) -> Config:
    """Load configuration, falling back to defaults when no file exists."""
    path = path or config_path()
    if not path.exists():
        return Config()
    try:
        raw = tomllib.loads(path.read_text(encoding="utf-8"))
    except (tomllib.TOMLDecodeError, OSError) as exc:
        raise ConfigError(f"{path}: {exc}") from exc

    data_dir = raw.get("data_dir")
    config = Config(
        data_dir=Path(data_dir).expanduser() if data_dir else default_data_dir(),
        port=int(raw.get("port", DEFAULT_PORT)),
        host=str(raw.get("host", "127.0.0.1")),
        profile=str(raw.get("profile", DEFAULT_PROFILE)),
    )
    for entry in raw.get("projects", []) or []:
        try:
            config.projects.append(
                Project(
                    name=str(entry["name"]),
                    repo_path=str(Path(entry["path"]).expanduser()),
                    profile=str(entry.get("profile", config.profile)),
                )
            )
        except KeyError as exc:
            raise ConfigError(f"{path}: project entry missing {exc}") from exc
    return config


def save(config: Config, path: Path | None = None) -> Path:
    """Write the configuration back as TOML that we can read again."""
    path = path or config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "# Salt configuration. Every value here has a working default.",
        f'data_dir = "{config.data_dir}"',
        f"port = {config.port}",
        f'host = "{config.host}"',
        f'profile = "{config.profile}"',
        "",
    ]
    for project in config.projects:
        lines += [
            "[[projects]]",
            f'name = "{project.name}"',
            f'path = "{project.repo_path}"',
            f'profile = "{project.profile}"',
            "",
        ]
    path.write_text("\n".join(lines), encoding="utf-8")
    return path
