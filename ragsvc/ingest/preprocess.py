"""OpenCV preprocessing for scanned pages. Owner: person 2.

Deskew, denoise, threshold -- in that order, and only for pages that are
actually scans. Running this over a rendered native page costs time and gains
nothing, so ingest/pipeline.py never calls it for those.

Every step here is chosen for speed as much as quality. A 20-page ingest has
about 3 seconds per page for *everything*, OCR included, and OCR wants most of
it. `cv2.fastNlMeansDenoising` produces a slightly cleaner image and costs
roughly 400 ms a page, which is 8 seconds of a 90-second budget for a
difference PP-OCRv4 mostly cannot see; a median blur costs 3 ms. That is the
trade being made, deliberately.
"""

from __future__ import annotations

import cv2
import numpy as np

# OpenCV's thread pool is a loss on the image sizes this pipeline handles, and
# a bigger loss when several OCR workers each spin up their own. Measured on a
# 20-page scan: 7.6 s/page single-threaded against 10.6 s/page with the default
# pool, with nothing else running. Set at import because every entry point into
# ingest goes through this module.
cv2.setNumThreads(1)

# Below this angle a rotation costs interpolation blur and buys nothing.
MIN_DESKEW_DEG = 0.25
# Above this, the estimate is far more likely to be a misdetection (a table
# border, a punched hole, a scanner artefact) than a genuinely rotated page.
MAX_DESKEW_DEG = 15.0


def to_gray(image: np.ndarray) -> np.ndarray:
    if image.ndim == 2:
        return image
    return cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)


def estimate_skew(gray: np.ndarray) -> float:
    """Estimate page rotation in degrees, positive meaning counter-clockwise.

    Works off the minimum-area rectangle of the ink: for a page of horizontal
    text lines, the tightest enclosing box is aligned with the baselines. The
    image is downscaled first because the angle of a page does not change with
    resolution, and the estimate at 1000 px wide is 6x cheaper.
    """
    height, width = gray.shape[:2]
    scale = 1000.0 / max(width, 1)
    if scale < 1.0:
        gray = cv2.resize(gray, (int(width * scale), int(height * scale)),
                          interpolation=cv2.INTER_AREA)

    inverted = cv2.bitwise_not(gray)
    _, binary = cv2.threshold(inverted, 0, 255, cv2.THRESH_BINARY | cv2.THRESH_OTSU)
    # Smear characters into lines so the rectangle follows text rows, not glyphs.
    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (25, 3))
    smeared = cv2.dilate(binary, kernel, iterations=1)

    coords = cv2.findNonZero(smeared)
    if coords is None or len(coords) < 50:
        return 0.0
    angle = cv2.minAreaRect(coords)[-1]
    # OpenCV reports the angle in (0, 90]; map it to a signed tilt about zero.
    if angle > 45:
        angle -= 90
    if abs(angle) > MAX_DESKEW_DEG:
        return 0.0
    return float(angle)


def deskew(image: np.ndarray, angle: float | None = None) -> tuple[np.ndarray, float]:
    """Rotate the page upright. Returns the image and the angle applied."""
    gray = to_gray(image)
    if angle is None:
        angle = estimate_skew(gray)
    if abs(angle) < MIN_DESKEW_DEG:
        return image, 0.0

    height, width = image.shape[:2]
    centre = (width / 2.0, height / 2.0)
    matrix = cv2.getRotationMatrix2D(centre, angle, 1.0)
    rotated = cv2.warpAffine(
        image,
        matrix,
        (width, height),
        flags=cv2.INTER_CUBIC,
        borderMode=cv2.BORDER_REPLICATE,
    )
    return rotated, angle


def denoise(gray: np.ndarray) -> np.ndarray:
    """Remove scanner speckle without softening thin strokes.

    A 3x3 median kills isolated black pixels -- dust, JPEG mosquito noise --
    and leaves the 1-2 px stems of 8 pt digits intact, which a Gaussian blur
    would not.
    """
    return cv2.medianBlur(gray, 3)


def threshold(gray: np.ndarray) -> np.ndarray:
    """Adaptive threshold, for pages that are unevenly lit.

    Global Otsu fails exactly where refinery scans are hardest: a photocopy
    with a dark band down one edge, or a page shot with a phone. The block size
    is large relative to the text so a whole character sits inside one
    neighbourhood.
    """
    return cv2.adaptiveThreshold(
        gray,
        255,
        cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
        cv2.THRESH_BINARY,
        blockSize=35,
        C=15,
    )


def prepare(image: np.ndarray) -> tuple[np.ndarray, dict]:
    """Full scanned-page pipeline. Returns the image OCR should read and a report.

    The returned image is 3-channel BGR: PP-OCRv4's detector was trained on
    photographs and colour scans, and handing it a hard-thresholded bitonal
    image measurably hurts recall on faint text. So the threshold is computed
    -- it is what the grid detector in layout.py consumes -- but what OCR gets
    is the deskewed, denoised greyscale. Both come back in the report.
    """
    deskewed, angle = deskew(image)
    gray = to_gray(deskewed)
    cleaned = denoise(gray)
    binary = threshold(cleaned)

    ocr_input = cv2.cvtColor(cleaned, cv2.COLOR_GRAY2BGR)
    return ocr_input, {"skew_deg": angle, "binary": binary}
