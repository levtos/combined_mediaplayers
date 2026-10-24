# Changelog

## 1.7.0 (2026-10-25)
- Use one shared, Home Assistant independent resolver for the media player and cover image: playing/buffering > paused, then slot order. on/idle sources provide controls but no media title or cover; paused remains paused.
- Replace global audio_sources with an optional control target per priority slot in setup and options, with native HA form sections and German/English descriptions. Track changes to every source and control target.
- Migrate config entries to version 2: retain ordered sources as slots, discard legacy audio_sources with a one-time info message, and keep existing entity IDs and unique IDs.
- Read source entity_picture for cover URLs and disk-cache keys. Hash source, picture and title for HA's media proxy; continue fetching through the existing cover entity. Relative paths remain memory-only.
- Limit last-known-good images to the same display source and reject in-flight images after source/title changes. Never show another source's cover as a fallback.
- Expose active_source, control_target, other_active_sources and slots. Keep browsing delegated to capable configured sources or controls, independently of their playback state.
- Add focused pure-resolver tests and minimal verification/release automation; update priority, configuration and cover documentation.

## 1.6.0–1.6.6 (reconstructed from Git history)
- Move priority ordering into the integration dialog and then explicit option slots (PRs #25, #26).
- Add separate audio/control-source routing and HA-proxied cover delivery (PR #27).
- Delegate media browsing, search all configured sources for BROWSE_MEDIA, correct component lookup, and delegate media images to the paired cover entity (PRs #28–#31).
- Historical tags advanced through 1.6.6 while the manifest stayed at 1.6.0; 1.7.0 restores consistent manifest/tag versioning.

## 1.5.0 (reconstructed from Git history)
- Add a device ordering panel for source priority (PRs #23, #24); later replaced by the integration options dialog.

## 1.4.0–1.4.7 (reconstructed from Git history)
- Publish the separate cover image entity, shared-device registration and brand assets (PRs #15–#18).
- Improve image fetching through source entities and HA-relative/absolute URL fallbacks; retain images through transitions (PRs #19–#21).
- Add shared memory and persistent disk cover caches with a 14-day TTL (PR #22).

## 1.3.x (reconstructed from Git history)
- Correct integration visibility, preserve entity identity on renaming, modernize OptionsFlow and add MINOR_VERSION (PRs #7–#11).
- Introduce and repair initialization of the cover image platform (PRs #12–#14), subsequently published as 1.4.0.

## 1.2.0 (2026-02-25)
- `integration_type` changed from `helper` to `integration` – integration now appears under Devices & Services, not Helpers
- Added `device_info` to the entity – the combined player is now registered as a proper HA device with manufacturer/model
- `entity_picture` property already forwarded cover art from the active source (no change needed)

## 1.1.0 (2026-02-25)
- README rewritten in English as primary language
- README: integration icon added
- `strings.json` confirmed as English base; `translations/de.json` as German translation
- Priority table updated to reflect removal of `standby`

## 1.0.0 (2026-02-25)
- Initial release: Combined Media Player integration
- Config flow: create combined player via UI with name + source selection
- Priority logic: position 1 = highest priority, descending
- State tiers: playing/buffering > paused/idle > on; off/unavailable/unknown → ignored
- Removed deprecated `MediaPlayerState.STANDBY` (removed in HA Core 2026.8.0)
- Transparent cover art forwarded from active source (native or media_cover_art)
- All media player controls forwarded to the active source
- Options flow: edit sources and priority order at any time
- State model: only `playing`, `idle`, `on`, `off` – no `unavailable` or `unknown`
