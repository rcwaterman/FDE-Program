"""PDF/image rendering with caching via pypdfium2."""
import os
from pathlib import Path

import pypdfium2
from PIL import Image

# Browser-displayable rasters: served as-is when unrotated.
IMAGE_PASSTHROUGH = (".png", ".jpg", ".jpeg", ".bmp", ".gif")
# Rasters browsers can't display directly (TIFF, WebP): always converted to a cached PNG.
IMAGE_CONVERT = (".tif", ".tiff", ".webp")


def _find_source(record_id: str, attachments_dir: str) -> Path:
    """Return the attachment file for a record. Only regular files count, and ties are broken
    by name so the choice is deterministic."""
    candidates = sorted(p for p in Path(attachments_dir).glob(f"{record_id}.*") if p.is_file())
    if not candidates:
        raise FileNotFoundError(f"No attachment found for record {record_id}")
    return candidates[0]


def get_page_image(
    record_id: str, page_num: int, attachments_dir: str, cache_dir: str, dpi: int = 150, rotation: int = 0
) -> str:
    """
    Render a PDF page or image as PNG and return the file path. Uses cache.
    Falls back to direct image display for non-PDF attachments.
    page_num is 1-indexed.
    rotation: degrees to rotate (0, 90, 180, 270). Rotated renders are cached separately.
    Returns the path to the rendered PNG file (or source file for unrotated images).
    """
    # Normalize rotation to 0-359 range
    rotation = rotation % 360

    source_file = _find_source(record_id, attachments_dir)
    suffix = source_file.suffix.lower()
    cache_path = Path(cache_dir)
    cache_path.mkdir(parents=True, exist_ok=True)

    # For non-PDF files, handle with rotation support
    if suffix != ".pdf":
        if suffix not in IMAGE_PASSTHROUGH and suffix not in IMAGE_CONVERT:
            raise ValueError(f"Unsupported file type for preview: {source_file.suffix}")

        # Browser-native and unrotated: return the source directly
        if suffix in IMAGE_PASSTHROUGH and rotation == 0:
            return str(source_file)

        # Otherwise convert/rotate once and cache the PNG
        cached_image = cache_path / f"{record_id}_p{page_num}_r{rotation}.png"
        if cached_image.exists():
            return str(cached_image)

        # Open (first frame for multi-page TIFF), rotate (negative for clockwise), and save
        with Image.open(source_file) as pil_image:
            if suffix in IMAGE_CONVERT:
                pil_image = pil_image.convert("RGB")
            if rotation:
                pil_image = pil_image.rotate(-rotation, expand=True)
            pil_image.save(cached_image, format="PNG")
        return str(cached_image)

    # PDF: render to PNG if not cached (with rotation support)
    cached_png = cache_path / f"{record_id}_p{page_num}_r{rotation}.png"

    if cached_png.exists():
        return str(cached_png)

    try:
        pdf = pypdfium2.PdfDocument(str(source_file))
        if page_num < 1 or page_num > len(pdf):
            raise ValueError(f"Page {page_num} out of range (1-{len(pdf)})")

        page = pdf[page_num - 1]
        scale = dpi / 72.0
        pil_image = page.render(scale=scale, rotation=rotation, grayscale=False).to_pil()
        pil_image.save(cached_png, format="PNG")
        return str(cached_png)
    except Exception as e:
        raise RuntimeError(f"Failed to render PDF page: {e}")


def get_page_count(record_id: str, attachments_dir: str) -> int:
    """Get the page count for a PDF attachment."""
    source_file = _find_source(record_id, attachments_dir)

    if source_file.suffix.lower() != ".pdf":
        return 1  # Single image = 1 page

    pdf = pypdfium2.PdfDocument(str(source_file))
    return len(pdf)
