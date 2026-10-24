"""Small per-entry state shared by the existing platforms and diagnostics."""

from collections.abc import Callable
import time

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, State, callback
from homeassistant.helpers import entity_registry as er, issue_registry as ir
from homeassistant.helpers.event import async_call_later, async_track_state_change_event
from homeassistant.helpers.start import async_at_started

from .const import (
    CONF_HOLD_SECONDS,
    DEFAULT_HOLD_SECONDS,
    DOMAIN,
    entry_option,
    slots_from_entry,
    tracked_entities,
)
from .resolver import Resolution, priority_slots, resolve_with_hold


class EntryRuntime:
    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        self.hass = hass
        self.slots = slots_from_entry(entry)
        self.override: str | None = None
        self.selection = Resolution(None, None, "off", ())
        self.display_state: State | None = None
        self.hold_active = False
        self.failed_at: float | None = None
        self.hold_seconds = float(
            entry_option(entry, CONF_HOLD_SECONDS, DEFAULT_HOLD_SECONDS)
        )
        self._listeners: list[Callable[[], None]] = []
        self._hold_cancel: Callable[[], None] | None = None
        self._repair_cancel: Callable[[], None] | None = None
        self._unsubs: list[Callable[[], None]] = []
        self._repairs_ready = False
        self._issue_id = f"{entry.entry_id}_missing_entities"

    @callback
    def start(self) -> None:
        entities = tracked_entities(self.slots)
        if entities:
            self._unsubs.append(
                async_track_state_change_event(self.hass, entities, self._changed)
            )
        self._unsubs.append(
            self.hass.bus.async_listen(
                er.EVENT_ENTITY_REGISTRY_UPDATED, self._registry_changed
            )
        )
        self._unsubs.append(async_at_started(self.hass, self._started))
        self.refresh()

    @callback
    def subscribe(self, listener: Callable[[], None]) -> Callable[[], None]:
        self._listeners.append(listener)
        return lambda: self._listeners.remove(listener)

    @callback
    def set_override(self, source: str | None) -> None:
        self.override = source if source in {sid for sid, _ in self.slots} else None
        self.refresh()

    @callback
    def refresh(self, _now=None) -> None:
        states = {
            sid: state
            for sid in tracked_entities(self.slots)
            if (state := self.hass.states.get(sid)) is not None
        }
        result = resolve_with_hold(
            priority_slots(self.slots, self.override),
            states,
            self.selection,
            self.failed_at,
            time.monotonic(),
            self.hold_seconds,
        )
        self.selection, self.failed_at, self.hold_active = result
        if not self.hold_active:
            self.display_state = states.get(self.selection.display_source)
        if self._hold_cancel:
            self._hold_cancel()
            self._hold_cancel = None
        if self.hold_active:
            remaining = self.failed_at + self.hold_seconds - time.monotonic()
            self._hold_cancel = async_call_later(
                self.hass, max(0, remaining), self._hold_expired
            )
        for listener in tuple(self._listeners):
            listener()

    @callback
    def _hold_expired(self, _now) -> None:
        self._hold_cancel = None
        self.refresh()

    @callback
    def _changed(self, _event) -> None:
        self.refresh()
        self._check_repairs()

    @callback
    def _registry_changed(self, _event) -> None:
        self._check_repairs()

    @callback
    def _started(self, _hass) -> None:
        self._repair_cancel = async_call_later(self.hass, 120, self._enable_repairs)

    @callback
    def _enable_repairs(self, _now) -> None:
        self._repair_cancel = None
        self._repairs_ready = True
        self._check_repairs()

    @callback
    def _check_repairs(self) -> None:
        if not self._repairs_ready:
            return
        registry = er.async_get(self.hass)
        missing = [
            sid
            for sid in tracked_entities(self.slots)
            if self.hass.states.get(sid) is None and registry.async_get(sid) is None
        ]
        if missing:
            ir.async_create_issue(
                self.hass,
                DOMAIN,
                self._issue_id,
                is_fixable=False,
                severity=ir.IssueSeverity.WARNING,
                translation_key="missing_entities",
                translation_placeholders={"entities": ", ".join(missing)},
                is_persistent=False,
            )
        else:
            ir.async_delete_issue(self.hass, DOMAIN, self._issue_id)

    @callback
    def stop(self) -> None:
        for cancel in self._unsubs:
            cancel()
        self._unsubs.clear()
        if self._hold_cancel:
            self._hold_cancel()
        if self._repair_cancel:
            self._repair_cancel()
        ir.async_delete_issue(self.hass, DOMAIN, self._issue_id)
