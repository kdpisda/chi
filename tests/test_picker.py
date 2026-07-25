import pytest

from chi.tui.picker import PickerUnavailable, fuzzy_select

CHOICES = [("a", "Model A"), ("b", "Model B")]


def test_injected_picker_multi() -> None:
    def fake(message, choices, multi):
        assert multi is True and choices == CHOICES
        return ["b"]

    assert fuzzy_select("pick", CHOICES, multi=True, picker_fn=fake) == ["b"]


def test_injected_picker_single_scalar_wrapped() -> None:
    assert fuzzy_select("pick", CHOICES, picker_fn=lambda m, c, mu: "a") == ["a"]


def test_non_tty_raises(monkeypatch) -> None:
    import sys

    monkeypatch.setattr(sys.stdin, "isatty", lambda: False)
    with pytest.raises(PickerUnavailable):
        fuzzy_select("pick", CHOICES)
