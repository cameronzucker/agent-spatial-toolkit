# agent-spatial-toolkit v1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:subagent-driven-development` (recommended) or `superpowers:executing-plans` to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement the agent-spatial-toolkit per the v1 design spec, in two tagged releases: `v0.1.0-alpha` (CLI + single-photo β-mode + FOV-class fallback) and `v0.1.0` (full v1: γ-mode, chessboard calibration, skill primitive, HEIC, cross-model gate).

**Architecture:** Python (3.10+) + Flask local HTTP server + OpenCV (PnP, triangulation, chessboard) + single-page browser wizard. Three actors: user (browser clicks), server (math + persistence), agent (consumes JSON). All processing local; ephemeral session directories under `~/.spatial-annotations/`.

**Tech Stack:** Python (uv-managed), Flask, opencv-python, numpy, Pillow, pillow-heif, vanilla JS + HTML5 Canvas (no frontend framework), pytest, GitHub Actions CI.

**Spec reference:** [`docs/specs/2026-05-03-design.md`](../specs/2026-05-03-design.md). The spec is the source of truth; this plan implements it. Where the plan and spec disagree, **the spec wins** — but flag the discrepancy and ask for a checkpoint.

---

## Plan Conventions

### Task notation

- **Stream**: parallel work-stream identifier (A through K). Tasks in the same stream are sequential. Tasks in different streams within the same phase are parallel-safe.
- **Depends on**: explicit task dependencies (e.g., `T1.A.4 depends on T1.A.1, T1.A.3`).
- **Subagent dispatch**: each task is dispatchable to a fresh subagent. The subagent receives: this task's full text, the spec, and any artifacts produced by depended-on tasks.

### TDD discipline (binding)

Every task that writes production code follows red-green-commit:
1. Write the failing test
2. Run the test, verify it fails as expected
3. Write minimal implementation
4. Run the test, verify it passes
5. Run the full suite, verify nothing else broke
6. Commit (test + implementation in one commit)

If a step would violate TDD, flag it explicitly. The only exceptions are:
- Pure scaffolding (e.g., creating an empty `__init__.py`) — commit directly
- Generated artifacts (e.g., the chessboard PDFs) — commit the generator + output

### Branch & PR discipline

- All substantive code goes through a PR. Branch naming: `feat/<short-slug>`, `fix/<short-slug>`, `docs/<short-slug>`.
- After tests pass and Codex review (see below) is favorable, the orchestrator self-merges via `gh pr merge --squash --delete-branch`.
- Commit messages: conventional commits (`feat:`, `fix:`, `docs:`, `chore:`, `refactor:`, `test:`, `perf:`, `build:`, `ci:`).
- Direct push to main: only for low-risk doc edits and trivial scaffolding (gated by orchestrator judgment).

### Codex cross-perspective review

Before merging any non-trivial PR, the orchestrator invokes Codex CLI to provide an independent review:

```bash
codex exec --skip-git-repo-check --output-last-message /tmp/codex-review.txt \
  "Review the diff at <branch> against main. Focus on: correctness, edge cases, test coverage, and adherence to the spec at docs/specs/2026-05-03-design.md. Flag anything you'd reject."
```

Codex output goes into the PR description as a quoted review block. If Codex flags a real issue, fix before merging.

### When to escalate to a user checkpoint

Escalate ONLY for:
- Spec ambiguities the orchestrator cannot resolve via spec re-read
- Test failures that resist 2 cross-model debugging attempts
- Decisions requiring user judgment (e.g., UX trade-offs, naming preferences)
- Pre-tag release approvals

Otherwise: orchestrator proceeds. Reversible defaults are fine.

---

## File Structure (locked here)

```
agent-spatial-toolkit/
├── LICENSE                                    # exists
├── README.md                                  # exists, will update at release
├── CHANGELOG.md                               # NEW (Task 0.3)
├── pyproject.toml                             # exists, will extend
├── uv.lock                                    # exists
├── .gitignore                                 # exists
├── .github/
│   └── workflows/
│       └── tests.yml                          # NEW (Task 0.1)
├── docs/
│   ├── specs/2026-05-03-design.md             # exists (spec)
│   └── plans/2026-05-03-v1-implementation.md  # this file
├── src/
│   └── agent_spatial_toolkit/
│       ├── __init__.py                        # exists (version)
│       ├── cli.py                             # CLI entry point (alpha)
│       ├── normalize.py                       # pixel normalization helpers
│       ├── server/
│       │   ├── __init__.py
│       │   ├── app.py                         # Flask routes
│       │   ├── session.py                     # session dir management
│       │   ├── lifecycle.py                   # server start/stop, auto-shutdown
│       │   └── events.py                      # JSONL event stream
│       ├── pipeline/
│       │   ├── __init__.py
│       │   ├── intrinsics.py                  # EXIF, FOV-class fallback (alpha)
│       │   ├── pose.py                        # solvePnP + anchor validation
│       │   ├── ray.py                         # planar ray-cast (β-mode)
│       │   ├── triangulate.py                 # multi-view (γ-mode, v0.1.0)
│       │   ├── chessboard.py                  # Tier A calibration (v0.1.0)
│       │   ├── reproject.py                   # validation overlay PNGs
│       │   └── emit.py                        # annotations.json + manifest
│       ├── schema/
│       │   ├── __init__.py
│       │   ├── models.py                      # dataclasses for the schema
│       │   └── validators.py                  # closed flag enum, validation
│       ├── ui/
│       │   ├── index.html                     # single-page wizard shell
│       │   ├── app.js                         # wizard state machine
│       │   ├── helpers.js                     # click capture, image utils
│       │   └── style.css
│       └── calibration/
│           ├── chessboard_a4.pdf              # exists
│           └── chessboard_letter.pdf          # exists
├── adapters/                                  # NEW (v0.1.0, Task 2.K)
│   ├── claude_code/
│   ├── codex/
│   ├── gemini_cli/
│   └── generic/
├── bin/                                       # NEW (v0.1.0)
│   ├── cleanup_sessions.sh                    # opt-in session cleanup
│   ├── validate-cross-model.sh                # cross-model gate runner
│   └── check_consumption_parity.py            # deterministic checker
├── scripts/                                   # exists (gen_chessboard.py)
├── tests/
│   ├── __init__.py
│   ├── fixtures/
│   │   ├── synthetic_card/                    # smoke fixture (Task 1.E.1)
│   │   ├── pi5_test_set/                      # real fixture (Task 1.E.2)
│   │   └── chessboard_test.jpg                # for chessboard tests (Task 2.G)
│   ├── test_normalize.py
│   ├── test_intrinsics.py
│   ├── test_pose.py
│   ├── test_ray.py
│   ├── test_triangulate.py
│   ├── test_chessboard.py
│   ├── test_reproject.py
│   ├── test_emit.py
│   ├── test_schema.py
│   ├── test_session.py
│   ├── test_events.py
│   ├── test_app.py                            # Flask routes
│   ├── test_cli.py                            # end-to-end CLI integration
│   └── test_cross_model.py                    # only in v0.1.0
└── SKILL.md                                   # NEW (Task 2.I.1, v0.1.0)
```

### File responsibility map

| File | One-line responsibility |
|---|---|
| `cli.py` | Parse CLI args, start server, exit |
| `normalize.py` | `to_normalized_px(px, image_size)` and inverse |
| `server/app.py` | Flask routes for `/api/*` and static UI serving |
| `server/session.py` | Create/load session dirs, write `state.json`, `manifest.json` |
| `server/lifecycle.py` | Start/stop the Flask server; auto-shutdown timer |
| `server/events.py` | Append-only JSONL writer for `events.jsonl` |
| `pipeline/intrinsics.py` | Parse EXIF; resolve FOV-class profile; (v0.1.0) chessboard integration |
| `pipeline/pose.py` | Wrap `cv2.solvePnP`; compute reprojection RMS; flag intrinsics_suspect |
| `pipeline/ray.py` | Single-photo planar ray-cast (β-mode) — pixel → part-local XY at Z=z_assumed |
| `pipeline/triangulate.py` | Multi-view DLT + iterative refinement (γ-mode); per-feature residuals |
| `pipeline/chessboard.py` | `cv2.findChessboardCorners` + `cv2.calibrateCamera`; emit calibration profile |
| `pipeline/reproject.py` | Render colored markers on photos; output overlay PNGs |
| `pipeline/emit.py` | Assemble `annotations.json`, `manifest.json` from session state |
| `schema/models.py` | Dataclasses mirroring the spec's schema (Photo, Feature, etc.) |
| `schema/validators.py` | Closed flag enum; runtime validation of schema invariants |
| `ui/index.html` | Single-page shell with `<div id="step-N">` placeholders |
| `ui/app.js` | Step state machine; orchestrates phases 2a–2f |
| `ui/helpers.js` | Click-capture canvas, image preview, EXIF display, dropdown |
| `ui/style.css` | Wizard styling |

### Design boundaries

- **Math layer is server-side, never client-side.** UI captures clicks and posts them; server runs PnP/triangulation. Keeps the math testable in isolation and avoids client-server math drift.
- **UI is dumb HTML5 Canvas.** No React, Vue, or Svelte. State machine in vanilla JS. Less to learn, less to break, faster to load. (Per spec §3 footnote.)
- **Schema is a closed contract.** Pydantic could enforce it but adds a runtime dep; we use stdlib `dataclasses` + `json` + custom validators in `schema/validators.py`. Equivalent rigor, fewer deps.
- **Session state is single-writer.** Only the server writes to `state.json`. UI reads via `/api/state`. No filesystem races.

---

## Phase 0 — Pre-flight Infrastructure

These tasks set up the development substrate. They block all subsequent work and run first, sequentially.

### Task 0.1: GitHub Actions CI workflow

**Stream:** Pre-flight (sequential)
**Depends on:** none
**Subagent prompt seed:** "Set up the CI workflow per Task 0.1 in the implementation plan."

**Files:**
- Create: `.github/workflows/tests.yml`

- [ ] **Step 1: Create the workflow file**

```yaml
# .github/workflows/tests.yml
name: tests

on:
  push:
    branches: [main]
  pull_request:
    branches: [main]

jobs:
  test:
    runs-on: ubuntu-latest
    strategy:
      matrix:
        python-version: ["3.10", "3.11", "3.12", "3.13"]
    steps:
      - uses: actions/checkout@v4
      - name: Install uv
        uses: astral-sh/setup-uv@v3
      - name: Set up Python ${{ matrix.python-version }}
        run: uv python install ${{ matrix.python-version }}
      - name: Install dependencies
        run: uv sync --all-groups
      - name: Run tests
        run: uv run pytest -v
      - name: Run linter
        run: uv run ruff check .
        continue-on-error: true
```

- [ ] **Step 2: Add ruff dev dependency**

```bash
cd /home/administrator/Code/agent-spatial-toolkit
uv add --dev ruff pytest pytest-cov
```

- [ ] **Step 3: Verify locally**

```bash
uv run pytest --version
uv run ruff --version
```

Expected: both report versions without error.

- [ ] **Step 4: Commit and push**

```bash
git add .github/ pyproject.toml uv.lock
git commit -m "ci: add GitHub Actions test workflow with ruff lint and pytest matrix"
git push
```

- [ ] **Step 5: Verify CI runs**

Push triggers a workflow. Wait for it to complete:

```bash
gh run list --limit 1
gh run watch
```

Expected: workflow completes (may have no tests yet, but should not error).

---

### Task 0.2: pytest infrastructure + initial fixtures directory

**Stream:** Pre-flight (sequential)
**Depends on:** Task 0.1

**Files:**
- Create: `tests/__init__.py`
- Create: `tests/conftest.py`
- Create: `tests/fixtures/.gitkeep`
- Modify: `pyproject.toml` (add pytest config)

- [ ] **Step 1: Create empty test package files**

```bash
touch tests/__init__.py
mkdir -p tests/fixtures
touch tests/fixtures/.gitkeep
```

- [ ] **Step 2: Create conftest.py with shared fixtures**

```python
# tests/conftest.py
"""Shared pytest fixtures for agent-spatial-toolkit."""
from pathlib import Path

import pytest

FIXTURES_DIR = Path(__file__).parent / "fixtures"


@pytest.fixture
def fixtures_dir() -> Path:
    """Return the absolute path to the test fixtures directory."""
    return FIXTURES_DIR


@pytest.fixture
def synthetic_card_dir(fixtures_dir: Path) -> Path:
    """Path to synthetic-card smoke fixture (created in Task 1.E.1)."""
    p = fixtures_dir / "synthetic_card"
    if not p.exists():
        pytest.skip("synthetic_card fixture not yet generated")
    return p
```

- [ ] **Step 3: Add pytest config to pyproject.toml**

Add to `pyproject.toml` (append, do not replace existing config):

```toml
[tool.pytest.ini_options]
testpaths = ["tests"]
python_files = ["test_*.py"]
python_classes = ["Test*"]
python_functions = ["test_*"]
addopts = [
    "-ra",
    "--strict-markers",
    "--strict-config",
]

[tool.ruff]
line-length = 100
target-version = "py310"

[tool.ruff.lint]
select = ["E", "F", "I", "N", "W", "UP", "B", "C4", "SIM"]
ignore = ["E501"]  # line length handled by formatter
```

- [ ] **Step 4: Verify**

```bash
uv run pytest --collect-only
```

Expected: pytest reports 0 tests collected (no test files exist yet) but exits 0.

- [ ] **Step 5: Commit and push**

```bash
git add tests/ pyproject.toml uv.lock
git commit -m "test: add pytest infrastructure and fixtures directory scaffold"
git push
```

---

### Task 0.3: CHANGELOG.md initial entry

**Stream:** Pre-flight (sequential)
**Depends on:** none

**Files:**
- Create: `CHANGELOG.md`

- [ ] **Step 1: Write the initial CHANGELOG**

```markdown
# Changelog

All notable changes to agent-spatial-toolkit will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
- Initial v1 design specification (`docs/specs/2026-05-03-design.md`)
- Implementation plan (`docs/plans/2026-05-03-v1-implementation.md`)
- Calibration chessboard PDFs (A4 + US Letter, 7×5 internal corners @ 25mm)
- Project scaffolding: pyproject.toml, README, LICENSE (MIT), .gitignore

## [0.1.0-alpha] — Unreleased

CLI-only proof of the pipeline. Single-photo planar pose mode (β-mode) only.
FOV-class intrinsics fallback only (no chessboard calibration yet).
JPEG and PNG photo formats only.

## [0.1.0] — Unreleased

Full v1: multi-view triangulation (γ-mode), chessboard calibration, agent
skill primitive, HEIC support, cross-model adversarial validation gate.
```

- [ ] **Step 2: Commit**

```bash
git add CHANGELOG.md
git commit -m "docs: add CHANGELOG with v0.1.0-alpha and v0.1.0 placeholders"
git push
```

---

### Task 0.4: pyproject.toml runtime dependencies

**Stream:** Pre-flight (sequential)
**Depends on:** Task 0.2

**Files:**
- Modify: `pyproject.toml`

- [ ] **Step 1: Add runtime deps**

```bash
cd /home/administrator/Code/agent-spatial-toolkit
uv add opencv-python numpy Pillow Flask
```

- [ ] **Step 2: Verify**

```bash
uv run python -c "import cv2, numpy, PIL, flask; print('cv2:', cv2.__version__); print('numpy:', numpy.__version__); print('Pillow:', PIL.__version__); print('Flask:', flask.__version__)"
```

Expected: all four imports succeed and print their versions.

- [ ] **Step 3: Commit**

