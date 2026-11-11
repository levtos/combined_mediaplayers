from __future__ import annotations

import asyncio
from collections.abc import Callable
import hashlib
import json
import logging
import re
import time
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import aiohttp
from homeassistant.components.image import ImageEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import ATTR_ENTITY_PICTURE, EVENT_HOMEASSISTANT_STOP
from homeassistant.core import HomeAssistant, State, callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.event import (
    async_track_time_interval,
)
from homeassistant.helpers.network import get_url as ha_get_url
from homeassistant.util import dt as dt_util, slugify

from .const import DOMAIN
from .image_validation import audit_cached_images, fetch_validated_image, image_error

_LOGGER = logging.getLogger(__name__)

_FINGERPRINT_ATTR = ATTR_ENTITY_PICTURE
_FETCH_TIMEOUT = aiohttp.ClientTimeout(total=5)

# Cache settings
_CACHE_TTL_SECONDS: float = 14 * 24 * 3600  # 14 days
_CACHE_DIR_NAME = "combined_media_player_cover_cache"
_CACHE_CLEANUP_INTERVAL = timedelta(hours=24)
_HASS_DATA_CACHE_KEY = "cover_cache"


# ---------------------------------------------------------------------------
# Cover art cache
# ---------------------------------------------------------------------------


@dataclass
class _MemEntry:
    data: bytes
    content_type: str
    last_used: float  # time.monotonic()


# Module-level executor helpers (must not be lambdas for thread-safety clarity)


def _read_file(path: Path) -> bytes | None:
    try:
        return path.read_bytes()
    except OSError:
        return None


def _write_file(path: Path, data: bytes) -> bool:
    try:
        path.write_bytes(data)
        return True
    except OSError:
        return False


def _write_json(path: Path, data: dict) -> bool:
    try:
        path.write_text(json.dumps(data))
        return True
    except OSError:
        return False


def _setup_cache_dir(cache_dir: Path, index_path: Path) -> dict:
    """Load only index metadata here; audit image bytes in the background."""
    cache_dir.mkdir(parents=True, exist_ok=True)
    index = {}
    if index_path.exists():
        try:
            loaded = json.loads(index_path.read_text())
            index = loaded if isinstance(loaded, dict) else {}
        except Exception:
            pass
    valid = {}
    for key, metadata in index.items():
        # The index is data; never use an unvalidated name as a filesystem path.
        if not re.fullmatch(r"[0-9a-f]{32}", key):
            continue
        path = cache_dir / f"{key}.bin"
        if (
            isinstance(metadata, dict)
            and isinstance(metadata.get("content_type"), str)
            and metadata["content_type"].lower().startswith("image/")
            and isinstance(metadata.get("last_used"), (int, float))
        ):
            valid[key] = metadata
        else:
            _delete_files([path])
    if valid != index:
        _write_json(index_path, valid)
    return valid


def _delete_files(paths: list[Path]) -> None:
    for p in paths:
        try:
            p.unlink(missing_ok=True)
        except OSError:
            pass


