"""Images that live inside the user's documents.

When RAG indexes the knowledge folder, this module pulls the pictures out of
each document (embedded images in PDFs, or image files themselves) and records a
{document -> [image paths]} map. The knowledge graph then uses it: clicking a
node shows the documents it came from and any images in them.
"""

import base64
import json
import os
import re

from . import config

IMAGE_EXT = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp"}
_MIME = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
         ".gif": "image/gif", ".webp": "image/webp", ".bmp": "image/bmp"}


def _dir():
    os.makedirs(config.MEDIA_DIR, exist_ok=True)
    return config.MEDIA_DIR


def _safe(name):
    return re.sub(r"[^A-Za-z0-9._-]+", "_", name)


def extract_pdf_images(path, doc_rel, max_images=12):
    """Save embedded images from a PDF to the media dir; return their paths."""
    try:
        import fitz  # PyMuPDF
    except ImportError:
        return []
    try:
        d = fitz.open(path)
    except Exception:
        return []
    out, seen, base = [], set(), _safe(doc_rel)
    try:
        for pi in range(len(d)):
            for img in d[pi].get_images(full=True):
                xref = img[0]
                if xref in seen:
                    continue
                seen.add(xref)
                try:
                    pix = fitz.Pixmap(d, xref)
                    if pix.n >= 5:                       # CMYK / with alpha → RGB
                        pix = fitz.Pixmap(fitz.csRGB, pix)
                    if pix.width < 32 or pix.height < 32:  # skip tiny icons/bullets
                        pix = None
                        continue
                    fn = os.path.join(_dir(), f"{base}__{xref}.png")
                    pix.save(fn)
                    out.append(fn)
                    pix = None
                except Exception:
                    continue
                if len(out) >= max_images:
                    return out
    finally:
        d.close()
    return out


def index_documents(doc_paths):
    """doc_paths = [(rel, abs_path)]. Build & save the {doc -> [images]} map."""
    mapping = {}
    for rel, ap in doc_paths:
        ext = os.path.splitext(ap)[1].lower()
        if ext == ".pdf":
            imgs = extract_pdf_images(ap, rel)
        elif ext in IMAGE_EXT:
            imgs = [ap]                                   # the file itself is the image
        else:
            imgs = []
        if imgs:
            mapping[rel] = imgs
    save_index(mapping)
    return mapping


def save_index(mapping):
    try:
        _dir()
        with open(config.MEDIA_INDEX_FILE, "w", encoding="utf-8") as f:
            json.dump(mapping, f, ensure_ascii=False)
    except Exception:
        pass


def load_index():
    try:
        with open(config.MEDIA_INDEX_FILE, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def images_for_doc(doc_rel):
    return [p for p in load_index().get(doc_rel, []) if os.path.exists(p)]


def data_uri(path, max_bytes=1_200_000):
    """A base64 data: URI for a local image, so the webview can show it inline.
    Large images are downscaled (via Pillow) to keep the payload small."""
    try:
        if os.path.getsize(path) > max_bytes:
            uri = _downscaled_uri(path)
            if uri:
                return uri
        with open(path, "rb") as f:
            raw = f.read()
    except Exception:
        return ""
    mime = _MIME.get(os.path.splitext(path)[1].lower(), "image/png")
    return f"data:{mime};base64," + base64.b64encode(raw).decode()


def _downscaled_uri(path):
    try:
        import io
        from PIL import Image
        im = Image.open(path)
        im.thumbnail((640, 640))
        if im.mode in ("RGBA", "P", "LA"):
            im = im.convert("RGB")
        buf = io.BytesIO()
        im.save(buf, format="JPEG", quality=80)
        return "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode()
    except Exception:
        return ""
