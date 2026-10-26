"""Pure cover rotation and secondary-line formatting; times are monotonic."""

from collections.abc import Mapping, Sequence
from typing import Any, NamedTuple


class Rotation(NamedTuple):
    sources: tuple[str, ...] = ()
    shown_source: str | None = None
    next_switch_at: float | None = None


def rotation_sources(
    sources: Sequence[str], states: Mapping[str, Any]
) -> tuple[str, ...]:
    """Keep active sources in slot order; retain only the first known title."""
    result, titles = [], set()
    for source in dict.fromkeys(sources):
        state = states.get(source)
        if state is None or state.state not in {"playing", "buffering"}:
            continue
        value = (state.attributes or {}).get("media_title")
        title = (value.strip().casefold() or None) if isinstance(value, str) else None
        if title and title in titles:
            continue
        if title:
            titles.add(title)
        result.append(source)
    return tuple(result)


def advance_rotation(
    previous: Rotation,
    sources: tuple[str, ...],
    display_source: str | None,
    now: float,
    global_seconds: float,
    durations: Mapping[str, float | None],
) -> Rotation:
    """Preserve a surviving source's deadline, including on membership changes."""
    if len(sources) < 2:
        return Rotation(sources, display_source)
    shown, deadline = previous.shown_source, previous.next_switch_at
    # The display winner may be a title duplicate selected by the priority select.
    # It is still shown immediately on a reset; the next tick enters slot order.
    if deadline is None or (
        shown not in sources
        and (sources != previous.sources or shown != display_source)
    ):
        shown = display_source or sources[0]
        deadline = now + (durations.get(shown) or global_seconds)
    elif now >= deadline:
        index = sources.index(shown) + 1 if shown in sources else 0
        shown = sources[index % len(sources)]
        deadline = now + (durations.get(shown) or global_seconds)
    return Rotation(sources, shown, deadline)


def other_sources_line(
    sources: Sequence[str], display_source: str | None, states: Mapping[str, Any]
) -> str | None:
    """Format other rotation members without altering the winner's media fields."""
    entries = []
    for source in sources:
        state = states.get(source)
        if source == display_source or state is None:
            continue
        attributes = state.attributes or {}
        title = str(attributes.get("media_title") or "").strip()
        content_type = attributes.get("media_content_type")
        if content_type == "music":
            artist = str(attributes.get("media_artist") or "").strip()
            entries.append(title + (f" – {artist}" if artist else ""))
        else:
            entries.append(f"{'🎮' if content_type == 'game' else '▶'} {title}")
    return " · ".join(entries) or None
