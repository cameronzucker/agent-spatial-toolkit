"""End-to-end smoke test: synthetic card -> pipeline -> ground truth match."""

import json
from pathlib import Path

import pytest

# This test calls into the math layer directly, simulating what the
# server/UI would do once a user finishes annotating.

FIXTURES = Path(__file__).parent / "fixtures/synthetic_card"


@pytest.fixture
def expected_geometry():
    return json.loads((FIXTURES / "expected_geometry.json").read_text())


def test_synthetic_card_pipeline_recovers_features(expected_geometry):
    """Given the synthetic card photo and known anchor pixel positions,
    the pipeline should recover each feature's part-local position to
    within 1 mm of ground truth."""
    import numpy as np
    from PIL import Image

    from agent_spatial_toolkit.pipeline.intrinsics import Intrinsics
    from agent_spatial_toolkit.pipeline.pose import solve_pnp
    from agent_spatial_toolkit.pipeline.ray import pixel_to_part_local

    # Synthetic card was rendered orthographically — for testing the pipeline,
    # we use synthetic intrinsics matching how the renderer projected.
    img = Image.open(FIXTURES / "test1.jpg")
    width, height = img.size

    # The synthetic card is rendered AT scale, so anchor pixel positions
    # are exactly:
    px_per_mm = expected_geometry["render_resolution_px_per_mm"]
    anchors_3d = np.array([a["pcb_xyz_mm"] for a in expected_geometry["anchors"]])
    anchors_2d = np.array(
        [
            [a["pcb_xyz_mm"][0] * px_per_mm, a["pcb_xyz_mm"][1] * px_per_mm]
            for a in expected_geometry["anchors"]
        ]
    )

    # For the synthetic case we use the renderer's exact intrinsics:
    intrinsics = Intrinsics(
        profile_source="fov_class_fallback",
        profile_id="synthetic_orthographic",
        fx_px=1e6,
        fy_px=1e6,  # near-orthographic (large focal length)
        cx=width / 2,
        cy=height / 2,
        distortion=[0.0, 0.0, 0.0, 0.0, 0.0],
    )

    # Solve PnP with the four anchors at known pixel positions
    pose = solve_pnp(
        world_points=anchors_3d,
        pixel_points=anchors_2d,
        intrinsics=intrinsics,
        image_size=(width, height),
    )
    assert pose.anchor_reprojection_rms_px < 5.0  # acceptable tolerance

    # Now reproject each feature using ray-cast and check it's at the right place
    for f in expected_geometry["features"]:
        true_xyz = f["pcb_xyz_mm"]
        feat_pixel = np.array([true_xyz[0] * px_per_mm, true_xyz[1] * px_per_mm])
        recovered = pixel_to_part_local(
            pixel=feat_pixel,
            pose=pose,
            intrinsics=intrinsics,
            z_assumed_mm=0.0,
        )
        # Recovered (X, Y) should be within 1 mm of ground truth
        assert abs(recovered[0] - true_xyz[0]) < 1.0, f"X mismatch for {f['id']}"
        assert abs(recovered[1] - true_xyz[1]) < 1.0, f"Y mismatch for {f['id']}"
