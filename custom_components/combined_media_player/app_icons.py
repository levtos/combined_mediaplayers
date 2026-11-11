"""App presentation helpers and executor-only, validated local icon loading."""

from collections import OrderedDict
from pathlib import Path
import re
from typing import NamedTuple

from homeassistant.util import slugify

from .image_validation import image_error
from .rotation import media_title_with_series


def nonempty(value):
    return value if isinstance(value, str) and value.strip() else None


def app_icon_slug(attributes):
    name = nonempty(attributes.get("app_name"))
    app_id = nonempty(attributes.get("app_id"))
    for value in (name, app_id.rsplit(".", 1)[-1] if app_id else None):
        slug = slugify(value) if value else None
        if slug and re.fullmatch(r"[a-z0-9_]+", slug):
            return slug
    return None


def presentation_title(attributes, enabled=True, placeholder=False):
    if placeholder:
        return nonempty(attributes.get("app_name")) if enabled else None
    if nonempty(attributes.get("media_title")):
        return media_title_with_series(attributes)
    return (
        nonempty(attributes.get("app_name"))
        if enabled
        else attributes.get("media_title")
    )


def placeholder_state(selection, ordered_slots, states, enabled=True):
    """Mirror the already chosen idle slot in presentation only, never resolve it."""
    if not enabled or selection.display_source or selection.cmp_state != "idle":
        return None
    # First on/idle slot is the selected source even with shared control targets.
    state = next(
        (
            states[source]
            for source, _ in ordered_slots
            if source in states and states[source].state in {"on", "idle"}
        ),
        None,
    )
    return state if state and nonempty(state.attributes.get("app_name")) else None


class AppIcon(NamedTuple):
    slug: str
    path: str
    mtime: int
    data: bytes

    @property
    def fingerprint(self):
        return (self.slug, self.path, self.mtime)


class AppIconCache:
    """Called only in an executor; cache both valid and invalid bytes by path/mtime."""

    def __init__(self):
        self._cache = OrderedDict()

    def load(self, slug, override_dir, bundled_dir):
        if not isinstance(slug, str) or not re.fullmatch(r"[a-z0-9_]+", slug):
            return None
        for directory in (override_dir, bundled_dir):
            path = Path(directory) / f"{slug}.png"
            try:
                # Resolve inside the executor, also rejecting symlinks outside the root.
                if not path.resolve().is_relative_to(Path(directory).resolve()):
                    continue
                mtime = path.stat().st_mtime_ns
                key = (str(path), mtime)
                if key not in self._cache:
                    data = path.read_bytes()
                    self._cache[key] = (
                        AppIcon(slug, str(path), mtime, data)
                        if image_error(data, "image/png") is None
                        else None
                    )
                    while len(self._cache) > 64:
                        self._cache.popitem(last=False)
                # An existing but invalid override counts as no icon.
                return self._cache[key]
            except OSError:
                continue
        return None
