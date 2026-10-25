"""Image acceptance and fallback tests without a Home Assistant harness."""

import asyncio
import importlib.util
from io import BytesIO
from pathlib import Path

from PIL import Image
import pytest

_SPEC = importlib.util.spec_from_file_location(
    "cmp_image_validation",
    Path(__file__).parents[1]
    / "custom_components/combined_media_player/image_validation.py",
)
_MODULE = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_MODULE)


def png():
    output = BytesIO()
    Image.new("RGB", (16, 16), "blue").save(output, format="PNG")
    return output.getvalue(), "image/png"


class Cache:
    def __init__(self, existing=None):
        self.existing = existing
        self.writes = []
        self.discards = []

    async def get(self, key):
        return self.existing

    async def put(self, key, *image):
        self.writes.append((key, image))

    async def discard(self, key):
        self.discards.append(key)


async def executor(function, *args):
    return await asyncio.to_thread(function, *args)


@pytest.mark.parametrize(
    "content_type,reason",
    [
        ("text/html", "not_image_content_type"),
        ("image/jpeg", "UnidentifiedImageError"),
    ],
)
def test_http_200_html_is_discarded_and_never_cached(content_type, reason):
    async def scenario():
        cache = Cache()
        reports = []

        async def successful_http_response():
            # The transport has accepted HTTP 200; content is still untrusted.
            return b"<html><body>Sign in</body></html>", content_type

        image = await _MODULE.fetch_validated_image(
            cache,
            "test-key",
            [("url", successful_http_response)],
            executor,
            lambda: True,
            lambda route, error: reports.append((route, error)),
        )
        assert image is None
        assert cache.writes == []
        assert reports == [("url", reason)]

    asyncio.run(scenario())


def test_valid_image_is_accepted_and_cached():
    async def scenario():
        cache = Cache()
        valid = png()

        async def entity():
            return valid

        result = await _MODULE.fetch_validated_image(
            cache,
            "test-key",
            [("entity", entity)],
            executor,
            lambda: True,
            lambda route, reason: None,
        )
        assert result == valid
        assert cache.writes == [("test-key", valid)]

    asyncio.run(scenario())


def test_invalid_entity_image_uses_entity_picture_fallback():
    async def scenario():
        cache = Cache()
        calls = []
        valid = png()

        async def entity():
            calls.append("entity")
            return b"<html>not an image</html>", "image/jpeg"

        async def url():
            calls.append("url")
            return valid

        result = await _MODULE.fetch_validated_image(
            cache,
            "test-key",
            [("entity", entity), ("url", url)],
            executor,
            lambda: True,
            lambda route, reason: None,
        )
        assert calls == ["entity", "url"]
        assert result == valid
        assert cache.writes == [("test-key", valid)]

    asyncio.run(scenario())


def test_invalid_cached_image_is_removed_before_fallback():
    async def scenario():
        cache = Cache((b"<html>old cache</html>", "image/jpeg"))
        valid = png()

        async def entity():
            return valid

        result = await _MODULE.fetch_validated_image(
            cache,
            "test-key",
            [("entity", entity)],
            executor,
            lambda: True,
            lambda route, reason: None,
        )
        assert result == valid
        assert cache.discards == ["test-key"]
        assert cache.writes == [("test-key", valid)]

    asyncio.run(scenario())


def test_good_bytes_with_non_image_content_type_are_not_accepted():
    data, _ = png()
    assert _MODULE.image_error(data, "text/html") == "not_image_content_type"
