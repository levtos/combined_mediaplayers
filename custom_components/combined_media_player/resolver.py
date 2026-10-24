"""Pure slot resolution shared by the player and its cover entity."""

from collections.abc import Mapping, Sequence
from typing import NamedTuple, Protocol


class SourceState(Protocol):
    """Only a state value is needed; no Home Assistant dependency."""

    @property
    def state(self) -> str: ...


class Resolution(NamedTuple):
    display_source: str | None
    control_target: str | None
    cmp_state: str
    other_active_sources: tuple[str, ...]


def resolve(
    slots: Sequence[tuple[str, str | None]],
    states: Mapping[str, SourceState],
) -> Resolution:
    """Resolve a snapshot; slot order breaks ties within an activity tier."""
    values = {entity_id: state.state for entity_id, state in states.items()}
    active = tuple(
        dict.fromkeys(
            source
            for source, _ in slots
            if values.get(source) in {"playing", "buffering"}
        )
    )
    display = next(
        (
            slot
            for tier in ({"playing", "buffering"}, {"paused"})
            for slot in slots
            if values.get(slot[0]) in tier
        ),
        None,
    )
    control_slot = display or next(
        (slot for slot in slots if values.get(slot[0]) in {"on", "idle"}),
        None,
    )
    target = None
    if control_slot:
        source, control = control_slot
        target = (
            control
            if control and values.get(control) not in {None, "unavailable", "unknown"}
            else source
        )
    cmp_state = (
        ("paused" if values[display[0]] == "paused" else "playing")
        if display
        else ("idle" if control_slot else "off")
    )
    return Resolution(
        display[0] if display else None,
        target,
        cmp_state,
        tuple(source for source in active if not display or source != display[0]),
    )


def priority_slots(
    slots: Sequence[tuple[str, str | None]],
    override: str | None,
) -> list[tuple[str, str | None]]:
    """Move the chosen source first without changing tiers or stored slots."""
    return [slot for slot in slots if slot[0] == override] + [
        slot for slot in slots if slot[0] != override
    ]


def is_radio(content_id: str | None) -> bool:
    """Recognize MA radio IDs without relying on a title or duration."""
    return isinstance(content_id, str) and "://radio/" in content_id


class HeldResolution(NamedTuple):
    resolution: Resolution
    failed_at: float | None
    hold_active: bool


class _HeldState(NamedTuple):
    state: str


def resolve_with_hold(
    slots: Sequence[tuple[str, str | None]],
    states: Mapping[str, SourceState],
    previous: Resolution | None,
    failed_at: float | None,
    now: float,
    hold_seconds: float,
) -> HeldResolution:
    """Hold a lost display winner, unless a higher-priority source plays.

    All times use the same monotonic clock. The caller keeps the last active
    media attributes and schedules a reevaluation at failed_at + hold_seconds.
    """
    current = resolve(slots, states)
    source = previous.display_source if previous else None
    order = [slot[0] for slot in slots]
    state = states.get(source) if source else None
    if (
        not source
        or source not in order
        or hold_seconds <= 0
        or (state and state.state in {"playing", "buffering", "paused"})
    ):
        return HeldResolution(current, None, False)
    winner = current.display_source
    if (
        winner
        and order.index(winner) < order.index(source)
        and states[winner].state in {"playing", "buffering"}
    ):
        return HeldResolution(current, None, False)
    failed_at = now if failed_at is None else failed_at
    if now - failed_at >= hold_seconds:
        return HeldResolution(current, None, False)
    held_states = dict(states)
    held_states[source] = _HeldState(previous.cmp_state)
    held_slot = next(slot for slot in slots if slot[0] == source)
    held = resolve([held_slot], held_states)
    others = tuple(
        dict.fromkeys(
            sid
            for sid, _ in slots
            if sid != source
            and sid in states
            and states[sid].state in {"playing", "buffering"}
        )
    )
    return HeldResolution(held._replace(other_active_sources=others), failed_at, True)
