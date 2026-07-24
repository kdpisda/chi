from pathlib import Path
from types import SimpleNamespace

import pytest

from chi.providers.budgets import BudgetExceededError, BudgetTracker
from chi.providers.llm import chat, ping
from chi.store.db import Store
from chi.store.events import list_events


def _fake_completion(model: str, messages: list, **kwargs):
    """Mimic a litellm ModelResponse closely enough for our wrapper."""
    return SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=f"ok:{model}", tool_calls=None))],
        usage=SimpleNamespace(prompt_tokens=10, completion_tokens=5),
        _hidden_params={"response_cost": 0.01},
    )


def _boom(model: str, messages: list, **kwargs):
    raise RuntimeError("provider down")


def test_chat_returns_text_and_records_cost() -> None:
    budget = BudgetTracker(total_usd=1.0)
    res = chat("test/model", [{"role": "user", "content": "hi"}],
               budget=budget, completion_fn=_fake_completion)
    assert res.text == "ok:test/model"
    assert res.tokens_in == 10 and res.tokens_out == 5
    assert budget.spent == pytest.approx(0.01)


def test_budget_blocks_when_exhausted() -> None:
    budget = BudgetTracker(total_usd=0.005)
    chat("test/model", [{"role": "user", "content": "hi"}],
         budget=budget, completion_fn=_fake_completion)  # spends 0.01 > cap
    with pytest.raises(BudgetExceededError):
        chat("test/model", [{"role": "user", "content": "hi"}],
             budget=budget, completion_fn=_fake_completion)


def test_role_cap_blocks_independently() -> None:
    budget = BudgetTracker(total_usd=10.0, per_role={"coder": 0.005})
    budget.record(0.01, role="coder")
    with pytest.raises(BudgetExceededError):
        budget.check(role="coder")
    budget.check(role="critic")  # other roles unaffected


def test_budget_block_event_when_store_attached(tmp_path: Path) -> None:
    store = Store.open(tmp_path / "run")
    budget = BudgetTracker(total_usd=0.0, store=store, run_id="r1")
    with pytest.raises(BudgetExceededError):
        budget.check()
    assert len(list_events(store, "r1", "BUDGET_BLOCK")) == 1


def test_ping_reports_ok_and_errors() -> None:
    budget = BudgetTracker(total_usd=1.0)
    rows = ping(["good/model"], budget, completion_fn=_fake_completion)
    assert rows[0]["ok"] is True and rows[0]["cost_usd"] == pytest.approx(0.01)
    rows = ping(["bad/model"], budget, completion_fn=_boom)
    assert rows[0]["ok"] is False and "provider down" in rows[0]["error"]