class _CoverCache:
    """Two-layer cover art cache: in-memory (fast) + disk (persistent, 14-day TTL).

    Stored in hass.data[DOMAIN][_HASS_DATA_CACHE_KEY] and shared across all
    CombinedCoverImage entities so multiple combined players sharing a source
    entity benefit from the same cached image.

    Cache key strategy:
    - Absolute entity_picture URLs (e.g. CDN links): stored on disk AND
      in memory – survive HA restarts.
    - Session-scoped URLs (entity_picture with HA auth token): in-memory only –
      the token changes on restart so disk persistence would be useless.
    """

    def __init__(self, hass: HomeAssistant) -> None:
        self._hass = hass
        self._mem: dict[str, _MemEntry] = {}
        # disk index: sha256[:32] -> {content_type: str, last_used: float (unix ts)}
        self._disk_index: dict[str, dict] = {}
        self._cache_dir: Path | None = None
        self._index_path: Path | None = None
        self._dirty = False
        self._cleanup_unsub: Any = None
        self._audit_task: asyncio.Task | None = None
        self._disk_generations: dict[str, int] = {}

    # ------------------------------------------------------------------
    # Initialization / teardown
    # ------------------------------------------------------------------

    async def async_setup(self) -> None:
        """Load the index promptly; validate old image files off the setup path."""
        cache_dir = Path(self._hass.config.path(".storage", _CACHE_DIR_NAME))
        index_path = cache_dir / "index.json"
        self._cache_dir = cache_dir
        self._index_path = index_path

        self._disk_index = await self._hass.async_add_executor_job(
            _setup_cache_dir, cache_dir, index_path
        )
        # Schedule daily cleanup
        self._cleanup_unsub = async_track_time_interval(
            self._hass,
            self._on_cleanup_interval,
            _CACHE_CLEANUP_INTERVAL,
        )

        # Flush index when HA shuts down so no disk entries are orphaned
        self._hass.bus.async_listen_once(
            EVENT_HOMEASSISTANT_STOP,
            self._on_ha_stop,
        )
        self._audit_task = self._hass.async_create_background_task(
            self._async_audit_disk(), "CMP cached image audit", eager_start=False
        )

        _LOGGER.debug("Cover cache ready (%d disk entries)", len(self._disk_index))

    async def async_teardown(self) -> None:
        if self._cleanup_unsub is not None:
            self._cleanup_unsub()
            self._cleanup_unsub = None
        if self._audit_task is not None:
            self._audit_task.cancel()
            await asyncio.gather(self._audit_task, return_exceptions=True)
            self._audit_task = None
        await self._flush_index()

    async def _async_audit_disk(self) -> None:
        async def read(key):
            return await self._hass.async_add_executor_job(
                _read_file, self._cache_dir / f"{key}.bin"
            )

        try:
            await audit_cached_images(
                self._disk_index,
                self._disk_generations,
                read,
                self._evict_disk,
                self._hass.async_add_executor_job,
                time.time(),
                _CACHE_TTL_SECONDS,
            )
        except Exception as error:
            _LOGGER.warning("Cover cache audit failed (%s)", type(error).__name__)

    # ------------------------------------------------------------------
    # Key helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _hash(key: str) -> str:
        return hashlib.sha256(key.encode()).hexdigest()[:32]

    @staticmethod
    def _is_stable(key: str) -> bool:
        """Return True if the key is an absolute CDN/server URL (disk-cacheable)."""
        return key.startswith(("http://", "https://"))

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def get(self, key: str) -> tuple[bytes, str] | None:
        """Return (data, content_type) from cache, or None on a miss."""
        # 1. Memory layer (fastest – no I/O)
        entry = self._mem.get(key)
        if entry is not None:
            error = await self._hass.async_add_executor_job(
                image_error, entry.data, entry.content_type
            )
            if (
                error is None
                and time.monotonic() - entry.last_used <= _CACHE_TTL_SECONDS
            ):
                entry.last_used = time.monotonic()
                return entry.data, entry.content_type
            self._mem.pop(key, None)
            if error is not None:
                self._evict_disk(self._hash(key))

        # 2. Disk layer (stable keys only)
        if not self._is_stable(key) or self._cache_dir is None:
            return None

        h = self._hash(key)
        meta = self._disk_index.get(h)
        if meta is None:
            return None

        if time.time() - meta.get("last_used", 0.0) > _CACHE_TTL_SECONDS:
            self._evict_disk(h)
            return None

        data = await self._hass.async_add_executor_job(
            _read_file, self._cache_dir / f"{h}.bin"
        )
        if data is None:
            self._evict_disk(h)
            return None

        content_type = meta.get("content_type")
        if (
            await self._hass.async_add_executor_job(image_error, data, content_type)
            is not None
        ):
            self._evict_disk(h)
            return None

        meta["last_used"] = time.time()
        self._dirty = True
        # Promote to memory so subsequent requests within the session skip disk I/O
        self._mem[key] = _MemEntry(
            data=data, content_type=content_type, last_used=time.monotonic()
        )
        return data, content_type

    async def put(self, key: str, data: bytes, content_type: str) -> None:
        """Store image in cache (memory always, disk for stable keys)."""
        if (
            await self._hass.async_add_executor_job(image_error, data, content_type)
            is not None
        ):
            return
        self._mem[key] = _MemEntry(
            data=data, content_type=content_type, last_used=time.monotonic()
        )
        if not self._is_stable(key) or self._cache_dir is None:
            return

        h = self._hash(key)
        # Mark the write before yielding to disk I/O: an audit of the old file
        # must never remove bytes that this writer is about to replace.
        self._disk_generations[h] = self._disk_generations.get(h, 0) + 1
        ok = await self._hass.async_add_executor_job(
            _write_file, self._cache_dir / f"{h}.bin", data
        )
        if ok:
            self._disk_index[h] = {
                "content_type": content_type,
                "last_used": time.time(),
            }
            self._dirty = True

    async def discard(self, key: str) -> None:
        self._mem.pop(key, None)
        self._evict_disk(self._hash(key))

    # ------------------------------------------------------------------
    # Maintenance
    # ------------------------------------------------------------------

    async def _on_cleanup_interval(self, _now: datetime) -> None:
        await self._prune_expired()
        await self._flush_index()

    async def _on_ha_stop(self, _event: Any) -> None:
        await self.async_teardown()

    async def _prune_expired(self) -> None:
        if self._cache_dir is None:
            return
        now = time.time()
        expired = [
            h
            for h, m in self._disk_index.items()
            if now - m.get("last_used", 0.0) > _CACHE_TTL_SECONDS
        ]
        if not expired:
            return
        paths = [self._cache_dir / f"{h}.bin" for h in expired]
        await self._hass.async_add_executor_job(_delete_files, paths)
        for h in expired:
            self._disk_index.pop(h, None)
        self._dirty = True
        _LOGGER.debug("Cover cache: pruned %d expired entries", len(expired))

    async def _flush_index(self) -> None:
        if not self._dirty or self._index_path is None:
            return
        ok = await self._hass.async_add_executor_job(
            _write_json, self._index_path, dict(self._disk_index)
        )
        if ok:
            self._dirty = False

    def _evict_disk(self, h: str) -> None:
        self._disk_generations[h] = self._disk_generations.get(h, 0) + 1
        self._disk_index.pop(h, None)
        self._dirty = True
        if self._cache_dir:
            try:
                (self._cache_dir / f"{h}.bin").unlink(missing_ok=True)
            except OSError:
                pass


