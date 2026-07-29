from chi.director.strategy import Strategist
from chi.director.types import DirectorState, RoundDigest
from chi.store.db import Store


def _store(tmp_path):
    s = Store.open(tmp_path / "r")
    s.execute("INSERT INTO runs (run_id, problem, fleet_config_json, started_at)"
              " VALUES ('r1','p','{}','t')")
    return s


def _digest(dead=None, repeated=None, near=None):
    return RoundDigest(round_index=1, best_score=636.0, champion_score=636.0,
                       prev_best=636.0, dead_classes=dead or [],
                       repeated_dead_classes=repeated or [], near_misses=near or [],
                       distinct_new_classes=0)


def test_dead_classes_become_a_do_not_retry_block(tmp_path):
    st = Strategist(_store(tmp_path), "r1", tmp_path / "r")
    upd = st.plan(_digest(dead=["bf16", "panel-inv"], repeated=["bf16"]),
                  DirectorState.PLATEAUED, {"c1": "tune-champion"})
    assert "DEAD — do not retry" in upd.steering_text
    assert "bf16" in upd.steering_text


def test_near_miss_promoted_to_bases(tmp_path):
    st = Strategist(_store(tmp_path), "r1", tmp_path / "r")
    upd = st.plan(_digest(near=[{"code_hash": "sha256:abc", "score": 637.8}]),
                  DirectorState.PLATEAUED, {"c1": "x"})
    assert "sha256:abc" in upd.steering_text
    assert "sha256:abc" in upd.promoted_near_misses


def test_brain_invents_new_strategy_when_stuck(tmp_path):
    st = Strategist(_store(tmp_path), "r1", tmp_path / "r",
                    brain_fn=lambda prompt: "recursive-right-looking-syrk")
    upd = st.plan(_digest(repeated=["bf16"]), DirectorState.STUCK, {"c1": "old"})
    assert upd.per_coder_strategy["c1"] == "recursive-right-looking-syrk"


def test_apply_writes_steering_file(tmp_path):
    rundir = tmp_path / "r"
    st = Strategist(_store(tmp_path), "r1", rundir)
    upd = st.plan(_digest(dead=["bf16"]), DirectorState.PLATEAUED, {"c1": "x"})
    st.apply(upd)
    assert "bf16" in (rundir / "steering.md").read_text()


def test_apply_preserves_operator_interjection(tmp_path):
    rundir = tmp_path / "r"
    rundir.mkdir()
    (rundir / "steering.md").write_text("old\n## §op (priority)\nfocus on n=8192\n")
    st = Strategist(_store(tmp_path), "r1", rundir)
    st.apply(st.plan(_digest(dead=["bf16"]), DirectorState.PLATEAUED, {"c1": "x"}))
    text = (rundir / "steering.md").read_text()
    assert "focus on n=8192" in text      # operator interjection survived
    assert "Director directives" in text  # director block present too
