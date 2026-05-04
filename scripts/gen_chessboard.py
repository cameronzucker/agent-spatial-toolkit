#!/usr/bin/env python3
"""
Generate the calibration chessboard PDFs that ship with agent-spatial-toolkit.

Produces 9×6 internal-corner pattern (10×7 squares) at 25 mm per square,
in both A4 and US Letter sizes. Suitable for laser-printing on plain paper
and used by the toolkit's Tier A camera calibration.

Usage:
    uv run python scripts/gen_chessboard.py
    # outputs:
    #   src/agent_spatial_toolkit/calibration/chessboard_a4.pdf
    #   src/agent_spatial_toolkit/calibration/chessboard_letter.pdf
"""
from pathlib import Path

from reportlab.lib.pagesizes import A4, LETTER
from reportlab.lib.units import mm
from reportlab.pdfgen import canvas

REPO = Path(__file__).resolve().parent.parent
OUT_DIR = REPO / "src/agent_spatial_toolkit/calibration"
OUT_DIR.mkdir(parents=True, exist_ok=True)

# 8 columns × 6 rows of squares → 7 × 5 internal corners.
# A common OpenCV chessboard convention; fits A4 and US Letter portrait
# with margin for labels below the grid.
COLS = 8
ROWS = 6
SQUARE_MM = 25.0
INTERNAL_CORNERS = (COLS - 1, ROWS - 1)  # (7, 5) — used by cv2.findChessboardCorners


def draw_chessboard(c: canvas.Canvas, page_w: float, page_h: float, label: str) -> None:
    """Draw the chessboard centered on the page, with a labeling block below."""
    grid_w = COLS * SQUARE_MM * mm
    grid_h = ROWS * SQUARE_MM * mm

    # Center horizontally; place ~30 mm from the top of the page (leaves room for label below)
    x0 = (page_w - grid_w) / 2.0
    y0 = page_h - 30 * mm - grid_h

    c.setFillColorRGB(0, 0, 0)
    for col in range(COLS):
        for row in range(ROWS):
            # Top-left of board is (col=0, row=0) — fill if (col+row) is even
            if (col + row) % 2 == 0:
                cx = x0 + col * SQUARE_MM * mm
                # ReportLab y origin is at the bottom; flip row
                cy = y0 + (ROWS - 1 - row) * SQUARE_MM * mm
                c.rect(cx, cy, SQUARE_MM * mm, SQUARE_MM * mm, stroke=0, fill=1)

    # Border around the board for crisp edge detection
    c.setStrokeColorRGB(0, 0, 0)
    c.setLineWidth(0.5)
    c.rect(x0, y0, grid_w, grid_h, stroke=1, fill=0)

    # Labeling block below the board
    c.setFillColorRGB(0, 0, 0)
    c.setFont("Helvetica", 11)
    label_y = y0 - 14 * mm
    c.drawString(x0, label_y, "agent-spatial-toolkit calibration target")
    c.setFont("Helvetica", 9)
    c.drawString(x0, label_y - 5 * mm,
                 f"{COLS}×{ROWS} squares ({COLS - 1}×{ROWS - 1} internal corners) · "
                 f"{int(SQUARE_MM)} mm per square · {label}")
    c.drawString(x0, label_y - 10 * mm,
                 "Print at 100% scale (no fit-to-page). Verify a square measures 25 mm with a ruler before use.")
    c.drawString(x0, label_y - 15 * mm,
                 "If your printer scales: re-print, or measure actual square size and pass it to the toolkit.")

    # Scale-verification ruler — a 50 mm scale bar
    bar_x = x0
    bar_y = label_y - 25 * mm
    c.setLineWidth(0.7)
    c.line(bar_x, bar_y, bar_x + 50 * mm, bar_y)
    c.line(bar_x, bar_y - 1 * mm, bar_x, bar_y + 1 * mm)
    c.line(bar_x + 25 * mm, bar_y - 1 * mm, bar_x + 25 * mm, bar_y + 1 * mm)
    c.line(bar_x + 50 * mm, bar_y - 1 * mm, bar_x + 50 * mm, bar_y + 1 * mm)
    c.setFont("Helvetica", 8)
    c.drawString(bar_x, bar_y - 4 * mm, "0")
    c.drawString(bar_x + 25 * mm - 3, bar_y - 4 * mm, "25")
    c.drawString(bar_x + 50 * mm - 3, bar_y - 4 * mm, "50 mm  (use a ruler to verify)")


def write_pdf(out_path: Path, pagesize, label: str) -> None:
    page_w, page_h = pagesize
    c = canvas.Canvas(str(out_path), pagesize=pagesize)
    c.setTitle("agent-spatial-toolkit calibration chessboard")
    draw_chessboard(c, page_w, page_h, label)
    c.showPage()
    c.save()
    print(f"Wrote {out_path} ({out_path.stat().st_size:,} bytes)")


def main() -> None:
    # Portrait orientation. Grid is 200×150mm; A4 (210×297) and US Letter
    # (216×279) both have ample margin and ~130mm of vertical space below the
    # grid for labels and the scale-verification ruler.
    write_pdf(OUT_DIR / "chessboard_a4.pdf", A4, "A4 (210 × 297 mm)")
    write_pdf(OUT_DIR / "chessboard_letter.pdf", LETTER, "US Letter (8.5 × 11 in)")


if __name__ == "__main__":
    main()
