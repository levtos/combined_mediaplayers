"""HA-independent image checks and the validated cache/fetch fallback chain."""

from io import BytesIO


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

        with Image.open(BytesIO(data)) as image:
            image.verify()
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