def _image_cache_key(state: State, entity_id: str) -> str | None:
    """Persist absolute entity_picture URLs; keep relative paths in memory."""
    picture = state.attributes.get(ATTR_ENTITY_PICTURE)
    if not picture:
        return None
    if picture.startswith(("http://", "https://")):
        return picture
    return f"{entity_id}:{picture}"


async def async_get_cover_cache(hass: HomeAssistant) -> _CoverCache:
    """Initialize the shared two-layer cache once, including concurrent loads."""
    data = hass.data.setdefault(DOMAIN, {})
    lock = data.setdefault("cover_cache_lock", asyncio.Lock())
    async with lock:
        if _HASS_DATA_CACHE_KEY not in data:
            cache = _CoverCache(hass)
            await cache.async_setup()
            data[_HASS_DATA_CACHE_KEY] = cache
        return data[_HASS_DATA_CACHE_KEY]


async def _async_prepare_cover_cache(hass: HomeAssistant) -> None:
    try:
        await async_get_cover_cache(hass)
    except Exception as error:
        _LOGGER.warning("Cover cache preparation failed (%s)", type(error).__name__)


@callback
def async_prepare_cover_cache(hass: HomeAssistant) -> None:
    """Warm the shared cache without delaying image entity/platform setup."""
    data = hass.data.setdefault(DOMAIN, {})
    if "cover_cache_preparation" not in data:
        data["cover_cache_preparation"] = hass.async_create_background_task(
            _async_prepare_cover_cache(hass),
            "CMP cover cache preparation",
            eager_start=False,
        )


