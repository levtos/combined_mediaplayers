"""Pure feature contracts, using synthetic states and in-memory test images."""

import pytest

from test_resolver import _MODULE, snapshot

priority_slots = _MODULE.priority_slots
resolve_with_hold = _MODULE.resolve_with_hold
resolve = _MODULE.resolve
is_radio = _MODULE.is_radio


def test_override_keeps_slot_controls_and_does_not_mutate_order():
    slots = [("a", "control_a"), ("b", "control_b"), ("c", None)]
    reordered = priority_slots(slots, "b")
    assert reordered == [("b", "control_b"), ("a", "control_a"), ("c", None)]
    assert slots[0] == ("a", "control_a")
    result = resolve(reordered, snapshot(a="playing", b="playing", control_b="on"))
    assert result.display_source == "b" and result.control_target == "control_b"


def test_override_still_respects_activity_tiers_and_unknown_choice():
    slots = [("a", None), ("b", None)]
    assert (
        resolve(
            priority_slots(slots, "b"), snapshot(a="playing", b="paused")
        ).display_source
        == "a"
    )
    assert priority_slots(slots, "missing") == slots
    assert priority_slots(slots, None) == slots


@pytest.mark.parametrize(
    "elapsed,active,winner", [(1.5, True, "a"), (5, False, "b"), (6, False, "b")]
)
def test_hold_until_deadline(elapsed, active, winner):
    slots = [("a", "control"), ("b", None)]
    previous = resolve(slots, snapshot(a="playing", control="idle"))
    result = resolve_with_hold(
        slots,
        snapshot(a="idle", b="playing", control="on"),
        previous,
        10,
        10 + elapsed,
        5,
    )
    assert result.hold_active is active
    assert result.resolution.display_source == winner
    if active:
        assert result.resolution.control_target == "control"
        assert result.resolution.cmp_state == "playing"
        assert result.resolution.other_active_sources == ("b",)


def test_higher_playing_priority_interrupts_hold_immediately():
    slots = [("a", None), ("b", None)]
    previous = resolve(slots, snapshot(b="paused"))
    result = resolve_with_hold(
        slots, snapshot(a="buffering", b="idle"), previous, None, 20, 5
    )
    assert result.hold_active is False
    assert result.resolution.display_source == "a"


def test_paused_higher_priority_does_not_interrupt_hold():
    slots = [("a", None), ("b", None)]
    previous = resolve(slots, snapshot(b="playing"))
    result = resolve_with_hold(
        slots, snapshot(a="paused", b="idle"), previous, None, 20, 5
    )
    assert result.hold_active is True
    assert result.resolution.display_source == "b"


def test_hold_disabled_and_recovery_reset_failure_time():
    slots = [("a", None)]
    previous = resolve(slots, snapshot(a="paused"))
    assert not resolve_with_hold(
        slots, snapshot(a="idle"), previous, None, 20, 0
    ).hold_active
    result = resolve_with_hold(slots, snapshot(a="paused"), previous, 19, 20, 5)
    assert result.failed_at is None and not result.hold_active
    assert result.resolution.cmp_state == "paused"


def test_hold_keeps_original_failure_time_and_paused_state():
    slots = [("a", None)]
    previous = resolve(slots, snapshot(a="paused"))
    first = resolve_with_hold(slots, {}, previous, None, 20, 5)
    second = resolve_with_hold(slots, {}, first.resolution, first.failed_at, 24, 5)
    assert second.failed_at == 20 and second.hold_active
    assert second.resolution.cmp_state == "paused"
    assert not resolve_with_hold(
        slots, {}, second.resolution, second.failed_at, 25.1, 5
    ).hold_active


@pytest.mark.parametrize(
    "content_id,expected",
    [
        ("radiobrowser://radio/example", True),
        ("library://radio/123", True),
        ("library://track/123", False),
        ("https://example.test/radio/123", False),
        ("", False),
        (None, False),
    ],
)
def test_radio_detection(content_id, expected):
    assert is_radio(content_id) is expected
