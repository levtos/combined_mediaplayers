# App icons

Add your own square PNG artwork as `<slug>.png`; no app artwork is bundled yet.
Use Home Assistant `slugify(app_name)` (Netflix → `netflix.png`, Prime Video →
`prime_video.png`), or the last `app_id` segment when the app name is absent.
Only lowercase ASCII letters, digits and underscores are allowed in the stem.

An optional `<HA-Config>/www/combined_media_player/app_icons/<slug>.png` overrides
the bundled file. Missing/invalid images quietly retain the previous fallback.
See the repository README's **App icon fallback** section for full behavior.
