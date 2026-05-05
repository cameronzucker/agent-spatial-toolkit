"""Next-prompt scoring algorithm (design §4 v1 simplification).

```
features_pending  = (1 if any un-triangulated user-labeled features exist else 0)
empty_cell_score  = (1 if candidate side is unfilled else 0)
total_score       = features_pending × 10 + empty_cell_score
```

Pick the candidate with the highest total_score. Tie-break by minimum
angular distance between the candidate cell's canonical axis and the
most-recent photo's optical axis (minimizes physical re-positioning per
design §4).

Honest reason branches (mapped to enum reason_code values for PR-4 prose):
- features_pending=1 + candidate fills empty cell → second_view_needed_empty_cell
- features_pending=1 + no empty cells           → second_view_needed_filled_cell
- features_pending=0 + empty cells              → unseen_side_low_priority
- features_pending=0 + ≥3 cells filled          → call_it_done
- features_pending=0 + ≥6 features uniformly green → call_it_done (suppression rule)
- no photos yet                                 → top_first_photo

The string `reason` in the response is FALLBACK English copy that
interpolates the internal axis label literally (e.g., '+long-side').
PR-4's UI MUST translate `reason_code` → localized prose and `direction`
→ silhouette icon; the fallback `reason` exists only so a debugger sees
something sensible. Per design §9 the user must NEVER see literal axis
labels.
"""

from __future__ import annotations

from typing import Any

from agent_spatial_toolkit.pipeline.pose import PoseResult
from agent_spatial_toolkit.server.coverage import (
    CELL_LABELS,
    angular_distance_to_cell,
    compute_coverage_cells,
)

# Threshold: a triangulated feature is "green tier" when its max-residual
# converted to mm is below this value. Matches design §4 tier table
# (Excellent + Good combined upper bound = 0.5 mm).
GREEN_TIER_MM_THRESHOLD = 0.5
# Suppression-rule feature-count threshold per design §4 completion criterion #2.
SUPPRESSION_RULE_FEATURE_COUNT = 6


def _is_pending(feature: dict[str, Any]) -> bool:
    """A feature is pending iff it has only single-view (planar_intersection)
    coverage AND the user has NOT explicitly marked it accepted as single-view.
    """
    method = feature.get("method", "")
    return method == "planar_intersection" and not feature.get("user_accepted_single_view", False)


def _is_uniformly_green_triangulated(features: list[dict[str, Any]]) -> bool:
    """True iff every feature is triangulated AND max_error_mm < threshold.

    Caller (app.py) is responsible for populating `feature["max_error_mm"]`
    via the per-photo depth-aware mm-conversion (pipeline.error_mm). If the
    field is absent for any feature we conservatively return False — design
    §4 calls for "uniformly green-tier triangulated" and absence is not
    confirmation.
    """
    if not features:
        return False
    for f in features:
        method = f.get("method", "")
        if not method.startswith("triangulation_"):
            return False
        max_err = f.get("max_error_mm")
        if max_err is None or float(max_err) >= GREEN_TIER_MM_THRESHOLD:
            return False
    return True


def score_next_prompt(
    photos: list[dict[str, Any]],
    features: list[dict[str, Any]],
) -> dict[str, Any]:
    """Compute the next-photo prompt for the wizard's UI.

    `photos`: list of {"id": str, "pose": PoseResult} entries. Order
    matters — photos[-1] is the most recent (used for tie-break).
    `features`: list of {"id": str, "method": str, "max_error_mm"?: float}
    entries. The optional `max_error_mm` is required for the suppression
    rule check; absence is treated conservatively as "not green tier".
    """
    poses = [p["pose"] for p in photos if isinstance(p.get("pose"), PoseResult)]
    coverage = compute_coverage_cells(poses)
    pending_features = [f for f in features if _is_pending(f)]
    features_pending = 1 if pending_features else 0

    side_labels = [c for c in CELL_LABELS if c != "top"]
    n_filled_cells = sum(1 for c in CELL_LABELS if coverage[c])

    feature_serialized = [{"id": f["id"], "method": f.get("method", "")} for f in features]

    # Branch 1: zero photos → top first.
    if not poses:
        return {
            "direction": "top",
            "reason_code": "top_first_photo",
            "reason": "Take your first photo straight down on your part.",
            "coverage_cells": coverage,
            "features": feature_serialized,
        }

    # Branch 2: 'call it done' — features done AND (≥3 cells filled OR the
    # suppression rule applies: ≥6 features all uniformly green-tier).
    suppression_applies = len(
        features
    ) >= SUPPRESSION_RULE_FEATURE_COUNT and _is_uniformly_green_triangulated(features)
    if features_pending == 0 and (n_filled_cells >= 3 or suppression_applies):
        # Pick a candidate side with the rotation tie-break, in case the
        # user wants to keep going.
        best_direction = _pick_side_with_rotation_tiebreak(
            side_labels=side_labels,
            coverage=coverage,
            features_pending=0,
            recent_pose=poses[-1],
        )
        return {
            "direction": best_direction,
            "reason_code": "call_it_done",
            "reason": (
                "All your labeled features are captured. "
                "Want to shoot the remaining sides, or call it done?"
            ),
            "coverage_cells": coverage,
            "features": feature_serialized,
        }

    # Branch 3: score every side and pick best with rotation tie-break.
    best_direction = _pick_side_with_rotation_tiebreak(
        side_labels=side_labels,
        coverage=coverage,
        features_pending=features_pending,
        recent_pose=poses[-1],
    )

    # Reason-code + fallback prose. Internal axis label leaks into the
    # fallback string by design — see module docstring.
    if features_pending == 1 and not coverage[best_direction]:
        reason_code = "second_view_needed_empty_cell"
        reason = (
            f"Take a photo of the {best_direction}-side — we still need a "
            "second view of some of your features."
        )
    elif features_pending == 1 and coverage[best_direction]:
        reason_code = "second_view_needed_filled_cell"
        reason = (
            f"Take a photo from a slightly different angle of the {best_direction}-side — "
            "we still need a second view of some of your features."
        )
    elif features_pending == 0 and not coverage[best_direction]:
        reason_code = "unseen_side_low_priority"
        reason = (
            f"Take a photo of the {best_direction}-side — you haven't seen it yet. "
            "Anything to label there?"
        )
    else:
        reason_code = "another_angle_low_priority"
        reason = f"Take another photo of the {best_direction}-side from a different angle."

    return {
        "direction": best_direction,
        "reason_code": reason_code,
        "reason": reason,
        "coverage_cells": coverage,
        "features": feature_serialized,
    }


def _pick_side_with_rotation_tiebreak(
    side_labels: list[str],
    coverage: dict[str, bool],
    features_pending: int,
    recent_pose: PoseResult,
) -> str:
    """Score each side per the design §4 algorithm; tie-break by minimum
    angular distance between the candidate cell's canonical axis and
    `recent_pose`'s optical axis.
    """
    best_score = -1.0
    best_dist = float("inf")
    best_direction = side_labels[0]
    for side in side_labels:
        empty_score = 1 if not coverage[side] else 0
        score = features_pending * 10 + empty_score
        dist = angular_distance_to_cell(recent_pose, side)
        if score > best_score or (score == best_score and dist < best_dist):
            best_score = score
            best_dist = dist
            best_direction = side
    return best_direction
