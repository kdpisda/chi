from chi.director.research import Researcher


def test_returns_findings_from_brain():
    r = Researcher(brain_fn=lambda p: "Try right-looking blocked SYRK with TF32 on the trailing panel.")
    out = r.research(champion_score=636.0, dead_classes=["bf16"])
    assert "SYRK" in out


def test_no_brain_degrades_to_empty():
    assert Researcher(brain_fn=None).research(636.0, []) == ""


def test_brain_error_degrades_to_empty():
    def boom(p):
        raise RuntimeError("no web")

    assert Researcher(brain_fn=boom).research(636.0, []) == ""


def test_output_is_truncated():
    r = Researcher(brain_fn=lambda p: "x" * 5000, max_chars=100)
    assert len(r.research(636.0, [])) <= 100


def test_research_prompt_is_domain_generic():
    # The brain prompt must template from the actual problem, not hardcode the
    # Cholesky/B200/cuSOLVER domain that leaked cross-problem advice.
    captured: dict[str, str] = {}

    def brain(prompt: str) -> str:
        captured["prompt"] = prompt
        return "meet-in-the-middle split"

    r = Researcher(brain_fn=brain, problem_name="knapsack-dp",
                   problem_description="0/1 knapsack via dynamic programming",
                   metric="iterations", direction="minimize")
    r.research(champion_score=42.0, dead_classes=["greedy"])
    prompt = captured["prompt"]
    assert "knapsack-dp" in prompt
    assert "iterations" in prompt
    for banned in ("cuSOLVER", "B200", "Cholesky", "µs"):
        assert banned not in prompt
