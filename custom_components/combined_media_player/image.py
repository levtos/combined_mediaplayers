from __future__ import annotations

import hashlib
import json
import logging
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
    async_track_state_change_event,
    async_track_time_interval,
)
from homeassistant.helpers.network import get_url as ha_get_url
from homeassistant.util import dt as dt_util, slugify

from .const import DOMAIN, slots_from_entry, tracked_entities
from .resolver import resolve

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
    cache_dir.mkdir(parents=True, exist_ok=True)
    if index_path.exists():
        try:
            return json.loads(index_path.read_text())
        except Exception:
            pass
    return {}


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

    # ------------------------------------------------------------------
    # Initialization / teardown
    # ------------------------------------------------------------------

    async def async_setup(self) -> None:
        """Create cache directory, load index, prune expired entries."""
        cache_dir = Path(self._hass.config.path(".storage", _CACHE_DIR_NAME))
        index_path = cache_dir / "index.json"
        self._cache_dir = cache_dir
        self._index_path = index_path

        self._disk_index = await self._hass.async_add_executor_job(
            _setup_cache_dir, cache_dir, index_path
        )
        await self._prune_expired()

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

        _LOGGER.debug("Cover cache ready (%d disk entries)", len(self._disk_index))

    async def async_teardown(self) -> None:
        if self._cleanup_unsub is not None:
            self._cleanup_unsub()
            self._cleanup_unsub = None
        await self._flush_index()

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
            if time.monotonic() - entry.last_used <= _CACHE_TTL_SECONDS:
                entry.last_used = time.monotonic()
                return entry.data, entry.content_type
            del self._mem[key]

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

        meta["last_used"] = time.time()
        self._dirty = True
        content_type = meta.get("content_type", "image/jpeg")
        # Promote to memory so subsequent requests within the session skip disk I/O
        self._mem[key] = _MemEntry(
            data=data, content_type=content_type, last_used=time.monotonic()
        )
        return data, content_type

    async def put(self, key: str, data: bytes, content_type: str) -> None:
        """Store image in cache (memory always, disk for stable keys)."""
        self._mem[key] = _MemEntry(
            data=data, content_type=content_type, last_used=time.monotonic()
        )
        if not self._is_stable(key) or self._cache_dir is None:
            return

        h = self._hash(key)
        ok = await self._hass.async_add_executor_job(
            _write_file, self._cache_dir / f"{h}.bin", data
        )
        if ok:
            self._disk_index[h] = {
                "content_type": content_type,
                "last_used": time.time(),
            }
            self._dirty = True

    # ------------------------------------------------------------------
    # Maintenance
    # ------------------------------------------------------------------

    async def _on_cleanup_interval(self, _now: datetime) -> None:
        await self._prune_expired()
        await self._flush_index()

    async def _on_ha_stop(self, _event: Any) -> None:
        await self._flush_index()

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
        self._slots = slots_from_entry(entry)
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

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _active_state(self) -> State | None:
        states = {
            entity_id: state
            for entity_id in tracked_entities(self._slots)
            if (state := self.hass.states.get(entity_id)) is not None
        }
        source = resolve(self._slots, states).display_source
        return states.get(source) if source else None

    def _image_fingerprint(self) -> str | None:
        active = self._active_state()
        if active is None:
            return None
        return json.dumps(
            [
                active.entity_id,
                active.attributes.get(_FINGERPRINT_ATTR),
                active.attributes.get("media_title"),
            ]
        )

    def _refresh_image_url(self) -> None:
        """Invalidate on every winner transition, including loss of its picture."""
        active = self._active_state()
        source = active.entity_id if active else None
        if source != self._last_source:
            self._last_source = source
            self._last_image = None
            self._attr_content_type = None
        fingerprint = self._image_fingerprint()
        if fingerprint != self._cached_fingerprint:
            self._cached_fingerprint = fingerprint
            self._attr_image_last_updated = dt_util.utcnow()

    def _get_cache(self) -> _CoverCache | None:
        return self.hass.data.get(DOMAIN, {}).get(_HASS_DATA_CACHE_KEY)

    # ------------------------------------------------------------------
    # Image fetching
    # ------------------------------------------------------------------

    async def _get_entity_image(self, entity_id: str) -> tuple[bytes, str] | None:
        """Delegate to the source entity's async_get_media_image()."""
        mp_component = self.hass.data.get("media_player")
        if mp_component is None or not hasattr(mp_component, "get_entity"):
            _LOGGER.debug(
                "%s: media_player component not available, falling back to URL fetch",
                entity_id,
            )
            return None
        entity = mp_component.get_entity(entity_id)
        if entity is None or not hasattr(entity, "async_get_media_image"):
            return None
        try:
            image_data, content_type = await entity.async_get_media_image()
            if image_data:
                return image_data, content_type or "image/jpeg"
        except Exception:
            _LOGGER.debug("%s: async_get_media_image() failed", entity_id)
        return None

    async def _fetch_url(
        self, session: aiohttp.ClientSession, url: str
    ) -> tuple[bytes, str] | None:
        """Fallback: fetch image bytes from a URL, resolving HA-relative paths."""
        if url.startswith("/"):
            base = None
            for kw in (
                {"allow_ip": True, "prefer_external": False},
                {"allow_ip": True, "prefer_external": True},
            ):
                try:
                    base = ha_get_url(self.hass, **kw)
                    break
                except Exception:
                    pass
            url = f"{base or 'http://127.0.0.1:8123'}{url}"
        try:
            async with session.get(url, timeout=_FETCH_TIMEOUT) as resp:
                if resp.status == 200:
                    return await resp.read(), resp.content_type or "image/jpeg"
        except Exception:
            pass
        return None

    async def async_image(self) -> bytes | None:
        """Fetch only the display winner; keep good art only for that source."""
        self._refresh_image_url()
        active = self._active_state()
        if active is None:
            return None
        source = active.entity_id
        fingerprint = self._image_fingerprint()
        cache = self._get_cache()
        cache_key = _image_cache_key(active, source)
        image = None
        if cache_key and cache is not None:
            image = await cache.get(cache_key)
        if image is None:
            image = await self._get_entity_image(source)
            if image is None and (url := active.attributes.get(ATTR_ENTITY_PICTURE)):
                image = await self._fetch_url(async_get_clientsession(self.hass), url)
            if self._image_fingerprint() != fingerprint:
                self._refresh_image_url()
                return None
            if image is not None and cache_key and cache is not None:
                await cache.put(cache_key, *image)
        # A source/title can change while cache, entity or HTTP I/O is awaited.
        # Never publish an in-flight response for the previous winner.
        if self._image_fingerprint() != fingerprint:
            self._refresh_image_url()
            return None
        if image is not None:
            self._last_image, self._attr_content_type = image
        return self._last_image

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self._slots = slots_from_entry(self._entry)
        self._attr_image_last_updated = dt_util.utcnow()
        self._refresh_image_url()

        # Initialize shared cache on first entity load; subsequent entities reuse it
        domain_data = self.hass.data.setdefault(DOMAIN, {})
        if _HASS_DATA_CACHE_KEY not in domain_data:
            cache = _CoverCache(self.hass)
            await cache.async_setup()
            domain_data[_HASS_DATA_CACHE_KEY] = cache

        all_tracked = tracked_entities(self._slots)
        if all_tracked:
            self._unsub = async_track_state_change_event(
                self.hass,
                all_tracked,
                self._handle_state_change,
            )

    async def async_will_remove_from_hass(self) -> None:
        if self._unsub is not None:
            self._unsub()
            self._unsub = None
        await super().async_will_remove_from_hass()

    @callback
    def _handle_state_change(self, _event) -> None:
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
