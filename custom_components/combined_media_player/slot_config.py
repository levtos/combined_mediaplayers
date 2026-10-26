"""Pure source/control mapping for both steps of the configuration flow."""

from collections.abc import Mapping, Sequence


def controls_by_source(
    slots: Sequence[tuple[str, str | None]],
) -> dict[str, str | None]:
    controls = {}
    for source, control in slots:
        controls.setdefault(source, control)
    return controls


def make_slots(
    sources: Sequence[str],
    controls: Mapping[str, str | None],
    durations: Mapping[str, float | None] | None = None,
) -> list[dict]:
    """Keep source order and attach settings by identity, never by position."""
    return [
        {
            "source": source,
            "control": controls.get(source) or None,
            **(
                {"display_seconds": durations[source]}
                if durations and durations.get(source) is not None
                else {}
            ),
        }
        for source in dict.fromkeys(sources)
        if source
    ]


def settings_by_source(slots: Sequence[Mapping]) -> dict[str, dict]:
    """First occurrence wins, including the optional duration override."""
    settings = {}
    for slot in slots:
        settings.setdefault(slot["source"], dict(slot))
    return settings
