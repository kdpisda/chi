"""Opt-in real-B200 integration for the Director.

Skipped unless CHI_ALLOW_REMOTE_BENCH=1 and the cholesky pack exists — it spends
real B200 quota. Asserts rounds advance, spend is counted, and (spec J1) NO ranked
leaderboard submit is ever fired.
"""

import json
import os
import time
from pathlib import Path

import pytest

CHOLESKY = Path.home() / ".local/share/chi/problems/cholesky"

pytestmark = pytest.mark.skipif(
    os.environ.get("CHI_ALLOW_REMOTE_BENCH") != "1" or not CHOLESKY.exists(),
    reason="real B200 run: set CHI_ALLOW_REMOTE_BENCH=1 and have the cholesky pack",
)


def test_director_runs_rounds_counts_spend_no_ranked_submit(tmp_path):
    from chi.config import BudgetsCfg, FleetConfig, PoliciesCfg
    from chi.session.director_runner import DirectorHandle
    from chi.store.db import Store
    from chi.store.events import list_events

    fleet = FleetConfig(run_name="cholesky", problem=CHOLESKY,
                        budgets=BudgetsCfg(total_usd=5.0),
                        coders=[], policies=PoliciesCfg(max_iterations=1))
    handle = DirectorHandle(fleet, runs_root=tmp_path / "runs", brain_fn=None)
    handle.start()
    assert handle.ready.wait(timeout=1800)
    assert handle.error is None
    time.sleep(90)  # let at least one full round land
    handle.request_stop()
    handle.join(timeout=1800)

    store = Store.open(handle.run_dir)
    assert len(list_events(store, handle.run_id, "DIRECTOR_ROUND")) >= 1
    assert handle.cumulative_benchmarks >= 1
    # spec J1: the director never fires a ranked submit — no STATUS event claims one
    for event in list_events(store, handle.run_id, "STATUS"):
        payload = json.loads(event["payload_json"])
        assert payload.get("submitted") is not True
