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
    sources: Sequence[str], controls: Mapping[str, str | None]
) -> list[dict]:
    """Keep source order and attach controls by identity, never by position."""
    return [
        {"source": source, "control": controls.get(source) or None}
        for source in dict.fromkeys(sources)
        if source
    ]
