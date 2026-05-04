"""Server-side lens catalog (Task 1.D.5 PR-α).

Maps human-friendly lens IDs (chosen in the wizard's Phase 2a dropdown) to
the data needed to resolve camera intrinsics for that lens at request time.
``resolve(lens_id, image_size, exif=None)`` returns an ``Intrinsics`` if the
catalog can compute it, or ``None`` if the caller must provide intrinsics
explicitly (manual mode, ultrawide-pending-chessboard, EXIF-without-focal).

The catalog stores ``fallback_focal_35mm_equiv_mm`` rather than pre-resolved
intrinsics because intrinsics depend on image size (cx, cy, fx all scale
with the photo's pixel dimensions). Per-request resolution avoids hardcoding
intrinsics for every possible image resolution.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from agent_spatial_toolkit.pipeline.intrinsics import (
    FOV_CLASS_ULTRAWIDE,
    Intrinsics,
    resolve_fallback_intrinsics,
    resolve_fov_class,
)


@dataclass(frozen=True)
class LensCatalogEntry:
    """One entry in the lens catalog.

    ``fallback_focal_35mm_equiv_mm`` is the 35mm-equivalent focal length used
    to compute intrinsics via ``resolve_fallback_intrinsics``. ``None`` for
    sentinel entries (``exif:detected``, ``other``) where focal comes from
    a different source at request time.
    """

    label: str
    fallback_focal_35mm_equiv_mm: float | None
    notes: str = ""


LENS_ID_EXIF_DETECTED = "exif:detected"
LENS_ID_OTHER = "other"


LENS_CATALOG: dict[str, LensCatalogEntry] = {
    "pi_camera_module_3_wide": LensCatalogEntry(
        label="Pi Camera Module 3 (wide)",
        fallback_focal_35mm_equiv_mm=20.0,
        notes="Ultrawide; FOV-class fallback rejects. Use chessboard calibration (Phase 2 Stream G) or manual intrinsics.",
    ),
    "pi_camera_module_3_standard": LensCatalogEntry(
        label="Pi Camera Module 3 (standard)",
        fallback_focal_35mm_equiv_mm=25.0,
    ),
    "iphone_15_pro_24mm": LensCatalogEntry(
        label="iPhone 15 Pro — 24mm equiv (main)",
        fallback_focal_35mm_equiv_mm=24.0,
    ),
    "iphone_15_pro_13mm": LensCatalogEntry(
        label="iPhone 15 Pro — 13mm equiv (ultrawide)",
        fallback_focal_35mm_equiv_mm=13.0,
        notes="Ultrawide; FOV-class fallback rejects. Use chessboard or manual.",
    ),
    "iphone_15_pro_77mm": LensCatalogEntry(
        label="iPhone 15 Pro — 77mm equiv (telephoto)",
        fallback_focal_35mm_equiv_mm=77.0,
    ),
    LENS_ID_EXIF_DETECTED: LensCatalogEntry(
        label="(detected from EXIF)",
        fallback_focal_35mm_equiv_mm=None,
        notes="Server reads focalLength35mm from the photo's EXIF at request time.",
    ),
    LENS_ID_OTHER: LensCatalogEntry(
        label="Other (manual entry)",
        fallback_focal_35mm_equiv_mm=None,
        notes="Caller must provide a full intrinsics dict in the /api/anchors body.",
    ),
}


def _is_resolvable(lens_id: str, entry: LensCatalogEntry) -> bool:
    if lens_id in (LENS_ID_EXIF_DETECTED, LENS_ID_OTHER):
        return False
    if entry.fallback_focal_35mm_equiv_mm is None:
        return False
    if resolve_fov_class(entry.fallback_focal_35mm_equiv_mm) == FOV_CLASS_ULTRAWIDE:
        return False
    return True


def list_lens_entries() -> list[dict[str, Any]]:
    """Return the catalog as a list of public-shape dicts for GET /api/lens_catalog.

    Intrinsics data is NOT exposed — clients only need id + label + resolvability.
    """
    return [
        {
            "id": lens_id,
            "label": entry.label,
            "resolvable": _is_resolvable(lens_id, entry),
            "notes": entry.notes,
        }
        for lens_id, entry in LENS_CATALOG.items()
    ]


def resolve(
    lens_id: str,
    image_size: tuple[int, int],
    exif: dict[str, Any] | None = None,
) -> Intrinsics | None:
    """Resolve a lens_id to Intrinsics for a photo of the given image_size.

    Returns None if:
    - lens_id is unknown
    - lens_id is "other" (caller must supply intrinsics)
    - lens_id is "exif:detected" but ``exif`` is missing or lacks focalLength35mm
    - the resolved focal length is ultrawide (FOV-class fallback rejects it)
    """
    entry = LENS_CATALOG.get(lens_id)
    if entry is None:
        return None
    if lens_id == LENS_ID_OTHER:
        return None
    if lens_id == LENS_ID_EXIF_DETECTED:
        if not exif:
            return None
        focal = exif.get("focalLength35mm")
        if focal is None:
            return None
        try:
            focal_f = float(focal)
        except (TypeError, ValueError):
            return None
        return resolve_fallback_intrinsics(focal_f, image_size)
    focal = entry.fallback_focal_35mm_equiv_mm
    if focal is None:
        return None
    return resolve_fallback_intrinsics(focal, image_size)
