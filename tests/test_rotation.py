"""Pure rotation contracts; no HA harness, clocks, network or real media."""

import importlib.util
from pathlib import Path

import pytest

from test_resolver import State, _MODULE as resolver
from test_secondary_and_order import _SLOTS

_SPEC = importlib.util.spec_from_file_location(
    "cmp_rotation",
    Path(__file__).parents[1] / "custom_components/combined_media_player/rotation.py",
)
rotation = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(rotation)


def state(title=None, media_type="music", artist=None, status="playing"):
    return State(
        status,
        {
            "media_title": title,
            "media_content_type": media_type,
            "media_artist": artist,
        },
    )


def test_rotation_membership_deduplicates_all_titles_in_slot_order():
    states = {
        "a": state(" Game ", "game"),
        "mirror": state("GAME", "game"),
        "b": state("Straße"),
        "mirror_b": state("STRASSE", status="buffering"),
        "c": state(None),
        "d": state("   "),
        "paused": state("Other", status="paused"),
        "idle": state("Placeholder", status="idle"),
    }
    assert rotation.rotation_sources(list(states) + ["a", "missing"], states) == (
        "a",
        "b",
        "c",
        "d",
    )


def test_held_display_snapshot_participates_until_existing_hold_expires():
    slots = [("a", None), ("b", None)]
    previous = resolver.resolve(slots, {"a": state("Game", "game")})
    states = {"a": state(status="idle"), "b": state("Song")}
    held = resolver.resolve_with_hold(slots, states, previous, 10, 12, 5)
    effective = dict(states)
    if held.hold_active:
        effective["a"] = state("Game", "game")
    assert rotation.rotation_sources(["a", "b"], effective) == ("a", "b")
    expired = resolver.resolve_with_hold(slots, states, previous, 10, 15, 5)
    assert not expired.hold_active
    assert rotation.rotation_sources(["a", "b"], states) == ("b",)


@pytest.mark.parametrize("sources", [(), ("a",)])
def test_below_two_sources_shows_display_without_deadline(sources):
    result = rotation.advance_rotation(
        rotation.Rotation(), sources, "display", 10, 30, {}
    )
    assert result.shown_source == "display"
    assert result.next_switch_at is None


def test_rotation_order_wraps_and_uses_source_duration_or_global():
    sources = ("a", "b", "c")
    result = rotation.advance_rotation(
        rotation.Rotation(), sources, "a", 10, 30, {"a": 5, "b": None, "c": 600}
    )
    assert result == (sources, "a", 15)
    result = rotation.advance_rotation(result, sources, "a", 15, 30, {"c": 600})
    assert result == (sources, "b", 45)
    result = rotation.advance_rotation(result, sources, "a", 45, 30, {"c": 600})
    assert result == (sources, "c", 645)
    result = rotation.advance_rotation(result, sources, "a", 645, 30, {})
    assert result == (sources, "a", 675)


def test_metadata_and_display_changes_keep_shown_sources_remaining_time():
    previous = rotation.Rotation(("a", "b"), "b", 40)
    assert rotation.advance_rotation(previous, ("a", "b"), "a", 20, 30, {}) == previous
    # Even a different main winner does not move the cover before its deadline.
    assert rotation.advance_rotation(previous, ("a", "b"), "b", 25, 30, {}) == previous


def test_membership_change_keeps_remaining_time_when_shown_survives():
    previous = rotation.Rotation(("a", "b"), "b", 40)
    result = rotation.advance_rotation(previous, ("b", "c", "a"), "a", 20, 30, {})
    assert result == (("b", "c", "a"), "b", 40)
    assert (
        rotation.advance_rotation(result, result.sources, "a", 40, 30, {}).shown_source
        == "c"
    )


def test_removed_shown_source_immediately_resets_to_display_and_its_duration():
    previous = rotation.Rotation(("a", "b", "c"), "b", 40)
    result = rotation.advance_rotation(previous, ("a", "c"), "a", 20, 30, {"a": 7})
    assert result == (("a", "c"), "a", 27)


def test_reduction_to_one_immediately_cancels_rotation_then_restarts_cleanly():
    previous = rotation.Rotation(("a", "b"), "b", 40)
    single = rotation.advance_rotation(previous, ("a",), "a", 20, 30, {})
    assert single == (("a",), "a", None)
    assert rotation.advance_rotation(single, ("a", "b"), "a", 25, 30, {}) == (
        ("a", "b"),
        "a",
        55,
    )


def test_overridden_duplicate_display_does_not_restart_timer_on_every_refresh():
    sources = ("a", "c")
    result = rotation.advance_rotation(
        rotation.Rotation(), sources, "mirror", 10, 30, {}
    )
    assert result == (sources, "mirror", 40)
    assert rotation.advance_rotation(result, sources, "mirror", 20, 30, {}) == result
    assert rotation.advance_rotation(result, sources, "mirror", 40, 30, {}) == (
        sources,
        "a",
        70,
    )


@pytest.mark.parametrize(
    "media_type,artist,expected",
    [
        ("music", "Artist", "Title – Artist"),
        ("music", None, "Title"),
        ("music", "  ", "Title"),
        ("game", "Ignored", "🎮 Title"),
        ("video", None, "▶ Title"),
        ("tvshow", None, "▶ Title"),
        ("movie", None, "▶ Title"),
    ],
)
def test_second_line_format(media_type, artist, expected):
    assert (
        rotation.other_sources_line(
            ("main", "other"), "main", {"other": state("Title", media_type, artist)}
        )
        == expected
    )


def test_second_line_orders_all_other_entries_and_omits_main():
    states = {
        "a": state("A", "game"),
        "b": state("B", "music", "Band"),
        "c": state("C", "video"),
    }
    assert (
        rotation.other_sources_line(("a", "b", "c"), "a", states) == "B – Band · ▶ C"
    )
    assert rotation.other_sources_line(("a",), "a", states) is None


def test_durations_and_controls_follow_sources_after_reordering_and_clear():
    saved = [
        {"source": "a", "control": "ctrl", "display_seconds": 10},
        {"source": "b", "control": None},
        {"source": "a", "display_seconds": 99},
    ]
    settings = _SLOTS.settings_by_source(saved)
    controls = {source: value.get("control") for source, value in settings.items()}
    durations = {
        source: value.get("display_seconds") for source, value in settings.items()
    }
    assert _SLOTS.make_slots(["b", "a", "b"], controls, durations) == [
        saved[1],
        saved[0],
    ]
    assert _SLOTS.make_slots(["a"], {}, {"a": None}) == [
        {"source": "a", "control": None}
    ]
