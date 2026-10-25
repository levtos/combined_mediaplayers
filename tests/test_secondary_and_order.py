"""Pure secondary selection and source-bound configuration contracts."""

import importlib.util
from pathlib import Path

import pytest

from test_resolver import _MODULE, State

_SPEC = importlib.util.spec_from_file_location(
    "cmp_slot_config",
    Path(__file__).parents[1]
    / "custom_components/combined_media_player/slot_config.py",
)
_SLOTS = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_SLOTS)


@pytest.mark.parametrize(
    "main,mirror",
    [
        ("Example Game", "Example Game"),
        (" Example Game ", "example game"),
        ("Straße", "STRASSE"),
    ],
)
def test_duplicate_title_is_skipped_and_other_sources_remain_complete(main, mirror):
    result = _MODULE.resolve(
        [("main", None), ("mirror", None), ("music", None)],
        {
            "main": State("playing", {"media_title": main}),
            "mirror": State("playing", {"media_title": mirror}),
            "music": State("playing", {"media_title": "Example Song"}),
        },
    )
    assert result.display_source == "main"
    assert result.secondary_source == "music"
    assert result.other_active_sources == ("mirror", "music")


@pytest.mark.parametrize(
    "main,other", [(None, "Track"), ("Track", None), ("", "Track"), ("Track", "   ")]
)
def test_missing_title_is_not_a_duplicate(main, other):
    result = _MODULE.resolve(
        [("main", None), ("other", None)],
        {
            "main": State("playing", {"media_title": main}),
            "other": State("playing", {"media_title": other}),
        },
    )
    assert result.secondary_source == "other"


def test_only_duplicate_titles_provide_no_secondary_source():
    result = _MODULE.resolve(
        [("main", None), ("mirror", None)],
        {
            "main": State("playing", {"media_title": " Example "}),
            "mirror": State("buffering", {"media_title": "example"}),
        },
    )
    assert result.secondary_source is None
    assert result.other_active_sources == ("mirror",)


def test_held_source_compares_its_retained_title_without_changing_hold_rules():
    slots = [("main", None), ("mirror", None), ("music", None)]
    previous = _MODULE.resolve(
        slots, {"main": State("playing", {"media_title": "Example"})}
    )
    result = _MODULE.resolve_with_hold(
        slots,
        {
            "main": State("idle"),
            "mirror": State("playing", {"media_title": "EXAMPLE"}),
            "music": State("playing", {"media_title": "Song"}),
        },
        previous,
        None,
        10,
        5,
        previous_attributes={"media_title": "Example"},
    )
    assert result.hold_active and result.failed_at == 10
    assert result.resolution.display_source == "main"
    assert result.resolution.secondary_source == "music"


def test_controls_follow_sources_when_reordered_and_duplicates_keep_first():
    saved = [("a", "control_a"), ("b", "control_b"), ("a", "ignored_control")]
    controls = _SLOTS.controls_by_source(saved)
    reordered = _SLOTS.make_slots(["b", "a", "b", "new"], controls)
    assert reordered == [
        {"source": "b", "control": "control_b"},
        {"source": "a", "control": "control_a"},
        {"source": "new", "control": None},
    ]
    assert _SLOTS.make_slots(["a"], {}) == [{"source": "a", "control": None}]
