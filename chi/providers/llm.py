"""LiteLLM wrapper: one chat entrypoint with cost capture and budget enforcement."""

import time
from dataclasses import dataclass
from typing import Any, Callable

from chi.providers.budgets import BudgetTracker


@dataclass
class ChatResult:
    message: Any
    text: str
    cost_usd: float
    tokens_in: int
    tokens_out: int


def _cost_of(response: Any) -> float:
    """Best-effort cost extraction; 0.0 when the provider/litellm can't price it."""
    hidden = getattr(response, "_hidden_params", None) or {}
    if isinstance(hidden, dict) and hidden.get("response_cost") is not None:
        return float(hidden["response_cost"])
    try:
        import litellm

        return float(litellm.completion_cost(completion_response=response))
    except Exception as exc:  # litellm raises many types for unpriced models; treat as free
        _ = exc
        return 0.0


def chat(
    model: str,
    messages: list[dict],
    *,
    budget: BudgetTracker,
    role: str = "coder",
    tools: list | None = None,
    completion_fn: Callable | None = None,
    **kwargs: Any,
) -> ChatResult:
    """Run one completion through LiteLLM with budget check/record around it."""
    budget.check(role=role)
    if completion_fn is None:
        import litellm

        completion_fn = litellm.completion
    call_kwargs = dict(kwargs)
    if tools is not None:
        call_kwargs["tools"] = tools
    response = completion_fn(model=model, messages=messages, **call_kwargs)
    usage = getattr(response, "usage", None)
    tokens_in = int(getattr(usage, "prompt_tokens", 0) or 0)
    tokens_out = int(getattr(usage, "completion_tokens", 0) or 0)
    cost = _cost_of(response)
    budget.record(cost, role=role)
    message = response.choices[0].message
    return ChatResult(
        message=message,
        text=getattr(message, "content", None) or "",
        cost_usd=cost,
        tokens_in=tokens_in,
        tokens_out=tokens_out,
    )


def ping(
    models: list[str], budget: BudgetTracker, completion_fn: Callable | None = None
) -> list[dict]:
    """Send a one-word prompt to each model; report reachability, latency, cost."""
    rows: list[dict] = []
    for model in models:
        start = time.perf_counter()
        try:
            res = chat(
                model,
                [{"role": "user", "content": "Reply with the single word: pong"}],
                budget=budget,
                role="ping",
                completion_fn=completion_fn,
            )
            rows.append({
                "model": model, "ok": True, "latency_s": time.perf_counter() - start,
                "cost_usd": res.cost_usd, "tokens_in": res.tokens_in,
                "tokens_out": res.tokens_out, "error": "",
            })
        except Exception as exc:  # report per-model failure, keep pinging the rest
            rows.append({
                "model": model, "ok": False, "latency_s": time.perf_counter() - start,
                "cost_usd": 0.0, "tokens_in": 0, "tokens_out": 0, "error": str(exc),
            })
    return rows
