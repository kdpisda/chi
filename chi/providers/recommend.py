"""Recommended fleet setup computed from what is actually installed/keyed."""

from chi.config import CoderCfg
from chi.providers.catalog import ModelInfo, ProviderInfo, cli_command

# strong roles get capable models; support roles get cheap ones
STRONG_PROVIDER_ORDER = ["anthropic", "openai", "gemini", "deepseek", "mistral", "xai"]
CHEAP_PROVIDER_ORDER = ["deepseek", "groq", "zhipuai", "minimax", "mistral", "gemini"]
ROLES_STRONG = ["orchestrator", "planner"]
ROLES_CHEAP = ["critic", "researcher"]


def _pick_model(models: list[ModelInfo], provider_order: list[str], strongest: bool) -> str | None:
    """Pick per provider preference; within a provider, cost is the capability proxy."""
    for provider in provider_order:
        candidates = [m for m in models if m.provider == provider and m.kind == "api"]
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
    """(default_coders, role_models, human summary) from detected providers/CLIs."""
    ready_clis = [p.key for p in providers if p.kind == "cli" and p.ready]
    coders: list[CoderCfg] = []
    summary: list[str] = []

    if "claude" in ready_clis:
        coders.append(CoderCfg(id=f"c{len(coders) + 1}", model="claude",
                               adapter="cli_subprocess", command=cli_command("claude")))
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
