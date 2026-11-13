from __future__ import annotations

import pytest

from salt import config as config_mod


def test_defaults_need_no_config_file(tmp_path, monkeypatch):
    """pipeline.PORTABILITY.3 — a fresh install works with no configuration."""
    # pipeline.PORTABILITY.3
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    config = config_mod.load(tmp_path / "missing.toml")
    assert config.port == config_mod.DEFAULT_PORT
    assert config.projects == []
    assert config.data_dir == tmp_path / "data" / "salt"


def test_toml_overrides_are_used(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text(
        'data_dir = "%s"\nport = 9999\n\n[[projects]]\nname = "x"\npath = "%s"\n'
        % (tmp_path / "d", tmp_path)
    )
    config = config_mod.load(path)
    assert config.port == 9999
    assert config.projects[0].name == "x"


def test_invalid_toml_names_the_path(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text("port = [not toml")
    with pytest.raises(config_mod.ConfigError) as excinfo:
        config_mod.load(path)
    assert str(path) in str(excinfo.value)


def test_saved_config_round_trips(tmp_path):
    path = tmp_path / "config.toml"
    original = config_mod.Config(data_dir=tmp_path / "d")
    original.add_project(config_mod.Project(name="p", repo_path=str(tmp_path)))
    config_mod.save(original, path)
    loaded = config_mod.load(path)
    assert loaded.port == original.port
    assert loaded.projects[0].repo_path == str(tmp_path)
