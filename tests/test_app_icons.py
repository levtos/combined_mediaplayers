"""Focused local-art and presentation contracts without a full HA installation."""

import ast
import asyncio
from datetime import datetime, timezone
from io import BytesIO
import os
from pathlib import Path
import re
import threading
from types import SimpleNamespace

from PIL import Image
import pytest

ROOT = Path(__file__).parents[1] / "custom_components/combined_media_player"


def load(name, only_class=None, **dependencies):
    """Use production bodies, substituting only HA imports/platform base classes."""
    tree = ast.parse((ROOT / f"{name}.py").read_text(encoding="utf-8"))
    tree.body = [
        node
        for node in tree.body
        if not isinstance(node, ast.ImportFrom)
        or not (node.level or (node.module or "").startswith("homeassistant"))
    ]
    if only_class:
        tree.body = [
            node
            for node in tree.body
            if (
                isinstance(node, ast.ClassDef)
                and node.name == only_class
                or isinstance(node, ast.ImportFrom)
                or isinstance(node, ast.Import)
                and all(alias.name != "aiohttp" for alias in node.names)
            )
        ]
    namespace = {"__file__": str(ROOT / f"{name}.py"), **dependencies}
    exec(compile(tree, str(ROOT / f"{name}.py"), "exec"), namespace)
    return {key: value for key, value in namespace.items() if not key.startswith("__")}


def slugify(value):
    # ASCII names in these tests; HA supplies its full unicode-aware util in use.
    return re.sub(r"[^a-z0-9]+", "_", value.lower()).strip("_")


validation = load("image_validation")
rotation = load("rotation")
resolver = load("resolver")
icons = SimpleNamespace(
    **load(
        "app_icons",
        slugify=slugify,
        **validation,
        **{"media_title_with_series": rotation["media_title_with_series"]},
    )
)


def png(color="blue"):
    output = BytesIO()
    Image.new("RGB", (16, 16), color).save(output, format="PNG")
    return output.getvalue()


def state(source="tv", status="playing", **attributes):
    return SimpleNamespace(entity_id=source, state=status, attributes=attributes)


def test_netflix_name_id_title_and_slug_contracts():
    attrs = {"app_id": "com.netflix.Netflix", "app_name": "Netflix"}
    assert icons.app_icon_slug(attrs) == "netflix"
    assert icons.presentation_title(attrs) == "Netflix"
    assert icons.app_icon_slug({"app_id": attrs["app_id"]}) == "netflix"
    assert icons.app_icon_slug({"app_name": "Prime Video"}) == "prime_video"
    assert icons.app_icon_slug({}) is None
    assert icons.presentation_title({**attrs, "media_title": "  "}) == "Netflix"
    assert (
        icons.presentation_title(
            {**attrs, "media_title": "Episode", "media_series_title": "Series"}
        )
        == "Episode – Series"
    )
    assert icons.presentation_title(attrs, enabled=False) is None


def test_override_missing_invalid_and_mtime_cache(tmp_path, monkeypatch):
    override, bundled = tmp_path / "www", tmp_path / "bundled"
    override.mkdir()
    bundled.mkdir()
    cache = icons.AppIconCache()
    assert cache.load("netflix", override, bundled) is None
    path = bundled / "netflix.png"
    path.write_bytes(png())
    first = cache.load("netflix", override, bundled)
    assert first.data == png()
    # A cache hit stats the file but never reads or decodes it again.
    with monkeypatch.context() as patch:
        patch.setattr(Path, "read_bytes", lambda self: pytest.fail("cached read"))
        assert cache.load("netflix", override, bundled) is first
    custom = override / "netflix.png"
    custom.write_bytes(png("red"))
    assert cache.load("netflix", override, bundled).data == png("red")
    before = custom.stat().st_mtime_ns
    custom.write_bytes(png("green"))
    os.utime(custom, ns=(before + 1_000_000, before + 1_000_000))
    second = cache.load("netflix", override, bundled)
    assert second.data == png("green")
    assert second.fingerprint != first.fingerprint
    custom.write_bytes(b"invalid PNG")
    os.utime(custom, ns=(before + 2_000_000, before + 2_000_000))
    assert cache.load("netflix", override, bundled) is None


@pytest.mark.parametrize(
    "slug", ["../netflix", "a/b", "a\\b", "/netflix", "NETFLIX", "", None]
)
def test_no_path_traversal(slug, tmp_path):
    assert icons.AppIconCache().load(slug, tmp_path, tmp_path) is None


