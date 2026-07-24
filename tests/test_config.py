from pathlib import Path

import pytest
import yaml

from chi.config import FleetConfig, load_fleet, load_problem

FLEET = {
    "run_name": "toy",
    "problem": "problems/optimize_function",
    "budgets": {"total_usd": 2.0, "per_role_usd": {"coder": 1.5}},
    "coders": [{"id": "c1", "model": "anthropic/claude-sonnet-5", "adapter": "litellm_loop"}],
}

PROBLEM = {
    "name": "optimize_function",
    "candidate": "candidate.py",
    "entrypoints": {
        "correctness": "python check.py {candidate} --seed {seed}",
        "benchmark": "python bench.py {candidate}",
    },
    "score": {"metric": "runtime_ms", "direction": "minimize", "repeats": 3},
    "correctness": {"seeds": [11, 27, 43], "tolerance": 1e-6},
}


def test_fleet_round_trips(tmp_path: Path) -> None:
    p = tmp_path / "fleet.yaml"
    p.write_text(yaml.safe_dump(FLEET))
    cfg = load_fleet(p)
    assert cfg.run_name == "toy"
    assert cfg.coders[0].adapter == "litellm_loop"
    assert cfg.policies.lease_seconds == 900
    # round-trip: dump and reload gives an equal model
    assert FleetConfig.model_validate(cfg.model_dump()) == cfg


def test_fleet_rejects_unknown_adapter(tmp_path: Path) -> None:
    bad = dict(FLEET, coders=[{"id": "c1", "model": "m", "adapter": "nope"}])
    p = tmp_path / "fleet.yaml"
    p.write_text(yaml.safe_dump(bad))
    with pytest.raises(ValueError):
        load_fleet(p)


def test_load_problem_sets_dir(tmp_path: Path) -> None:
    (tmp_path / "problem.yaml").write_text(yaml.safe_dump(PROBLEM))
    prob = load_problem(tmp_path)
    assert prob.dir == tmp_path
    assert prob.correctness.seeds == [11, 27, 43]
    assert prob.score.direction == "minimize"


def test_load_problem_missing_manifest_raises(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        load_problem(tmp_path)
