import json
from pathlib import Path

import pytest
import yaml
from typer.testing import CliRunner

import chi.cli as cli_mod
from chi.cli import app
from chi.config import CoderCfg, FleetConfig, resolve_coders
from chi.providers.catalog import CLI_SUBSTRATES, ModelInfo, ProviderInfo
from chi.userconfig import credentials_path, load_user_config, save_user_config, UserConfig

runner = CliRunner()

PROVIDERS = [
    ProviderInfo(key="anthropic", kind="api", ready=True, detail="key found"),
    ProviderInfo(key="deepseek", kind="api", ready=False, detail="missing DEEPSEEK_API_KEY"),
    ProviderInfo(key="claude", kind="cli", ready=True, detail="CLI on PATH"),
]
MODELS = [
    ModelInfo(id="anthropic/claude-sonnet-5", provider="anthropic", kind="api",
              input_cost_per_m=3.0, output_cost_per_m=15.0),
    ModelInfo(id="claude", provider="claude", kind="cli",
              input_cost_per_m=None, output_cost_per_m=None,
              command=CLI_SUBSTRATES["claude"]),
]


@pytest.fixture(autouse=True)
def _isolated_config(tmp_path, monkeypatch):
    monkeypatch.setenv("CHI_CONFIG_DIR", str(tmp_path / "cfg"))
    monkeypatch.setattr(cli_mod, "list_providers", lambda **kw: PROVIDERS)
    monkeypatch.setattr(cli_mod, "list_models", lambda keys: MODELS)


def test_providers_table_and_enable(tmp_path: Path) -> None:
    result = runner.invoke(app, ["providers", "--enable", "anthropic,claude"])
    assert result.exit_code == 0
    assert "anthropic" in result.output and "missing DEEPSEEK_API_KEY" in result.output
    assert load_user_config().enabled_providers == ["anthropic", "claude"]


def test_vendors_alias_works(tmp_path: Path) -> None:
    result = runner.invoke(app, ["vendors", "--enable", "anthropic"])
    assert result.exit_code == 0


def test_set_key_writes_credentials(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(cli_mod, "key_env_var", lambda p: "DEEPSEEK_API_KEY")
    result = runner.invoke(app, ["providers", "--set-key", "deepseek"], input="sk-test\n")
    assert result.exit_code == 0
    assert "DEEPSEEK_API_KEY=sk-test" in credentials_path().read_text()


def test_models_pick_writes_global_defaults(tmp_path: Path) -> None:
    result = runner.invoke(app, ["models", "--pick", "anthropic/claude-sonnet-5,claude"])
    assert result.exit_code == 0
    coders = load_user_config().default_coders
    assert coders[0].model == "anthropic/claude-sonnet-5"
    assert coders[0].adapter == "litellm_loop"
    assert coders[1].adapter == "cli_subprocess"
    assert coders[1].command == CLI_SUBSTRATES["claude"]


def test_models_pick_into_fleet_preserves_other_keys(tmp_path: Path) -> None:
    fleet_path = tmp_path / "fleet.yaml"
    fleet_path.write_text(yaml.safe_dump({
        "run_name": "x", "problem": "problems/optimize_function",
        "budgets": {"total_usd": 9.0},
        "coders": [{"id": "old", "model": "m"}],
    }))
    result = runner.invoke(app, ["models", "--pick", "claude", "--fleet", str(fleet_path)])
    assert result.exit_code == 0
    data = yaml.safe_load(fleet_path.read_text())
    assert data["budgets"]["total_usd"] == 9.0
    assert data["coders"][0]["model"] == "claude"
    assert data["coders"][0]["adapter"] == "cli_subprocess"


def test_models_interactive_uses_fuzzy_picker(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(cli_mod, "fuzzy_select", lambda m, c, multi: ["claude"])
    result = runner.invoke(app, ["models"])
    assert result.exit_code == 0
    assert load_user_config().default_coders[0].model == "claude"


def test_resolve_coders_precedence(tmp_path: Path) -> None:
    fleet = FleetConfig(run_name="x", problem=Path("p"),
                        coders=[CoderCfg(id="f", model="fleet-model")])
    assert resolve_coders(fleet)[0].model == "fleet-model"
    empty = FleetConfig(run_name="x", problem=Path("p"))
    save_user_config(UserConfig(default_coders=[CoderCfg(id="d", model="default-model")]))
    assert resolve_coders(empty)[0].model == "default-model"
    save_user_config(UserConfig())
    with pytest.raises(ValueError):
        resolve_coders(empty)


def test_run_uses_default_coders_when_fleet_omits(tmp_path: Path) -> None:
    from chi.orchestrator.loop import start_run

    script = tmp_path / "script.json"
    script.write_text(json.dumps([
        "import itertools\n\n\ndef solve(xs: list[float]) -> list[float]:\n"
        "    return list(itertools.accumulate(xs))\n"
    ]))
    save_user_config(UserConfig(default_coders=[
        CoderCfg(id="d1", model="scripted", adapter="scripted", script=str(script))
    ]))
    fleet = FleetConfig.model_validate({
        "run_name": "defaults", "problem": "problems/optimize_function",
        "budgets": {"total_usd": 1.0},
        "policies": {"max_iterations": 1},
    })
    summary = start_run(fleet, runs_root=tmp_path / "runs")
    assert summary.status == "done" and summary.champion_score is not None
