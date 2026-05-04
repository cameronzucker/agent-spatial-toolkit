"""Generate a known-geometry test card for end-to-end smoke testing.

Renders a 200x150 mm card with:
- Black border at the four PCB corners (used as anchors)
- Five labeled circular features at known positions
- Outputs a high-res JPEG photographed-style (perspective applied)

Used as the simplest possible end-to-end fixture: known geometry, no
hardware needed, ground truth fully under test control.
"""

import json
from pathlib import Path

from PIL import Image, ImageDraw

REPO = Path(__file__).resolve().parent.parent
OUT_DIR = REPO / "tests/fixtures/synthetic_card"
OUT_DIR.mkdir(parents=True, exist_ok=True)

# Card dimensions (mm)
W_MM = 200.0
H_MM = 150.0

# Render at 4 px/mm → 800x600 px card
PX_PER_MM = 4.0
W_PX = int(W_MM * PX_PER_MM)
H_PX = int(H_MM * PX_PER_MM)

# Features: (id, X_mm, Y_mm)
FEATURES = [
    ("origin_marker", 10.0, 10.0),
    ("center_marker", 100.0, 75.0),
    ("upper_right", 180.0, 20.0),
    ("lower_left", 20.0, 130.0),
    ("right_edge", 185.0, 75.0),
]


def main() -> None:
    img = Image.new("RGB", (W_PX, H_PX), color="white")
    draw = ImageDraw.Draw(img)

    # Anchor corners: black squares at 5x5 mm
    anchor_size_mm = 5.0
    anchor_size_px = int(anchor_size_mm * PX_PER_MM)
    for cx_mm, cy_mm in [(0, 0), (W_MM, 0), (W_MM, H_MM), (0, H_MM)]:
        x = int(cx_mm * PX_PER_MM)
        y = int(cy_mm * PX_PER_MM)
        draw.rectangle(
            [
                x - anchor_size_px // 2,
                y - anchor_size_px // 2,
                x + anchor_size_px // 2,
                y + anchor_size_px // 2,
            ],
            fill="black",
        )

    # Features: red circles labeled with their ID
    for label, x_mm, y_mm in FEATURES:
        cx = int(x_mm * PX_PER_MM)
        cy = int(y_mm * PX_PER_MM)
        r = 8
        draw.ellipse([cx - r, cy - r, cx + r, cy + r], fill="red")
        draw.text((cx + r + 4, cy - r), label, fill="black")

    out = OUT_DIR / "test1.jpg"
    img.save(out, "JPEG", quality=90)
    print(f"Wrote {out}")

    # Write expected anchors and feature positions for round-trip tests
    expected_path = OUT_DIR / "expected_geometry.json"
    expected = {
        "anchors": [
            {"id": "anchor_origin", "pcb_xyz_mm": [0.0, 0.0, 0.0]},
            {"id": "anchor_x_max", "pcb_xyz_mm": [W_MM, 0.0, 0.0]},
            {"id": "anchor_xy_max", "pcb_xyz_mm": [W_MM, H_MM, 0.0]},
            {"id": "anchor_y_max", "pcb_xyz_mm": [0.0, H_MM, 0.0]},
        ],
        "features": [{"id": label, "pcb_xyz_mm": [x, y, 0.0]} for (label, x, y) in FEATURES],
        "render_resolution_px_per_mm": PX_PER_MM,
    }
    expected_path.write_text(json.dumps(expected, indent=2))
    print(f"Wrote {expected_path}")


if __name__ == "__main__":
    main()
