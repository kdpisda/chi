"""Global user configuration: enabled providers, default coders, credentials."""

import os
from pathlib import Path

import yaml
from pydantic import BaseModel, Field

from chi.config import CoderCfg


def config_dir() -> Path:
    """Chi's global config directory ($CHI_CONFIG_DIR override, else ~/.config/chi)."""
    root = Path(os.environ.get("CHI_CONFIG_DIR", "~/.config/chi")).expanduser()
    root.mkdir(parents=True, exist_ok=True)
    return root


class UserConfig(BaseModel):
    enabled_providers: list[str] = Field(default_factory=list)
    default_coders: list[CoderCfg] = Field(default_factory=list)


def _config_path() -> Path:
    return config_dir() / "config.yaml"


def load_user_config() -> UserConfig:
    """Load the global config; missing or unreadable file yields defaults."""
    path = _config_path()
    if not path.exists():
        return UserConfig()
    try:
        data = yaml.safe_load(path.read_text()) or {}
        return UserConfig.model_validate(data)
    except (yaml.YAMLError, ValueError):
        return UserConfig()


def save_user_config(cfg: UserConfig) -> Path:
    """Persist the global config; returns its path."""
    path = _config_path()
    path.write_text(yaml.safe_dump(cfg.model_dump(mode="json"), sort_keys=False))
    return path


def credentials_path() -> Path:
    """Path of the global credentials env file."""
    return config_dir() / "credentials.env"


def set_credential(env_var: str, value: str) -> Path:
    """Upsert VAR=value into credentials.env (created chmod 600)."""
    path = credentials_path()
    lines: list[str] = []
    if path.exists():
        lines = [
            line for line in path.read_text().splitlines()
            if line.strip() and not line.startswith(f"{env_var}=")
        ]
    lines.append(f"{env_var}={value}")
    path.write_text("\n".join(lines) + "\n")
    path.chmod(0o600)
    return path


def load_env(project_dir: Path = Path(".")) -> None:
    """Load project .env then global credentials; earlier wins (dotenv no-override)."""
    from dotenv import load_dotenv

    load_dotenv(Path(project_dir) / ".env")
    creds = credentials_path()
    if creds.exists():
        load_dotenv(creds)
