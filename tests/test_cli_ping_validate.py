from pathlib import Path

import yaml
from typer.testing import CliRunner

import chi.cli as cli_mod
from chi.cli import app

runner = CliRunner()

FLEET = {
    "run_name": "toy",
    "problem": "problems/optimize_function",
    "coders": [{"id": "c1", "model": "test/model-a"}, {"id": "c2", "model": "test/model-b"}],
}


def _fake_ping(models, budget, completion_fn=None):
    return [{"model": m, "ok": True, "latency_s": 0.1, "cost_usd": 0.001,
             "tokens_in": 5, "tokens_out": 1, "error": ""} for m in models]


def test_ping_prints_each_model(tmp_path: Path, monkeypatch) -> None:
    p = tmp_path / "fleet.yaml"
    p.write_text(yaml.safe_dump(FLEET))
    monkeypatch.setattr(cli_mod, "_ping_impl", _fake_ping)
    result = runner.invoke(app, ["ping", "--fleet", str(p)])
    assert result.exit_code == 0
    assert "test/model-a" in result.output and "test/model-b" in result.output


def test_validate_fleet_ok(tmp_path: Path) -> None:
    p = tmp_path / "fleet.yaml"
    p.write_text(yaml.safe_dump(FLEET))
    result = runner.invoke(app, ["validate", str(p)])
    assert result.exit_code == 0 and "OK" in result.output


def test_validate_bad_fleet_fails(tmp_path: Path) -> None:
    p = tmp_path / "fleet.yaml"
    p.write_text(yaml.safe_dump({"run_name": "x"}))  # missing coders/problem
    result = runner.invoke(app, ["validate", str(p)])
    assert result.exit_code == 1
