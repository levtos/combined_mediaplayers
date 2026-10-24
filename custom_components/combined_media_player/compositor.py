"""Pillow-only image composition; call in Home Assistant's executor."""

from io import BytesIO


def pip_geometry(width: int, height: int) -> tuple[int, int, int]:
    shorter = min(width, height)
    side = max(1, round(shorter * 0.34))
    margin = max(1, round(shorter * 0.04))
    return max(0, width - margin - side), max(0, height - margin - side), side


def compose_picture(main: bytes, secondary: bytes) -> bytes:
    """Retain the main canvas; center-crop just the secondary into a square."""
    # Lazy import allows the caller to fall back cleanly if Pillow is absent.
    from PIL import Image, ImageDraw, ImageFilter, ImageOps

    with Image.open(BytesIO(main)) as image:
        canvas = image.convert("RGBA")
    x, y, side = pip_geometry(*canvas.size)
    with Image.open(BytesIO(secondary)) as image:
        inset = ImageOps.fit(
            image.convert("RGBA"),
            (side, side),
            method=Image.Resampling.LANCZOS,
            centering=(0.5, 0.5),
        )
    radius = max(1, round(side * 0.12))
    mask = Image.new("L", (side, side), 0)
    ImageDraw.Draw(mask).rounded_rectangle(
        (0, 0, side - 1, side - 1), radius=radius, fill=255
    )
    blur = max(1, round(side * 0.03))
    pad = blur * 3
    shadow = Image.new("RGBA", (side + 2 * pad, side + 2 * pad))
    shadow_mask = Image.new("L", shadow.size, 0)
    shadow_mask.paste(mask.point(lambda value: value * 100 // 255), (pad, pad))
    shadow.putalpha(shadow_mask.filter(ImageFilter.GaussianBlur(blur)))
    offset = max(1, round(side * 0.02))
    canvas.alpha_composite(shadow, (x - pad + offset, y - pad + offset))
    # Multiply the rounded mask with the source alpha, preserving transparent art.
    from PIL import ImageChops

    inset.putalpha(ImageChops.multiply(inset.getchannel("A"), mask))
    canvas.alpha_composite(inset, (x, y))
    border = Image.new("RGBA", canvas.size)
    ImageDraw.Draw(border).rounded_rectangle(
        (x, y, x + side - 1, y + side - 1),
        radius=radius,
        outline=(255, 255, 255, 160),
        width=max(1, round(side * 0.015)),
    )
    canvas.alpha_composite(border)
    output = BytesIO()
    canvas.save(output, format="PNG")
    return output.getvalue()
