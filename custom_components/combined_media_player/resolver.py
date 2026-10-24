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
