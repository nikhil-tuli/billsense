"""Bill upload: content check, quality gate (resolution, blur) and save. Makes no model call."""
import io
import math
import os
import threading
from contextlib import contextmanager
from datetime import date

import numpy as np
from PIL import Image

from app import db

MAX_BYTES = 2 * 1024 * 1024
EXTENSIONS = (".jpg", ".jpeg", ".png", ".pdf")       # hint for the file picker only; content decides the type


def min_short_side() -> int:
    return int(os.environ.get("UPLOAD_MIN_SHORT_SIDE", "500"))


def min_blur_score() -> float:
    return float(os.environ.get("UPLOAD_MIN_BLUR_SCORE", "8"))


def max_pixels() -> int:
    return int(os.environ.get("UPLOAD_MAX_PIXELS", "25000000"))


class Rejected(Exception):
    """The file cannot be used; the message is shown to the user as the reason."""


class Busy(RuntimeError):
    pass


_lock = threading.Lock()


@contextmanager
def exclusive():
    """One upload at a time (server side), in addition to the greyed-out screen."""
    if not _lock.acquire(blocking=False):
        raise Busy("Another upload is in progress. Try again in a moment.")
    try:
        yield
    finally:
        _lock.release()


def clean(s) -> str:
    return " ".join(str(s or "").split())


def sniff(data: bytes) -> str:
    """File type from the first bytes, never from the file name."""
    if data.startswith(b"\xff\xd8\xff"):
        return "jpeg"
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if data.startswith(b"%PDF-"):
        return "pdf"
    raise Rejected("Not a JPG, PNG or PDF file")


def open_image(data: bytes, kind: str):
    """-> (PIL image, bytes to store, mime). A PDF is rendered to an image (first page only)."""
    if kind == "pdf":
        import pymupdf
        try:
            doc = pymupdf.open(stream=data, filetype="pdf")
        except Exception as e:                                    # noqa: BLE001
            raise Rejected("Not a valid PDF") from e
        if doc.needs_pass or doc.is_encrypted:
            raise Rejected("PDF is password protected")
        if doc.page_count < 1:
            raise Rejected("PDF has no pages")
        page = doc[0]
        zoom = min(2.0, math.sqrt(12_000_000 / max(1.0, page.rect.width * page.rect.height)))
        try:
            pix = pymupdf.Pixmap(page.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom), alpha=False))
            img = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
        except Exception as e:                                    # noqa: BLE001
            raise Rejected("PDF page could not be read") from e
        buf = io.BytesIO()
        img.save(buf, "JPEG", quality=90)
        return img, buf.getvalue(), "image/jpeg"
    try:
        img = Image.open(io.BytesIO(data))
        if img.width * img.height > max_pixels():
            raise Rejected("Image is too large (over 25 million pixels)")
        img.load()
    except Rejected:
        raise
    except Exception as e:                                        # noqa: BLE001
        raise Rejected("Image is corrupt or cannot be opened") from e
    return img, data, "image/jpeg" if kind == "jpeg" else "image/png"


def blur_score(img) -> float:
    """Variance of the Laplacian after resizing to 1000 px width (so it is comparable across sizes)."""
    g = img.convert("L")
    g = g.resize((1000, max(3, round(g.height * 1000 / g.width))))
    a = np.asarray(g, dtype=np.float32)
    lap = -4 * a[1:-1, 1:-1] + a[:-2, 1:-1] + a[2:, 1:-1] + a[1:-1, :-2] + a[1:-1, 2:]
    return float(lap.var())


def check_quality(img):
    short = min(img.size)
    if short < min_short_side():
        raise Rejected(f"Resolution too low ({short} px on the short side, need {min_short_side()})")
    score = blur_score(img)
    if score < min_blur_score():
        raise Rejected(f"Too blurry (score {score:.1f}, need {min_blur_score():g})")
    return short, score


def find_duplicate(city, prop, owner, month: date):
    return db.find_bill(clean(city), clean(prop), clean(owner), month)


class Job:
    """One upload attempt. Steps run in order; the UI runs them one at a time to show real progress."""
    LABELS = ("Uploading", "Validating", "Quality check", "Saving")

    def __init__(self, city, prop, owner, month: date, file_name, data: bytes, replace=False, upload_id=None):
        self.meta = {"city": clean(city), "property": clean(prop), "owner": clean(owner),
                     "month": month.replace(day=1), "file_name": os.path.basename(str(file_name or "bill"))[:200]}
        self.data, self.replace, self.upload_id = data, replace, upload_id
        self.kind = self.img = self.stored = self.mime = None
        self.bill_id = None

    @classmethod
    def from_upload(cls, upload_id, replace=False):
        """Rebuild a job from a kept file (Retry)."""
        row = db.get_upload(upload_id)
        if not row or row["file"] is None:
            raise Rejected("The stored file is missing. Upload the file again.")
        return cls(row["city"], row["property_name"], row["owner"], row["bill_month"], row["file_name"],
                   bytes(row["file"]), replace=replace, upload_id=upload_id)

    # --- steps
    def _store(self):
        if len(self.data) == 0:
            raise Rejected("The file is empty")
        if len(self.data) > MAX_BYTES:
            raise Rejected("File is larger than 2 MB")
        if self.upload_id is None:                      # keep the file from the moment it is received, so Retry works
            self.upload_id = db.upload_insert(self.meta, None, len(self.data), self.data)
        else:
            db.upload_reset(self.upload_id)

    def _validate(self):
        self.kind = sniff(self.data)
        self.img, self.stored, self.mime = open_image(self.data, self.kind)

    def _quality(self):
        check_quality(self.img)

    def _save(self):
        try:
            self.bill_id = db.save_uploaded_bill(self.upload_id, self.meta, self.stored, self.mime, self.replace)
        except db.Duplicate as e:
            raise Rejected("A bill for this property and month already exists. Upload again and choose Replace.") from e

    def steps(self):
        return list(zip(self.LABELS, (self._store, self._validate, self._quality, self._save)))

    # --- outcome
    def fail(self, exc) -> dict:
        reason = str(exc) if isinstance(exc, Rejected) else f"Technical error: {type(exc).__name__}: {exc}"[:300]
        if self.upload_id is not None:
            try:
                db.upload_finish(self.upload_id, "failure", reason)
            except Exception:                                    # noqa: BLE001 - nothing more we can do
                pass
        return {"ok": False, "message": reason, "upload_id": self.upload_id, "bill_id": None}

    def done(self) -> dict:
        return {"ok": True, "message": f"Uploaded {self.meta['file_name']}", "upload_id": self.upload_id, "bill_id": self.bill_id}

    def run(self, on_stage=None) -> dict:
        """Run every step (used by Retry and tests; the UI drives steps() itself)."""
        with exclusive():
            for i, (label, fn) in enumerate(self.steps()):
                if on_stage:
                    on_stage(i, label)
                try:
                    fn()
                except Exception as e:                           # noqa: BLE001
                    return self.fail(e)
            return self.done()