@pytest.mark.parametrize("status", ["on", "idle"])
def test_idle_placeholder_preserves_resolution_and_control_slot(status):
    slots = [("tv", "audio"), ("other", "audio")]
    states = {
        "tv": state(status=status, app_name="Netflix"),
        "audio": state("audio", "on"),
        "other": state("other", "idle", app_name="Plex"),
    }
    selection = resolver["resolve"](slots, states)
    assert selection.cmp_state == "idle"
    assert selection.display_source is None
    assert selection.control_target == "audio"
    assert icons.placeholder_state(selection, slots, states) is states["tv"]
    assert icons.placeholder_state(selection, slots, states, enabled=False) is None
    states["tv"].attributes = {"app_name": "  ", "app_id": "com.netflix.Netflix"}
    assert icons.placeholder_state(selection, slots, states) is None
    assert resolver["resolve"](slots, states) == selection


class Entity:
    def __init__(self, hass=None):
        self.hass = hass

    def async_write_ha_state(self):
        pass

    @property
    def content_type(self):
        return getattr(self, "_attr_content_type", None)


def platform_classes():
    common = {
        "HomeAssistant": object,
        "ConfigEntry": object,
        "DeviceInfo": dict,
        "State": object,
        "callback": lambda function: function,
        "DOMAIN": "combined_media_player",
        "dt_util": SimpleNamespace(utcnow=lambda: datetime.now(timezone.utc)),
    }
    # Runtime is instantiated without lifecycle/registries, using real presentation methods.
    runtime = load(
        "runtime",
        **common,
        **{
            key: getattr(icons, key)
            for key in ("AppIconCache", "app_icon_slug", "placeholder_state")
        },
        **(resolver | rotation),
    )["EntryRuntime"]
    image = load(
        "image",
        only_class="CombinedCoverImage",
        **common,
        ImageEntity=Entity,
        slugify=slugify,
        ATTR_ENTITY_PICTURE="entity_picture",
        _FINGERPRINT_ATTR="entity_picture",
        **validation,
    )["CombinedCoverImage"]
    player = load(
        "media_player",
        **common,
        MediaPlayerEntity=Entity,
        MediaPlayerState=lambda value: value,
        presentation_title=icons.presentation_title,
        other_sources_line=rotation["other_sources_line"],
    )["CombinedMediaPlayer"]
    return runtime, image, player


@pytest.fixture
def presentation(monkeypatch, tmp_path):
    Runtime, Cover, Player = platform_classes()
    current = state(app_name="Netflix", app_id="com.netflix.Netflix")
    runtime = object.__new__(Runtime)
    runtime.show_app_icon_fallback = True
    runtime._icons = {"netflix": icons.AppIcon("netflix", "netflix.png", 1, png())}
    runtime._cover_origins = {}
    runtime._listeners = []
    runtime.display_state = current
    runtime.selection = resolver["Resolution"]("tv", "audio", "playing", ())
    runtime.rotation = rotation["Rotation"](("tv",), "tv")
    runtime.slots = [("tv", "audio")]
    runtime.override = None
    runtime.hold_active = False
    runtime.slot_settings = []
    runtime.next_switch_at = None
    runtime._icon_lock = asyncio.Lock()
    runtime._icon_cache = icons.AppIconCache()
    states = {"tv": current}
    hass = SimpleNamespace(
        states=SimpleNamespace(get=states.get),
        data={"combined_media_player": {"entry": runtime}},
        config=SimpleNamespace(path=lambda *parts: str(tmp_path.joinpath(*parts))),
    )

    async def executor(function, *args):
        return await asyncio.to_thread(function, *args)

    hass.async_add_executor_job = executor
    runtime.hass = hass

    async def prepare():
        pass

    monkeypatch.setattr(runtime, "async_prepare_icons", prepare)
    entry = SimpleNamespace(entry_id="entry", unique_id="combined_media", title="Media")
    cover = Cover(hass, entry)
    player = object.__new__(Player)
    player.hass, player._entry = hass, entry
    # Real player delegates its main image to the paired image entity.
    player.async_get_media_image.__func__.__globals__["async_get_entity_registry"] = (
        lambda hass: SimpleNamespace(
            async_get_entity_id=lambda *args: "image.media_cover"
        )
    )
    hass.data["image"] = SimpleNamespace(get_entity=lambda key: cover)
    return runtime, cover, player, current, states


def test_real_cover_then_last_good_beat_icon_and_hash_changes(
    presentation, monkeypatch
):
    runtime, cover, player, current, _ = presentation
    real = png("red"), "image/png"

    async def fetch(*args):
        return real

    monkeypatch.setitem(
        cover.async_image.__func__.__globals__, "async_fetch_source_image", fetch
    )

    async def scenario():
        assert player.media_title == "Netflix"
        assert await player.async_get_media_image() == real
        initial_hash = player.media_image_hash
        assert runtime.cover_attributes == {
            "cover_origin": "source",
            "app_icon_slug": None,
        }

        async def missing(*args):
            return None

        monkeypatch.setitem(
            cover.async_image.__func__.__globals__, "async_fetch_source_image", missing
        )
        assert await cover.async_image() == real[0]  # same-source last good wins
        cover._last_image = None
        assert await player.async_get_media_image() == (png(), "image/png")
        assert runtime.cover_attributes == {
            "cover_origin": "app_icon",
            "app_icon_slug": "netflix",
        }
        assert player.media_image_hash != initial_hash
        before = player.media_image_hash
        runtime._icons["netflix"] = runtime._icons["netflix"]._replace(mtime=2)
        assert player.media_image_hash != before
        current.attributes["media_title"] = "Real title"
        monkeypatch.setitem(
            cover.async_image.__func__.__globals__, "async_fetch_source_image", fetch
        )
        assert player.media_title == "Real title"
        assert await player.async_get_media_image() == real
        assert runtime.cover_attributes["cover_origin"] == "source"

    asyncio.run(scenario())


