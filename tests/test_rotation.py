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
        ("music", "Artist", "Artist – Title"),
        ("music", None, "Title"),
        ("music", "  ", "Title"),
        ("game", "Ignored", "Title"),
        ("video", None, "Title"),
        ("tvshow", None, "Title"),
        ("movie", None, "Title"),
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
    assert rotation.other_sources_line(("a", "b", "c"), "a", states) == "Band – B · C"
    assert rotation.other_sources_line(("a",), "a", states) is None


def test_title_artist_and_other_line_follow_the_cover_cycle_without_swapping():
    states = {
        "game": state("Example Game", "game"),
        "music": state("Example Song", "music", "Example Artist"),
    }
    current = rotation.advance_rotation(
        rotation.Rotation(), ("game", "music"), "game", 0, 30, {}
    )
    for now, source, title, artist, other_line in [
        (0, "game", "Example Game", None, "Example Artist – Example Song"),
        (30, "music", "Example Song", "Example Artist", "Example Game"),
        (60, "game", "Example Game", None, "Example Artist – Example Song"),
    ]:
        current = rotation.advance_rotation(
            current, current.sources, "game", now, 30, {}
        )
        shown = rotation.shown_source_state(
            current.shown_source, "game", states["game"], states
        )
        assert current.shown_source == source
        assert shown.attributes["media_title"] == title
        assert shown.attributes["media_artist"] == artist
        assert (
            rotation.other_sources_line(current.sources, source, states) == other_line
        )


def test_shown_state_preserves_held_snapshot_and_handles_one_or_missing_source():
    held = state("Retained Game", "game")
    idle = state(status="idle")
    assert rotation.shown_source_state("game", "game", held, {"game": idle}) is held
    assert rotation.shown_source_state(None, "game", held, {}) is held
    assert rotation.shown_source_state("missing", "game", held, {}) is held
    assert rotation.shown_source_state(None, None, None, {}) is None
    assert rotation.other_sources_line(("game",), "game", {"game": held}) is None


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


@pytest.mark.parametrize("duration", [None, 7, 600])
def test_changed_title_switches_after_two_seconds_then_gets_full_interval(duration):
    sources = ("game", "music")
    previous = rotation.Rotation(sources, "game", 50)
    old = {"game": "Game", "music": "Old Song"}
    new = {"game": "Game", "music": "New Song"}
    pending = rotation.title_switch_after_change(
        rotation.TitleSwitch(), old, new, sources, 10
    )
    assert pending == ("music", 12)
    before = rotation.advance_rotation(
        previous, sources, "game", 11.9, 30, {"music": duration}, pending
    )
    assert before.shown_source == "game" and before.next_switch_at == 50
    shown = rotation.advance_rotation(
        before, sources, "game", 12, 30, {"music": duration}, pending
    )
    assert shown == (sources, "music", 12 + (duration or 30))


def test_unchanged_titles_do_not_reset_pending_or_regular_deadlines():
    sources = ("a", "b")
    titles = {"a": "A", "b": "B"}
    empty = rotation.title_switch_after_change(
        rotation.TitleSwitch(), titles, titles, sources, 10
    )
    assert empty == (None, None)
    current = rotation.Rotation(sources, "a", 30)
    assert (
        rotation.advance_rotation(current, sources, "a", 10, 30, {}, empty) == current
    )
    pending = rotation.TitleSwitch("b", 12)
    assert (
        rotation.title_switch_after_change(pending, titles, titles, sources, 11)
        == pending
    )
    # The next unchanged update after a focus switch retains its full deadline.
    focused = rotation.advance_rotation(current, sources, "a", 12, 30, {}, pending)
    assert (
        rotation.advance_rotation(focused, sources, "a", 13, 30, {}, empty) == focused
    )


def test_newer_title_replaces_pending_event_and_normal_tick_waits_for_focus():
    sources = ("a", "b")
    pending = rotation.TitleSwitch("b", 12)
    replacement = rotation.title_switch_after_change(
        pending, {"a": "A", "b": "B"}, {"a": "A", "b": "C"}, sources, 11
    )
    assert replacement == ("b", 13)
    current = rotation.Rotation(sources, "a", 11.5)
    assert (
        rotation.advance_rotation(current, sources, "a", 12, 30, {}, replacement)
        == current
    )
    assert rotation.advance_rotation(
        current, sources, "a", 13, 30, {}, replacement
    ) == (sources, "b", 43)


def test_title_change_on_already_shown_source_restarts_full_interval_after_delay():
    sources = ("a", "b")
    current = rotation.Rotation(sources, "b", 15)
    pending = rotation.TitleSwitch("b", 12)
    assert (
        rotation.advance_rotation(current, sources, "a", 11, 30, {}, pending) == current
    )
    assert rotation.advance_rotation(current, sources, "a", 12, 30, {}, pending) == (
        sources,
        "b",
        42,
    )


def test_inactive_removed_or_duplicate_sources_cancel_pending_focus():
    for sources, titles in [
        (("a",), {"a": "A"}),
        (("a", "b"), {"a": "A"}),
        (("a", "c"), {"a": "A", "b": "B", "c": "C"}),
    ]:
        assert rotation.title_switch_after_change(
            rotation.TitleSwitch("b", 12), titles, titles, sources, 11
        ) == (None, None)
    assert rotation.advance_rotation(
        rotation.Rotation(("a", "b"), "a", 30),
        ("a",),
        "a",
        12,
        30,
        {},
        rotation.TitleSwitch("b", 12),
    ) == (("a",), "a", None)


def test_initial_snapshot_and_other_attributes_do_not_count_as_title_change():
    sources = ("a", "b")
    states = {
        "a": state("A"),
        "b": state("B", artist="First Artist"),
        "idle": state("Placeholder", status="idle"),
        "control": state("Control"),
    }
    titles = rotation.active_titles(sources + ("idle",), states)
    assert titles == {"a": "A", "b": "B"}
    assert rotation.title_switch_after_change(
        rotation.TitleSwitch(), {}, titles, sources, 10
    ) == (None, None)
    states["b"] = state("B", artist="New Artist")
    assert rotation.title_switch_after_change(
        rotation.TitleSwitch(),
        titles,
        rotation.active_titles(sources, states),
        sources,
        11,
    ) == (None, None)


@pytest.mark.parametrize(
    "title,series,expected",
    [
        ("Episode", "Series", "Episode – Series"),
        ("Series – Episode", "Series", "Episode – Series"),
        ("Series - Episode", "Series", "Episode – Series"),
        ("series: Episode", "Series", "Episode – Series"),
        ("STRASSE – Episode", "Straße", "Episode – Straße"),
        ("Episode – Series", "Series", "Episode – Series"),
        ("Series", "Series", "Series"),
        ("Song - Remix", None, "Song - Remix"),
        (None, "Series", None),
    ],
)
def test_series_display_orders_title_before_series_without_repurposing_fields(
    title, series, expected
):
    attributes = {
        "media_title": title,
        "media_series_title": series,
        "media_artist": "Actual Artist",
        "media_album_name": "Actual Album",
    }
    original = dict(attributes)
    assert rotation.media_title_with_series(attributes) == expected
    assert attributes == original