def _record_fetch_status(
    hass: HomeAssistant, source: str, route: str, reason: str | None
) -> None:
    hass.data.setdefault(DOMAIN, {}).setdefault("image_fetch_status", {})[source] = {
        "status": "ok" if reason is None else "failed",
        "reason": reason,
        "route": route,
        "at": dt_util.utcnow().isoformat(),
    }
    if reason is not None:
        _LOGGER.debug("%s: %s image fetch failed (%s)", source, route, reason)


async def _validated_result(hass, source, route, image):
    reason = await hass.async_add_executor_job(image_error, *image)
    _record_fetch_status(hass, source, route, reason)
    return image if reason is None else None


async def _async_entity_image(
    hass: HomeAssistant, source: str
) -> tuple[bytes, str] | None:
    component = hass.data.get("media_player")
    entity = (
        component.get_entity(source)
        if component is not None and hasattr(component, "get_entity")
        else None
    )
    if entity is not None and hasattr(entity, "async_get_media_image"):
        try:
            data, content_type = await entity.async_get_media_image()
            return await _validated_result(hass, source, "entity", (data, content_type))
        except Exception as error:
            # Never log an exception string: provider errors may contain token URLs.
            _record_fetch_status(hass, source, "entity", type(error).__name__)
            return None
    _record_fetch_status(hass, source, "entity", "no_image")
    return None


async def _async_fetch_url(
    hass: HomeAssistant, url: str, source: str
) -> tuple[bytes, str] | None:
    if url.startswith("/"):
        base = None
        for kw in (
            {"allow_ip": True, "prefer_external": False},
            {"allow_ip": True, "prefer_external": True},
        ):
            try:
                base = ha_get_url(hass, **kw)
                break
            except Exception:
                pass
        url = f"{base or 'http://127.0.0.1:8123'}{url}"
    try:
        async with async_get_clientsession(hass).get(
            url, timeout=_FETCH_TIMEOUT
        ) as response:
            if response.status == 200:
                return await _validated_result(
                    hass, source, "url", (await response.read(), response.content_type)
                )
            _record_fetch_status(hass, source, "url", f"HTTP {response.status}")
    except Exception as error:
        _record_fetch_status(hass, source, "url", type(error).__name__)
    return None


async def async_fetch_source_image(
    hass: HomeAssistant,
    state: State,
    is_current: Callable[[], bool],
) -> tuple[bytes, str] | None:
    """Shared cache/entity/URL fetch for main and secondary images."""
    cache = await async_get_cover_cache(hass)
    key = _image_cache_key(state, state.entity_id)
    fetchers = [("entity", lambda: _async_entity_image(hass, state.entity_id))]
    if url := state.attributes.get(ATTR_ENTITY_PICTURE):
        fetchers.append(("url", lambda: _async_fetch_url(hass, url, state.entity_id)))
    return await fetch_validated_image(
        cache,
        key,
        fetchers,
        hass.async_add_executor_job,
        is_current,
        lambda route, reason: _record_fetch_status(
            hass, state.entity_id, route, reason
        ),
    )


# ---------------------------------------------------------------------------
# Setup
# ---------------------------------------------------------------------------


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities
) -> None:
    async_add_entities([CombinedCoverImage(hass, entry)], update_before_add=False)


# ---------------------------------------------------------------------------
# Entity
# ---------------------------------------------------------------------------