def test_idle_placeholder_and_option_off_use_no_source_cover(presentation, monkeypatch):
    runtime, cover, player, current, _ = presentation
    current.state = "idle"
    runtime.display_state = None
    runtime.selection = resolver["Resolution"](None, "audio", "idle", ())
    runtime.rotation = rotation["Rotation"]()

    async def unexpected(*args):
        pytest.fail("Idle placeholder fetched real cover")

    monkeypatch.setitem(
        cover.async_image.__func__.__globals__, "async_fetch_source_image", unexpected
    )
    monkeypatch.setitem(
        player.async_get_media_image.__func__.__globals__,
        "async_fetch_source_image",
        unexpected,
    )

    async def scenario():
        assert player.state == "idle"
        assert player.media_title == "Netflix"
        assert await player.async_get_media_image() == (png(), "image/png")
        assert await cover.async_image() == png()
        current.attributes["app_name"] = " "
        assert player.media_title is None
        assert await cover.async_image() is None
        current.attributes["app_name"] = "Netflix"
        runtime.show_app_icon_fallback = False
        assert player.media_title is None
        assert await player.async_get_media_image() == (None, None)

    asyncio.run(scenario())


def test_rotating_icon_and_stale_fetch(presentation, monkeypatch):
    runtime, cover, player, _, states = presentation
    other = state("other", app_name="Netflix")
    states["other"] = other
    runtime.rotation = rotation["Rotation"](("tv", "other"), "other")

    async def missing(*args):
        return None

    monkeypatch.setitem(
        player.async_get_media_image.__func__.__globals__,
        "async_fetch_source_image",
        missing,
    )

    async def scenario():
        assert await player.async_get_media_image() == (png(), "image/png")
        assert runtime.cover_attributes["cover_origin"] == "app_icon"

        async def stale(*args):
            runtime.rotation = rotation["Rotation"](("tv", "other"), "tv")
            return png("red"), "image/png"

        monkeypatch.setitem(
            player.async_get_media_image.__func__.__globals__,
            "async_fetch_source_image",
            stale,
        )
        assert await player.async_get_media_image() == (None, None)

    asyncio.run(scenario())


def test_runtime_icon_io_is_in_executor_and_missing_files_clear_icon(
    presentation, monkeypatch, tmp_path
):
    runtime, _, player, _, _ = presentation
    original_prepare = type(runtime).async_prepare_icons.__get__(runtime)
    monkeypatch.setattr(runtime, "async_prepare_icons", original_prepare)
    directory = tmp_path / "www/combined_media_player/app_icons"
    directory.mkdir(parents=True)
    path = directory / "netflix.png"
    path.write_bytes(png("red"))
    loop_thread = threading.get_ident()
    original_load = runtime._icon_cache.load

    def load(*args):
        assert threading.get_ident() != loop_thread
        return original_load(*args)

    monkeypatch.setattr(runtime._icon_cache, "load", load)

    async def scenario():
        before = player.media_image_hash
        await runtime.async_prepare_icons()
        assert runtime.icon_for(runtime.display_state).data == png("red")
        assert player.media_image_hash != before
        path.unlink()
        await runtime.async_prepare_icons()
        assert runtime.icon_for(runtime.display_state) is None

    asyncio.run(scenario())


def test_disabled_playing_fallback_and_stale_main_image(presentation, monkeypatch):
    runtime, cover, player, current, _ = presentation
    runtime.show_app_icon_fallback = False

    async def missing(*args):
        return None

    monkeypatch.setitem(
        cover.async_image.__func__.__globals__, "async_fetch_source_image", missing
    )

    async def scenario():
        assert player.media_title is None
        assert await player.async_get_media_image() == (None, None)
        assert runtime.cover_attributes == {"cover_origin": None, "app_icon_slug": None}

        async def stale(*args):
            current.attributes["media_title"] = "Next title"
            return png("red"), "image/png"

        monkeypatch.setitem(
            cover.async_image.__func__.__globals__, "async_fetch_source_image", stale
        )
        assert await cover.async_image() is None
        assert cover._last_image is None

    asyncio.run(scenario())
