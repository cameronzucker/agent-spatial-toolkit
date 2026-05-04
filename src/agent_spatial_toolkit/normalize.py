"""Pixel normalization for resolution-independent reprojection thresholds.

Per spec §5.1, all reprojection thresholds in the geometric pipeline are stated
in normalized pixels: ``normalized_px = absolute_px * 2000 / max(image_w, image_h)``.

This makes the same threshold meaningful regardless of sensor resolution. A 5 px
RMS on a 4032×3024 phone photo and a 5 px RMS on a 1024×768 thumbnail mean
different things in absolute terms but should be treated identically by the
quality-gate logic.
"""

NORMALIZED_REFERENCE_LONG_EDGE: int = 2000
"""Reference long-edge dimension. All thresholds are calibrated against this."""


def to_normalized_px(absolute_px: float, image_size: tuple[int, int]) -> float:
    """Convert an absolute pixel measurement to normalized pixels.

    Args:
        absolute_px: pixel value in the original image's coordinate system.
        image_size: (width, height) in absolute pixels.

    Returns:
        Equivalent value in normalized-pixel units.

    Raises:
        ValueError: if either image dimension is non-positive.
    """
    width, height = image_size
    if width <= 0 or height <= 0:
        raise ValueError(f"image_size must be positive, got {image_size}")
    long_edge = max(width, height)
    return absolute_px * NORMALIZED_REFERENCE_LONG_EDGE / long_edge


def to_absolute_px(normalized_px: float, image_size: tuple[int, int]) -> float:
    """Inverse of to_normalized_px — recover absolute pixels for a given image."""
    width, height = image_size
    if width <= 0 or height <= 0:
        raise ValueError(f"image_size must be positive, got {image_size}")
    long_edge = max(width, height)
    return normalized_px * long_edge / NORMALIZED_REFERENCE_LONG_EDGE
