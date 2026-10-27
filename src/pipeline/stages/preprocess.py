"""Stage 3: render pages, read the text layer, check image integrity (pypdfium2, no system
dependencies). PDFs are rendered page by page; raster images are treated as a single-page
document with no text layer. Any other attachment type (Excel, Word, CSV, audio, etc.) cannot be
visually inspected by the model; ingest rejects those before download, and this stage keeps a
defensive skip with a clear reason for anything that reaches it anyway (e.g. --source folder)."""
from __future__ import annotations

import logging
import threading

from .base import Context, StageSkip

log = logging.getLogger(__name__)

# Single source of truth for what the pipeline can visually process. ingest.py imports
# ALLOWED_EXTENSIONS to reject everything else before download.
IMAGE_EXTENSIONS = frozenset({"png", "jpg", "jpeg", "gif", "bmp", "tif", "tiff", "webp"})
ALLOWED_EXTENSIONS = frozenset({"pdf"}) | IMAGE_EXTENSIONS

# pypdfium2's native PDFium core is not safely reentrant across threads. run_records() processes
# several records concurrently (ThreadPoolExecutor, PipelineConfig.max_records), each opening its
# own PdfDocument here, so without this lock concurrent native calls (and GC-triggered teardown of
# one document racing with another's in-flight render) can segfault the whole process. This
# serializes PDFium work across threads; it does not block the rest of each record's pipeline.
_PDFIUM_LOCK = threading.Lock()

# Anthropic API limit: base64-encoded image must be <10 MB (raw bytes are ~4/3 smaller). PDFs at
# the configured render_dpi stay well under this; raster image attachments (especially phone
# photos of timesheets) can easily hit 10-14 MB at full resolution. Cap both at a safe size.
_MAX_IMAGE_BYTES = 9 * 1024 * 1024  # 9 MB margin before Anthropic's 10 MB base64 limit
_VISION_RESIZE_THRESHOLD_PX = 1568  # Anthropic's documented image downscaling threshold


def _save_sized(im, path) -> str:
    """Save image to path, resizing progressively if needed to stay under API byte limit.
    Returns the format actually written: "png" or "jpeg"."""
    from pathlib import Path
    path_obj = Path(path)
    im.save(path, format="PNG", optimize=True)
    if path_obj.stat().st_size <= _MAX_IMAGE_BYTES:
        return "png"
    im_rgb = im.convert("RGB")
    for quality in (75, 50, 25):
        im_rgb.save(path, format="JPEG", quality=quality, optimize=True)
        if path_obj.stat().st_size <= _MAX_IMAGE_BYTES:
            return "jpeg"
    return "jpeg"


def _cap_image_size(im):
    """Resize image in-place if largest dimension exceeds the threshold."""
    w, h = im.size
    if max(w, h) > _VISION_RESIZE_THRESHOLD_PX:
        scale = _VISION_RESIZE_THRESHOLD_PX / max(w, h)
        im = im.resize((int(w * scale), int(h * scale)), resample=2)  # LANCZOS
    return im


def _preprocess_pdf(ctx: Context, rec, src) -> None:
    import pypdfium2 as pdfium
    from PIL import Image

    with _PDFIUM_LOCK:
        try:
            pdf = pdfium.PdfDocument(src)
        except Exception as e:  # encrypted / corrupt
            rec.write("preprocess.json", {"ok": False, "error": f"{type(e).__name__}: {e}"})
            raise StageSkip(f"cannot open PDF: {e}")
        try:
            scale = ctx.cfg.render_dpi / 72.0
            pages = []
            for i in range(len(pdf)):
                page = pdf[i]
                text = page.get_textpage().get_text_range() or ""
                img_path = rec.page_image(i + 1)
                needs_render = (not img_path.exists() or ctx.options.get("force_render") or
                               (img_path.exists() and img_path.stat().st_size > _MAX_IMAGE_BYTES))
                image_format = "png"  # default; overwritten if re-rendered
                if needs_render:
                    bmp = page.render(scale=scale, grayscale=True)
                    im = bmp.to_pil()
                    im = _cap_image_size(im)
                    image_format = _save_sized(im, str(img_path))
                try:
                    with Image.open(img_path) as im:
                        im.load()
                        size = im.size
                    img_ok = True
                except Exception:
                    size, img_ok = None, False
                w, h = page.get_size()
                pages.append({"page": i + 1, "width_pt": round(w, 1), "height_pt": round(h, 1),
                              "landscape": w > h, "text_chars": len(text.strip()), "text": text,
                              "image": img_path.name, "image_size": size, "image_ok": img_ok,
                              "image_format": image_format})
        finally:
            pdf.close()
    n_text = sum(1 for p in pages if p["text_chars"] > 40)
    level = "full" if n_text == len(pages) and pages else ("partial" if n_text else "none")
    rec.write("preprocess.json", {"ok": all(p["image_ok"] for p in pages), "page_count": len(pages),
                                  "text_layer": level, "dpi": ctx.cfg.render_dpi, "pages": pages})


def _preprocess_image(ctx: Context, rec, src) -> None:
    from PIL import Image, ImageOps

    img_path = rec.page_image(1)
    try:
        with Image.open(src) as im:
            im = ImageOps.exif_transpose(im) or im
            im = im.convert("RGB")
            needs_render = (not img_path.exists() or ctx.options.get("force_render") or
                           (img_path.exists() and img_path.stat().st_size > _MAX_IMAGE_BYTES))
            image_format = "png"  # default; overwritten if re-rendered
            if needs_render:
                im = _cap_image_size(im)
                image_format = _save_sized(im, str(img_path))
            size = im.size
        img_ok = True
    except Exception as e:
        rec.write("preprocess.json", {"ok": False, "error": f"{type(e).__name__}: {e}"})
        raise StageSkip(f"cannot open image: {e}")
    w, h = size
    page = {"page": 1, "width_pt": w, "height_pt": h, "landscape": w > h, "text_chars": 0,
            "text": "", "image": img_path.name, "image_size": size, "image_ok": img_ok,
            "image_format": image_format}
    rec.write("preprocess.json", {"ok": img_ok, "page_count": 1, "text_layer": "none",
                                  "dpi": None, "pages": [page]})


def run(ctx: Context, rec, entry: dict) -> None:
    src = entry["path"]
    ext = (entry.get("extension") or "").lower()
    if ext == "pdf":
        _preprocess_pdf(ctx, rec, src)
    elif ext in IMAGE_EXTENSIONS:
        _preprocess_image(ctx, rec, src)
    else:
        rec.write("preprocess.json", {"ok": False, "page_count": 0, "text_layer": "none", "pages": [],
                                      "reason": f"unsupported format for visual extraction: .{ext}"})
        raise StageSkip(f"unsupported format for visual extraction: .{ext}")