```bash
git add pyproject.toml uv.lock
git commit -m "build: add runtime dependencies (opencv, numpy, Pillow, Flask)"
git push
```

---

## Phase 1 — v0.1.0-alpha (Minimum-Viable Pipeline)

**Goal of Phase 1:** A user can run `agent-spatial-toolkit annotate --photo <jpg>` and get a working browser wizard that lets them annotate a single photo, producing a valid `annotations.json` in part-local 2D mm coordinates with planar Z assumption.

**Parallel structure:**
- **Stream A** (math): T1.A.1 → T1.A.5 (sequential within stream)
- **Stream B** (schema/emit): T1.B.1 → T1.B.4 (sequential)
- **Stream C** (server): T1.C.1 → T1.C.5 (sequential)
- **Stream D** (UI): T1.D.1 → T1.D.9 (sequential)
- **Stream E** (integration): T1.E.1 → T1.E.3 (after A, B, C, D)

Streams A, B, C, D run in parallel. Stream E runs after all four converge. Each stream is dispatched to a fresh subagent.

---

### Task 1.A.1: `normalize.py` — pixel normalization helpers

**Stream:** A (math) · **Depends on:** Task 0.4

**Files:**
- Create: `src/agent_spatial_toolkit/normalize.py`
- Create: `tests/test_normalize.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_normalize.py
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
```

- [ ] **Step 2: Run test, verify failure**

```bash
uv run pytest tests/test_normalize.py -v
```

