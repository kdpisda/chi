"""Provider and model catalog: litellm registry + installed vendor CLIs."""

import shutil
from dataclasses import dataclass
from typing import Callable

FEATURED_PROVIDERS = [
    "anthropic", "openai", "deepseek", "zhipuai", "gemini", "groq",
    "minimax", "mistral", "xai", "openrouter",
]

CLI_SUBSTRATES: dict[str, str] = {
    "claude": ('claude -p "Follow the instructions in {prompt_file} exactly."'
               " --allowedTools Bash,Edit,Write,Read"),
    "codex": 'codex exec --full-auto "Follow the instructions in {prompt_file} exactly."',
    "grok": 'grok --no-interactive "Follow the instructions in {prompt_file} exactly."',
}

# model variants selectable per CLI substrate ("default" = the CLI's own setting)
CLI_MODEL_CHOICES: dict[str, list[str]] = {
    "claude": ["default", "opus", "sonnet", "haiku"],
    "codex": ["default", "gpt-5.6-codex", "gpt-5.6"],
    "grok": ["default", "grok-4", "grok-code-fast"],
}
CLI_MODEL_FLAGS: dict[str, str] = {
    "claude": "--model {model}",
    "codex": "-m {model}",
    "grok": "--model {model}",
}


def cli_command(cli: str, model: str | None = None) -> str:
    """Command template for a CLI substrate, with the model flag when not default."""
    base = CLI_SUBSTRATES[cli]
    if model and model != "default":
        return f"{base} {CLI_MODEL_FLAGS[cli].format(model=model)}"
    return base


def split_cli_pick(pick: str) -> tuple[str, str | None]:
    """'claude:opus' -> ('claude', 'opus'); 'claude' -> ('claude', None)."""
    if ":" in pick and pick.split(":", 1)[0] in CLI_SUBSTRATES:
        cli, model = pick.split(":", 1)
        return cli, model or None
    return pick, None

KEY_ENV_FALLBACK: dict[str, str] = {
    "anthropic": "ANTHROPIC_API_KEY",
    "openai": "OPENAI_API_KEY",
    "deepseek": "DEEPSEEK_API_KEY",
    "zhipuai": "ZHIPUAI_API_KEY",
    "gemini": "GEMINI_API_KEY",
    "groq": "GROQ_API_KEY",
    "minimax": "MINIMAX_API_KEY",
    "mistral": "MISTRAL_API_KEY",
    "xai": "XAI_API_KEY",
    "openrouter": "OPENROUTER_API_KEY",
}


@dataclass
class ProviderInfo:
    key: str
    kind: str  # "api" | "cli"
    ready: bool
    detail: str


@dataclass
class ModelInfo:
    id: str
    provider: str
    kind: str  # "api" | "cli"
    input_cost_per_m: float | None
    output_cost_per_m: float | None
    command: str | None = None
    mode: str | None = None  # litellm mode ("chat", "embedding", …); None for CLIs
    deprecated: bool = False  # past its litellm deprecation_date


def _is_deprecated(deprecation_date: str | None) -> bool:
    """True when litellm's deprecation_date (YYYY-MM-DD) is in the past."""
    if not deprecation_date:
        return False
    from datetime import date, datetime

    try:
        return datetime.strptime(deprecation_date, "%Y-%m-%d").date() <= date.today()
    except ValueError:
        return False


def _default_registry() -> dict:
    import litellm

    return litellm.models_by_provider


def _default_cost_map() -> dict:
    import litellm

    return litellm.model_cost


def _default_validate(model: str) -> dict:
    import litellm

    return litellm.validate_environment(model=model)


def _readiness(provider: str, models: list[str], validate_fn: Callable) -> tuple[bool, str]:
    """API-provider readiness: does the environment hold the needed key?"""
    if not models:
        return False, "no models in registry"
    try:
        result = validate_fn(models[0])
    except Exception as exc:  # unknown model/provider quirks in litellm
        return False, f"validate failed: {exc}"[:120]
    if result.get("keys_in_environment"):
        return True, "key found"
    missing = result.get("missing_keys") or []
    if missing:
        return False, "missing " + ", ".join(missing)
    return False, "key not found"


def list_providers(
    all_providers: bool = False,
    registry: dict | None = None,
    validate_fn: Callable | None = None,
    which_fn: Callable | None = None,
) -> list[ProviderInfo]:
    """API providers (featured subset unless all_providers) plus detected CLIs."""
    registry = registry if registry is not None else _default_registry()
    validate_fn = validate_fn or _default_validate
    which_fn = which_fn or shutil.which
    keys = list(registry.keys()) if all_providers else [
        p for p in FEATURED_PROVIDERS if p in registry
    ]
    out: list[ProviderInfo] = []
    for key in keys:
        ready, detail = _readiness(key, list(registry.get(key, [])), validate_fn)
        out.append(ProviderInfo(key=key, kind="api", ready=ready, detail=detail))
    for cli in CLI_SUBSTRATES:
        found = which_fn(cli) is not None
        out.append(ProviderInfo(
            key=cli, kind="cli", ready=found,
            detail="CLI on PATH" if found else "CLI not found",
        ))
    return out


def list_models(
    provider_keys: list[str] | None = None,
    registry: dict | None = None,
    cost_map: dict | None = None,
    which_fn: Callable | None = None,
) -> list[ModelInfo]:
    """Models for the given providers (all featured when None), plus detected CLIs."""
    registry = registry if registry is not None else _default_registry()
    cost_map = cost_map if cost_map is not None else _default_cost_map()
    which_fn = which_fn or shutil.which
    api_keys = provider_keys if provider_keys is not None else [
        p for p in FEATURED_PROVIDERS if p in registry
    ]
    out: list[ModelInfo] = []
    for provider in api_keys:
        for model in registry.get(provider, []):
            cost = cost_map.get(model, {})
            mode = cost.get("mode")
            if mode not in (None, "chat"):
                continue  # embeddings/audio/image models don't belong in coder pickers
            in_cost = cost.get("input_cost_per_token")
            out_cost = cost.get("output_cost_per_token")
            out.append(ModelInfo(
                id=model, provider=provider, kind="api",
                input_cost_per_m=None if in_cost is None else in_cost * 1_000_000,
                output_cost_per_m=None if out_cost is None else out_cost * 1_000_000,
                mode=mode,
                deprecated=_is_deprecated(cost.get("deprecation_date")),
            ))
    for cli, command in CLI_SUBSTRATES.items():
        if provider_keys is not None and cli not in provider_keys:
            continue
        if which_fn(cli) is not None:
            out.append(ModelInfo(id=cli, provider=cli, kind="cli",
                                 input_cost_per_m=None, output_cost_per_m=None,
                                 command=command))
    return out


def key_env_var(provider: str, validate_fn: Callable | None = None) -> str | None:
    """API-key env var name for a provider (validate_environment, else fallback map)."""
    validate_fn = validate_fn or _default_validate
    try:
        import litellm

        models = litellm.models_by_provider.get(provider, [])
        if models:
            missing = validate_fn(models[0]).get("missing_keys") or []
            if missing:
                return str(missing[0])
    except Exception:  # fall through to the curated map
        pass
    return KEY_ENV_FALLBACK.get(provider)
