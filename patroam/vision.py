"""Vision input for PATROAM — capture an image to hand to a vision model.

Used so PATROAM can "look at the screen" (or an image file) and answer about it
with a multimodal model like llama3.2-vision. Returns raw base64 (no data-URI
prefix), which is what Ollama's chat API expects in a message's `images` list.
"""

import base64
import io


def _encode(img, max_side=1280):
    """Downscale (keep it light for the model) and return base64 PNG."""
    try:
        img.thumbnail((max_side, max_side))
    except Exception:
        pass
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return base64.b64encode(buf.getvalue()).decode("ascii")


def screenshot_b64():
    """Capture the primary screen as base64 PNG, or None if it can't."""
    # Preferred: Pillow's ImageGrab (Windows/macOS).
    try:
        from PIL import ImageGrab
        return _encode(ImageGrab.grab())
    except Exception:
        pass
    # Fallback: mss (cross-platform).
    try:
        import mss
        from PIL import Image
        with mss.mss() as sct:
            shot = sct.grab(sct.monitors[0])
            img = Image.frombytes("RGB", shot.size, shot.bgra, "raw", "BGRX")
        return _encode(img)
    except Exception:
        return None


def image_file_b64(path):
    """Load an image file as base64 PNG, or None if it can't be read."""
    try:
        from PIL import Image
        return _encode(Image.open(path).convert("RGB"))
    except Exception:
        return None


def normalize_image_b64(b64):
    """Re-encode an arbitrary base64 image (JPEG/WebP/PNG/…) into a downscaled
    base64 PNG — the single format we hand to the vision models. Falls back to
    the input unchanged if it can't be decoded."""
    try:
        from PIL import Image
        raw = base64.b64decode(b64)
        return _encode(Image.open(io.BytesIO(raw)).convert("RGB"))
    except Exception:
        return b64
