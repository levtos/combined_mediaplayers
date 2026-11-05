"""Image acceptance and fallback tests without a Home Assistant harness."""

import asyncio
import importlib.util
from io import BytesIO
from pathlib import Path
import warnings

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


def jpeg_with_broken_exif():
    output = BytesIO()
    Image.new("RGB", (32, 32), "blue").save(
        output,
        format="JPEG",
        exif=b"Exif\x00\x00II*\x00\x08\x00\x00\x00\x01\x00",
    )
    return output.getvalue()


def test_decodable_jpeg_with_corrupt_exif_is_accepted_without_warning():
    data = jpeg_with_broken_exif()
    # Prove this fixture exercises Pillow's real metadata warning.
    with pytest.warns(UserWarning, match="Corrupt EXIF data"):
        with Image.open(BytesIO(data)) as image:
            image.load()
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        assert _MODULE.image_error(data, "image/jpeg") is None
    assert caught == []


def test_truncated_pixels_are_rejected_even_with_corrupt_exif():
    data = jpeg_with_broken_exif()[:-10]
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        assert _MODULE.image_error(data, "image/jpeg") == "OSError"
    assert caught == []


def test_warning_filter_is_local_and_does_not_hide_other_pillow_warnings(monkeypatch):
    original_open = Image.open

    def warn_then_open(*args, **kwargs):
        warnings.warn("unrelated Pillow warning", UserWarning)
        return original_open(*args, **kwargs)

    monkeypatch.setattr(Image, "open", warn_then_open)
    with pytest.warns(UserWarning, match="unrelated Pillow warning"):
        assert _MODULE.image_error(*png()) is None
    monkeypatch.setattr(Image, "open", original_open)
    with pytest.warns(UserWarning, match="Corrupt EXIF data"):
        with Image.open(BytesIO(jpeg_with_broken_exif())) as image:
            image.load()
