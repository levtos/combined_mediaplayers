"""HA-independent image checks and the validated cache/fetch fallback chain."""

import asyncio
from io import BytesIO
import warnings


def image_error(data: bytes, content_type: str | None) -> str | None:
    """Return only a safe reason class; run Pillow work in an executor."""
    if not data:
        return "no_image"
    if not isinstance(content_type, str) or not content_type.lower().startswith(
        "image/"
    ):
        return "not_image_content_type"
    try:
        from PIL import Image

        with warnings.catch_warnings():
            # Broken optional metadata can coexist with fully decodable pixels.
            # Suppress only Pillow's EXIF warning within this validation call.
            warnings.filterwarnings(
                "ignore",
                message=r"^(?:Possibly )?[Cc]orrupt EXIF data[.\s]",
                category=UserWarning,
                module=r"PIL\.TiffImagePlugin",
            )
            with Image.open(BytesIO(data)) as image:
                image.verify()
            # verify() does not decode every format's pixels (notably JPEG).
            with Image.open(BytesIO(data)) as image:
                image.load()
    except Exception as error:
        return type(error).__name__
    return None


async def fetch_validated_image(cache, key, fetchers, executor, is_current, report):
    """Reject bad cached/provider bytes before any use or cache write.

    Fetchers are ordered (route, async callback) pairs. Production callbacks
    already validate and report transport failures; this final boundary also
    protects against old cache data or a provider returning malformed results.
    """
    if key and (image := await cache.get(key)) is not None:
        reason = await executor(image_error, *image)
        report("cache", reason)
        if reason is None:
            return image if is_current() else None
        await cache.discard(key)
    for route, fetch in fetchers:
        image = await fetch()
        if image is None:
            continue
        reason = await executor(image_error, *image)
        report(route, reason)
        if reason is not None:
            continue
        if not is_current():
            return None
        if key:
            await cache.put(key, *image)
        return image if is_current() else None
    return None


async def audit_cached_images(index, generations, read, discard, executor, now, ttl):
    """Audit old entries without blocking requests or deleting newer writes."""
    for key, metadata in list(index.items()):
        # Expired entries need no image read, so explicitly yield even when an
        # entire old cache is expired instead of monopolizing the event loop.
        await asyncio.sleep(0)
        generation = generations.get(key, 0)
        if now - metadata.get("last_used", 0) > ttl:
            reason = "expired"
        else:
            data = await read(key)
            reason = await executor(image_error, data, metadata.get("content_type"))
        if (
            reason is not None
            and index.get(key) is metadata
            and generations.get(key, 0) == generation
        ):
            discard(key)