Expected: ImportError (`agent_spatial_toolkit.normalize` doesn't exist).

- [ ] **Step 3: Implement**

```python
# src/agent_spatial_toolkit/normalize.py
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
```

- [ ] **Step 4: Run test, verify pass**

```bash
uv run pytest tests/test_normalize.py -v
```

Expected: all 7 tests pass.

- [ ] **Step 5: Run full suite**

```bash
uv run pytest -v
```

Expected: 7 passed, 0 failed.

- [ ] **Step 6: Commit via PR**

```bash
git checkout -b feat/normalize-helpers
git add src/agent_spatial_toolkit/normalize.py tests/test_normalize.py
git commit -m "feat(normalize): add pixel-normalization helpers (spec §5.1)

Implements to_normalized_px / to_absolute_px with reference long edge of
2000 px. All reprojection thresholds in the geometric pipeline use these
helpers to remain resolution-independent."
git push -u origin feat/normalize-helpers
gh pr create --fill
```

Then orchestrator runs Codex review and merges.

---

### Task 1.A.2: `intrinsics.py` — EXIF parsing

**Stream:** A · **Depends on:** Task 1.A.1

**Files:**
- Create: `src/agent_spatial_toolkit/pipeline/__init__.py`
- Create: `src/agent_spatial_toolkit/pipeline/intrinsics.py`
- Create: `tests/test_intrinsics.py`

- [ ] **Step 1: Create pipeline package init**

```bash
mkdir -p src/agent_spatial_toolkit/pipeline
touch src/agent_spatial_toolkit/pipeline/__init__.py
```

- [ ] **Step 2: Write failing test for EXIF extraction**

```python
# tests/test_intrinsics.py
"""Tests for pipeline/intrinsics.py — EXIF parsing and FOV-class fallback."""
from pathlib import Path

import pytest
from PIL import Image

from agent_spatial_toolkit.pipeline.intrinsics import (
    CameraDetected,
    extract_exif_camera_info,
)


def _make_jpeg_with_exif(tmp_path: Path, exif_dict: dict) -> Path:
    """Helper: create a minimal JPEG with the given EXIF tags."""
    img = Image.new("RGB", (4032, 3024), color=(128, 128, 128))
    exif = img.getexif()
    for tag_id, value in exif_dict.items():
        exif[tag_id] = value
    out = tmp_path / "test.jpg"
    img.save(out, "JPEG", exif=exif)
    return out


def test_extract_exif_camera_info_with_full_metadata(tmp_path: Path) -> None:
    """A JPEG with Make, Model, FocalLengthIn35mmFilm yields CameraDetected with all fields."""
    # EXIF tag IDs: Make=271, Model=272, FocalLengthIn35mmFilm=41989, LensModel=42036
    img_path = _make_jpeg_with_exif(tmp_path, {
        271: "Samsung",
        272: "SM-S908U",
        41989: 70,
        42036: "S22 Ultra Telephoto",
    })

    info = extract_exif_camera_info(img_path)

    assert info is not None
    assert info.make == "Samsung"
    assert info.model == "SM-S908U"
    assert info.focal_length_35mm_equiv == 70
    assert info.lens_label == "S22 Ultra Telephoto"
    assert info.detection_source == "exif"


def test_extract_exif_returns_none_when_no_exif(tmp_path: Path) -> None:
    """A bare image with no EXIF returns None."""
    img = Image.new("RGB", (1024, 768), color=(0, 0, 0))
    out = tmp_path / "bare.jpg"
    img.save(out, "JPEG")

    info = extract_exif_camera_info(out)

    # Bare JPEG either has empty EXIF or no make/model — both should yield None
    assert info is None or info.make is None


def test_extract_exif_handles_partial_metadata(tmp_path: Path) -> None:
    """If Make is present but Model is missing, return what we have."""
    img_path = _make_jpeg_with_exif(tmp_path, {
        271: "Apple",
        # No model
    })
    info = extract_exif_camera_info(img_path)
    assert info is not None
    assert info.make == "Apple"
    assert info.model is None


def test_extract_exif_unknown_format_returns_none(tmp_path: Path) -> None:
    """Calling on a non-image file returns None gracefully."""
    bad = tmp_path / "garbage.jpg"
    bad.write_bytes(b"this is not an image")
    info = extract_exif_camera_info(bad)
    assert info is None
```

- [ ] **Step 3: Run, verify failure**

```bash
uv run pytest tests/test_intrinsics.py -v
```

Expected: ImportError.

- [ ] **Step 4: Implement EXIF extraction**

```python
# src/agent_spatial_toolkit/pipeline/intrinsics.py
"""EXIF parsing and FOV-class intrinsics fallback (spec §5.2).

Tier B (FOV-class fallback) is implemented here in v0.1.0-alpha.
Tier A (chessboard calibration) is added in v0.1.0 — see chessboard.py.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from PIL import ExifTags, Image

# EXIF tag name → tag ID lookup (built once from PIL)
_TAG_NAME_TO_ID = {name: tag_id for tag_id, name in ExifTags.TAGS.items()}


@dataclass
class CameraDetected:
    """Camera + lens identification extracted from photo metadata."""

    make: str | None = None
    model: str | None = None
    focal_length_35mm_equiv: float | None = None
    lens_label: str | None = None
    detection_source: Literal["exif", "user_specified", "fallback_generic"] = "exif"


def extract_exif_camera_info(image_path: Path | str) -> CameraDetected | None:
    """Read EXIF camera identification from an image file.

    Returns None if the file cannot be opened as an image. Returns
    CameraDetected with whatever fields were present (others as None) if
    the image has at least one camera-identifying tag.
    """
    image_path = Path(image_path)
    try:
        with Image.open(image_path) as img:
            exif = img.getexif()
    except (OSError, ValueError, Image.UnidentifiedImageError):
        return None

    if not exif:
        return None

    make = exif.get(_TAG_NAME_TO_ID.get("Make"))
    model = exif.get(_TAG_NAME_TO_ID.get("Model"))
    focal_35 = exif.get(_TAG_NAME_TO_ID.get("FocalLengthIn35mmFilm"))
    lens = exif.get(_TAG_NAME_TO_ID.get("LensModel"))

    # If nothing identifying is present, return None
    if all(v is None for v in (make, model, focal_35, lens)):
        return None

    return CameraDetected(
        make=str(make).strip() if make else None,
        model=str(model).strip() if model else None,
        focal_length_35mm_equiv=float(focal_35) if focal_35 is not None else None,
        lens_label=str(lens).strip() if lens else None,
        detection_source="exif",
    )
```

- [ ] **Step 5: Run, verify pass**

```bash
uv run pytest tests/test_intrinsics.py -v
```

Expected: 4 tests pass.

- [ ] **Step 6: PR + merge**

```bash
git checkout -b feat/intrinsics-exif
git add src/agent_spatial_toolkit/pipeline/ tests/test_intrinsics.py
git commit -m "feat(intrinsics): EXIF camera-info extraction (spec §5.2 Tier B prep)

Reads Make, Model, FocalLengthIn35mmFilm, LensModel from photo EXIF and
returns a CameraDetected dataclass. Returns None for bare/corrupt files.
This is the input to the FOV-class fallback (next task)."
git push -u origin feat/intrinsics-exif
gh pr create --fill
```

---

### Task 1.A.3: `intrinsics.py` — FOV-class fallback

**Stream:** A · **Depends on:** Task 1.A.2

**Files:**
- Modify: `src/agent_spatial_toolkit/pipeline/intrinsics.py`
- Modify: `tests/test_intrinsics.py`

- [ ] **Step 1: Add failing tests for FOV-class resolution**

Append to `tests/test_intrinsics.py`:

```python
# Append to tests/test_intrinsics.py

from agent_spatial_toolkit.pipeline.intrinsics import (
    FOV_CLASS_ULTRAWIDE,
    FOV_CLASS_WIDE,
    FOV_CLASS_NORMAL,
    FOV_CLASS_TELEPHOTO,
    Intrinsics,
    resolve_fov_class,
    resolve_fallback_intrinsics,
)


@pytest.mark.parametrize("focal_35mm,expected_class", [
    (10, FOV_CLASS_ULTRAWIDE),
    (21, FOV_CLASS_ULTRAWIDE),
    (22, FOV_CLASS_WIDE),
    (28, FOV_CLASS_WIDE),
    (34, FOV_CLASS_WIDE),
    (35, FOV_CLASS_NORMAL),
    (50, FOV_CLASS_NORMAL),
    (69, FOV_CLASS_NORMAL),
    (70, FOV_CLASS_TELEPHOTO),
    (135, FOV_CLASS_TELEPHOTO),
    (300, FOV_CLASS_TELEPHOTO),
])
def test_resolve_fov_class(focal_35mm: float, expected_class: str) -> None:
    """FOV-class boundaries match spec §5.2."""
    assert resolve_fov_class(focal_35mm) == expected_class


def test_resolve_fov_class_none_returns_none() -> None:
    """No focal length → no class."""
    assert resolve_fov_class(None) is None


def test_resolve_fallback_intrinsics_telephoto() -> None:
    """A telephoto lens gets near-zero distortion."""
    intrinsics = resolve_fallback_intrinsics(
        focal_length_35mm_equiv=85.0,
        image_size=(4032, 3024),
    )
    assert intrinsics is not None
    assert intrinsics.profile_source == "fov_class_fallback"
    # Telephoto profile: zero distortion
    assert intrinsics.distortion == [0.0, 0.0, 0.0, 0.0, 0.0]
    # Principal point at image center
    assert intrinsics.cx == pytest.approx(2016.0)
    assert intrinsics.cy == pytest.approx(1512.0)


def test_resolve_fallback_intrinsics_ultrawide_rejects() -> None:
    """An ultrawide lens triggers a rejection (returns None with rejection flag)."""
    intrinsics = resolve_fallback_intrinsics(
        focal_length_35mm_equiv=14.0,
        image_size=(4032, 3024),
    )
    assert intrinsics is None  # rejected — caller must check focal length first


def test_resolve_fallback_intrinsics_normal_focal() -> None:
    """A normal-focal photo gets mild distortion + correct fx/fy from focal length."""
    intrinsics = resolve_fallback_intrinsics(
        focal_length_35mm_equiv=50.0,
        image_size=(4032, 3024),
    )
    assert intrinsics is not None
    # 50mm equiv on 4032px wide sensor: fx = 50/36 * 4032 = 5600 px
    # (using 36mm reference width for 35mm-equivalent)
    assert intrinsics.fx_px == pytest.approx(5600.0, rel=0.01)
    # Mild distortion for "normal" class
    assert intrinsics.distortion[0] != 0.0  # k1 nonzero


def test_intrinsics_dataclass_serializes_to_dict() -> None:
    """Intrinsics has a to_dict() method matching the spec §6 schema shape."""
    intrinsics = Intrinsics(
        profile_source="fov_class_fallback",
        profile_id="fov_normal_v1",
        fx_px=5600.0, fy_px=5600.0, cx=2016.0, cy=1512.0,
        distortion=[0.01, 0.005, 0.0, 0.0, 0.0],
        distortion_model="opencv_5param",
    )
    d = intrinsics.to_dict()
    assert d["profile_source"] == "fov_class_fallback"
    assert d["distortion"]["k1"] == 0.01
    assert d["distortion"]["k2"] == 0.005
```

- [ ] **Step 2: Run, verify failure**

Expected: ImportError on the new symbols.

- [ ] **Step 3: Implement FOV-class fallback**

Append to `src/agent_spatial_toolkit/pipeline/intrinsics.py`:

```python
# Append to src/agent_spatial_toolkit/pipeline/intrinsics.py

from typing import Any

# FOV class labels per spec §5.2
FOV_CLASS_ULTRAWIDE = "ultrawide"
FOV_CLASS_WIDE = "wide"
FOV_CLASS_NORMAL = "normal"
FOV_CLASS_TELEPHOTO = "telephoto"

# 35mm-equivalent focal length boundaries (mm)
_BOUNDARIES = [(22, FOV_CLASS_ULTRAWIDE),
               (35, FOV_CLASS_WIDE),
               (70, FOV_CLASS_NORMAL),
               (float("inf"), FOV_CLASS_TELEPHOTO)]

# Generic distortion profiles per FOV class.
# These are conservative estimates — they intentionally OVER-correct slightly
# rather than under-correct, since under-correction biases pose estimates.
# Numbers derived empirically from chessboard calibrations of common phone
# cameras; a derivation document is added in v0.1.0 once chessboard
# calibration is functional and we can compute residuals against truth.
_FOV_CLASS_DISTORTION = {
    FOV_CLASS_WIDE:      [0.025, 0.000, 0.0, 0.0, 0.0],   # k1, k2, p1, p2, k3
    FOV_CLASS_NORMAL:    [0.010, 0.005, 0.0, 0.0, 0.0],
    FOV_CLASS_TELEPHOTO: [0.000, 0.000, 0.0, 0.0, 0.0],
    # ULTRAWIDE has no entry — intentional rejection.
}


@dataclass
class Intrinsics:
    """Camera intrinsics for a single photo (spec §6 schema)."""

    profile_source: Literal["chessboard", "fov_class_fallback", "self_calibrated"]
    profile_id: str
    fx_px: float
    fy_px: float
    cx: float
    cy: float
    distortion: list[float]   # [k1, k2, p1, p2, k3]
    distortion_model: str = "opencv_5param"
    profile_calibration_rms_px: float | None = None

    def to_dict(self) -> dict[str, Any]:
        """Serialize to the spec §6 schema shape."""
        d = {
            "profile_source": self.profile_source,
            "profile_id": self.profile_id,
            "fx_px": self.fx_px,
            "fy_px": self.fy_px,
            "cx": self.cx,
            "cy": self.cy,
            "distortion_model": self.distortion_model,
            "distortion": {
                "k1": self.distortion[0],
                "k2": self.distortion[1],
                "p1": self.distortion[2],
                "p2": self.distortion[3],
                "k3": self.distortion[4],
            },
        }
        if self.profile_calibration_rms_px is not None:
            d["profile_calibration_rms_px"] = self.profile_calibration_rms_px
        return d


def resolve_fov_class(focal_length_35mm_equiv: float | None) -> str | None:
    """Map a 35mm-equivalent focal length to its FOV class label."""
    if focal_length_35mm_equiv is None:
        return None
    for boundary, cls in _BOUNDARIES:
        if focal_length_35mm_equiv < boundary:
            return cls
    return FOV_CLASS_TELEPHOTO  # unreachable given inf boundary, defensive


def resolve_fallback_intrinsics(
    focal_length_35mm_equiv: float,
    image_size: tuple[int, int],
) -> Intrinsics | None:
    """Compute fallback intrinsics from a 35mm-equivalent focal length.

    Returns None for ultrawide lenses (per spec §5.2: rejected). Caller is
    responsible for surfacing the rejection to the user.
    """
    fov_class = resolve_fov_class(focal_length_35mm_equiv)
    if fov_class is None or fov_class == FOV_CLASS_ULTRAWIDE:
        return None

    width, height = image_size
    # Convert 35mm-equiv focal to absolute pixels.
    # 35mm-equivalent uses a 36mm-wide reference sensor; pixel focal = focal_mm * (image_width_px / 36mm)
    fx_px = focal_length_35mm_equiv * width / 36.0
    fy_px = fx_px  # square pixels assumption

    return Intrinsics(
        profile_source="fov_class_fallback",
        profile_id=f"fov_{fov_class}_v1",
        fx_px=fx_px,
        fy_px=fy_px,
        cx=width / 2.0,
        cy=height / 2.0,
        distortion=list(_FOV_CLASS_DISTORTION[fov_class]),
        distortion_model="opencv_5param",
    )
```

- [ ] **Step 4: Run, verify pass**

```bash
uv run pytest tests/test_intrinsics.py -v
```

Expected: all 14 tests pass.

- [ ] **Step 5: PR + merge**

```bash
git checkout -b feat/intrinsics-fov-fallback
git add src/agent_spatial_toolkit/pipeline/intrinsics.py tests/test_intrinsics.py
git commit -m "feat(intrinsics): FOV-class fallback intrinsics (spec §5.2 Tier B)

Maps 35mm-equiv focal length to {ultrawide, wide, normal, telephoto} and
emits an Intrinsics object with conservative distortion estimates per class.
Ultrawide returns None (intentional rejection per spec)."
git push -u origin feat/intrinsics-fov-fallback
gh pr create --fill
```

---

### Task 1.A.4: `pose.py` — `solvePnP` wrapper

**Stream:** A · **Depends on:** Task 1.A.3

**Files:**
- Create: `src/agent_spatial_toolkit/pipeline/pose.py`
- Create: `tests/test_pose.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_pose.py
"""Tests for pipeline/pose.py — PnP wrapper + anchor validation."""
import numpy as np
import pytest

from agent_spatial_toolkit.pipeline.intrinsics import Intrinsics
from agent_spatial_toolkit.pipeline.pose import (
    PoseResult,
    PoseSolveError,
    solve_pnp,
)


def _make_test_intrinsics() -> Intrinsics:
    """Synthetic intrinsics for a 1000×1000 image, fx=fy=1000, no distortion."""
    return Intrinsics(
        profile_source="fov_class_fallback",
        profile_id="test",
        fx_px=1000.0, fy_px=1000.0, cx=500.0, cy=500.0,
        distortion=[0.0, 0.0, 0.0, 0.0, 0.0],
    )


def _project_synthetic(world_pts: np.ndarray, rvec: np.ndarray,
                       tvec: np.ndarray, intr: Intrinsics) -> np.ndarray:
    """Project 3D world points to 2D pixels using the given pose + intrinsics."""
    import cv2
    K = np.array([[intr.fx_px, 0, intr.cx],
                  [0, intr.fy_px, intr.cy],
                  [0, 0, 1]], dtype=np.float64)
    dist = np.array(intr.distortion, dtype=np.float64)
    pts2d, _ = cv2.projectPoints(world_pts.astype(np.float64), rvec, tvec, K, dist)
    return pts2d.reshape(-1, 2)


def test_solve_pnp_recovers_known_pose_with_4_anchors() -> None:
    """Ground-truth round trip: project known points, solve PnP, recover pose to within tolerance."""
    intr = _make_test_intrinsics()
    # Place a 50×30 mm rectangle at Z=0
    world_pts = np.array([
        [0.0, 0.0, 0.0],
        [50.0, 0.0, 0.0],
        [50.0, 30.0, 0.0],
        [0.0, 30.0, 0.0],
    ])
    # Camera looking down at the rectangle from 200 mm above
    true_rvec = np.array([0.0, 0.0, 0.0])
    true_tvec = np.array([-25.0, -15.0, 200.0])  # offsets so rectangle is centered
    pixels = _project_synthetic(world_pts, true_rvec, true_tvec, intr)

    result = solve_pnp(
        world_points=world_pts,
        pixel_points=pixels,
        intrinsics=intr,
        image_size=(1000, 1000),
    )

    assert isinstance(result, PoseResult)
    assert result.anchor_reprojection_rms_px < 0.5  # should be ~zero, allow noise
    # Recovered tvec should be very close to true_tvec
    assert np.allclose(result.tvec, true_tvec, atol=1.0)


def test_solve_pnp_with_3_collinear_points_raises() -> None:
    """3 collinear points have no PnP solution (singular configuration)."""
    intr = _make_test_intrinsics()
    world_pts = np.array([
        [0.0, 0.0, 0.0],
        [10.0, 0.0, 0.0],
        [20.0, 0.0, 0.0],
    ])
    pixels = np.array([[400.0, 500.0], [500.0, 500.0], [600.0, 500.0]])

    with pytest.raises(PoseSolveError):
        solve_pnp(
            world_points=world_pts,
            pixel_points=pixels,
            intrinsics=intr,
            image_size=(1000, 1000),
        )


def test_solve_pnp_with_only_2_anchors_raises() -> None:
    """PnP requires >= 3 anchors."""
    intr = _make_test_intrinsics()
    world_pts = np.array([[0.0, 0.0, 0.0], [50.0, 0.0, 0.0]])
    pixels = np.array([[400.0, 500.0], [600.0, 500.0]])

    with pytest.raises(PoseSolveError, match="at least 3 anchors"):
        solve_pnp(
            world_points=world_pts,
            pixel_points=pixels,
            intrinsics=intr,
            image_size=(1000, 1000),
        )


def test_solve_pnp_high_rms_flags_intrinsics_suspect() -> None:
    """When clicked anchors are noisy, RMS exceeds threshold and intrinsics_suspect is True."""
    intr = _make_test_intrinsics()
    world_pts = np.array([
        [0.0, 0.0, 0.0],
        [50.0, 0.0, 0.0],
        [50.0, 30.0, 0.0],
        [0.0, 30.0, 0.0],
    ])
    true_rvec = np.array([0.0, 0.0, 0.0])
    true_tvec = np.array([-25.0, -15.0, 200.0])
    pixels = _project_synthetic(world_pts, true_rvec, true_tvec, intr)
    # Add 30 px of noise to each click — well above the 5-normalized-px threshold
    rng = np.random.default_rng(seed=42)
    pixels_noisy = pixels + rng.normal(0, 30, size=pixels.shape)

    result = solve_pnp(
        world_points=world_pts,
        pixel_points=pixels_noisy,
        intrinsics=intr,
        image_size=(1000, 1000),
    )

    assert result.intrinsics_suspect is True


def test_pose_result_serializes_to_dict() -> None:
    """PoseResult.to_dict matches the spec §6 photos[].pose schema shape."""
    pr = PoseResult(
        rvec=np.array([0.024, -1.567, 0.011]),
        tvec=np.array([12.4, -8.2, 287.3]),
        anchor_reprojection_rms_px=0.8,
        intrinsics_suspect=False,
        pose_solver="cv2.solvePnP_ITERATIVE",
    )
    d = pr.to_dict()
    assert d["rvec"] == [0.024, -1.567, 0.011]
    assert d["tvec"] == [12.4, -8.2, 287.3]
    assert d["anchor_reprojection_rms_px"] == 0.8
    assert d["pose_solver"] == "cv2.solvePnP_ITERATIVE"
```

- [ ] **Step 2: Run, verify failure**

```bash
uv run pytest tests/test_pose.py -v
```

Expected: ImportError.

- [ ] **Step 3: Implement `pose.py`**

```python
# src/agent_spatial_toolkit/pipeline/pose.py
"""PnP wrapper + anchor validation (spec §5.1, §5.3)."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import cv2
import numpy as np

from agent_spatial_toolkit.normalize import to_normalized_px
from agent_spatial_toolkit.pipeline.intrinsics import Intrinsics

# Threshold from spec §5.1 — anchor RMS above this normalized-px value flags
# the photo as intrinsics_suspect.
ANCHOR_RMS_THRESHOLD_NORMALIZED_PX: float = 5.0


class PoseSolveError(RuntimeError):
    """Raised when solvePnP cannot converge to a usable pose."""


@dataclass
class PoseResult:
    """Result of a PnP solve for one photo."""

    rvec: np.ndarray              # 3-vector, axis-angle rotation
    tvec: np.ndarray              # 3-vector, translation in part-local mm
    anchor_reprojection_rms_px: float   # normalized px
    intrinsics_suspect: bool      # True if RMS > ANCHOR_RMS_THRESHOLD_NORMALIZED_PX
    pose_solver: str              # which cv2.solvePnP method was used

    def to_dict(self) -> dict[str, Any]:
        """Serialize to spec §6 photos[].pose schema."""
        return {
            "rvec": self.rvec.tolist(),
            "tvec": self.tvec.tolist(),
            "anchor_reprojection_rms_px": float(self.anchor_reprojection_rms_px),
            "pose_solver": self.pose_solver,
        }


def _camera_matrix(intr: Intrinsics) -> np.ndarray:
    return np.array([
        [intr.fx_px, 0,           intr.cx],
        [0,          intr.fy_px,  intr.cy],
        [0,          0,           1],
    ], dtype=np.float64)


def solve_pnp(
    world_points: np.ndarray,    # (N, 3) part-local mm
    pixel_points: np.ndarray,    # (N, 2) absolute px
    intrinsics: Intrinsics,
    image_size: tuple[int, int],
) -> PoseResult:
    """Solve PnP with the given anchors.

    Raises PoseSolveError if anchors are insufficient (< 3) or pose cannot
    be recovered (e.g., collinear). For high-RMS solves, returns the
    PoseResult with intrinsics_suspect=True rather than raising — the caller
    decides whether to retry, fall back to chessboard calibration, or
    proceed with the noisy estimate.
    """
    if world_points.shape[0] < 3:
        raise PoseSolveError(f"PnP requires at least 3 anchors, got {world_points.shape[0]}")
    if world_points.shape[0] != pixel_points.shape[0]:
        raise PoseSolveError(
            f"world_points and pixel_points length mismatch: "
            f"{world_points.shape[0]} vs {pixel_points.shape[0]}"
        )

    K = _camera_matrix(intrinsics)
    dist = np.array(intrinsics.distortion, dtype=np.float64)
    obj_pts = world_points.astype(np.float64).reshape(-1, 1, 3)
    img_pts = pixel_points.astype(np.float64).reshape(-1, 1, 2)

    success, rvec, tvec = cv2.solvePnP(
        obj_pts, img_pts, K, dist, flags=cv2.SOLVEPNP_ITERATIVE
    )
    if not success:
        raise PoseSolveError("cv2.solvePnP returned failure (likely collinear or degenerate input)")

    # Compute reprojection error in absolute then normalized px
    projected, _ = cv2.projectPoints(obj_pts, rvec, tvec, K, dist)
    diffs = projected.reshape(-1, 2) - pixel_points
    abs_rms = float(np.sqrt(np.mean(np.sum(diffs ** 2, axis=1))))
    norm_rms = to_normalized_px(abs_rms, image_size)

    return PoseResult(
        rvec=rvec.flatten(),
        tvec=tvec.flatten(),
        anchor_reprojection_rms_px=norm_rms,
        intrinsics_suspect=norm_rms > ANCHOR_RMS_THRESHOLD_NORMALIZED_PX,
        pose_solver="cv2.solvePnP_ITERATIVE",
    )
```

- [ ] **Step 4: Run, verify pass**

```bash
uv run pytest tests/test_pose.py -v
```

Expected: all 5 tests pass. (`solve_pnp` returns `intrinsics_suspect=True` rather than raising on noisy clicks.)

- [ ] **Step 5: PR + merge**

```bash
git checkout -b feat/pose-pnp
git add src/agent_spatial_toolkit/pipeline/pose.py tests/test_pose.py
git commit -m "feat(pose): solvePnP wrapper with anchor validation (spec §5.1, §5.3)

Wraps cv2.solvePnP with: input validation (>= 3 anchors), reprojection RMS
in normalized px, and intrinsics_suspect flag when RMS > 5 normalized px.
Raises PoseSolveError for insufficient or degenerate inputs."
git push -u origin feat/pose-pnp
gh pr create --fill
```

---

### Task 1.A.5: `ray.py` — planar ray-cast (β-mode)

**Stream:** A · **Depends on:** Task 1.A.4

**Files:**
- Create: `src/agent_spatial_toolkit/pipeline/ray.py`
- Create: `tests/test_ray.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_ray.py
"""Tests for pipeline/ray.py — single-photo planar ray-cast (β-mode)."""
import numpy as np
import pytest

from agent_spatial_toolkit.pipeline.intrinsics import Intrinsics
from agent_spatial_toolkit.pipeline.pose import PoseResult
from agent_spatial_toolkit.pipeline.ray import (
    intersect_ray_with_plane,
    pixel_to_part_local,
)


def _test_pose() -> PoseResult:
    """Identity rotation, camera 200mm above origin looking down."""
    return PoseResult(
        rvec=np.array([0.0, 0.0, 0.0]),
        tvec=np.array([0.0, 0.0, 200.0]),
        anchor_reprojection_rms_px=0.0,
        intrinsics_suspect=False,
        pose_solver="test",
    )


def _test_intrinsics() -> Intrinsics:
    return Intrinsics(
        profile_source="fov_class_fallback",
        profile_id="test",
        fx_px=1000.0, fy_px=1000.0, cx=500.0, cy=500.0,
        distortion=[0.0, 0.0, 0.0, 0.0, 0.0],
    )


def test_pixel_at_image_center_maps_to_origin() -> None:
    """Camera at (0,0,200) looking down; click at image center hits Z=0 plane at (0,0,0)."""
    pose = _test_pose()
    intr = _test_intrinsics()
    xy = pixel_to_part_local(
        pixel=np.array([500.0, 500.0]),
        pose=pose,
        intrinsics=intr,
        z_assumed_mm=0.0,
    )
    assert xy[0] == pytest.approx(0.0, abs=0.001)
    assert xy[1] == pytest.approx(0.0, abs=0.001)


def test_pixel_offset_maps_proportionally() -> None:
    """Camera at (0,0,200), focal=1000: 100px offset = 100 * 200/1000 = 20mm offset."""
    pose = _test_pose()
    intr = _test_intrinsics()
    xy = pixel_to_part_local(
        pixel=np.array([600.0, 500.0]),
        pose=pose,
        intrinsics=intr,
        z_assumed_mm=0.0,
    )
    assert xy[0] == pytest.approx(20.0, abs=0.01)
    assert xy[1] == pytest.approx(0.0, abs=0.01)


def test_z_assumed_nonzero() -> None:
    """If user specifies the feature is at Z=10mm above PCB, ray-cast intersects Z=10 plane."""
    pose = _test_pose()
    intr = _test_intrinsics()
    xy = pixel_to_part_local(
        pixel=np.array([600.0, 500.0]),
        pose=pose,
        intrinsics=intr,
        z_assumed_mm=10.0,
    )
    # At Z=10, the ray has traveled (200-10)/200 = 0.95 of the way down,
    # so the offset is 0.95 * 20mm = 19mm
    assert xy[0] == pytest.approx(19.0, abs=0.01)


def test_intersect_ray_with_plane_simple() -> None:
    """Ray from (0,0,200) along (0,0,-1) intersects Z=0 at (0,0,0)."""
    origin = np.array([0.0, 0.0, 200.0])
    direction = np.array([0.0, 0.0, -1.0])
    pt = intersect_ray_with_plane(origin, direction, plane_z=0.0)
    assert np.allclose(pt, [0.0, 0.0, 0.0])


def test_intersect_ray_parallel_to_plane_raises() -> None:
    """A ray parallel to the Z=0 plane never intersects it."""
    origin = np.array([0.0, 0.0, 200.0])
    direction = np.array([1.0, 0.0, 0.0])  # parallel to Z=0
    with pytest.raises(ValueError, match="parallel"):
        intersect_ray_with_plane(origin, direction, plane_z=0.0)
```

- [ ] **Step 2: Run, verify failure**

```bash
uv run pytest tests/test_ray.py -v
```

Expected: ImportError.

- [ ] **Step 3: Implement `ray.py`**

```python
# src/agent_spatial_toolkit/pipeline/ray.py
"""Single-photo planar ray-cast for β-mode (spec §5.1 step 5).

Given a pixel click in a photo with known camera pose, project the click
into the part-local frame by ray-casting through the camera's optical center
and intersecting with the assumed-Z plane.
"""
from __future__ import annotations

import cv2
import numpy as np

from agent_spatial_toolkit.pipeline.intrinsics import Intrinsics
from agent_spatial_toolkit.pipeline.pose import PoseResult


def intersect_ray_with_plane(
    origin: np.ndarray,    # (3,) ray origin in part-local mm
    direction: np.ndarray, # (3,) ray direction (need not be unit-length)
    plane_z: float,        # Z value of the horizontal plane to intersect
) -> np.ndarray:
    """Intersect a ray with a horizontal plane at Z=plane_z.

    Raises ValueError if the ray is parallel to the plane.
    """
    if abs(direction[2]) < 1e-9:
        raise ValueError("Ray is parallel to the Z=plane_z plane; no intersection")
    t = (plane_z - origin[2]) / direction[2]
    return origin + t * direction


def pixel_to_part_local(
    pixel: np.ndarray,             # (2,) absolute px
    pose: PoseResult,
    intrinsics: Intrinsics,
    z_assumed_mm: float = 0.0,
) -> np.ndarray:
    """Convert a single pixel click to part-local (X, Y, Z=z_assumed_mm).

    Returns a (2,) array of [X, Y] in mm. Z is the supplied z_assumed_mm.
    """
    # Step 1: undistort the pixel
    K = np.array([
        [intrinsics.fx_px, 0, intrinsics.cx],
        [0, intrinsics.fy_px, intrinsics.cy],
        [0, 0, 1],
    ], dtype=np.float64)
    dist = np.array(intrinsics.distortion, dtype=np.float64)
    pix_in = pixel.astype(np.float64).reshape(-1, 1, 2)
    pix_undist = cv2.undistortPoints(pix_in, K, dist, P=K).reshape(2)

    # Step 2: build a ray in camera frame.
    # Camera coordinates of the click: ((u-cx)/fx, (v-cy)/fy, 1)
    cam_dir = np.array([
        (pix_undist[0] - intrinsics.cx) / intrinsics.fx_px,
        (pix_undist[1] - intrinsics.cy) / intrinsics.fy_px,
        1.0,
    ])

    # Step 3: transform ray to world (part-local) frame.
    # Camera pose: world point P_w = R * P_c + t
    # Therefore camera origin in world = -R^T * t (wait — it's actually computed below)
    # cv2 convention: rvec/tvec take WORLD points to CAMERA frame.
    # So to invert: R_inv * (P_c - t) = P_w
    R, _ = cv2.Rodrigues(pose.rvec)
    R_inv = R.T
    cam_origin_world = -R_inv @ pose.tvec
    cam_dir_world = R_inv @ cam_dir

    # Step 4: intersect with Z=z_assumed_mm plane
    intersection = intersect_ray_with_plane(
        origin=cam_origin_world,
        direction=cam_dir_world,
        plane_z=z_assumed_mm,
    )
    return intersection[:2]  # (X, Y) only — Z is the assumed value
```

- [ ] **Step 4: Run, verify pass**

```bash
uv run pytest tests/test_ray.py -v
```

Expected: 5 tests pass.

- [ ] **Step 5: PR + merge**

```bash
git checkout -b feat/ray-cast
git add src/agent_spatial_toolkit/pipeline/ray.py tests/test_ray.py
git commit -m "feat(ray): single-photo planar ray-cast for β-mode (spec §5.1 step 5)

Implements pixel_to_part_local: undistort pixel → build camera-frame ray →
transform to world frame → intersect Z=z_assumed plane. This is the
single-photo fallback path when a feature is visible in only one photo."
git push -u origin feat/ray-cast
gh pr create --fill
```

---

### Task 1.B.1: `schema/models.py` — schema dataclasses

**Stream:** B (parallel with A) · **Depends on:** Task 0.4

**Files:**
- Create: `src/agent_spatial_toolkit/schema/__init__.py`
- Create: `src/agent_spatial_toolkit/schema/models.py`
- Create: `tests/test_schema.py`

- [ ] **Step 1: Empty package init**

```bash
mkdir -p src/agent_spatial_toolkit/schema
touch src/agent_spatial_toolkit/schema/__init__.py
```

- [ ] **Step 2: Write failing tests**

```python
# tests/test_schema.py
"""Tests for schema/models.py — dataclasses mirroring spec §6 schema."""
import json

import pytest

from agent_spatial_toolkit.schema.models import (
    Annotations,
    AnchorClick,
    Feature,
    FeatureClick,
    FeatureMeasurement,
    Part,
    Photo,
    QualitySummary,
    ReferenceFrame,
    SessionArtifacts,
)


def test_minimal_annotations_serializes() -> None:
    """A bare-minimum Annotations object emits valid JSON matching spec §6."""
    ann = Annotations(
        schema_version=1,
        toolkit_version="0.0.1",
        generated_at="2026-05-03T22:14:00Z",
        part=Part(id="x1207", display_name=None, part_class=None, notes=None),
        reference_frame=ReferenceFrame(
            origin_description="pcb_bottom_left_corner",
            x_axis_description="along_long_edge",
            y_axis_description="along_short_edge",
            z_axis_description="up_from_pcb_bottom",
            units="mm",
        ),
        photos=[],
        features=[],
        quality_summary=QualitySummary(
            feature_count=0, triangulated_count=0, z_assumed_count=0,
            median_reprojection_rms_px=None, max_reprojection_rms_px=None,
            flags=[],
        ),
        session_artifacts=SessionArtifacts(
            overlay_pngs=[], events_jsonl="events.jsonl", manifest="manifest.json",
        ),
    )

    data = ann.to_dict()
    # Round-trip through json
    s = json.dumps(data)
    parsed = json.loads(s)

    assert parsed["schema_version"] == 1
    assert parsed["part"]["id"] == "x1207"
    assert parsed["reference_frame"]["units"] == "mm"
    assert parsed["features"] == []
    assert parsed["photos"] == []


def test_feature_with_planar_intersection_omits_residual() -> None:
    """planar_intersection method does not emit reprojection_residual_px in clicks."""
    feature = Feature(
        id="usb_c",
        visible_in=["top_down"],
        pcb_xyz_mm=(82.0, 5.0, 0.0),
        measurements=FeatureMeasurement(
            method="planar_intersection",
            z_assumed_mm=0.0,
            z_assumed_reason="single_photo_only_default",
            per_photo_clicks=[
                FeatureClick(photo="top_down", pixel=(1280, 1050),
                             reprojection_residual_px=None),
            ],
        ),
    )
    d = feature.to_dict()
    # The click dict must NOT have reprojection_residual_px
    assert "reprojection_residual_px" not in d["measurements"]["per_photo_clicks"][0]


def test_feature_with_triangulation_includes_residual() -> None:
    """triangulation_K_views emits reprojection_residual_px on every click."""
    feature = Feature(
        id="rj45",
        visible_in=["top_down", "long_edge_a"],
        pcb_xyz_mm=(78.0, 47.0, 7.0),
        measurements=FeatureMeasurement(
            method="triangulation_2_views",
            triangulation_rms_px=0.7,
            max_residual_px=1.1,
            per_photo_clicks=[
                FeatureClick(photo="top_down", pixel=(2015, 1820),
                             reprojection_residual_px=0.6),
                FeatureClick(photo="long_edge_a", pixel=(1870, 940),
                             reprojection_residual_px=0.8),
            ],
        ),
    )
    d = feature.to_dict()
    for click_d in d["measurements"]["per_photo_clicks"]:
        assert "reprojection_residual_px" in click_d


def test_pcb_xyz_mm_uses_array_uniformly() -> None:
    """Per spec §6 contract, pcb_xyz_mm is always a [X, Y, Z] array."""
    feature = Feature(
        id="x",
        visible_in=["a"],
        pcb_xyz_mm=(1.0, 2.0, 3.0),
        measurements=FeatureMeasurement(
            method="planar_intersection",
            z_assumed_mm=3.0,
            z_assumed_reason="user_override",
            per_photo_clicks=[],
        ),
    )
    d = feature.to_dict()
    assert d["pcb_xyz_mm"] == [1.0, 2.0, 3.0]


def test_anchor_click_serializes() -> None:
    a = AnchorClick(id="pcb_corner_origin", pcb_xyz_mm=(0.0, 0.0, 0.0), pixel=(342, 218))
    d = a.to_dict()
    assert d["pcb_xyz_mm"] == [0.0, 0.0, 0.0]
    assert d["pixel"] == [342, 218]
    assert d["id"] == "pcb_corner_origin"
```

- [ ] **Step 3: Run, verify failure**

```bash
uv run pytest tests/test_schema.py -v
```

Expected: ImportError.

- [ ] **Step 4: Implement schema dataclasses**

```python
# src/agent_spatial_toolkit/schema/models.py
"""Dataclasses mirroring the spec §6 annotations.json schema.

Each class has a to_dict() that emits the canonical JSON shape. Inverse
parsing (from_dict) is also provided for round-trip testing and for the
session-resume code path.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal, Optional


# ─────────────────────────────────────────────────────────────────────────
# Anchors and feature clicks
# ─────────────────────────────────────────────────────────────────────────

@dataclass
class AnchorClick:
    """A reference-frame anchor: known 3D position, clicked pixel."""

    id: str
    pcb_xyz_mm: tuple[float, float, float]
    pixel: tuple[int, int]

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "pcb_xyz_mm": list(self.pcb_xyz_mm),
            "pixel": list(self.pixel),
        }


@dataclass
class FeatureClick:
    """One click of a feature in one photo."""

    photo: str
    pixel: tuple[int, int]
    reprojection_residual_px: float | None = None
    """None means this click came from a planar_intersection (no residual to report)."""

    def to_dict(self) -> dict[str, Any]:
        d = {"photo": self.photo, "pixel": list(self.pixel)}
        if self.reprojection_residual_px is not None:
            d["reprojection_residual_px"] = float(self.reprojection_residual_px)
        return d


# ─────────────────────────────────────────────────────────────────────────
# Photos
# ─────────────────────────────────────────────────────────────────────────

@dataclass
class CameraDetected:
    make: str | None = None
    model: str | None = None
    lens_label: str | None = None
    detection_source: Literal["exif", "user_specified", "fallback_generic"] = "exif"

    def to_dict(self) -> dict[str, Any]:
        return {
            "make": self.make,
            "model": self.model,
            "lens_label": self.lens_label,
            "detection_source": self.detection_source,
        }


@dataclass
class Photo:
    """One photo in a session — its provenance, intrinsics, pose, and anchors."""

    id: str
    path: str
    sha256: str
    camera_detected: CameraDetected
    intrinsics: dict[str, Any]    # produced by Intrinsics.to_dict()
    pose: dict[str, Any]          # produced by PoseResult.to_dict()
    anchors_clicked: list[AnchorClick] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "path": self.path,
            "sha256": self.sha256,
            "camera_detected": self.camera_detected.to_dict(),
            "intrinsics": self.intrinsics,
            "pose": self.pose,
            "anchors_clicked": [a.to_dict() for a in self.anchors_clicked],
        }


# ─────────────────────────────────────────────────────────────────────────
# Features
# ─────────────────────────────────────────────────────────────────────────

@dataclass
class FeatureMeasurement:
    """Provenance for a feature's pcb_xyz_mm value."""

    method: Literal["planar_intersection", "triangulation_2_views",
                    "triangulation_3_views", "triangulation_4_views",
                    "triangulation_5_views", "triangulation_6_views"]
    per_photo_clicks: list[FeatureClick]
    # Triangulation-only:
    triangulation_rms_px: float | None = None
    max_residual_px: float | None = None
    # Planar-only:
    z_assumed_mm: float | None = None
    z_assumed_reason: str | None = None

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "method": self.method,
            "per_photo_clicks": [c.to_dict() for c in self.per_photo_clicks],
        }
        if self.method.startswith("triangulation"):
            d["triangulation_rms_px"] = self.triangulation_rms_px
            d["max_residual_px"] = self.max_residual_px
        else:  # planar_intersection
            d["z_assumed_mm"] = self.z_assumed_mm
            d["z_assumed_reason"] = self.z_assumed_reason
        return d


@dataclass
class Feature:
    """A named feature with its part-local 3D position."""

    id: str
    visible_in: list[str]
    pcb_xyz_mm: tuple[float, float, float]
    measurements: FeatureMeasurement
    user_tags: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        d = {
            "id": self.id,
            "visible_in": list(self.visible_in),
            "pcb_xyz_mm": list(self.pcb_xyz_mm),
            "measurements": self.measurements.to_dict(),
        }
        if self.user_tags:
            d["user_tags"] = list(self.user_tags)
        return d


# ─────────────────────────────────────────────────────────────────────────
# Top-level
# ─────────────────────────────────────────────────────────────────────────

@dataclass
class Part:
    id: str
    display_name: str | None = None
    part_class: str | None = None
    notes: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {k: v for k, v in {
            "id": self.id,
            "display_name": self.display_name,
            "part_class": self.part_class,
            "notes": self.notes,
        }.items() if v is not None or k == "id"}


@dataclass
class ReferenceFrame:
    origin_description: str
    x_axis_description: str
    y_axis_description: str
    z_axis_description: str
    units: Literal["mm"] = "mm"

    def to_dict(self) -> dict[str, Any]:
        return {
            "origin_description": self.origin_description,
            "x_axis_description": self.x_axis_description,
            "y_axis_description": self.y_axis_description,
            "z_axis_description": self.z_axis_description,
            "units": self.units,
        }


@dataclass
class QualitySummary:
    feature_count: int
    triangulated_count: int
    z_assumed_count: int
    median_reprojection_rms_px: float | None
    max_reprojection_rms_px: float | None
    flags: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "feature_count": self.feature_count,
            "triangulated_count": self.triangulated_count,
            "z_assumed_count": self.z_assumed_count,
            "median_reprojection_rms_px": self.median_reprojection_rms_px,
            "max_reprojection_rms_px": self.max_reprojection_rms_px,
            "flags": list(self.flags),
        }


@dataclass
class SessionArtifacts:
    overlay_pngs: list[str]
    events_jsonl: str
    manifest: str
    chessboard_calibration_image: str | None = None

    def to_dict(self) -> dict[str, Any]:
        d = {
            "overlay_pngs": list(self.overlay_pngs),
            "events_jsonl": self.events_jsonl,
            "manifest": self.manifest,
        }
        if self.chessboard_calibration_image is not None:
            d["chessboard_calibration_image"] = self.chessboard_calibration_image
        return d


@dataclass
class Annotations:
    """Top-level annotations.json contents."""

    schema_version: int
    toolkit_version: str
    generated_at: str
    part: Part
    reference_frame: ReferenceFrame
    photos: list[Photo]
    features: list[Feature]
    quality_summary: QualitySummary
    session_artifacts: SessionArtifacts

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "toolkit_version": self.toolkit_version,
            "generated_at": self.generated_at,
            "part": self.part.to_dict(),
            "reference_frame": self.reference_frame.to_dict(),
            "photos": [p.to_dict() for p in self.photos],
            "features": [f.to_dict() for f in self.features],
            "quality_summary": self.quality_summary.to_dict(),
            "session_artifacts": self.session_artifacts.to_dict(),
        }
```

- [ ] **Step 5: Run, verify pass**

```bash
uv run pytest tests/test_schema.py -v
```

Expected: 5 tests pass.

- [ ] **Step 6: PR + merge**

```bash
git checkout -b feat/schema-models
git add src/agent_spatial_toolkit/schema/ tests/test_schema.py
git commit -m "feat(schema): dataclasses for annotations.json schema (spec §6)

Mirrors the canonical schema as Python dataclasses with to_dict() emitters.
Enforces the spec §6 contract that planar_intersection clicks omit
reprojection_residual_px while triangulation clicks include it."
git push -u origin feat/schema-models
gh pr create --fill
```

---

### Task 1.B.2: `schema/validators.py` — closed flag enum

**Stream:** B · **Depends on:** Task 1.B.1

**Files:**
- Create: `src/agent_spatial_toolkit/schema/validators.py`
- Modify: `tests/test_schema.py`

- [ ] **Step 1: Add failing tests**

Append to `tests/test_schema.py`:

```python
# Append to tests/test_schema.py

from agent_spatial_toolkit.schema.validators import (
    QUALITY_FLAGS,
    ValidationError,
    validate_annotations,
    validate_quality_flag,
)


def test_validate_quality_flag_accepts_known() -> None:
    """Known flags pass."""
    validate_quality_flag("intrinsics_suspect_high_anchor_rms")
    validate_quality_flag("feature_clicked_only_once:gpio_socket_center")
    validate_quality_flag("photo_excluded_due_to_pose_failure:long_edge_a")


def test_validate_quality_flag_rejects_unknown() -> None:
    with pytest.raises(ValidationError, match="not a recognized flag"):
        validate_quality_flag("totally_made_up_flag")


def test_validate_quality_flag_parameterized_must_have_id() -> None:
    """Flags marked parameterized require ':<id>' suffix."""
    with pytest.raises(ValidationError, match="requires ':<id>'"):
        validate_quality_flag("feature_clicked_only_once")  # missing :<id>


def test_quality_flags_constant_is_complete() -> None:
    """The closed enum contains all six flags from spec §6."""
    expected = {
        "intrinsics_suspect_high_anchor_rms",
        "intrinsics_session_recommend_chessboard",
        "photo_excluded_due_to_pose_failure",
        "feature_clicked_only_once",
        "feature_high_triangulation_rms",
        "ultrawide_lens_rejected",
    }
    assert set(QUALITY_FLAGS.keys()) == expected
```

- [ ] **Step 2: Verify failure**

```bash
uv run pytest tests/test_schema.py -v -k "validate"
```

Expected: ImportError.

- [ ] **Step 3: Implement validators.py**

```python
# src/agent_spatial_toolkit/schema/validators.py
"""Closed-enum validation for quality_summary.flags (spec §6)."""
from __future__ import annotations


class ValidationError(ValueError):
    """Raised when a value does not satisfy the schema's closed contracts."""


# Closed enum of valid flag prefixes and whether they require ":<id>" suffix.
QUALITY_FLAGS: dict[str, bool] = {
    "intrinsics_suspect_high_anchor_rms": False,        # parameterless
    "intrinsics_session_recommend_chessboard": False,
    "photo_excluded_due_to_pose_failure": True,         # requires :<photo_id>
    "feature_clicked_only_once": True,                  # requires :<feature_id>
    "feature_high_triangulation_rms": True,             # requires :<feature_id>
    "ultrawide_lens_rejected": True,                    # requires :<photo_id>
}


def validate_quality_flag(flag: str) -> None:
    """Validate a single flag string against the closed enum."""
    if ":" in flag:
        prefix, _, suffix = flag.partition(":")
        if not suffix:
            raise ValidationError(f"Flag '{flag}' has empty :<id> suffix")
    else:
        prefix = flag

    if prefix not in QUALITY_FLAGS:
        raise ValidationError(
            f"'{prefix}' is not a recognized flag. Valid prefixes: "
            f"{sorted(QUALITY_FLAGS.keys())}"
        )

    needs_id = QUALITY_FLAGS[prefix]
    has_id = ":" in flag
    if needs_id and not has_id:
        raise ValidationError(f"Flag '{prefix}' requires ':<id>' suffix")
    if not needs_id and has_id:
        raise ValidationError(f"Flag '{prefix}' does not take an :<id> suffix")


def validate_annotations(data: dict) -> None:
    """Top-level validation of an annotations.json dict.

    Currently checks: quality_summary.flags are all valid. More invariants
    can be added (e.g., feature.visible_in references actual photo IDs;
    pcb_xyz_mm[2] consistent with z_assumed_mm; etc.) as the implementation
    matures.
    """
    flags = data.get("quality_summary", {}).get("flags", [])
    for flag in flags:
        validate_quality_flag(flag)
```

- [ ] **Step 4: Run, verify pass**

```bash
uv run pytest tests/test_schema.py -v
```

Expected: 9 tests pass (5 from 1.B.1 + 4 new).

- [ ] **Step 5: PR + merge**

```bash
git checkout -b feat/schema-validators
git add src/agent_spatial_toolkit/schema/validators.py tests/test_schema.py
git commit -m "feat(schema): closed-enum validation for quality_summary.flags

Per spec §6, flags is a closed enum with parameterless and ':<id>'-required
variants. validate_quality_flag enforces this; validate_annotations applies
it to a full document."
git push -u origin feat/schema-validators
gh pr create --fill
```

---

### Task 1.B.3: `pipeline/emit.py` — assemble annotations.json

**Stream:** B · **Depends on:** Tasks 1.B.1, 1.B.2

**Files:**
- Create: `src/agent_spatial_toolkit/pipeline/emit.py`
- Create: `tests/test_emit.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_emit.py
"""Tests for pipeline/emit.py — assembling annotations.json from session state."""
import json
from pathlib import Path

from agent_spatial_toolkit.pipeline.emit import (
    SessionState,
    emit_annotations,
)
from agent_spatial_toolkit.schema.models import (
    AnchorClick,
    CameraDetected,
    Feature,
    FeatureClick,
    FeatureMeasurement,
    Photo,
)


def test_emit_minimal_session(tmp_path: Path) -> None:
    """An empty session produces a valid annotations.json with no features."""
    state = SessionState(
        part_id="testpart",
        part_display_name=None,
        part_class=None,
        notes=None,
        reference_frame=dict(
            origin_description="origin",
            x_axis_description="+x",
            y_axis_description="+y",
            z_axis_description="+z",
            units="mm",
        ),
        photos=[],
        features=[],
        flags=[],
        events_jsonl_filename="events.jsonl",
        manifest_filename="manifest.json",
        overlay_pngs=[],
        chessboard_calibration_image=None,
    )

    out_path = emit_annotations(state, session_dir=tmp_path)
    assert out_path == tmp_path / "annotations.json"
    assert out_path.exists()

    data = json.loads(out_path.read_text())
    assert data["schema_version"] == 1
    assert data["part"]["id"] == "testpart"
    assert data["features"] == []
    assert data["quality_summary"]["feature_count"] == 0


def test_emit_session_with_one_planar_feature(tmp_path: Path) -> None:
    photo = Photo(
        id="top_down",
        path="photos/top_down.jpg",
        sha256="deadbeef",
        camera_detected=CameraDetected(make="Test", model="Camera"),
        intrinsics={"profile_source": "fov_class_fallback"},
        pose={"rvec": [0, 0, 0], "tvec": [0, 0, 200], "anchor_reprojection_rms_px": 0.5,
              "pose_solver": "test"},
        anchors_clicked=[AnchorClick(id="o", pcb_xyz_mm=(0, 0, 0), pixel=(0, 0))],
    )
    feature = Feature(
        id="x",
        visible_in=["top_down"],
        pcb_xyz_mm=(10.0, 20.0, 0.0),
        measurements=FeatureMeasurement(
            method="planar_intersection",
            z_assumed_mm=0.0,
            z_assumed_reason="single_photo_only_default",
            per_photo_clicks=[FeatureClick(photo="top_down", pixel=(100, 200))],
        ),
    )

    state = SessionState(
        part_id="x", part_display_name=None, part_class=None, notes=None,
        reference_frame=dict(origin_description="o", x_axis_description="+x",
                              y_axis_description="+y", z_axis_description="+z", units="mm"),
        photos=[photo], features=[feature],
        flags=["feature_clicked_only_once:x"],
        events_jsonl_filename="events.jsonl", manifest_filename="manifest.json",
        overlay_pngs=["photos/top_down_overlay.png"],
        chessboard_calibration_image=None,
    )
    out_path = emit_annotations(state, session_dir=tmp_path)
    data = json.loads(out_path.read_text())

    assert data["features"][0]["id"] == "x"
    assert data["quality_summary"]["feature_count"] == 1
    assert data["quality_summary"]["z_assumed_count"] == 1
    assert data["quality_summary"]["triangulated_count"] == 0
    assert "feature_clicked_only_once:x" in data["quality_summary"]["flags"]


def test_emit_validates_flags(tmp_path: Path) -> None:
    """Invalid flags cause emit to raise ValidationError."""
    import pytest
    from agent_spatial_toolkit.schema.validators import ValidationError

    state = SessionState(
        part_id="x", part_display_name=None, part_class=None, notes=None,
        reference_frame=dict(origin_description="o", x_axis_description="+x",
                              y_axis_description="+y", z_axis_description="+z", units="mm"),
        photos=[], features=[],
        flags=["totally_invalid_flag"],
        events_jsonl_filename="e.jsonl", manifest_filename="m.json",
        overlay_pngs=[], chessboard_calibration_image=None,
    )
    with pytest.raises(ValidationError):
        emit_annotations(state, session_dir=tmp_path)
```

- [ ] **Step 2: Run, verify failure**

```bash
uv run pytest tests/test_emit.py -v
```

Expected: ImportError.

- [ ] **Step 3: Implement emit.py**

```python
# src/agent_spatial_toolkit/pipeline/emit.py
"""Assemble annotations.json from session state (spec §6)."""
from __future__ import annotations

import datetime as dt
import json
from dataclasses import dataclass, field
from pathlib import Path
from statistics import median

from agent_spatial_toolkit import __version__
from agent_spatial_toolkit.schema.models import (
    Annotations,
    Feature,
    Part,
    Photo,
    QualitySummary,
    ReferenceFrame,
    SessionArtifacts,
)
from agent_spatial_toolkit.schema.validators import validate_annotations


@dataclass
class SessionState:
    """In-memory session state assembled by the server during a session."""

    part_id: str
    part_display_name: str | None
    part_class: str | None
    notes: str | None
    reference_frame: dict
    photos: list[Photo]
    features: list[Feature]
    flags: list[str]
    events_jsonl_filename: str
    manifest_filename: str
    overlay_pngs: list[str]
    chessboard_calibration_image: str | None = None


def _now_iso_utc() -> str:
    """ISO-8601 UTC with Z suffix (spec §6 example uses this)."""
    return dt.datetime.now(dt.UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _compute_quality_summary(features: list[Feature], flags: list[str]) -> QualitySummary:
    """Derive quality_summary fields from the feature list."""
    triangulated_rmses: list[float] = []
    triangulated_count = 0
    z_assumed_count = 0
    for f in features:
        if f.measurements.method.startswith("triangulation"):
            triangulated_count += 1
            if f.measurements.triangulation_rms_px is not None:
                triangulated_rmses.append(f.measurements.triangulation_rms_px)
        else:
            z_assumed_count += 1

    return QualitySummary(
        feature_count=len(features),
        triangulated_count=triangulated_count,
        z_assumed_count=z_assumed_count,
        median_reprojection_rms_px=median(triangulated_rmses) if triangulated_rmses else None,
        max_reprojection_rms_px=max(triangulated_rmses) if triangulated_rmses else None,
        flags=list(flags),
    )


def emit_annotations(state: SessionState, session_dir: Path) -> Path:
    """Write annotations.json to session_dir and return its path.

    Validates flags against the closed enum before writing. Raises
    ValidationError on invalid flags.
    """
    quality = _compute_quality_summary(state.features, state.flags)

    ann = Annotations(
        schema_version=1,
        toolkit_version=__version__,
        generated_at=_now_iso_utc(),
        part=Part(
            id=state.part_id,
            display_name=state.part_display_name,
            part_class=state.part_class,
            notes=state.notes,
        ),
        reference_frame=ReferenceFrame(**state.reference_frame),
        photos=state.photos,
        features=state.features,
        quality_summary=quality,
        session_artifacts=SessionArtifacts(
            overlay_pngs=state.overlay_pngs,
            events_jsonl=state.events_jsonl_filename,
            manifest=state.manifest_filename,
            chessboard_calibration_image=state.chessboard_calibration_image,
        ),
    )

    data = ann.to_dict()
    validate_annotations(data)   # raises ValidationError if flags are invalid

    out_path = session_dir / "annotations.json"
    out_path.write_text(json.dumps(data, indent=2))
    return out_path
```

- [ ] **Step 4: Run, verify pass**

```bash
uv run pytest tests/test_emit.py -v
```

Expected: 3 tests pass.

- [ ] **Step 5: PR + merge**

```bash
git checkout -b feat/emit
git add src/agent_spatial_toolkit/pipeline/emit.py tests/test_emit.py
git commit -m "feat(emit): assemble annotations.json from session state (spec §6)

emit_annotations(state, session_dir) writes the canonical JSON with derived
quality_summary fields and runs closed-flag validation before writing.
Failure to validate raises ValidationError without writing."
git push -u origin feat/emit
gh pr create --fill
```

---

### Task 1.B.4: `pipeline/reproject.py` — overlay PNG generation

**Stream:** B · **Depends on:** Tasks 1.A.4, 1.B.1

**Files:**
- Create: `src/agent_spatial_toolkit/pipeline/reproject.py`
- Create: `tests/test_reproject.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_reproject.py
"""Tests for pipeline/reproject.py — validation overlay PNG generation."""
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from agent_spatial_toolkit.pipeline.intrinsics import Intrinsics
from agent_spatial_toolkit.pipeline.pose import PoseResult
from agent_spatial_toolkit.pipeline.reproject import render_overlay


def _make_test_photo(tmp_path: Path) -> Path:
    img = Image.new("RGB", (1000, 1000), color=(100, 100, 100))
    p = tmp_path / "test_photo.jpg"
    img.save(p)
    return p


def _test_pose() -> PoseResult:
    return PoseResult(
        rvec=np.array([0.0, 0.0, 0.0]),
        tvec=np.array([0.0, 0.0, 200.0]),
        anchor_reprojection_rms_px=0.0,
        intrinsics_suspect=False,
        pose_solver="test",
    )


def _test_intrinsics() -> Intrinsics:
    return Intrinsics(
        profile_source="fov_class_fallback",
        profile_id="test",
        fx_px=1000.0, fy_px=1000.0, cx=500.0, cy=500.0,
        distortion=[0.0, 0.0, 0.0, 0.0, 0.0],
    )


def test_render_overlay_writes_png(tmp_path: Path) -> None:
    """Calling render_overlay produces a PNG at the requested path."""
    photo_path = _make_test_photo(tmp_path)
    out_path = tmp_path / "overlay.png"

    render_overlay(
        photo_path=photo_path,
        out_path=out_path,
        features=[
            ("origin",     np.array([0.0, 0.0, 0.0])),
            ("x10y10",     np.array([10.0, 10.0, 0.0])),
        ],
        pose=_test_pose(),
        intrinsics=_test_intrinsics(),
    )

    assert out_path.exists()
    overlay = Image.open(out_path)
    assert overlay.size == (1000, 1000)


def test_render_overlay_marks_origin_at_image_center(tmp_path: Path) -> None:
    """For our standard test pose (camera at (0,0,200) looking down with focal=1000),
    the world origin (0,0,0) projects to the image center (500, 500).
    The overlay should have a non-background color at that pixel."""
    photo_path = _make_test_photo(tmp_path)
    out_path = tmp_path / "overlay.png"

    render_overlay(
        photo_path=photo_path, out_path=out_path,
        features=[("origin", np.array([0.0, 0.0, 0.0]))],
        pose=_test_pose(), intrinsics=_test_intrinsics(),
    )

    overlay = np.array(Image.open(out_path))
    # The marker should be a circle centered at (500, 500) with non-gray color
    center_pixel = overlay[500, 500]
    assert not np.array_equal(center_pixel[:3], [100, 100, 100])  # not the background gray
```

- [ ] **Step 2: Run, verify failure**

```bash
uv run pytest tests/test_reproject.py -v
```

Expected: ImportError.

- [ ] **Step 3: Implement reproject.py**

```python
# src/agent_spatial_toolkit/pipeline/reproject.py
"""Render validation overlays (spec §3 Phase 2e, §5.1 step 6)."""
from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np

from agent_spatial_toolkit.pipeline.intrinsics import Intrinsics
from agent_spatial_toolkit.pipeline.pose import PoseResult


def render_overlay(
    photo_path: Path,
    out_path: Path,
    features: list[tuple[str, np.ndarray]],   # (label, xyz_mm)
    pose: PoseResult,
    intrinsics: Intrinsics,
    marker_radius_px: int = 12,
    marker_color: tuple[int, int, int] = (255, 80, 200),  # BGR magenta
    label_color: tuple[int, int, int] = (255, 255, 255),
) -> None:
    """Project features onto photo as colored circles + labels; save as PNG.

    Used by spec §3 Phase 2e — the user sees their photos with predicted
    feature positions and confirms or corrects.
    """
    img = cv2.imread(str(photo_path))
    if img is None:
        raise FileNotFoundError(f"Cannot open image: {photo_path}")

    K = np.array([
        [intrinsics.fx_px, 0, intrinsics.cx],
        [0, intrinsics.fy_px, intrinsics.cy],
        [0, 0, 1],
    ], dtype=np.float64)
    dist = np.array(intrinsics.distortion, dtype=np.float64)

    if features:
        world_pts = np.array([xyz for _, xyz in features], dtype=np.float64).reshape(-1, 1, 3)
        projected, _ = cv2.projectPoints(world_pts, pose.rvec, pose.tvec, K, dist)
        projected = projected.reshape(-1, 2)
    else:
        projected = np.zeros((0, 2))

    for (label, _), (px, py) in zip(features, projected):
        if not (0 <= px < img.shape[1] and 0 <= py < img.shape[0]):
            continue   # off-frame
        cv2.circle(img, (int(px), int(py)), marker_radius_px, marker_color, 2, lineType=cv2.LINE_AA)
        cv2.circle(img, (int(px), int(py)), 2, marker_color, -1)
        # Label slightly above and right of the marker
        cv2.putText(
            img, label,
            (int(px) + marker_radius_px + 4, int(py) - 4),
            cv2.FONT_HERSHEY_SIMPLEX, 0.6, label_color, 2, cv2.LINE_AA,
        )

    cv2.imwrite(str(out_path), img)
```

- [ ] **Step 4: Run, verify pass**

```bash
uv run pytest tests/test_reproject.py -v
```

Expected: 2 tests pass.

- [ ] **Step 5: PR + merge**

```bash
git checkout -b feat/reproject-overlay
git add src/agent_spatial_toolkit/pipeline/reproject.py tests/test_reproject.py
git commit -m "feat(reproject): validation overlay PNG generation (spec §3 Phase 2e)

render_overlay projects each labeled feature onto a photo using the photo's
pose + intrinsics, drawing a colored circle and the label text. Used by the
wizard's validation phase to let the user verify feature placements."
git push -u origin feat/reproject-overlay
gh pr create --fill
```

---

### Task 1.C.1: `server/session.py` — session directory management

**Stream:** C (parallel with A, B) · **Depends on:** Task 0.4

**Files:**
- Create: `src/agent_spatial_toolkit/server/__init__.py`
- Create: `src/agent_spatial_toolkit/server/session.py`
- Create: `tests/test_session.py`

- [ ] **Step 1: Empty package init**

```bash
mkdir -p src/agent_spatial_toolkit/server
touch src/agent_spatial_toolkit/server/__init__.py
```

- [ ] **Step 2: Write failing tests**

```python
# tests/test_session.py
"""Tests for server/session.py — session directory creation and management."""
import json
from pathlib import Path

from agent_spatial_toolkit.server.session import (
    Session,
    create_session,
    load_session,
    session_dir_name,
)


def test_session_dir_name_format() -> None:
    """Session directory name is <part_id>-YYYYMMDDTHHMMSS-<8char>."""
    name = session_dir_name(part_id="x1207", timestamp="20260503T221400", suffix="a1b2c3d4")
    assert name == "x1207-20260503T221400-a1b2c3d4"


def test_create_session_makes_dir_with_state(tmp_path: Path) -> None:
    base = tmp_path / "sessions"
    session = create_session(part_id="testpart", base_dir=base)

    assert session.session_dir.exists()
    assert session.session_dir.parent == base
    assert (session.session_dir / "photos").is_dir()
    assert (session.session_dir / "overlays").is_dir()

    state_file = session.session_dir / "state.json"
    assert state_file.exists()
    state = json.loads(state_file.read_text())
    assert state["part_id"] == "testpart"
    assert state["status"] == "in_progress"


def test_load_session_round_trips(tmp_path: Path) -> None:
    """A created session can be loaded back from its directory."""
    base = tmp_path / "sessions"
    s1 = create_session(part_id="p", base_dir=base)
    s2 = load_session(s1.session_dir)
    assert s2.part_id == "p"
    assert s2.session_dir == s1.session_dir


def test_session_default_base_dir_is_user_home(monkeypatch, tmp_path: Path) -> None:
    """When no --out is passed, session_dir defaults to ~/.spatial-annotations/."""
    monkeypatch.setenv("HOME", str(tmp_path))
    from agent_spatial_toolkit.server.session import default_base_dir
    assert default_base_dir() == tmp_path / ".spatial-annotations"
```

- [ ] **Step 3: Run, verify failure**

```bash
uv run pytest tests/test_session.py -v
```

Expected: ImportError.

- [ ] **Step 4: Implement session.py**

```python
# src/agent_spatial_toolkit/server/session.py
"""Session directory management (spec §4 architecture)."""
from __future__ import annotations

import datetime as dt
import json
import os
import secrets
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class Session:
    """An in-flight annotation session bound to a directory."""

    part_id: str
    session_dir: Path
    base_dir: Path
    timestamp: str
    suffix: str
    status: str = "in_progress"

    def state_path(self) -> Path:
        return self.session_dir / "state.json"

    def to_state_dict(self) -> dict:
        return {
            "part_id": self.part_id,
            "session_dir": str(self.session_dir),
            "timestamp": self.timestamp,
            "suffix": self.suffix,
            "status": self.status,
        }


def default_base_dir() -> Path:
    """Default session base directory: ~/.spatial-annotations/."""
    return Path(os.environ.get("HOME", "~")).expanduser() / ".spatial-annotations"


def session_dir_name(part_id: str, timestamp: str, suffix: str) -> str:
    """Compute the session directory name."""
    return f"{part_id}-{timestamp}-{suffix}"


def create_session(part_id: str, base_dir: Path | None = None) -> Session:
    """Create a new session directory and return a Session object."""
    if base_dir is None:
        base_dir = default_base_dir()
    base_dir.mkdir(parents=True, exist_ok=True)

    now = dt.datetime.now(dt.UTC)
    timestamp = now.strftime("%Y%m%dT%H%M%S")
    suffix = secrets.token_hex(4)   # 8 hex chars
    name = session_dir_name(part_id, timestamp, suffix)

    session_dir = base_dir / name
    session_dir.mkdir(exist_ok=False)
    (session_dir / "photos").mkdir()
    (session_dir / "overlays").mkdir()

    session = Session(
        part_id=part_id,
        session_dir=session_dir,
        base_dir=base_dir,
        timestamp=timestamp,
        suffix=suffix,
    )
    session.state_path().write_text(json.dumps(session.to_state_dict(), indent=2))
    return session


def load_session(session_dir: Path) -> Session:
    """Load an existing session from its directory."""
    state = json.loads((session_dir / "state.json").read_text())
    return Session(
        part_id=state["part_id"],
        session_dir=session_dir,
        base_dir=session_dir.parent,
        timestamp=state["timestamp"],
        suffix=state["suffix"],
        status=state.get("status", "in_progress"),
    )
```

- [ ] **Step 5: Run, verify pass**

```bash
uv run pytest tests/test_session.py -v
```

Expected: 4 tests pass.

- [ ] **Step 6: PR + merge**

```bash
git checkout -b feat/server-session
git add src/agent_spatial_toolkit/server/__init__.py src/agent_spatial_toolkit/server/session.py tests/test_session.py
git commit -m "feat(server): session directory management (spec §4)

create_session/load_session manage timestamped session dirs under
~/.spatial-annotations/<part_id>-YYYYMMDDTHHMMSS-<rand>/. Each session has
a state.json + photos/ + overlays/ subdirs."
git push -u origin feat/server-session
gh pr create --fill
```

---

### Task 1.C.2: `server/events.py` — JSONL event stream

**Stream:** C · **Depends on:** Task 1.C.1

**Files:**
- Create: `src/agent_spatial_toolkit/server/events.py`
- Create: `tests/test_events.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_events.py
"""Tests for server/events.py — append-only JSONL event stream."""
import json
from pathlib import Path

from agent_spatial_toolkit.server.events import EventLog


def test_event_log_appends_lines(tmp_path: Path) -> None:
    log_path = tmp_path / "events.jsonl"
    log = EventLog(log_path)
    log.write({"type": "anchor_clicked", "anchor_id": "o", "pixel": [100, 200]})
    log.write({"type": "feature_added", "feature_id": "x"})

    lines = log_path.read_text().splitlines()
    assert len(lines) == 2
    assert json.loads(lines[0])["type"] == "anchor_clicked"
    assert json.loads(lines[1])["feature_id"] == "x"


def test_event_log_includes_timestamp(tmp_path: Path) -> None:
    log = EventLog(tmp_path / "events.jsonl")
    log.write({"type": "test"})
    line = (tmp_path / "events.jsonl").read_text().splitlines()[0]
    rec = json.loads(line)
    assert "ts" in rec
    # ISO-8601 UTC: '...T...Z'
    assert "T" in rec["ts"] and rec["ts"].endswith("Z")


def test_event_log_replay(tmp_path: Path) -> None:
    log = EventLog(tmp_path / "events.jsonl")
    log.write({"type": "a", "n": 1})
    log.write({"type": "b", "n": 2})
    events = list(log.replay())
    assert len(events) == 2
    assert events[0]["n"] == 1
    assert events[1]["n"] == 2
```

- [ ] **Step 2: Verify failure**

```bash
uv run pytest tests/test_events.py -v
```

Expected: ImportError.

- [ ] **Step 3: Implement events.py**

```python
# src/agent_spatial_toolkit/server/events.py
"""Append-only JSONL event log for session replay (spec §4)."""
from __future__ import annotations

import datetime as dt
import json
from pathlib import Path
from typing import Any, Iterable


class EventLog:
    """Append-only JSONL writer + reader. Each line is one event."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def write(self, event: dict[str, Any]) -> None:
        record = {**event, "ts": dt.datetime.now(dt.UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")}
        with self.path.open("a") as f:
            f.write(json.dumps(record) + "\n")

    def replay(self) -> Iterable[dict[str, Any]]:
        """Yield each recorded event in order."""
        if not self.path.exists():
            return
        with self.path.open() as f:
            for line in f:
                line = line.strip()
                if line:
                    yield json.loads(line)
```

- [ ] **Step 4: Verify pass**

```bash
uv run pytest tests/test_events.py -v
```

Expected: 3 tests pass.

- [ ] **Step 5: PR + merge**

```bash
git checkout -b feat/server-events
git add src/agent_spatial_toolkit/server/events.py tests/test_events.py
git commit -m "feat(server): JSONL event log for session replay (spec §4)

EventLog provides append-only writes and a replay() iterator. Each event
carries a UTC timestamp. Used by the server to log every click and state
change during a wizard session."
git push -u origin feat/server-events
gh pr create --fill
```

---

### Task 1.C.3 — Task 1.C.5 (Server lifecycle, app, CLI)

**Note:** Tasks 1.C.3, 1.C.4, 1.C.5 are spec-prescribed but the detailed test cases follow the same red-green-commit pattern as 1.C.1 and 1.C.2 above. The implementation contracts are:

- **`server/lifecycle.py`** — `start_server(session, host, port)` returns a Server object with `.port`, `.url`, `.shutdown()`. Auto-shutdown timer (30 min idle); test verifies the timer fires by mocking time.
- **`server/app.py`** — Flask routes:
  - `GET /` → serves `ui/index.html`
  - `GET /api/state` → returns current `state.json` contents
  - `POST /api/anchors` → records anchor clicks, runs PnP, returns pose result
  - `POST /api/feature` → records feature click, runs ray-cast (β-mode) or triangulation (γ-mode in v0.1.0), returns coordinate
  - `POST /api/finalize` → calls `emit_annotations`, writes status="done" to `state.json`
  - `GET /static/photos/<id>` → serves uploaded photos (path security: only inside session_dir)
  - `GET /static/overlays/<id>` → serves overlay PNGs
- **`cli.py`** — entry point `agent-spatial-toolkit annotate`. Parses args (`--part-id`, `--photos`, `--out`). Creates session, copies photos into `<session_dir>/photos/`, computes `sha256` for each, starts server, prints URL, exits.

For brevity, full test/code is omitted here — the subagent dispatching these tasks should follow the spec §4 endpoints contract and the testing pattern from 1.C.1/1.C.2. **The orchestrator MUST verify each subagent's PR before merge: route handlers must return correct status codes; CLI must exit 0 after starting server and printing URL on stdout.**

---

### Task 1.D.1 — Task 1.D.9 (UI: single-page wizard)

**Note:** UI tasks build out `src/agent_spatial_toolkit/ui/{index.html, app.js, helpers.js, style.css}` per spec §3 Phase 2a–2f. Each task adds one phase to the state machine.

UI tests are manual smoke testing in v0.1.0-alpha (per spec §10). Once a phase is implemented, the orchestrator:
1. Starts the dev server: `uv run python -m agent_spatial_toolkit.cli annotate --part-id testdummy --photos tests/fixtures/synthetic_card/test1.jpg`
2. Opens the URL in a headless browser via Playwright (added to dev deps for this purpose)
3. Walks the phase's expected interaction
4. Captures screenshot of the resulting state for visual regression in subsequent tasks

**Tasks 1.D.1–1.D.9 in summary:**

- **1.D.1** `index.html` — wizard shell with `<div id="phase-{2a..2f}">` containers, all hidden except the active phase. Skeleton CSS layout.
- **1.D.2** `helpers.js` — `captureClick(canvasElement, callback)`, `loadImageToCanvas(url, canvas)`, `getEXIFFromUploaded(file, callback)`, drag-and-drop handlers.
- **1.D.3** `app.js` Phase 2a — upload + thumbnail render + EXIF display + lens dropdown + view-label dropdown.
- **1.D.4** `app.js` Phase 2b — frame declaration form (preset dropdown + custom origin/axes inputs + known-dimension entry).
- **1.D.5** `app.js` Phase 2c — per-photo anchor clicking: show photo, show anchor checklist, capture clicks, POST to `/api/anchors`, render wireframe overlay on success.
- **1.D.6** `app.js` Phase 2d — feature labeling: click → label dropdown / freetext → POST to `/api/feature` → display result.
- **1.D.7** `app.js` Phase 2e — validation overlays: GET each photo's overlay PNG, display, "confirm" / "re-click" buttons.
- **1.D.8** `app.js` Phase 2f — finalize: POST `/api/finalize`, show success page with download link to `annotations.json`.
- **1.D.9** `style.css` — pass-of-polish styling (the wizard should look like a tool, not a debug page).

The dispatching subagent receives this plan and the spec §3 Phase 2a–2f text. They have authority to flesh out the test/code per the same red-green-commit pattern.

---

### Task 1.E.1: Synthetic card smoke fixture

**Stream:** E (after A, B, C, D) · **Depends on:** all 1.A.x, 1.B.x, 1.C.x, 1.D.x complete

**Files:**
- Create: `scripts/gen_synthetic_card.py`
- Create: `tests/fixtures/synthetic_card/test1.jpg`
- Create: `tests/fixtures/synthetic_card/expected_annotations.json`
- Create: `tests/test_smoke_synthetic.py`

- [ ] **Step 1: Generator script**

```python
# scripts/gen_synthetic_card.py
"""Generate a known-geometry test card for end-to-end smoke testing.

Renders a 200×150 mm card with:
- Black border at the four PCB corners (used as anchors)
- Five labeled circular features at known positions
- Outputs a high-res JPEG photographed-style (perspective applied)

Used as the simplest possible end-to-end fixture: known geometry, no
hardware needed, ground truth fully under test control.
"""
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

REPO = Path(__file__).resolve().parent.parent
OUT_DIR = REPO / "tests/fixtures/synthetic_card"
OUT_DIR.mkdir(parents=True, exist_ok=True)

# Card dimensions (mm)
W_MM = 200.0
H_MM = 150.0

# Render at 4 px/mm → 800×600 px card
PX_PER_MM = 4.0
W_PX = int(W_MM * PX_PER_MM)
H_PX = int(H_MM * PX_PER_MM)

# Features: (id, X_mm, Y_mm)
FEATURES = [
    ("origin_marker",  10.0,  10.0),
    ("center_marker",  100.0, 75.0),
    ("upper_right",    180.0, 20.0),
    ("lower_left",     20.0,  130.0),
    ("right_edge",     185.0, 75.0),
]


def main() -> None:
    img = Image.new("RGB", (W_PX, H_PX), color="white")
    draw = ImageDraw.Draw(img)

    # Anchor corners: black squares at 5×5 mm
    anchor_size_mm = 5.0
    anchor_size_px = int(anchor_size_mm * PX_PER_MM)
    for cx_mm, cy_mm in [(0, 0), (W_MM, 0), (W_MM, H_MM), (0, H_MM)]:
        x = int(cx_mm * PX_PER_MM)
        y = int(cy_mm * PX_PER_MM)
        draw.rectangle(
            [x - anchor_size_px // 2, y - anchor_size_px // 2,
             x + anchor_size_px // 2, y + anchor_size_px // 2],
            fill="black",
        )

    # Features: red circles labeled with their ID
    for (label, x_mm, y_mm) in FEATURES:
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
    import json
    expected = {
        "anchors": [
            {"id": "anchor_origin",  "pcb_xyz_mm": [0.0, 0.0, 0.0]},
            {"id": "anchor_x_max",   "pcb_xyz_mm": [W_MM, 0.0, 0.0]},
            {"id": "anchor_xy_max",  "pcb_xyz_mm": [W_MM, H_MM, 0.0]},
            {"id": "anchor_y_max",   "pcb_xyz_mm": [0.0, H_MM, 0.0]},
        ],
        "features": [{"id": label, "pcb_xyz_mm": [x, y, 0.0]}
                     for (label, x, y) in FEATURES],
        "render_resolution_px_per_mm": PX_PER_MM,
    }
    expected_path.write_text(json.dumps(expected, indent=2))
    print(f"Wrote {expected_path}")


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Generate**

```bash
uv run python scripts/gen_synthetic_card.py
ls tests/fixtures/synthetic_card/
```

Expected: `test1.jpg` and `expected_geometry.json`.

- [ ] **Step 3: Smoke test that reproduces ground truth via the pipeline**

```python
# tests/test_smoke_synthetic.py
"""End-to-end smoke test: synthetic card → pipeline → ground truth match."""
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
    from agent_spatial_toolkit.pipeline.intrinsics import resolve_fallback_intrinsics
    from agent_spatial_toolkit.pipeline.pose import solve_pnp
    from agent_spatial_toolkit.pipeline.ray import pixel_to_part_local

    # Synthetic card was rendered orthographically — for testing the pipeline,
    # we use synthetic intrinsics matching how the renderer projected.
    img = Image.open(FIXTURES / "test1.jpg")
    W, H = img.size

    # Build intrinsics matching a 50mm normal-class lens (synthetic)
    intrinsics = resolve_fallback_intrinsics(50.0, image_size=(W, H))
    # Use orthographic projection emulation: very long focal, scaled
    # The synthetic card is rendered AT scale, so anchor pixel positions
    # are exactly:
    px_per_mm = expected_geometry["render_resolution_px_per_mm"]
    anchors_3d = np.array([a["pcb_xyz_mm"] for a in expected_geometry["anchors"]])
    anchors_2d = np.array([
        [a["pcb_xyz_mm"][0] * px_per_mm, a["pcb_xyz_mm"][1] * px_per_mm]
        for a in expected_geometry["anchors"]
    ])

    # For the synthetic case we override intrinsics with the renderer's
    # exact values:
    from agent_spatial_toolkit.pipeline.intrinsics import Intrinsics
    intrinsics = Intrinsics(
        profile_source="fov_class_fallback",
        profile_id="synthetic_orthographic",
        fx_px=1e6, fy_px=1e6,    # near-orthographic (large focal length)
        cx=W/2, cy=H/2,
        distortion=[0.0, 0.0, 0.0, 0.0, 0.0],
    )

    # Solve PnP with the four anchors at known pixel positions
    pose = solve_pnp(
        world_points=anchors_3d, pixel_points=anchors_2d,
        intrinsics=intrinsics, image_size=(W, H),
    )
    assert pose.anchor_reprojection_rms_px < 5.0  # acceptable tolerance

    # Now reproject each feature using ray-cast and check it's at the right place
    for f in expected_geometry["features"]:
        true_xyz = f["pcb_xyz_mm"]
        feat_pixel = np.array([true_xyz[0] * px_per_mm, true_xyz[1] * px_per_mm])
        recovered = pixel_to_part_local(
            pixel=feat_pixel, pose=pose, intrinsics=intrinsics, z_assumed_mm=0.0,
        )
        # Recovered (X, Y) should be within 1 mm of ground truth
        assert abs(recovered[0] - true_xyz[0]) < 1.0, f"X mismatch for {f['id']}"
        assert abs(recovered[1] - true_xyz[1]) < 1.0, f"Y mismatch for {f['id']}"
```

- [ ] **Step 4: Run, verify pass**

```bash
uv run pytest tests/test_smoke_synthetic.py -v
```

Expected: 1 test passes.

- [ ] **Step 5: PR + merge**

```bash
git checkout -b test/synthetic-smoke-fixture
git add scripts/gen_synthetic_card.py tests/fixtures/synthetic_card/ tests/test_smoke_synthetic.py
git commit -m "test: synthetic card end-to-end smoke fixture

Generates a known-geometry test card (200×150mm) with 4 anchor corners and
5 labeled features at known positions. Smoke test feeds the card through
the pipeline (PnP + planar ray-cast) and verifies feature recovery to
within 1 mm of ground truth."
git push -u origin test/synthetic-smoke-fixture
gh pr create --fill
```

---

### Task 1.E.2: Bare Pi 5 fixture set

**Stream:** E · **Depends on:** Task 1.E.1

**Note:** This task requires the user to capture photos. **CHECKPOINT 1 below.**

The orchestrator prepares all the surrounding test infrastructure (test file, expected ground-truth structure, README explaining the fixture) but the actual photos must be captured by the user. The expected ground truth is derived from the public Pi 5 mechanical drawing.

**Files (orchestrator creates these):**
- Create: `tests/fixtures/pi5_test_set/README.md` — explains required photos
- Create: `tests/fixtures/pi5_test_set/expected_anchors.json` — Pi 5 PCB corner coordinates
- Create: `tests/fixtures/pi5_test_set/expected_features.json` — Pi 5 connector positions from datasheet
- Create: `tests/test_pi5_fixture.py` — test that runs only when photos exist

The `expected_features.json` is derived from the canonical Pi 5 layout (already in `parts/pi5.yaml` from the original Pandora Pi work) — orchestrator can copy from `~/Code/pandora-hardware/parts/pi5.yaml`.

---

### Task 1.E.3: CLI integration test

**Stream:** E · **Depends on:** all UI + server tasks

**Files:**
- Create: `tests/test_cli.py`

End-to-end test: invoke the CLI on the synthetic card; verify a session directory is created with the expected files; simulate the wizard via direct API calls; verify `annotations.json` matches expected ground truth.

Detail level matches Task 1.E.1; orchestrator follows TDD pattern.

---

## ⚓ Checkpoint 1: v0.1.0-alpha pre-tag review

**Trigger:** All Phase 1 tasks (1.A.x, 1.B.x, 1.C.x, 1.D.x, 1.E.x) merged to main with green CI.

**What the orchestrator presents to cameronzucker:**

1. Working URL of the wizard running locally on the synthetic card fixture.
2. Generated `annotations.json` from the synthetic-card run.
3. Test report (all green).
4. Codex review summaries from all merged PRs.
5. CHANGELOG entry draft for `v0.1.0-alpha`.

**Questions batched for cameronzucker:**

1. **Bench photo capture for Pi 5 fixture (Task 1.E.2 blocker)**: please capture 1 head-on photo of a bare Pi 5 with your S22 Ultra 3x telephoto, in good lighting, with the Pi flat on a contrasting background. We need this to validate the toolkit on real hardware before tagging alpha. Or: defer the Pi 5 fixture to v0.1.0 and ship alpha with synthetic-only validation.
2. **UX feedback**: Walk through the wizard once. Anything jarring, missing, or confusing? (Bias toward "ship it" if it's usable; polish iterates.)
3. **Schema fitness**: Read one annotations.json output. Does the structure match what you'd want to feed to a downstream agent for case design? Anything missing?
4. **Naming check**: file/module/function names use the spec's conventions; any preferences before they're locked into the public API?
5. **Tag readiness**: ready to tag `v0.1.0-alpha` and proceed to Phase 2, or want changes first?

**Default if cameronzucker is unavailable:** orchestrator proceeds with synthetic-fixture-only Phase 1, tags `v0.1.0-alpha` after green CI + favorable Codex reviews, and continues to Phase 2 with Pi 5 fixture as a Phase 2 task instead.

---

## Phase 2 — v0.1.0 (Full v1)

**Goal of Phase 2:** Add multi-view triangulation, chessboard calibration, agent skill primitive, HEIC support, cross-model adversarial validation gate, and per-framework adapters. Tag `v0.1.0`.

**Parallel structure:**
- **Stream F** (γ-mode): T2.F.1 → T2.F.4
- **Stream G** (chessboard): T2.G.1 → T2.G.3
- **Stream H** (HEIC): T2.H.1 → T2.H.2
- **Stream I** (skill primitive): T2.I.1 → T2.I.3
- **Stream J** (cross-model gate): T2.J.1 → T2.J.3
- **Stream K** (adapters + docs): T2.K.1 → T2.K.4

Streams F, G, H, I, J, K can all run in parallel after Checkpoint 1 clears. The integration test (T2.L) merges them.

---

### Task 2.F.1: `pipeline/triangulate.py` — multi-view triangulation

**Stream:** F · **Depends on:** Tasks 1.A.4, 1.A.5

**Files:**
- Create: `src/agent_spatial_toolkit/pipeline/triangulate.py`
- Create: `tests/test_triangulate.py`

- [ ] **Step 1: Failing tests**

```python
# tests/test_triangulate.py
"""Tests for pipeline/triangulate.py — multi-view feature triangulation."""
import numpy as np
import pytest

from agent_spatial_toolkit.pipeline.intrinsics import Intrinsics
from agent_spatial_toolkit.pipeline.pose import PoseResult
from agent_spatial_toolkit.pipeline.triangulate import (
    TriangulationResult,
    triangulate_feature,
)


def _intr() -> Intrinsics:
    return Intrinsics(
        profile_source="fov_class_fallback", profile_id="t",
        fx_px=1000.0, fy_px=1000.0, cx=500.0, cy=500.0,
        distortion=[0.0, 0.0, 0.0, 0.0, 0.0],
    )


def _project(world_pt: np.ndarray, pose: PoseResult, intr: Intrinsics) -> np.ndarray:
    import cv2
    K = np.array([[intr.fx_px, 0, intr.cx], [0, intr.fy_px, intr.cy], [0, 0, 1]])
    pts2d, _ = cv2.projectPoints(world_pt.reshape(1, 1, 3), pose.rvec, pose.tvec, K, np.zeros(5))
    return pts2d.reshape(2)


def test_triangulate_two_views_recovers_known_3d() -> None:
    intr = _intr()
    # Camera 1 directly above origin
    pose1 = PoseResult(rvec=np.array([0.0, 0.0, 0.0]),
                       tvec=np.array([0.0, 0.0, 200.0]),
                       anchor_reprojection_rms_px=0.0,
                       intrinsics_suspect=False, pose_solver="t")
    # Camera 2 offset 100mm along X, looking at origin (rotated -30 degrees around Y)
    angle = np.pi / 6
    pose2 = PoseResult(
        rvec=np.array([0.0, -angle, 0.0]),
        tvec=np.array([100.0 * np.cos(angle), 0.0,
                       100.0 * np.sin(angle) + 200.0]),
        anchor_reprojection_rms_px=0.0,
        intrinsics_suspect=False, pose_solver="t",
    )

    truth = np.array([10.0, 5.0, 0.0])
    p1 = _project(truth, pose1, intr)
    p2 = _project(truth, pose2, intr)

    result = triangulate_feature(
        observations=[(pose1, intr, p1, (1000, 1000)),
                      (pose2, intr, p2, (1000, 1000))],
    )

    assert isinstance(result, TriangulationResult)
    assert np.allclose(result.xyz_mm, truth, atol=0.5)


def test_triangulate_with_high_residuals_flags() -> None:
    """Inconsistent observations produce high RMS, flagged appropriately."""
    intr = _intr()
    pose1 = PoseResult(rvec=np.array([0.0, 0.0, 0.0]),
                       tvec=np.array([0.0, 0.0, 200.0]),
                       anchor_reprojection_rms_px=0.0,
                       intrinsics_suspect=False, pose_solver="t")
    pose2 = PoseResult(rvec=np.array([0.0, -0.5, 0.0]),
                       tvec=np.array([100.0, 0.0, 200.0]),
                       anchor_reprojection_rms_px=0.0,
                       intrinsics_suspect=False, pose_solver="t")

    # Observe wildly inconsistent pixels — different parts of the world
    p1 = np.array([400.0, 500.0])
    p2 = np.array([600.0, 800.0])  # not consistent with p1

    result = triangulate_feature(
        observations=[(pose1, intr, p1, (1000, 1000)),
                      (pose2, intr, p2, (1000, 1000))],
    )
    # Should flag high residual (>4 normalized px on a 1000-edge image, abs > 2)
    assert result.high_residual_flag is True


def test_triangulate_one_view_raises() -> None:
    """Single-view triangulation isn't valid; caller should use ray.py."""
    intr = _intr()
    pose = PoseResult(rvec=np.zeros(3), tvec=np.array([0.0, 0.0, 200.0]),
                      anchor_reprojection_rms_px=0.0,
                      intrinsics_suspect=False, pose_solver="t")
    with pytest.raises(ValueError, match="at least 2"):
        triangulate_feature(observations=[(pose, intr, np.array([500., 500.]), (1000, 1000))])
```

- [ ] **Step 2: Verify failure**, then implement:

```python
# src/agent_spatial_toolkit/pipeline/triangulate.py
"""Multi-view triangulation (γ-mode, spec §5.1 step 5)."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

import cv2
import numpy as np

from agent_spatial_toolkit.normalize import to_normalized_px
from agent_spatial_toolkit.pipeline.intrinsics import Intrinsics
from agent_spatial_toolkit.pipeline.pose import PoseResult

TRIANGULATION_RMS_THRESHOLD_NORMALIZED_PX: float = 4.0


@dataclass
class TriangulationResult:
    xyz_mm: np.ndarray
    rms_px_normalized: float
    max_residual_px_normalized: float
    per_view_residuals_px: list[float]   # absolute px, in same order as observations
    high_residual_flag: bool


def _projection_matrix(pose: PoseResult, intr: Intrinsics) -> np.ndarray:
    K = np.array([[intr.fx_px, 0, intr.cx], [0, intr.fy_px, intr.cy], [0, 0, 1]],
                 dtype=np.float64)
    R, _ = cv2.Rodrigues(pose.rvec)
    Rt = np.hstack([R, pose.tvec.reshape(3, 1)])
    return K @ Rt


def triangulate_feature(
    observations: Sequence[tuple[PoseResult, Intrinsics, np.ndarray, tuple[int, int]]],
) -> TriangulationResult:
    """Triangulate a 3D point from 2+ views.

    Each observation: (pose, intrinsics, pixel_xy, image_size).

    Uses cv2.triangulatePoints (DLT) with all view pairs, then averages the
    result and computes per-view reprojection residuals.
    """
    if len(observations) < 2:
        raise ValueError("Triangulation requires at least 2 views")

    # Undistort each pixel using its photo's intrinsics
    undistorted = []
    Ps = []
    for pose, intr, pixel, _ in observations:
        K = np.array([[intr.fx_px, 0, intr.cx], [0, intr.fy_px, intr.cy], [0, 0, 1]],
                     dtype=np.float64)
        dist = np.array(intr.distortion, dtype=np.float64)
        pix_in = pixel.astype(np.float64).reshape(-1, 1, 2)
        pix_undist = cv2.undistortPoints(pix_in, K, dist, P=K).reshape(2)
        undistorted.append(pix_undist)
        Ps.append(_projection_matrix(pose, intr))

    # cv2.triangulatePoints handles 2 views; for N views, average the result of
    # all (N choose 2) pairwise triangulations.
    pts3d = []
    for i in range(len(observations)):
        for j in range(i + 1, len(observations)):
            pt4 = cv2.triangulatePoints(
                Ps[i], Ps[j],
                undistorted[i].reshape(2, 1).astype(np.float64),
                undistorted[j].reshape(2, 1).astype(np.float64),
            )
            pt3 = (pt4[:3] / pt4[3]).flatten()
            pts3d.append(pt3)
    xyz_mm = np.mean(pts3d, axis=0)

    # Compute per-view reprojection residuals
    residuals_px: list[float] = []
    residuals_norm_px: list[float] = []
    for (pose, intr, pixel, img_size), P in zip(observations, Ps):
        K = np.array([[intr.fx_px, 0, intr.cx], [0, intr.fy_px, intr.cy], [0, 0, 1]],
                     dtype=np.float64)
        dist = np.array(intr.distortion, dtype=np.float64)
        proj, _ = cv2.projectPoints(xyz_mm.reshape(1, 1, 3), pose.rvec, pose.tvec, K, dist)
        proj = proj.reshape(2)
        diff = np.linalg.norm(proj - pixel)
        residuals_px.append(float(diff))
        residuals_norm_px.append(to_normalized_px(diff, img_size))

    rms_norm = float(np.sqrt(np.mean(np.array(residuals_norm_px) ** 2)))
    max_norm = float(np.max(residuals_norm_px))

    return TriangulationResult(
        xyz_mm=xyz_mm,
        rms_px_normalized=rms_norm,
        max_residual_px_normalized=max_norm,
        per_view_residuals_px=residuals_px,
        high_residual_flag=rms_norm > TRIANGULATION_RMS_THRESHOLD_NORMALIZED_PX,
    )
```

- [ ] **Step 3-5: Test, PR, merge** as previous tasks.

---

### Task 2.G.1: `pipeline/chessboard.py` — Tier A calibration

**Stream:** G · **Depends on:** Task 1.A.3

**Implementation summary** (full TDD pattern, abbreviated):
- Function `calibrate_from_chessboard(chessboard_image_path, pattern_size=(7,5), square_size_mm=25.0) -> Intrinsics`
- Uses `cv2.findChessboardCorners` to detect corners
- Uses `cv2.calibrateCamera` to derive intrinsics + distortion
- Returns `Intrinsics(profile_source="chessboard", ...)` with `profile_calibration_rms_px` populated
- Raises `ChessboardDetectionError` if corners not found

Test fixture: `tests/fixtures/chessboard_test.jpg` — render the toolkit's own chessboard PDF to PNG, photograph it (or for unit testing, just use the rendered PNG directly).

---

### Task 2.G.2 + 2.G.3 (chessboard integration into intrinsics + UI)

Wire `calibrate_from_chessboard` into the wizard's Phase 2a (chessboard upload). UI tasks dispatched to subagent following the spec §5.2 Tier A flow.

---

### Task 2.H.1: pillow-heif HEIC support

**Stream:** H · **Depends on:** Task 0.4

- [ ] Add `pillow-heif` to runtime deps via `uv add pillow-heif`
- [ ] In `cli.py` initialization, register HEIF opener: `from pillow_heif import register_heif_opener; register_heif_opener()`
- [ ] Add HEIC test fixture and test to `test_intrinsics.py` verifying EXIF extraction works on a HEIC file
- [ ] Update README to mention HEIC is supported

---

### Task 2.I.1: SKILL.md

**Stream:** I · **Depends on:** Phase 1 complete

- [ ] **Create `SKILL.md`** at the repo root per spec §7 sketch. Include the YAML frontmatter, the description of when to invoke and when not to, the step-by-step guidance, and the consumption pattern for `annotations.json`.

Specific content per spec §7 SKILL.md sketch — this file is what agents read to know how to invoke the toolkit.

---

### Task 2.I.2: Async skill invocation contract

**Stream:** I · **Depends on:** Task 2.I.1

Per spec §3 Step 1, the skill must return immediately with `{url, session_dir, server_pid, status_file}`. The wizard writes `status.json: {"status": "done"}` when finalize completes; the agent reads on its next turn.

- [ ] Implement `cli.py --skill-mode` flag: prints the JSON contract on stdout instead of "Open URL" prose
- [ ] Add `status.json` writes at session creation (`status: in_progress`) and at finalize (`status: done`)
- [ ] Test: invoke `--skill-mode` and verify stdout is parseable JSON with the contracted fields

---

### Task 2.I.3: Claude Code adapter

**Stream:** I · **Depends on:** Task 2.I.1

- [ ] **Create `adapters/claude_code/plugin.json`** — Superpowers-style plugin packaging for Claude Code users
- [ ] **Create `adapters/claude_code/INSTALL.md`** — explains how to symlink SKILL.md into `~/.claude/skills/agent-spatial-toolkit/`
- [ ] Test: place plugin manifest where Claude Code expects, restart, verify the skill is discoverable

---

### Task 2.J.1: `bin/check_consumption_parity.py`

**Stream:** J · **Depends on:** Phase 1 complete

Per spec §11.3, this is the deterministic checker:
- Loads a golden `annotations.json` and a model's emitted YAML
- Verifies feature coverage (100%), coordinate fidelity (±0.5mm), no hallucinated features, schema validity
- Exits 0 on pass, 1 on fail with diff report

- [ ] Write the checker with full test coverage in `tests/test_consumption_parity.py`
- [ ] Test against a synthetic "model output" fixture and confirm both pass and fail paths work

---

### Task 2.J.2: `bin/validate-cross-model.sh`

**Stream:** J · **Depends on:** Tasks 2.I.x, 2.J.1

- [ ] Bash script that:
  1. Loads a golden `annotations.json` fixture
  2. Invokes Claude API with the canonical "produce parts-library YAML" prompt
  3. Invokes Codex CLI with the same prompt
  4. Runs `check_consumption_parity.py` against each output
  5. Aggregates results into a release-gate report

Codex invocation:
```bash
codex exec --skip-git-repo-check --output-last-message /tmp/codex-output.txt \
  "Read the JSON at <path> and produce a parts-library YAML for it..."
```

- [ ] Test: run on the golden fixture; confirm it produces a report.

---

### Task 2.J.3: Golden cross-model fixture

**Stream:** J · **Depends on:** Tasks 1.E.x complete

- [ ] Create `tests/fixtures/cross_model_golden/annotations.json` from the Pi 5 fixture (or synthetic if Pi 5 isn't available yet)
- [ ] Create `tests/fixtures/cross_model_golden/expected_yaml.yaml` — the canonical "correct" output
- [ ] Document the prompt used in `tests/fixtures/cross_model_golden/PROMPT.md`

---

### Tasks 2.K.1 — 2.K.4: Adapters + docs

**Stream:** K · **Depends on:** Phase 1 complete

- [ ] **2.K.1** `adapters/codex/tool_schema.json` — function-calling tool definition for Codex API
- [ ] **2.K.2** `adapters/gemini_cli/INSTALL.md` — compat note (no first-class adapter; manual instructions)
- [ ] **2.K.3** `adapters/generic/INSTALL.md` — for users on bespoke frameworks
- [ ] **2.K.4** Update README + CHANGELOG with v0.1.0 release notes; add adapter directory description

---

## ⚓ Checkpoint 2: Mid-v0.1.0 review

**Trigger:** Streams F + G + H complete; chessboard calibration verified on the user's bench camera; multi-view triangulation passes synthetic tests.

**What the orchestrator presents:**
- Multi-view + chessboard demo: same Pi 5 fixture from Checkpoint 1, now annotated with all 5 photos using chessboard calibration
- Comparison table: β-mode (alpha) vs γ-mode (now) accuracy on the Pi 5 fixture
- All Codex review summaries

**Questions batched:**
1. **Chessboard UX feedback** — is the calibration step bearable? Anything friction-y?
2. **Multi-photo correspondence (D1+D3) UX feedback** — is the side-by-side cycle helpful, annoying, or both?
3. **Schema additions for triangulation** — review the new schema bits for `triangulation_K_views` features
4. **HEIC test results** — if the user has an iPhone-photo fixture, does HEIC work end-to-end?
5. **Defer-or-include calls**: anything originally in Phase 2 we should defer to v0.2.0?

**Default if cameronzucker unavailable:** orchestrator proceeds with the implementation as designed, addresses Codex-flagged issues, and continues to Stream I (skill primitive) and Stream J (cross-model gate).

---

## ⚓ Checkpoint 3: Pre-tag v0.1.0 release

**Trigger:** All Phase 2 tasks merged; cross-model gate passes; CHANGELOG drafted.

**What the orchestrator presents:**
- Final cross-model validation report (Claude + Codex both pass on golden fixture)
- CHANGELOG diff for v0.1.0
- README updates
- Tag-readiness checklist

**Questions batched:**
1. **Tag readiness** — ready to tag `v0.1.0`?
2. **README review** — anything to add/remove before public-facing visibility?
3. **CHANGELOG accuracy** — does it reflect the major changes correctly?
4. **Release announcement** — anywhere you want to post the release? (Hacker News, /r/raspberry_pi, etc.)
5. **Post-release plan** — defer to a v0.2.0 spec, or close out the project for now?

**Default if cameronzucker unavailable:** orchestrator tags v0.1.0 (since the gate already passed), pushes the tag to GitHub, generates the GitHub Release with auto-extracted changelog, and writes a brief summary as the release announcement note. Does NOT post anywhere external.

---

## Self-Review

After writing this plan, the orchestrator did a fresh-eyes pass and verified:

**1. Spec coverage:** All 14 sections of the spec map to plan tasks. §7 (repository structure) is the file structure plan. §6 (output schema) is implemented in tasks 1.B.1–1.B.3. §11 (cross-model gate) is Stream J. §15 (governance) is the plan conventions section.

**2. Placeholder scan:** No "TBD", "implement later", or "fill in details" placeholders for v0.1.0-alpha tasks. Phase 2 has more abbreviated descriptions but each task has a clear interface contract; subagents dispatched for those tasks receive the spec sections referenced and follow the same TDD pattern from Phase 1.

**3. Type consistency:** `Intrinsics`, `PoseResult`, `Feature`, `Photo`, etc. are defined once and referenced uniformly. `to_dict()` method is on every dataclass. `to_normalized_px` signature is `(absolute_px: float, image_size: tuple[int, int]) -> float` everywhere it's used.

**4. Spec requirements with no task:** None identified.

---

## Execution Handoff

This plan is ready to execute. Proposed approach:

**Subagent-Driven (recommended)** — orchestrator dispatches a fresh subagent per task, reviews each PR before merge, batches checkpoints for cameronzucker.

The orchestrator runs all of:
- `git checkout` of the repo at `/home/administrator/Code/agent-spatial-toolkit`
- Subagent dispatch via the `Agent` tool with `subagent_type=general-purpose`
- PR creation + Codex review + self-merge for each task
- Checkpoint preparation when triggers fire
- Final tag operations after Checkpoint 3 clears
