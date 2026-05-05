"""Tests for the wallet-item / marker reference-object dimension lookup
that replaces the legacy lens_catalog for the redesigned wizard."""

from __future__ import annotations

import pytest

from agent_spatial_toolkit.server.reference_objects import (
    ReferenceObjectError,
    get_corner_positions_mm,
)


def test_credit_card_corners_match_iso_iec_7810_id_1() -> None:
    """Credit card is 85.60 × 53.98 mm per ISO/IEC 7810 ID-1."""
    corners = get_corner_positions_mm("credit_card")
    # 4 corners, clockwise from top-left in the reference's own frame.
    assert corners == [
        (0.0, 0.0, 0.0),
        (85.60, 0.0, 0.0),
        (85.60, 53.98, 0.0),
        (0.0, 53.98, 0.0),
    ]


def test_dollar_bill_corners_match_us_treasury() -> None:
    """US dollar bill is 156.1 × 66.3 mm."""
    corners = get_corner_positions_mm("dollar_bill")
    assert corners == [
        (0.0, 0.0, 0.0),
        (156.1, 0.0, 0.0),
        (156.1, 66.3, 0.0),
        (0.0, 66.3, 0.0),
    ]


def test_marker_corners_use_default_charuco_dimensions() -> None:
    """Default ChArUco marker bundled with the wizard is 100 × 100 mm.
    (The actual PDF served by the wizard will encode this size; the
    lookup mirrors the printed dimensions.)"""
    corners = get_corner_positions_mm("marker")
    assert corners == [
        (0.0, 0.0, 0.0),
        (100.0, 0.0, 0.0),
        (100.0, 100.0, 0.0),
        (0.0, 100.0, 0.0),
    ]


def test_unknown_reference_type_raises() -> None:
    with pytest.raises(ReferenceObjectError, match="unknown reference type"):
        get_corner_positions_mm("not_a_real_type")


def test_invalid_argument_type_raises() -> None:
    with pytest.raises(ReferenceObjectError, match="must be a string"):
        get_corner_positions_mm(123)  # type: ignore[arg-type]
