"""Global user configuration: enabled providers, default coders, credentials,
plus chi's system-wide data home (runs, session registry, input history)."""

import json
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


def data_dir() -> Path:
    """Chi's global data directory ($CHI_DATA_DIR override, else ~/.local/share/chi)."""
    root = Path(os.environ.get("CHI_DATA_DIR", "~/.local/share/chi")).expanduser()
    root.mkdir(parents=True, exist_ok=True)
    return root


def default_runs_root() -> Path:
    """System-wide runs directory — history is global, not per-project."""
    return data_dir() / "runs"


def record_session(record: dict) -> None:
    """Append one session record to the global registry (best-effort)."""
    try:
        with open(data_dir() / "sessions.jsonl", "a") as f:
            f.write(json.dumps(record, sort_keys=True) + "\n")
    except OSError:
        pass  # a registry hiccup must never kill a run


def list_sessions() -> list[dict]:
    """All known sessions, newest first; the last record per run_id wins."""
    path = data_dir() / "sessions.jsonl"
    if not path.exists():
        return []
    by_id: dict[str, dict] = {}
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        run_id = record.get("run_id")
        if run_id:
            by_id[run_id] = {**by_id.get(run_id, {}), **record}
    return sorted(by_id.values(), key=lambda r: r.get("started_at", ""), reverse=True)


def history_path() -> Path:
    """Global prompt-history file shared by every chi session."""
    return data_dir() / "input_history"


def load_history(limit: int = 500) -> list[str]:
    """Recent prompt history, oldest first."""
    path = history_path()
    if not path.exists():
        return []
    return path.read_text().splitlines()[-limit:]


def append_history(text: str) -> None:
    """Append one submitted line to the global prompt history (best-effort)."""
    if not text.strip():
        return
    try:
        with open(history_path(), "a") as f:
            f.write(text + "\n")
    except OSError:
        pass


class UserConfig(BaseModel):
    enabled_providers: list[str] = Field(default_factory=list)
    default_coders: list[CoderCfg] = Field(default_factory=list)
    role_models: dict[str, str] = Field(default_factory=dict)  # orchestrator/planner/…


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
    """Upsert VAR=value into credentials.env (file is created 0600, never wider)."""
    path = credentials_path()
    lines: list[str] = []
    if path.exists():
        lines = [
            line for line in path.read_text().splitlines()
            if line.strip() and not line.startswith(f"{env_var}=")
        ]
    lines.append(f"{env_var}={value}")
    # O_CREAT with mode 0600 so the secret is never on disk world-readable,
    # even briefly; chmod afterwards repairs pre-existing wider files.
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write("\n".join(lines) + "\n")
    path.chmod(0o600)
    return path


def load_env(project_dir: Path = Path(".")) -> None:
    """Load project .env then global credentials; earlier wins (dotenv no-override)."""
    from dotenv import load_dotenv

    load_dotenv(Path(project_dir) / ".env")
    creds = credentials_path()
    if creds.exists():
        load_dotenv(creds)
