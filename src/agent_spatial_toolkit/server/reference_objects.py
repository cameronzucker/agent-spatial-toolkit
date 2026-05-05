"""Reference-object dimensions for the redesigned wizard's scale references.

The wizard's first-screen picker (design §2 step 2) offers three options:
credit card, US dollar bill, or printed ChArUco marker. Each has known
physical dimensions; this module returns the 4 corner positions in 3D
(in the reference's own frame) for any chosen type.

Frame convention: origin at the reference's visual top-left corner, X
along the long edge (positive), Y along the short edge (positive), Z=0
(reference is flat on the surface).
"""

from __future__ import annotations

# Reference type → (long_edge_mm, short_edge_mm)
# Sources:
#   credit_card: ISO/IEC 7810 ID-1
#   dollar_bill: US Treasury (https://www.bep.gov)
#   marker: bundled ChArUco PDF (wizard-controlled; matches PDF size)
_DIMENSIONS_MM: dict[str, tuple[float, float]] = {
    "credit_card": (85.60, 53.98),
    "dollar_bill": (156.1, 66.3),
    "marker": (100.0, 100.0),
}


class ReferenceObjectError(ValueError):
    """Raised when the requested reference type is unknown or argument is invalid."""


def get_corner_positions_mm(reference_type: str) -> list[tuple[float, float, float]]:
    """Return the 4 corner positions in 3D for the given reference type.

    Corners are clockwise from top-left in the reference's own frame.
    Origin = top-left corner; X = along long edge; Y = along short edge;
    Z = 0.
    """
    if not isinstance(reference_type, str):
        raise ReferenceObjectError(
            f"reference_type must be a string, got {type(reference_type).__name__}"
        )
    if reference_type not in _DIMENSIONS_MM:
        raise ReferenceObjectError(
            f"unknown reference type {reference_type!r}; valid: {sorted(_DIMENSIONS_MM.keys())}"
        )
    long_mm, short_mm = _DIMENSIONS_MM[reference_type]
    return [
        (0.0, 0.0, 0.0),
        (long_mm, 0.0, 0.0),
        (long_mm, short_mm, 0.0),
        (0.0, short_mm, 0.0),
    ]
