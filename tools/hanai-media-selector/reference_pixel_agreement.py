"""Numeric pixel-agreement metrics for --verify-reference-copies.

Fixed direct (no mirror) comparison: long edge 256, aspect relative
difference <= 0.02 (else aspect_mismatch), grayscale global SSIM, min 4x4
tile SSIM, RGB MAD, max per-channel absolute residual. Formulas follow the public near-copy evaluation. The
output is numeric only: no copy classification or threshold is emitted.
Pillow/numpy are imported lazily and never installed. No ICC handling: images are
EXIF-transposed and converted to plain RGB, so color is not calibrated.
This is pixel agreement only, not identity, semantics or a duplicate verdict.
"""

import math
import os

MAX_SIDE = 16384
MAX_PIXELS = 16000000
SIZE = 256
ASPECT_TOL = 0.02
C1, C2 = (0.01 * 255) ** 2, (0.03 * 255) ** 2
GRAY = (0.299, 0.587, 0.114)


class PixelError(Exception):
    pass


def _libs():
    try:
        import numpy
        from PIL import Image, ImageOps
    except Exception:
        raise PixelError("pixel_dependencies_missing")
    return numpy, Image, ImageOps


def load_rgb(path):
    np, Image, ImageOps = _libs()
    if not isinstance(path, str) or not path or "://" in path or not os.path.isfile(path):
        raise PixelError("pixel_input_invalid")
    try:
        with Image.open(path) as im:
            if im.width > MAX_SIDE or im.height > MAX_SIDE or im.width * im.height > MAX_PIXELS:
                raise PixelError("pixel_image_too_large")
            im = ImageOps.exif_transpose(im).convert("RGB")
            im.load()
            return im
    except PixelError:
        raise
    except Exception:
        raise PixelError("pixel_decode_failed")


def _dims(im):
    s = SIZE / max(im.size)
    dims = (max(1, round(im.width * s)), max(1, round(im.height * s)))
    if dims[0] < 4 or dims[1] < 4:
        raise PixelError("pixel_aspect_extreme")
    return dims


def _resized(np, Image, im, dims):
    return np.asarray(im.resize(dims, Image.BICUBIC), dtype=np.float64)


def _ssim(a, b):
    ma, mb = a.mean(), b.mean()
    cov = ((a - ma) * (b - mb)).mean()
    return float(((2 * ma * mb + C1) * (2 * cov + C2)) / ((ma ** 2 + mb ** 2 + C1) * (a.var() + b.var() + C2)))


def compare(ref, cand):
    """Returns {"status", "metrics"}; status is computed or aspect_mismatch.
    Raises PixelError on numeric failure."""
    np, Image, _ = _libs()
    if abs(ref.width / ref.height - cand.width / cand.height) > ASPECT_TOL * ref.width / ref.height:
        return {"status": "aspect_mismatch", "metrics": None}
    dims = _dims(ref)
    a = _resized(np, Image, ref, dims)
    b = _resized(np, Image, cand, dims)
    ga, gb = a @ GRAY, b @ GRAY
    h, w = ga.shape
    tiles = [_ssim(ga[i * h // 4:(i + 1) * h // 4, j * w // 4:(j + 1) * w // 4],
                   gb[i * h // 4:(i + 1) * h // 4, j * w // 4:(j + 1) * w // 4])
             for i in range(4) for j in range(4)]
    m = {"ssimGlobal": _ssim(ga, gb), "tileSsimMin": float(min(tiles)),
         "rgbMad": float(np.abs(a - b).mean() / 255.0),
         "maxAbsoluteResidual": float(np.abs(a - b).max() / 255.0)}
    if not all(math.isfinite(v) for v in m.values()):
        raise PixelError("pixel_non_finite")
    return {"status": "computed", "metrics": m}


CONFIG = {"id": "direct-numeric", "longEdge": SIZE, "aspectTolerance": ASPECT_TOL, "mirror": False,
          "colorManagement": "none"}
