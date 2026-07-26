"""Recommended fleet setup computed from what is actually installed/keyed."""

from chi.config import CoderCfg
from chi.providers.catalog import ModelInfo, ProviderInfo, cli_command

# strong roles get capable models; support roles get cheap ones
STRONG_PROVIDER_ORDER = ["anthropic", "openai", "gemini", "deepseek", "mistral", "xai"]
CHEAP_PROVIDER_ORDER = ["deepseek", "groq", "zhipuai", "minimax", "mistral", "gemini"]
ROLES_STRONG = ["orchestrator", "planner"]
ROLES_CHEAP = ["critic", "researcher"]


def _is_stale_snapshot(model_id: str) -> bool:
    """True for date-pinned snapshots more than a year old (e.g. *-20240229)."""
    import re
    from datetime import date, datetime, timedelta

    match = re.search(r"-(20\d{6})$", model_id)
    if match is None:
        return False
    try:
        snapshot = datetime.strptime(match.group(1), "%Y%m%d").date()
    except ValueError:
        return False
    return snapshot < date.today() - timedelta(days=365)


def _eligible(models: list[ModelInfo], ready_providers: set[str]) -> list[ModelInfo]:
    """Only models the user can actually call, current-generation, chat-capable."""
    return [
        m for m in models
        if m.kind == "api"
        and m.provider in ready_providers
        and not m.deprecated
        and not _is_stale_snapshot(m.id)
    ]


def _pick_model(models: list[ModelInfo], provider_order: list[str], strongest: bool) -> str | None:
    """Pick per provider preference; within a provider, cost is the capability proxy."""
    for provider in provider_order:
        candidates = [m for m in models if m.provider == provider]
        if not candidates:
            continue
        keyed = sorted(
            candidates,
            key=lambda m: (m.input_cost_per_m if m.input_cost_per_m is not None else 0.0),
            reverse=strongest,
        )
        return keyed[0].id
    return None


def recommend_setup(
    providers: list[ProviderInfo], models: list[ModelInfo]
) -> tuple[list[CoderCfg], dict[str, str], list[str]]:
    """(default_coders, role_models, human summary) from detected providers/CLIs.

    Only recommends models the user can call today: ready (keyed) providers,
    not deprecated, not stale snapshots — a recommendation that needs a key
    the user doesn't have is worse than none.
    """
    ready_clis = [p.key for p in providers if p.kind == "cli" and p.ready]
    ready_apis = {p.key for p in providers if p.kind == "api" and p.ready}
    models = _eligible(models, ready_apis)
    coders: list[CoderCfg] = []
    summary: list[str] = []

    if "claude" in ready_clis:
        # claude supports --output-format stream-json, so use the json_stream
        # adapter: chi sees every tool call and gets real cost/token accounting
        coders.append(CoderCfg(id=f"c{len(coders) + 1}", model="claude",
                               adapter="json_stream", command=cli_command("claude")))
    elif "codex" in ready_clis:
        coders.append(CoderCfg(id=f"c{len(coders) + 1}", model="codex",
                               adapter="cli_subprocess", command=cli_command("codex")))

    strong = _pick_model(models, STRONG_PROVIDER_ORDER, strongest=True)
    if strong is not None:
        coders.append(CoderCfg(id=f"c{len(coders) + 1}", model=strong,
                               adapter="litellm_loop"))

    role_models: dict[str, str] = {}
    cheap = _pick_model(models, CHEAP_PROVIDER_ORDER, strongest=False)
    for role in ROLES_STRONG:
        if strong is not None:
            role_models[role] = strong
    for role in ROLES_CHEAP:
        if cheap is not None:
            role_models[role] = cheap

    for coder in coders:
        summary.append(f"  {coder.id}: {coder.model} ({coder.adapter})")
    for role, model in role_models.items():
        summary.append(f"  {role}: {model}")
    if not summary:
        summary.append("  nothing detected — /vendors to enable providers,"
                       " /setkey <provider> to store a key")
    return coders, role_models, summary
