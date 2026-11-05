"""Pure cover rotation and secondary-line formatting; times are monotonic."""

from collections.abc import Mapping, Sequence
from typing import Any, NamedTuple


class Rotation(NamedTuple):
    sources: tuple[str, ...] = ()
    shown_source: str | None = None
    next_switch_at: float | None = None


class TitleSwitch(NamedTuple):
    source: str | None = None
    at: float | None = None


def media_title_with_series(attributes: Mapping[str, Any]) -> str | None:
    """Format the actual title with series context, never artist/album surrogates."""
    title = attributes.get("media_title")
    series = attributes.get("media_series_title")
    if not isinstance(title, str) or not title.strip():
        return title
    if not isinstance(series, str) or not series.strip():
        return title
    title, series = title.strip(), series.strip()
    if title.casefold() == series.casefold():
        return title
    for separator in (" – ", " - ", " — ", ": "):
        prefix, found, episode = title.partition(separator)
        if found and prefix.strip().casefold() == series.casefold():
            title = episode.strip()
            break
    if not title:
        return series
    for separator in (" – ", " - ", " — "):
        suffix = separator + series
        if title.casefold().endswith(suffix.casefold()):
            return title
    return f"{title} – {series}"


def active_titles(sources: Sequence[str], states: Mapping[str, Any]) -> dict:
    """Observe actual active sources, excluding held snapshots and controls."""
    return {
        source: (state.attributes or {}).get("media_title")
        for source in sources
        if (state := states.get(source)) is not None
        and state.state in {"playing", "buffering"}
    }


def title_switch_after_change(
    pending: TitleSwitch,
    previous_titles: Mapping,
    titles: Mapping,
    sources: Sequence[str],
    now: float,
) -> TitleSwitch:
    """Latest title event wins; simultaneous changes use slot priority."""
    for source in sources:
        title = titles.get(source)
        if (
            isinstance(title, str)
            and title.strip()
            and source in previous_titles
            and title != previous_titles[source]
        ):
            return TitleSwitch(source, now + 2)
    if pending.source not in sources or pending.source not in titles:
        return TitleSwitch()
    return pending


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
    title_switch: TitleSwitch = TitleSwitch(),
) -> Rotation:
    """Preserve a surviving source's deadline, including on membership changes."""
    if len(sources) < 2:
        return Rotation(sources, display_source)
    shown, deadline = previous.shown_source, previous.next_switch_at
    if title_switch.source in sources and title_switch.at is not None:
        if now >= title_switch.at:
            source = title_switch.source
            return Rotation(
                sources, source, now + (durations.get(source) or global_seconds)
            )
        if shown in sources and deadline is not None:
            # Wait the full two seconds even if the normal interval expires first.
            return Rotation(sources, shown, deadline)
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
    sources: Sequence[str], shown_source: str | None, states: Mapping[str, Any]
) -> str | None:
    """Plain text for other rotation members, with artist before music title."""
    entries = []
    for source in sources:
        state = states.get(source)
        if source == shown_source or state is None:
            continue
        attributes = state.attributes or {}
        title = str(attributes.get("media_title") or "").strip()
        content_type = attributes.get("media_content_type")
        if content_type == "music":
            artist = str(attributes.get("media_artist") or "").strip()
            entries.append(f"{artist} – {title}" if artist else title)
        else:
            entries.append(title)
    return " · ".join(entries) or None


def shown_source_state(
    shown_source: str | None,
    display_source: str | None,
    display_state: Any,
    states: Mapping[str, Any],
) -> Any:
    """Use one state for cover and text, preserving the held main snapshot."""
    if not shown_source or shown_source == display_source:
        return display_state
    return states.get(shown_source) or display_state
