import asyncio
import base64
import binascii

import cv2
import numpy as np

# Room photos only feed a color palette, which survives heavy downscaling: 256 px on the longest
# side keeps the dominant colors while cutting the k-means work by ~100x for a phone photo.
MAX_IMAGE_SIDE = 256


class ImageDecodeError(ValueError):
    """Raised when the uploaded data is not a readable image."""


def decode_image(data: bytes, max_side: int = MAX_IMAGE_SIDE) -> np.ndarray:
    """Decode JPEG/PNG/WebP bytes into a BGR uint8 matrix whose longest side is at most max_side."""
    if not data:
        raise ImageDecodeError("the image is empty")
    # np.frombuffer wraps the bytes without copying them; OpenCV decodes straight from that view.
    image = cv2.imdecode(np.frombuffer(data, dtype=np.uint8), cv2.IMREAD_COLOR)
    if image is None:
        raise ImageDecodeError("the data is not a supported image")
    height, width = image.shape[:2]
    scale = max_side / max(height, width)
    if scale < 1:
        # INTER_AREA averages the source pixels, so shrinking does not alias or invent colors.
        size = (max(1, round(width * scale)), max(1, round(height * scale)))
        image = cv2.resize(image, size, interpolation=cv2.INTER_AREA)
    return image


async def load_image_matrix(encoded: str, max_side: int = MAX_IMAGE_SIDE) -> np.ndarray:
    """Decode a base64 image (a data: URL is accepted too) into an OpenCV matrix in a worker thread."""
    if encoded.startswith("data:"):
        encoded = encoded.partition(",")[2]
    try:
        data = base64.b64decode(encoded, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise ImageDecodeError("the image is not valid base64") from exc
    return await asyncio.to_thread(decode_image, data, max_side)


def summarize_image(image: np.ndarray, colors: int = 4) -> dict:
    """Mean brightness (0-1) and the dominant colors of a BGR image, found with k-means."""
    pixels = image.reshape(-1, 3).astype(np.float32)
    k = min(colors, len(pixels))
    criteria = (cv2.TERM_CRITERIA_EPS + cv2.TERM_CRITERIA_MAX_ITER, 20, 1.0)
    _, labels, centers = cv2.kmeans(pixels, k, None, criteria, 3, cv2.KMEANS_PP_CENTERS)
    counts = np.bincount(labels.ravel(), minlength=k)

    # Images with fewer distinct colors than k produce duplicate centers; merge them by hex code.
    shares = {}
    for center, count in zip(centers, counts):
        if count == 0:
            continue
        blue, green, red = (int(v) for v in np.clip(np.rint(center), 0, 255))
        hex_code = f"#{red:02x}{green:02x}{blue:02x}"
        shares[hex_code] = shares.get(hex_code, 0) + int(count)

    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    return {
        "brillo": round(float(gray.mean()) / 255, 3),
        "colores": [
            {"hex": hex_code, "proporcion": round(count / len(pixels), 3)}
            for hex_code, count in sorted(shares.items(), key=lambda item: item[1], reverse=True)
        ],
    }
