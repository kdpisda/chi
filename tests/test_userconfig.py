import os
import stat
from pathlib import Path

from chi.config import CoderCfg
from chi.userconfig import (
    UserConfig, credentials_path, load_env, load_user_config, save_user_config,
    set_credential,
)


def test_round_trip_under_custom_dir(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("CHI_CONFIG_DIR", str(tmp_path / "cfg"))
    cfg = UserConfig(
        enabled_providers=["anthropic", "deepseek"],
        default_coders=[CoderCfg(id="c1", model="anthropic/claude-sonnet-5")],
    )
    save_user_config(cfg)
    loaded = load_user_config()
    assert loaded == cfg


def test_missing_file_gives_defaults(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("CHI_CONFIG_DIR", str(tmp_path / "empty"))
    cfg = load_user_config()
    assert cfg.enabled_providers == [] and cfg.default_coders == []


def test_set_credential_creates_600_and_upserts(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("CHI_CONFIG_DIR", str(tmp_path / "cfg"))
    set_credential("FOO_API_KEY", "old")
    set_credential("BAR_API_KEY", "bar")
    set_credential("FOO_API_KEY", "new")
    content = credentials_path().read_text().splitlines()
    assert "FOO_API_KEY=new" in content and "BAR_API_KEY=bar" in content
    assert len([l for l in content if l.startswith("FOO_API_KEY=")]) == 1
    mode = stat.S_IMODE(credentials_path().stat().st_mode)
    assert mode == 0o600


def test_load_env_project_wins(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("CHI_CONFIG_DIR", str(tmp_path / "cfg"))
    monkeypatch.delenv("SHARED_KEY", raising=False)
    monkeypatch.delenv("GLOBAL_ONLY", raising=False)
    set_credential("SHARED_KEY", "from-credentials")
    set_credential("GLOBAL_ONLY", "global")
    project = tmp_path / "proj"
    project.mkdir()
    (project / ".env").write_text("SHARED_KEY=from-project\n")
    load_env(project)
    assert os.environ["SHARED_KEY"] == "from-project"
    assert os.environ["GLOBAL_ONLY"] == "global"
    monkeypatch.delenv("SHARED_KEY", raising=False)
    monkeypatch.delenv("GLOBAL_ONLY", raising=False)
