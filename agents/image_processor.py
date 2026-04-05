"""
agents/image_processor.py
DITA Converter Tool — Image Processing Agent

Core image processing logic adapted from the standalone Image Processor tool.
Provides proportional scaling, padding, border, and format conversion.

Scaling uses Gilbarco manual page dimensions at _DPI (default 96).
Change _DPI to 300 for print-quality output.
"""

from __future__ import annotations

import io

# ── DPI constant ───────────────────────────────────────────────────────────────
# Default: 96 (screen). Change to 300 for print output.
_DPI = 96

# ── Scaling presets (width_px, height_px) at _DPI ────────────────────────────
# Portrait:  8.5in wide, 1in top + 0.5in bottom margin → 9in text height
# Landscape: 11in wide,  0.75in top + 0.75in bottom   → 6in text height
# Indent:    0.5in additional left margin
IMAGE_SCALE_PRESETS: dict[str, tuple[int, int]] = {
    "portrait_max":     (648,  864),   # 6.75in × 9in
    "portrait_indent":  (600,  864),   # 6.25in × 9in
    "landscape_max":    (912,  576),   # 9.5in  × 6in
    "landscape_indent": (864,  576),   # 9in    × 6in
}

SCALE_PRESET_LABELS: dict[str, str] = {
    "no_scale":         "No scaling",
    "portrait_max":     "Portrait max",
    "portrait_indent":  "Portrait indent",
    "landscape_max":    "Landscape max",
    "landscape_indent": "Landscape indent",
}

_FORMAT_MAP: dict[str, tuple[str, str]] = {
    "png":  ("PNG",  ".png"),
    "jpeg": ("JPEG", ".jpg"),
    "jpg":  ("JPEG", ".jpg"),
    "webp": ("WEBP", ".webp"),
}


def hex_to_rgb(hex_color: str) -> tuple[int, int, int]:
    h = hex_color.lstrip("#")
    if len(h) == 3:
        h = "".join(c * 2 for c in h)
    return (int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16))


def scale_to_width(
    img_bytes: bytes,
    target_width_px: int,
    target_height_px: int,
) -> bytes:
    """
    Proportionally scale image to fit within target_width_px × target_height_px.

    Width constraint is applied first. If the resulting height exceeds
    target_height_px, the image is re-scaled to the height constraint instead.
    Aspect ratio is always preserved.
    """
    from PIL import Image

    with Image.open(io.BytesIO(img_bytes)) as img:
        orig_w, orig_h = img.size
        if orig_w == 0 or orig_h == 0:
            return img_bytes

        scale = target_width_px / orig_w
        new_w = target_width_px
        new_h = int(orig_h * scale)

        if new_h > target_height_px:
            scale = target_height_px / orig_h
            new_h = target_height_px
            new_w = int(orig_w * scale)

        resized = img.resize((new_w, new_h), Image.LANCZOS)
        buf = io.BytesIO()
        fmt = img.format or "PNG"
        try:
            resized.save(buf, format=fmt)
        except Exception:
            resized.save(buf, format="PNG")
        buf.seek(0)
        return buf.read()


