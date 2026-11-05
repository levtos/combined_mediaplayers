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


def test_background_audit_discards_invalid_missing_and_expired_entries():
    async def scenario():
        index = {
            key: {"content_type": "image/png", "last_used": timestamp}
            for key, timestamp in [
                ("good", 100),
                ("bad", 100),
                ("missing", 100),
                ("expired", 1),
            ]
        }
        reads, discarded = [], []

        async def read(key):
            reads.append(key)
            return {"good": png()[0], "bad": b"<html>error</html>", "missing": None}[
                key
            ]

        def discard(key):
            discarded.append(key)
            index.pop(key)

        await _MODULE.audit_cached_images(index, {}, read, discard, executor, 105, 10)
        assert reads == ["good", "bad", "missing"]
        assert discarded == ["bad", "missing", "expired"]
        assert list(index) == ["good"]

    asyncio.run(scenario())


@pytest.mark.parametrize("writer_state", ["new_metadata", "write_in_progress"])
def test_audit_never_discards_entries_replaced_during_validation(writer_state):
    async def scenario():
        index = {"cover": {"content_type": "image/png", "last_used": 100}}
        generations, discarded = {}, []

        async def read(key):
            if writer_state == "new_metadata":
                index[key] = dict(index[key])
            else:
                # A writer marks its generation before awaiting disk I/O.
                generations[key] = 1
            return b"<html>old invalid file</html>"

        await _MODULE.audit_cached_images(
            index, generations, read, discarded.append, executor, 105, 10
        )
        assert discarded == []
        assert "cover" in index

    asyncio.run(scenario())


def test_cover_fetch_completes_while_background_audit_is_blocked_on_old_file():
    async def scenario():
        started, release = asyncio.Event(), asyncio.Event()
        discarded = []

        async def read(_key):
            started.set()
            await release.wait()
            return b"<html>old invalid file</html>"

        audit = asyncio.create_task(
            _MODULE.audit_cached_images(
                {"old": {"content_type": "image/png", "last_used": 100}},
                {},
                read,
                discarded.append,
                executor,
                105,
                10,
            )
        )
        try:
            await started.wait()
            valid = png()

            async def source():
                return valid

            image = await asyncio.wait_for(
                _MODULE.fetch_validated_image(
                    Cache(),
                    "current",
                    [("entity", source)],
                    executor,
                    lambda: True,
                    lambda route, reason: None,
                ),
                timeout=2,
            )
            assert image == valid
            assert not audit.done()
            assert discarded == []
        finally:
            release.set()
            await audit
        assert discarded == ["old"]

    asyncio.run(scenario())


def test_background_audit_cancels_without_eviction_after_shutdown():
    async def scenario():
        started = asyncio.Event()
        discarded = []

        async def read(_key):
            started.set()
            await asyncio.Event().wait()

        audit = asyncio.create_task(
            _MODULE.audit_cached_images(
                {"old": {"content_type": "image/png", "last_used": 100}},
                {},
                read,
                discarded.append,
                executor,
                105,
                10,
            )
        )
        await started.wait()
        audit.cancel()
        with pytest.raises(asyncio.CancelledError):
            await audit
        assert discarded == []

    asyncio.run(scenario())


def test_fully_expired_cache_yields_to_other_work_and_can_be_cancelled():
    async def scenario():
        index = {
            str(key): {"content_type": "image/png", "last_used": 0}
            for key in range(1000)
        }
        first_discard = asyncio.Event()
        discarded = []

        async def read(_key):
            pytest.fail("Expired images must not be read or decoded")

        def discard(key):
            discarded.append(key)
            first_discard.set()

        audit = asyncio.create_task(
            _MODULE.audit_cached_images(index, {}, read, discard, executor, 100, 10)
        )
        await first_discard.wait()
        assert 0 < len(discarded) < len(index)
        audit.cancel()
        with pytest.raises(asyncio.CancelledError):
            await audit

    asyncio.run(scenario())
