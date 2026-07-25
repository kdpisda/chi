from chi.providers.catalog import (
    CLI_SUBSTRATES, ModelInfo, list_models, list_providers,
)

REGISTRY = {
    "anthropic": ["anthropic/claude-sonnet-5"],
    "deepseek": ["deepseek/deepseek-chat"],
    "obscureprov": ["obscureprov/model-1"],
}
COSTS = {
    "anthropic/claude-sonnet-5": {"input_cost_per_token": 3e-06,
                                   "output_cost_per_token": 1.5e-05},
}


def _validate_ready(model: str) -> dict:
    return {"keys_in_environment": model.startswith("anthropic/"),
            "missing_keys": [] if model.startswith("anthropic/") else ["DEEPSEEK_API_KEY"]}


def _which_claude_only(name: str):
    return "/usr/local/bin/claude" if name == "claude" else None


def test_list_providers_featured_and_cli() -> None:
    infos = list_providers(registry=REGISTRY, validate_fn=_validate_ready,
                           which_fn=_which_claude_only)
    by_key = {i.key: i for i in infos}
    assert by_key["anthropic"].ready is True
    assert by_key["deepseek"].ready is False and "DEEPSEEK_API_KEY" in by_key["deepseek"].detail
    assert "obscureprov" not in by_key  # not featured
    assert by_key["claude"].kind == "cli" and by_key["claude"].ready is True
    assert by_key["codex"].ready is False


def test_list_providers_all_includes_obscure() -> None:
    infos = list_providers(all_providers=True, registry=REGISTRY,
                           validate_fn=_validate_ready, which_fn=lambda n: None)
    assert any(i.key == "obscureprov" for i in infos)


def test_list_models_costs_and_cli() -> None:
    models = list_models(["anthropic", "claude"], registry=REGISTRY, cost_map=COSTS,
                         which_fn=_which_claude_only)
    api = [m for m in models if m.kind == "api"]
    cli = [m for m in models if m.kind == "cli"]
    assert api[0].id == "anthropic/claude-sonnet-5"
    assert api[0].input_cost_per_m == 3.0 and api[0].output_cost_per_m == 15.0
    assert cli[0].id == "claude" and cli[0].command == CLI_SUBSTRATES["claude"]


def test_list_models_skips_undetected_cli() -> None:
    models = list_models(["codex"], registry=REGISTRY, cost_map={},
                         which_fn=lambda n: None)
    assert models == []