def process_image(
    img_bytes: bytes,
    ext: str,
    crop: bool = False,
    padding_px: int = 0,
    border_px: int = 0,
    border_colour: str = "#000000",
    pad_colour: str = "#ffffff",
    target_width_px: int = 0,
    target_height_px: int = 0,
    convert_format: str = "keep",
) -> tuple[bytes, str]:
    """
    Process image bytes: crop → scale → pad → border → convert format.

    Returns (processed_bytes, final_ext).
    If all options are at defaults/zero, returns img_bytes unchanged (no-op).

    Args:
        img_bytes:        Raw image bytes.
        ext:              Original file extension including dot, e.g. ".png".
        crop:             Trim near-white background from all edges before scaling.
        padding_px:       Pixels of padding around the image (default 0).
        border_px:        Pixels of border around the padded image (default 0).
        border_colour:    Border hex colour (default #000000).
        pad_colour:       Padding hex colour (default #ffffff).
        target_width_px:  Scaling target width — 0 = no scaling.
        target_height_px: Scaling target height — 0 = no scaling.
        convert_format:   Output format: "keep" | "png" | "jpeg" | "webp".
    """
    from PIL import Image, ImageOps

    no_op = (
        not crop
        and padding_px == 0
        and border_px == 0
        and target_width_px == 0
        and target_height_px == 0
        and convert_format in ("keep", "")
    )
    if no_op:
        return img_bytes, ext

    # Step 0: Auto-crop near-white background edges
    if crop:
        import io as _io
        from PIL import Image as _Image

        _img = _Image.open(_io.BytesIO(img_bytes)).convert("RGB")

        # Convert to grayscale and build a mask:
        #   background pixels (>= 220 grayscale) → 0
        #   content pixels (< 220 grayscale)     → 255
        # getbbox() returns the bounding box of all non-zero (content) pixels.
        # This correctly handles:
        #   - UI screenshots: black border frame is content → crops to frame
        #   - Report pages: text is content → crops to text extent
        #   - Internal white areas (form fields, white panels) are INSIDE the
        #     content bbox so they are preserved, not cropped
        _gray = _img.convert("L")
        _mask = _gray.point(lambda p: 0 if p >= 220 else 255)
        _bbox = _mask.getbbox()

        if _bbox:
            _pad = 4   # breathing room around detected content
            _w, _h = _img.size
            _l = max(0,  _bbox[0] - _pad)
            _t = max(0,  _bbox[1] - _pad)
            _r = min(_w, _bbox[2] + _pad)
            _b = min(_h, _bbox[3] + _pad)

            # Only crop if we're actually removing something
            if (_l, _t, _r, _b) != (0, 0, _w, _h):
                _img = _img.crop((_l, _t, _r, _b))
                _buf = _io.BytesIO()
                _img.save(_buf, format="PNG")
                img_bytes = _buf.getvalue()
                ext = ".png"

    # Determine output format
    if convert_format and convert_format not in ("keep", ""):
        pil_format, final_ext = _FORMAT_MAP.get(convert_format.lower(), ("PNG", ".png"))
    else:
        ext_lower = ext.lstrip(".").lower()
        pil_format, final_ext = _FORMAT_MAP.get(ext_lower, ("PNG", ".png"))

    border_rgb = hex_to_rgb(border_colour)
    pad_rgb    = hex_to_rgb(pad_colour)

    # Step 1: Scale
    if target_width_px > 0 and target_height_px > 0:
        img_bytes = scale_to_width(img_bytes, target_width_px, target_height_px)

    with Image.open(io.BytesIO(img_bytes)) as img:
        if img.mode not in ("RGB", "RGBA", "L"):
            img = img.convert("RGBA")

        # Step 2: Padding
        if padding_px > 0:
            padded = Image.new(
                "RGBA",
                (img.width + padding_px * 2, img.height + padding_px * 2),
                pad_rgb + (255,),
            )
            padded.paste(img, (padding_px, padding_px))
        else:
            padded = img.convert("RGBA")

        # Step 3: Border
        if border_px > 0:
            bordered = ImageOps.expand(padded, border=border_px, fill=border_rgb + (255,))
        else:
            bordered = padded

        # Step 4: Flatten alpha for formats that don't support it
        if pil_format in ("JPEG", "BMP"):
            final = Image.new("RGB", bordered.size, pad_rgb)
            if bordered.mode == "RGBA":
                final.paste(bordered, mask=bordered.split()[3])
            else:
                final.paste(bordered)
        else:
            final = bordered.convert("RGBA") if pil_format == "PNG" else bordered.convert("RGB")

        buf = io.BytesIO()
        kwargs = {"quality": 95, "subsampling": 0} if pil_format == "JPEG" else {}
        final.save(buf, format=pil_format, **kwargs)
        buf.seek(0)
        return buf.read(), final_ext
