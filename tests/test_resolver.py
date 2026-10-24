"""Resolver contract tests without importing Home Assistant or the integration."""

import importlib.util
from pathlib import Path
import sys
from typing import NamedTuple

import pytest

_PATH = (
    Path(__file__).parents[1] / "custom_components/combined_media_player/resolver.py"
)
_SPEC = importlib.util.spec_from_file_location("cmp_resolver", _PATH)
_MODULE = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = _MODULE
_SPEC.loader.exec_module(_MODULE)
resolve = _MODULE.resolve


class State(NamedTuple):
    state: str


def snapshot(**values):
    return {entity: State(value) for entity, value in values.items()}


@pytest.mark.parametrize(
    "first,second,winner,state",
    [
        ("playing", "playing", "a", "playing"),
        ("buffering", "playing", "a", "playing"),
        ("paused", "playing", "b", "playing"),
        ("paused", "buffering", "b", "playing"),
        ("paused", "paused", "a", "paused"),
        ("idle", "paused", "b", "paused"),
        ("on", "playing", "b", "playing"),
    ],
)
def test_priority_and_display_state(first, second, winner, state):
    result = resolve([("a", None), ("b", None)], snapshot(a=first, b=second))
    assert (result.display_source, result.control_target, result.cmp_state) == (
        winner,
        winner,
        state,
    )


@pytest.mark.parametrize("value", ["on", "idle"])
def test_ready_source_has_controls_but_no_display_for_title_or_cover(value):
    result = resolve([("a", "control")], snapshot(a=value, control="off"))
    assert result == (None, "control", "idle", ())


@pytest.mark.parametrize("value", ["unavailable", "unknown", "off", "unexpected"])
def test_inactive_sources_cannot_win(value):
    assert resolve([("a", None)], snapshot(a=value)) == (None, None, "off", ())


@pytest.mark.parametrize("control", ["unavailable", "unknown", None])
def test_control_falls_back_to_winning_source(control):
    states = snapshot(a="playing")
    if control is not None:
        states["control"] = State(control)
    result = resolve([("a", "control")], states)
    assert result.control_target == "a"


def test_control_belongs_to_winning_slot_even_if_other_control_is_playing():
    result = resolve(
        [("a", "control_a"), ("b", "control_b")],
        snapshot(a="paused", b="buffering", control_a="playing", control_b="idle"),
    )
    assert result == ("b", "control_b", "playing", ())


def test_idle_control_priority_is_slot_order():
    result = resolve(
        [("a", None), ("b", "control")], snapshot(a="idle", b="on", control="playing")
    )
    assert result == (None, "a", "idle", ())


def test_other_active_sources_are_ordered_and_exclude_winner_and_paused():
    result = resolve(
        [("a", None), ("b", None), ("c", None), ("d", None)],
        snapshot(a="paused", b="buffering", c="playing", d="buffering"),
    )
    assert result.display_source == "b"
    assert result.other_active_sources == ("c", "d")


def test_empty_and_missing_states():
    assert resolve([], {}) == (None, None, "off", ())
    assert resolve([("missing", None)], {}) == (None, None, "off", ())
