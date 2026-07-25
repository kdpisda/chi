import os
from pathlib import Path

from chi.cli import _coders_from_picks
from chi.providers.catalog import (
    CLI_SUBSTRATES, ModelInfo, ProviderInfo, cli_command, split_cli_pick,
)
from chi.providers.recommend import recommend_setup
from chi.session.engine import SessionEngine
from chi.userconfig import credentials_path, load_user_config

PROVIDERS = [
    ProviderInfo(key="anthropic", kind="api", ready=True, detail="key found"),
    ProviderInfo(key="deepseek", kind="api", ready=True, detail="key found"),
    ProviderInfo(key="claude", kind="cli", ready=True, detail="CLI on PATH"),
    ProviderInfo(key="codex", kind="cli", ready=False, detail="CLI not found"),
]
MODELS = [
    ModelInfo(id="anthropic/claude-opus-4-8", provider="anthropic", kind="api",
              input_cost_per_m=15.0, output_cost_per_m=75.0),
    ModelInfo(id="anthropic/claude-haiku-4-5", provider="anthropic", kind="api",
              input_cost_per_m=1.0, output_cost_per_m=5.0),
    ModelInfo(id="deepseek/deepseek-chat", provider="deepseek", kind="api",
              input_cost_per_m=0.3, output_cost_per_m=1.1),
    ModelInfo(id="claude", provider="claude", kind="cli", input_cost_per_m=None,
              output_cost_per_m=None, command=CLI_SUBSTRATES["claude"]),
]


def test_cli_command_and_split() -> None:
    assert "--model opus" in cli_command("claude", "opus")
    assert "-m gpt-5.6-codex" in cli_command("codex", "gpt-5.6-codex")
    assert cli_command("claude", "default") == CLI_SUBSTRATES["claude"]
    assert split_cli_pick("claude:opus") == ("claude", "opus")
    assert split_cli_pick("claude") == ("claude", None)
    assert split_cli_pick("anthropic/claude-sonnet-5") == ("anthropic/claude-sonnet-5", None)


def test_coders_from_picks_cli_variant() -> None:
    coders = _coders_from_picks(["claude:opus", "deepseek/deepseek-chat"], MODELS)
    assert coders[0].model == "claude:opus" and coders[0].adapter == "cli_subprocess"
    assert "--model opus" in coders[0].command
    assert coders[1].adapter == "litellm_loop"


def test_recommend_setup_prefers_cli_plus_strong_api() -> None:
    coders, roles, summary = recommend_setup(PROVIDERS, MODELS)
    assert coders[0].model == "claude" and coders[0].adapter == "cli_subprocess"
    assert coders[1].model == "anthropic/claude-opus-4-8"  # highest input cost = strongest
    assert roles["orchestrator"] == "anthropic/claude-opus-4-8"
    assert roles["critic"] == "deepseek/deepseek-chat"  # cheapest preferred provider
    assert any("c1: claude" in line for line in summary)


def test_recommend_setup_empty_when_nothing_ready() -> None:
    coders, roles, summary = recommend_setup(
        [ProviderInfo(key="anthropic", kind="api", ready=False, detail="missing")], []
    )
    assert coders == [] and roles == {}
    assert any("nothing detected" in line for line in summary)


def _patch_catalog(monkeypatch) -> None:
    import chi.providers.catalog as catalog

    monkeypatch.setattr(catalog, "list_providers", lambda **kw: PROVIDERS)
    monkeypatch.setattr(catalog, "list_models", lambda keys=None, **kw: MODELS)


def test_engine_setup_applies_recommendation(tmp_path: Path, monkeypatch) -> None:
    _patch_catalog(monkeypatch)
    engine = SessionEngine(runs_root=tmp_path / "runs")
    engine.ask_fn = lambda q, options: "apply"
    lines = engine.submit("/setup")
    assert any("applied recommended setup" in line for line in lines)
    cfg = load_user_config()
    assert cfg.default_coders[0].model == "claude"
    assert cfg.role_models["planner"] == "anthropic/claude-opus-4-8"


def test_first_run_setup_offers_and_applies(tmp_path: Path, monkeypatch) -> None:
    _patch_catalog(monkeypatch)
    engine = SessionEngine(runs_root=tmp_path / "runs")
    engine.ask_fn = lambda q, options: "recommended"
    lines = engine.maybe_first_run_setup()
    assert any("applied recommended setup" in line for line in lines)
    # second call: config exists now, no re-ask
    assert engine.maybe_first_run_setup() == []


def test_models_cli_pick_asks_for_variant(tmp_path: Path, monkeypatch) -> None:
    _patch_catalog(monkeypatch)
    engine = SessionEngine(runs_root=tmp_path / "runs",
                           picker_fn=lambda m, c, multi: ["claude"])
    engine.ask_fn = lambda q, options: "opus"
    lines = engine.submit("/models")
    assert any("claude:opus" in line for line in lines)
    coder = load_user_config().default_coders[0]
    assert "--model opus" in coder.command


def test_models_roles_flow(tmp_path: Path, monkeypatch) -> None:
    _patch_catalog(monkeypatch)
    engine = SessionEngine(runs_root=tmp_path / "runs",
                           picker_fn=lambda m, c, multi: ["deepseek/deepseek-chat"])
    engine.ask_fn = lambda q, options: "critic"
    lines = engine.submit("/models roles")
    assert lines == ["critic: deepseek/deepseek-chat"]
    assert load_user_config().role_models["critic"] == "deepseek/deepseek-chat"


def test_setkey_stores_and_activates(tmp_path: Path, monkeypatch) -> None:
    import chi.providers.catalog as catalog

    monkeypatch.setattr(catalog, "key_env_var", lambda p: "DEEPSEEK_API_KEY")
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    engine = SessionEngine(runs_root=tmp_path / "runs")
    engine.secret_fn = lambda prompt: "sk-live-123"
    lines = engine.submit("/setkey deepseek")
    assert any("deepseek is ready" in line for line in lines)
    assert "DEEPSEEK_API_KEY=sk-live-123" in credentials_path().read_text()
    assert os.environ["DEEPSEEK_API_KEY"] == "sk-live-123"
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