class CombinedCoverImage(ImageEntity):
    """Image entity exposing cover art from the active combined source."""

    _attr_has_entity_name = False
    _attr_icon = "mdi:image"

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        super().__init__(hass)
        self._entry = entry
        self._attr_unique_id = f"{entry.unique_id}_cover"
        self._attr_name = f"{entry.title} Cover"
        self._attr_suggested_object_id = (
            f"{slugify(entry.unique_id or entry.entry_id)}_cover"
        )
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
        )
        self._unsub: Any = None
        self._last_image: bytes | None = None
        self._last_source: str | None = None
        self._cached_fingerprint: str | None = None
        self._last_placeholder = False

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _active_state(self) -> State | None:
        return self._runtime.main_presentation_state

    @property
    def _runtime(self):
        return self.hass.data[DOMAIN][self._entry.entry_id]

    def _image_fingerprint(self) -> str | None:
        active = self._active_state()
        if active is None:
            return None
        icon = self._runtime.icon_for(active)
        return json.dumps(
            [
                active.entity_id,
                active.attributes.get(_FINGERPRINT_ATTR),
                active.attributes.get("media_title"),
                active.attributes.get("app_name"),
                active.attributes.get("app_id"),
                icon.fingerprint if icon else None,
                self._runtime.cover_attributes_for(active),
            ]
        )

    def _refresh_image_url(self) -> None:
        """Invalidate on every winner transition, including loss of its picture."""
        active = self._active_state()
        source = active.entity_id if active else None
        placeholder = self._runtime.selection.display_source is None
        if source != self._last_source or placeholder != self._last_placeholder:
            self._last_source = source
            self._last_placeholder = placeholder
            self._last_image = None
            self._attr_content_type = None
        fingerprint = self._image_fingerprint()
        if fingerprint != self._cached_fingerprint:
            self._cached_fingerprint = fingerprint
            self._attr_image_last_updated = dt_util.utcnow()

    async def async_image(self) -> bytes | None:
        """Fetch only the display winner; keep good art only for that source."""
        await self._runtime.async_prepare_icons()
        self._refresh_image_url()
        active = self._active_state()
        if active is None:
            return None
        fingerprint = self._image_fingerprint()
        try:
            image = (
                None
                if self._last_placeholder
                else await async_fetch_source_image(
                    self.hass,
                    active,
                    lambda: self._image_fingerprint() == fingerprint,
                )
            )
        except Exception:
            image = None
        # A source/title can change while cache, entity or HTTP I/O is awaited.
        # Never publish an in-flight response for the previous winner.
        if self._image_fingerprint() != fingerprint:
            self._refresh_image_url()
            return None
        if image is not None:
            self._last_image, self._attr_content_type = image
        if self._last_image is not None:
            self._runtime.record_cover(active, "source")
        elif icon := self._runtime.icon_for(active):
            self._attr_content_type = "image/png"
            self._runtime.record_cover(active, "app_icon", icon.slug)
            return icon.data
        else:
            self._runtime.record_cover(active, None)
        return self._last_image

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self._attr_image_last_updated = dt_util.utcnow()
        self._refresh_image_url()

        self._unsub = self.hass.data[DOMAIN][self._entry.entry_id].subscribe(
            self._handle_state_change
        )
        async_prepare_cover_cache(self.hass)

    async def async_will_remove_from_hass(self) -> None:
        if self._unsub is not None:
            self._unsub()
            self._unsub = None
        await super().async_will_remove_from_hass()

    @callback
    def _handle_state_change(self) -> None:
        self._refresh_image_url()
        self.async_write_ha_state()

    # ------------------------------------------------------------------
    # State / attributes
    # ------------------------------------------------------------------

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        active = self._active_state()
        return {
            "active_source": active.entity_id if active else None,
            "active_source_name": (
                active.attributes.get("friendly_name") or active.entity_id
            )
            if active
            else None,
        }
