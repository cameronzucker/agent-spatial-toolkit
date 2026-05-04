"""Tests for src/agent_spatial_toolkit/normalize.py — pixel normalization helpers."""

import pytest

from agent_spatial_toolkit.normalize import (
    NORMALIZED_REFERENCE_LONG_EDGE,
    to_absolute_px,
    to_normalized_px,
)


def test_normalized_reference_constant() -> None:
    """The normalized reference long edge is documented as 2000 px (spec §5.1)."""
    assert NORMALIZED_REFERENCE_LONG_EDGE == 2000


def test_to_normalized_px_at_reference_resolution() -> None:
    """A photo whose long edge is exactly 2000 px gets a 1:1 normalization."""
    assert to_normalized_px(absolute_px=5.0, image_size=(2000, 1500)) == pytest.approx(5.0)


def test_to_normalized_px_at_4k_phone_resolution() -> None:
    """A 4032×3024 photo (typical 12 MP phone): factor = 2000/4032 ~= 0.4960."""
    result = to_normalized_px(absolute_px=10.0, image_size=(4032, 3024))
    assert result == pytest.approx(10.0 * 2000 / 4032)


def test_to_normalized_px_at_thumbnail_resolution() -> None:
    """A 1024×768 thumbnail: factor = 2000/1024 ~= 1.9531."""
    result = to_normalized_px(absolute_px=5.0, image_size=(1024, 768))
    assert result == pytest.approx(5.0 * 2000 / 1024)


def test_to_normalized_px_uses_max_edge() -> None:
    """Normalization divides by max(width, height)."""
    # Portrait orientation: height is the long edge
    result = to_normalized_px(absolute_px=10.0, image_size=(1500, 2000))
    assert result == pytest.approx(10.0)  # 2000/2000 = 1.0


def test_round_trip() -> None:
    """to_absolute_px is the inverse of to_normalized_px."""
    image_size = (4032, 3024)
    original = 7.42
    normalized = to_normalized_px(original, image_size)
    recovered = to_absolute_px(normalized, image_size)
    assert recovered == pytest.approx(original)


def test_zero_image_size_raises() -> None:
    """Zero or negative image dimensions are invalid."""
    with pytest.raises(ValueError, match="image_size must be positive"):
        to_normalized_px(5.0, (0, 100))
    with pytest.raises(ValueError, match="image_size must be positive"):
        to_normalized_px(5.0, (100, -1))
