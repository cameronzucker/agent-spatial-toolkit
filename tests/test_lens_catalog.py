"""Tests for server/lens_catalog.py — lens-id → Intrinsics resolution."""
from __future__ import annotations

import pytest

from agent_spatial_toolkit.pipeline.intrinsics import Intrinsics
from agent_spatial_toolkit.server.lens_catalog import (
    LENS_CATALOG,
    LensCatalogEntry,
    list_lens_entries,
    resolve,
)


def test_catalog_has_expected_lens_ids() -> None:
    expected = {
        "pi_camera_module_3_wide",
        "pi_camera_module_3_standard",
        "iphone_15_pro_24mm",
        "iphone_15_pro_13mm",
        "iphone_15_pro_77mm",
        "exif:detected",
        "other",
    }
    assert set(LENS_CATALOG.keys()) == expected


def test_list_lens_entries_returns_id_label_resolvable_tuples() -> None:
    entries = list_lens_entries()
    assert isinstance(entries, list)
    by_id = {e["id"]: e for e in entries}
    assert "pi_camera_module_3_standard" in by_id
    assert by_id["pi_camera_module_3_standard"]["label"] == "Pi Camera Module 3 (standard)"
    assert by_id["pi_camera_module_3_standard"]["resolvable"] is True
    assert by_id["exif:detected"]["resolvable"] is False
    assert by_id["other"]["resolvable"] is False
    assert by_id["pi_camera_module_3_wide"]["resolvable"] is False


def test_resolve_pi_camera_standard_returns_intrinsics() -> None:
    intr = resolve("pi_camera_module_3_standard", image_size=(4608, 2592))
    assert isinstance(intr, Intrinsics)
    assert intr.profile_source == "fov_class_fallback"
    assert intr.fx_px == pytest.approx(4608 * 25.0 / 36.0, rel=1e-6)
    assert intr.cx == pytest.approx(4608 / 2.0)
    assert intr.cy == pytest.approx(2592 / 2.0)


def test_resolve_ultrawide_returns_none() -> None:
    assert resolve("pi_camera_module_3_wide", image_size=(2304, 1296)) is None
    assert resolve("iphone_15_pro_13mm", image_size=(4032, 3024)) is None


def test_resolve_unknown_lens_id_returns_none() -> None:
    assert resolve("does_not_exist", image_size=(1000, 1000)) is None


def test_resolve_exif_detected_uses_exif_focal_length() -> None:
    exif = {"focalLength35mm": 50.0}
    intr = resolve("exif:detected", image_size=(4032, 3024), exif=exif)
    assert isinstance(intr, Intrinsics)
    assert intr.fx_px == pytest.approx(4032 * 50.0 / 36.0)


def test_resolve_exif_detected_without_exif_returns_none() -> None:
    assert resolve("exif:detected", image_size=(1000, 1000)) is None


def test_resolve_exif_detected_with_missing_focal_returns_none() -> None:
    assert resolve("exif:detected", image_size=(1000, 1000), exif={"make": "X"}) is None


def test_resolve_other_returns_none() -> None:
    assert resolve("other", image_size=(1000, 1000)) is None


def test_lens_catalog_entry_dataclass_shape() -> None:
    entry = LENS_CATALOG["pi_camera_module_3_standard"]
    assert isinstance(entry, LensCatalogEntry)
    assert entry.label == "Pi Camera Module 3 (standard)"
    assert entry.fallback_focal_35mm_equiv_mm == 25.0
    assert isinstance(entry.notes, str)
