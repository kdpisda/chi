import json
from pathlib import Path

from chi.store.db import Store, utcnow
from chi.store.events import append_event, list_events


def test_utcnow_is_iso_z() -> None:
    ts = utcnow()
    assert ts.endswith("Z") and "T" in ts


def test_open_creates_db_and_mirror_dir(tmp_path: Path) -> None:
    store = Store.open(tmp_path / "run1")
    assert (tmp_path / "run1" / "chi.db").exists()
    assert (tmp_path / "run1" / "mirror").is_dir()
    store.close()


def test_append_event_writes_db_and_mirror(tmp_path: Path) -> None:
    store = Store.open(tmp_path / "run1")
    eid = append_event(store, "r1", "STATUS", agent_id="a1", payload={"k": "v"})
    assert eid >= 1
    rows = list_events(store, "r1", "STATUS")
    assert len(rows) == 1 and json.loads(rows[0]["payload_json"]) == {"k": "v"}
    mirror = (tmp_path / "run1" / "mirror" / "events.jsonl").read_text().strip().splitlines()
    assert len(mirror) == 1 and json.loads(mirror[0])["type"] == "STATUS"
    store.close()


def test_events_are_append_only_ordered(tmp_path: Path) -> None:
    store = Store.open(tmp_path / "run1")
    append_event(store, "r1", "STATUS")
    append_event(store, "r1", "HEARTBEAT")
    types = [r["type"] for r in list_events(store, "r1")]
    assert types == ["STATUS", "HEARTBEAT"]
    store.close()
