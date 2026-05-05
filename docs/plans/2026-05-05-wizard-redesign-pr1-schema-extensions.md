# Wizard Redesign PR-1 — Schema Extensions Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Extend the `annotations.json` schema with the additive fields and quality flags required by the redesigned wizard. Backwards-compatible — every existing valid `annotations.json` still parses and validates after this PR.

**Architecture:** Pure additions to `src/agent_spatial_toolkit/schema/` — two new closed-enum quality flags in `validators.py`, two new optional `Feature` fields in `models.py`, a docstring clarification noting that the wizard's "project name" maps to existing `Part.display_name` (no schema change required for that), and one regression test that locks in backwards-compat against the existing 139-test fixture pattern.

**Tech Stack:** Python 3.10+, dataclasses, pytest. No new dependencies.

**Reference:** [docs/specs/2026-05-05-wizard-ux-redesign-design.md](../specs/2026-05-05-wizard-ux-redesign-design.md) §3 (Components, Endpoint changes), §4 (Skip-feature affordance JSON example), §5 (Bucket 2 soft warnings), §6 (PR-1 row).

**All implementer and reviewer subagents dispatched on Opus 4.7** (`model: "opus"` per Agent tool call). See `feedback_subagent_model_opus_for_spatial.md` in auto-memory.

---

## File Structure

| File | Action | Responsibility |
|---|---|---|
| `src/agent_spatial_toolkit/schema/validators.py` | Modify | Add `intrinsics_estimated` and `underside_unverified` to `QUALITY_FLAGS`. Existing closed-enum validation logic unchanged. |
| `src/agent_spatial_toolkit/schema/models.py` | Modify | Add `noisy: bool = False` and `warning: str \| None = None` to `Feature`. Update `Feature.to_dict()` to emit them conditionally (only when non-default). Add a docstring note on `Part` clarifying that `display_name` is what the wizard's "project name" input populates. |
| `tests/test_schema.py` | Modify | Validator tests live here (no separate `test_validators.py` — corrected from the original draft). Adds: two tests for `intrinsics_estimated` flag (Task 1), two tests for `underside_unverified` flag (Task 2), four tests for `Feature.noisy` and `Feature.warning` (Task 3), and a one-line update to the existing `test_quality_flags_constant_is_complete` invariant per Task 1 and Task 2. |
| `tests/test_schema_backwards_compat.py` | Create | New test file — one regression test that loads a fixture representing a v0.0.1-shaped `annotations.json` (the shape produced by current `main`) and asserts every dataclass parses + emits + the round-trip preserves the original fields. Locks in the additive-only contract. |

No changes to `pipeline/`, `server/`, or `ui/` in this PR.

---

## Task 1: Add `intrinsics_estimated` quality flag

**Files:**
- Modify: `src/agent_spatial_toolkit/schema/validators.py:14-20` (the `QUALITY_FLAGS` dict)
- Test: `tests/test_schema.py` (existing file — validator tests live here; no separate `test_validators.py`)

**Status (post-implementation):** ✅ Shipped at commit `39a938c`. Implementer correctly identified that `tests/test_validators.py` does not exist and used `tests/test_schema.py` per the actual codebase convention. Implementer also updated `test_quality_flags_constant_is_complete` (closed-enum invariant) in the same commit — necessary in-scope work the original plan didn't anticipate. Spec-compliance and code-quality reviews both passed.

This flag fires when EXIF-derived camera intrinsics were missing on at least one photo and the wizard fell back to FOV-class estimation (per design §3 "Camera intrinsics handling"). Parameterless — applies session-wide, no `:<id>` suffix.

- [x] **Step 1: Write the failing tests** *(shipped)*

```python
# Append to tests/test_schema.py
def test_intrinsics_estimated_flag_accepted() -> None:
    # Should not raise — parameterless flag, valid as-is.
    validate_quality_flag("intrinsics_estimated")


def test_intrinsics_estimated_flag_rejects_suffix() -> None:
    # Parameterless flags must not carry an :<id> suffix.
    with pytest.raises(ValidationError, match="does not take an :<id> suffix"):
        validate_quality_flag("intrinsics_estimated:photo_001")
```

(Imports already present at top of `tests/test_schema.py`; no new imports added.)

- [x] **Step 2: Run tests to verify they fail** *(shipped)*

Run: `uv run pytest tests/test_schema.py::test_intrinsics_estimated_flag_accepted tests/test_schema.py::test_intrinsics_estimated_flag_rejects_suffix -v`

Expected: Both FAIL with `ValidationError: 'intrinsics_estimated' is not a recognized flag.`

- [x] **Step 3: Add the flag to `QUALITY_FLAGS`** *(shipped)*

In `src/agent_spatial_toolkit/schema/validators.py`, modify the `QUALITY_FLAGS` dict:

```python
QUALITY_FLAGS: dict[str, bool] = {
    "intrinsics_suspect_high_anchor_rms": False,
    "intrinsics_session_recommend_chessboard": False,
    "intrinsics_estimated": False,  # NEW: EXIF missing, fell back to FOV-class (design §3)
    "photo_excluded_due_to_pose_failure": True,
    "feature_clicked_only_once": True,
    "feature_high_triangulation_rms": True,
    "ultrawide_lens_rejected": True,
}
```

Also update `test_quality_flags_constant_is_complete` in `tests/test_schema.py` to include the new flag in its expected-set assertion. Without this, that invariant test fails.

- [x] **Step 4: Run tests to verify they pass** *(shipped)*

Run: `uv run pytest tests/test_schema.py -v`

Expected: All tests PASS, including the two new ones and the updated invariant. No existing test should regress.

- [x] **Step 5: Commit** *(shipped at `39a938c`)*

```bash
git add src/agent_spatial_toolkit/schema/validators.py tests/test_schema.py
GIT_AUTHOR_NAME="cameronzucker" GIT_AUTHOR_EMAIL="cameronzucker@gmail.com" \
GIT_COMMITTER_NAME="cameronzucker" GIT_COMMITTER_EMAIL="cameronzucker@gmail.com" \
git commit -m "feat(schema): add intrinsics_estimated quality flag

Parameterless flag fired when EXIF-derived camera intrinsics were
missing on at least one photo and the wizard fell back to FOV-class
estimation. Used by the redesigned wizard to warn LLM consumers that
coordinate accuracy may be ±2 mm rather than ±0.5 mm. Backwards-
compatible additive change.

Refs: docs/specs/2026-05-05-wizard-ux-redesign-design.md §3, §5.
Part of: Wizard UX redesign PR-1.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 2: Add `underside_unverified` quality flag

**Files:**
- Modify: `src/agent_spatial_toolkit/schema/validators.py:14-21` (the `QUALITY_FLAGS` dict, after Task 1's addition)
- Test: `tests/test_schema.py` (Task 1's implementer corrected `test_validators.py` → `test_schema.py` since `test_validators.py` doesn't exist; this task follows the same convention)

This flag fires when at least one labeled feature could not be captured from a true bottom-up view because the user's reference object setup couldn't support it (per design §1 non-goals — flat reference fails beyond ~70° from vertical). Parameterless.

**Note on `test_quality_flags_constant_is_complete`:** Task 1's implementer also updated the closed-enum invariant test in `tests/test_schema.py` to include `intrinsics_estimated`. This task must do the same for `underside_unverified` — append it to the expected-set in that invariant test, otherwise the test will fail.

- [ ] **Step 1: Write the failing tests**

```python
# Append to tests/test_schema.py (the existing validator-tests section, near the Task 1 additions)
def test_underside_unverified_flag_accepted() -> None:
    validate_quality_flag("underside_unverified")


def test_underside_unverified_flag_rejects_suffix() -> None:
    with pytest.raises(ValidationError, match="does not take an :<id> suffix"):
        validate_quality_flag("underside_unverified:feature_42")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_schema.py::test_underside_unverified_flag_accepted tests/test_schema.py::test_underside_unverified_flag_rejects_suffix -v`

Expected: Both FAIL with `ValidationError: 'underside_unverified' is not a recognized flag.`

- [ ] **Step 3: Add the flag to `QUALITY_FLAGS` and update the closed-enum completeness invariant**

In `src/agent_spatial_toolkit/schema/validators.py`:

```python
QUALITY_FLAGS: dict[str, bool] = {
    "intrinsics_suspect_high_anchor_rms": False,
    "intrinsics_session_recommend_chessboard": False,
    "intrinsics_estimated": False,
    "underside_unverified": False,  # NEW: bottom-of-part not photographable; v1 non-goal (design §1)
    "photo_excluded_due_to_pose_failure": True,
    "feature_clicked_only_once": True,
    "feature_high_triangulation_rms": True,
    "ultrawide_lens_rejected": True,
}
```

In `tests/test_schema.py`, find `test_quality_flags_constant_is_complete` and add `"underside_unverified"` to the expected-set assertion (it's the closed-enum invariant test — without this update, the test will fail).

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_schema.py -v`

Expected: All tests PASS, including the two new tests AND the updated invariant test.

- [ ] **Step 5: Commit**

```bash
git add src/agent_spatial_toolkit/schema/validators.py tests/test_schema.py
GIT_AUTHOR_NAME="cameronzucker" GIT_AUTHOR_EMAIL="cameronzucker@gmail.com" \
GIT_COMMITTER_NAME="cameronzucker" GIT_COMMITTER_EMAIL="cameronzucker@gmail.com" \
git commit -m "feat(schema): add underside_unverified quality flag

Parameterless flag fired when the wizard's flat reference-object
setup couldn't capture an underside view (geometric limitation of
flat references at >70° from vertical, per design §1 non-goals).
LLM consumers see this and treat any underside-mounted features as
approximate. Backwards-compatible additive change.

Refs: docs/specs/2026-05-05-wizard-ux-redesign-design.md §1, §5.
Part of: Wizard UX redesign PR-1.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 3: Add `noisy` and `warning` optional fields to `Feature`

**Files:**
- Modify: `src/agent_spatial_toolkit/schema/models.py:156-176` (the `Feature` dataclass and its `to_dict` method)
- Test: `tests/test_schema.py`

Per design §4 (tier table) + §5 (Bucket 2): a feature can be marked `noisy: true` when its triangulation reprojection error falls in the 0.5–1.0 mm yellow band, and can carry a free-text `warning` (e.g., for single-view-planar fallbacks). Both fields are optional with safe defaults; both are emitted in `to_dict()` only when they carry a non-default value (consistent with the existing `user_tags` pattern at line 173-174).

- [ ] **Step 1: Write the failing tests**

```python
# Append to tests/test_schema.py
from agent_spatial_toolkit.schema.models import (
    Feature,
    FeatureClick,
    FeatureMeasurement,
)


def _make_feature(noisy: bool = False, warning: str | None = None) -> Feature:
    """Helper: minimal valid Feature for noisy/warning tests."""
    return Feature(
        id="usb_c",
        visible_in=["photo_001", "photo_002"],
        pcb_xyz_mm=(12.4, 28.3, 5.1),
        measurements=FeatureMeasurement(
            method="triangulation_2_views",
            per_photo_clicks=[
                FeatureClick(photo="photo_001", pixel=(100, 200), reprojection_residual_px=0.4),
                FeatureClick(photo="photo_002", pixel=(110, 210), reprojection_residual_px=0.3),
            ],
            triangulation_rms_px=0.35,
            max_residual_px=0.4,
        ),
        noisy=noisy,
        warning=warning,
    )


def test_feature_noisy_default_false_omitted_from_dict():
    f = _make_feature()  # noisy defaults to False
    d = f.to_dict()
    assert "noisy" not in d, "default-False noisy must not appear in to_dict() output"


def test_feature_noisy_true_emitted_in_dict():
    f = _make_feature(noisy=True)
    d = f.to_dict()
    assert d["noisy"] is True


def test_feature_warning_default_none_omitted_from_dict():
    f = _make_feature()  # warning defaults to None
    d = f.to_dict()
    assert "warning" not in d, "default-None warning must not appear in to_dict() output"


def test_feature_warning_string_emitted_in_dict():
    f = _make_feature(warning="Z is approximate; only 1 view available")
    d = f.to_dict()
    assert d["warning"] == "Z is approximate; only 1 view available"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_schema.py -k "noisy or warning" -v`

Expected: All four FAIL with `TypeError: Feature.__init__() got an unexpected keyword argument 'noisy'` (or similar for `warning`).

- [ ] **Step 3: Add the fields and update `to_dict()`**

In `src/agent_spatial_toolkit/schema/models.py`, modify the `Feature` dataclass (currently lines 156-175):

```python
@dataclass
class Feature:
    """A named feature with its part-local 3D position."""

    id: str
    visible_in: list[str]
    pcb_xyz_mm: tuple[float, float, float]
    measurements: FeatureMeasurement
    user_tags: list[str] = field(default_factory=list)
    noisy: bool = False
    """True when triangulation reprojection error falls in the yellow band
    (0.5–1.0 mm). Set by the redesigned wizard's tier-classification logic.
    Emitted only when True (default-False is omitted)."""
    warning: str | None = None
    """Free-text human-readable note (e.g., 'Z is approximate; only 1 view
    available' for single-view-planar features). Emitted only when set."""

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "id": self.id,
            "visible_in": list(self.visible_in),
            "pcb_xyz_mm": list(self.pcb_xyz_mm),
            "measurements": self.measurements.to_dict(),
        }
        if self.user_tags:
            d["user_tags"] = list(self.user_tags)
        if self.noisy:
            d["noisy"] = True
        if self.warning is not None:
            d["warning"] = self.warning
        return d
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_schema.py -v`

Expected: All tests PASS — both the four new tests and all existing ones (the existing tests should be unaffected because both new fields have safe defaults).

- [ ] **Step 5: Commit**

```bash
git add src/agent_spatial_toolkit/schema/models.py tests/test_schema.py
GIT_AUTHOR_NAME="cameronzucker" GIT_AUTHOR_EMAIL="cameronzucker@gmail.com" \
GIT_COMMITTER_NAME="cameronzucker" GIT_COMMITTER_EMAIL="cameronzucker@gmail.com" \
git commit -m "feat(schema): add noisy and warning optional fields to Feature

Two additive fields on the Feature dataclass for the redesigned
wizard's tier-classification system:

- noisy (bool, default False): set when per-feature triangulation
  reprojection error is in the 0.5-1.0 mm yellow band. Emitted in
  to_dict() only when True.
- warning (str | None, default None): free-text human-readable note
  for single-view-planar features and other approximate cases.
  Emitted in to_dict() only when set.

Both follow the existing user_tags omit-when-default pattern, so
the produced annotations.json is byte-identical for features that
don't use them. Backwards-compatible.

Refs: docs/specs/2026-05-05-wizard-ux-redesign-design.md §4, §5.
Part of: Wizard UX redesign PR-1.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 4: Document `project_name` → `Part.display_name` mapping

**Files:**
- Modify: `src/agent_spatial_toolkit/schema/models.py:184-200` (the `Part` dataclass)
- No test (docstring change only).

The redesigned wizard collects a "project name" string from the user (design §2 step 1: *"What are you capturing?"*) and stores it verbatim in `annotations.json`. The existing `Part.display_name` field already serves this role — no schema change is required, but the mapping is non-obvious to a future implementer reading the code, who might add a redundant `project_name` field. Document the mapping in the `Part` class docstring so this knowledge is encoded next to the type.

- [ ] **Step 1: Update the `Part` docstring**

In `src/agent_spatial_toolkit/schema/models.py`, add a class docstring to `Part` (it currently has none):

```python
@dataclass
class Part:
    """Identity and human-readable description of the captured assembly.

    The redesigned wizard's "project name" input (design §2 step 1 —
    "What are you capturing?") populates `display_name`. Do NOT add a
    separate `project_name` field; the mapping is intentional and the
    wizard wires its first-screen text input directly to this field.
    """

    id: str
    display_name: str | None = None
    part_class: str | None = None
    notes: str | None = None
    # ... existing to_dict() method unchanged
```

The existing `to_dict()` method below the docstring is unchanged.

- [ ] **Step 2: Run tests to verify nothing regressed**

Run: `uv run pytest tests/test_schema.py -v`

Expected: All tests PASS (this is a docstring-only change; no behavior changed).

- [ ] **Step 3: Commit**

```bash
git add src/agent_spatial_toolkit/schema/models.py
GIT_AUTHOR_NAME="cameronzucker" GIT_AUTHOR_EMAIL="cameronzucker@gmail.com" \
GIT_COMMITTER_NAME="cameronzucker" GIT_COMMITTER_EMAIL="cameronzucker@gmail.com" \
git commit -m "docs(schema): note Part.display_name is the wizard's project name target

The redesigned wizard's 'What are you capturing?' first-screen input
populates Part.display_name directly. Future implementers reading the
schema might be tempted to add a separate 'project_name' field; this
docstring locks in the intentional mapping so that doesn't happen.

No behavior change. Backwards-compatible.

Refs: docs/specs/2026-05-05-wizard-ux-redesign-design.md §2 step 1.
Part of: Wizard UX redesign PR-1.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 5: Backwards-compatibility regression test

**Files:**
- Create: `tests/test_schema_backwards_compat.py`
- No source changes — this task locks in the contract that PR-1's additions don't break old data.

This test loads a representative `annotations.json` shape (v0.0.1 — what `main` produces today) and asserts every dataclass parses, emits, and round-trips. The intent: lock in *now* that nothing in PR-1's additions broke the additive-only contract. Future PRs in the wizard redesign series that touch the schema will run against this same regression suite.

- [ ] **Step 1: Write the failing test (file doesn't exist yet)**

Create `tests/test_schema_backwards_compat.py`:

```python
"""Lock in backwards-compat: v0.0.1-shaped annotations.json must still
round-trip cleanly after PR-1's additive schema changes.

If this test ever fails, the wizard-redesign PR series violated its
'additive only' commitment per docs/specs/2026-05-05-wizard-ux-redesign-design.md §6.
"""

from __future__ import annotations

import json
from pathlib import Path

from agent_spatial_toolkit.schema.models import (
    AnchorClick,
    Annotations,
    CameraDetected,
    Feature,
    FeatureClick,
    FeatureMeasurement,
    Part,
    Photo,
    QualitySummary,
    ReferenceFrame,
    SessionArtifacts,
)
from agent_spatial_toolkit.schema.validators import validate_annotations


def _v0_0_1_fixture() -> Annotations:
    """A representative annotations.json shape from current main (pre-redesign)."""
    return Annotations(
        schema_version=1,
        toolkit_version="0.0.1",
        generated_at="2026-05-04T12:00:00Z",
        part=Part(id="testpart_001", display_name="Test PCB", part_class="pcb"),
        reference_frame=ReferenceFrame(
            origin_description="upper-left corner of PCB top face",
            x_axis_description="along the long edge",
            y_axis_description="along the short edge",
            z_axis_description="up out of PCB top face",
        ),
        photos=[
            Photo(
                id="photo_001",
                path="photos/photo_001.jpg",
                sha256="a" * 64,
                camera_detected=CameraDetected(
                    make="Apple", model="iPhone 14", lens_label="main", detection_source="exif"
                ),
                intrinsics={"fx_px": 3000.0, "fy_px": 3000.0, "cx_px": 2016.0, "cy_px": 1512.0},
                pose={"rvec": [0.0, 0.0, 0.0], "tvec": [0.0, 0.0, 300.0]},
                anchors_clicked=[
                    AnchorClick(id="corner_a", pcb_xyz_mm=(0.0, 0.0, 0.0), pixel=(100, 100)),
                ],
            ),
        ],
        features=[
            Feature(
                id="usb_c",
                visible_in=["photo_001"],
                pcb_xyz_mm=(12.4, 28.3, 0.0),
                measurements=FeatureMeasurement(
                    method="planar_intersection",
                    per_photo_clicks=[
                        FeatureClick(photo="photo_001", pixel=(150, 200)),
                    ],
                    z_assumed_mm=0.0,
                    z_assumed_reason="PCB top, z=0 assumed",
                ),
            ),
        ],
        quality_summary=QualitySummary(
            feature_count=1,
            triangulated_count=0,
            z_assumed_count=1,
            median_reprojection_rms_px=None,
            max_reprojection_rms_px=None,
            flags=["feature_clicked_only_once:usb_c"],
        ),
        session_artifacts=SessionArtifacts(
            overlay_pngs=["overlays/photo_001.png"],
            events_jsonl="events.jsonl",
            manifest="manifest.json",
        ),
    )


def test_v0_0_1_fixture_emits_and_validates():
    """An old-shape annotations.json must still emit and validate after PR-1."""
    fixture = _v0_0_1_fixture()
    d = fixture.to_dict()
    # Validate against the (extended) closed-flag enum.
    validate_annotations(d)


def test_v0_0_1_fixture_omits_new_optional_fields():
    """PR-1's additive fields (noisy, warning) must not appear in
    output when not set — guarantees byte-stable output for old-style data."""
    fixture = _v0_0_1_fixture()
    d = fixture.to_dict()
    feature_dict = d["features"][0]
    assert "noisy" not in feature_dict, (
        "default-False noisy must be omitted; old-shape annotations.json "
        "must remain byte-identical for features that don't use the new field"
    )
    assert "warning" not in feature_dict, (
        "default-None warning must be omitted; old-shape annotations.json "
        "must remain byte-identical for features that don't use the new field"
    )


def test_v0_0_1_fixture_round_trips_through_json():
    """JSON-serialize and parse the fixture; required keys all survive."""
    fixture = _v0_0_1_fixture()
    text = json.dumps(fixture.to_dict(), ensure_ascii=False)
    parsed = json.loads(text)
    assert parsed["schema_version"] == 1
    assert parsed["part"]["id"] == "testpart_001"
    assert parsed["part"]["display_name"] == "Test PCB"
    assert parsed["features"][0]["id"] == "usb_c"
    assert parsed["features"][0]["measurements"]["method"] == "planar_intersection"
    assert parsed["quality_summary"]["flags"] == ["feature_clicked_only_once:usb_c"]
```

- [ ] **Step 2: Run the test to verify it passes immediately**

Run: `uv run pytest tests/test_schema_backwards_compat.py -v`

Expected: All three tests PASS on first run. (PR-1's additive changes don't affect this fixture's shape because the new fields have safe defaults that omit from `to_dict()`.)

If any test fails: **STOP**. Failure means PR-1 broke the additive-only contract somewhere — investigate which task introduced the regression before committing this test.

- [ ] **Step 3: Commit**

```bash
git add tests/test_schema_backwards_compat.py
GIT_AUTHOR_NAME="cameronzucker" GIT_AUTHOR_EMAIL="cameronzucker@gmail.com" \
GIT_COMMITTER_NAME="cameronzucker" GIT_COMMITTER_EMAIL="cameronzucker@gmail.com" \
git commit -m "test(schema): backwards-compat regression suite for v0.0.1 shape

Locks in the additive-only commitment from the wizard redesign
migration plan: every annotations.json that current main produces
must continue to parse, validate, and emit byte-identically after
PR-1's schema additions (and after every subsequent wizard-redesign
PR that touches the schema).

If this test ever fails, the wizard-redesign PR series violated its
backwards-compat contract per design doc §6 'Don't do' rules.

Refs: docs/specs/2026-05-05-wizard-ux-redesign-design.md §6.
Part of: Wizard UX redesign PR-1.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 6: Open the PR

**Files:** none (git/gh operations only).

After all five preceding tasks land on the feature branch, run the full test suite, push, and open the PR.

- [ ] **Step 1: Verify the full test suite is green**

Run: `uv run pytest -q`

Expected output ends with `191 passed` (180 existing baseline as of main `2893f49` + 11 new from this PR — 2 validator tests in Task 1, 2 in Task 2, 4 schema tests in Task 3, 3 backwards-compat tests in Task 5; Task 4 was docstring-only with no test). The original plan claimed 139→150; that count came from a stale handoff note (session 4) and the codebase had grown to 180 tests on main by the time PR-1 began. If the count is off or any test fails, fix before pushing.

- [ ] **Step 2: Verify ruff passes (matches PR CI)**

Run: `uv run ruff format --check src tests && uv run ruff check src tests`

Expected: both commands exit 0 with no output. If formatting or lint issues appear, run `uv run ruff format src tests` and re-run.

- [ ] **Step 3: Push the branch**

The branch should already exist locally as `feat/wizard-redesign-pr1-schema` (or similar — created at the start of execution). Push:

```bash
git push -u origin feat/wizard-redesign-pr1-schema
```

- [ ] **Step 4: Open the PR**

```bash
gh pr create --title "feat(schema): wizard redesign PR-1 — additive schema extensions" --body "$(cat <<'EOF'
## Summary
- Adds two parameterless quality flags (\`intrinsics_estimated\`, \`underside_unverified\`) for the redesigned wizard's soft-warning surface.
- Adds two optional Feature fields (\`noisy\` boolean, \`warning\` string) for the tier-classification system.
- Documents the intentional mapping: wizard's "project name" input → existing \`Part.display_name\` (no new field).
- Adds a backwards-compatibility regression suite locking in additive-only commitment.

All changes are backwards-compatible. Every existing valid annotations.json still parses, validates, and round-trips. No new dependencies. No behavior change for code paths that don't opt into the new fields.

## Test plan
- [x] Two new validator tests pass (\`intrinsics_estimated\`, \`underside_unverified\`)
- [x] Four new schema tests pass (\`Feature.noisy\`, \`Feature.warning\` default-omit + explicit-emit)
- [x] Three new backwards-compat regression tests pass against v0.0.1 fixture
- [x] All 139 prior tests still pass
- [x] \`ruff format --check\` and \`ruff check\` both clean
- [ ] CI green on Python 3.10/3.11/3.12/3.13
- [ ] Cross-model review per design doc §7 (UX-and-functionality reviewer + Codex)

## Refs
- Design: docs/specs/2026-05-05-wizard-ux-redesign-design.md (PR-1 row in §6, schema additions in §3 + §4 + §5)
- Plan: docs/plans/2026-05-05-wizard-redesign-pr1-schema-extensions.md
- Tracking issue: (linked at design-doc level)

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

- [ ] **Step 5: Verify CI starts**

Run: `gh pr checks` and confirm the test matrix is queued. Wait for green; if any check fails, address inline before requesting review.

The PR is now ready for cross-model review per the design doc's §7 reviewer composition (spec-compliance + UX-and-functionality + Codex). The orchestrator dispatches reviewers; this plan does not specify them since they're orchestrator-managed.

---

## Self-review checklist (orchestrator runs this before claiming the plan complete)

**Spec coverage** (against design doc §3, §4, §5, §6 PR-1 row):
- ✅ `intrinsics_estimated` flag — Task 1
- ✅ `underside_unverified` flag — Task 2
- ✅ `noisy` boolean field — Task 3
- ✅ `warning` string field — Task 3
- ✅ `project_name` mapping documented — Task 4
- ✅ Backwards-compat lock-in — Task 5
- ✅ PR opened with correct title/body — Task 6
- ✅ Hierarchical feature names — no schema change required (`Feature.id: str` already accepts `pi/usb_c`); explicit no-task is correct, not a gap.
- ✅ `method` enum extension — explicitly deferred to PR-3 per design doc §6 PR-1 row scope; not a gap.

**Placeholder scan:** No "TBD" / "TODO" / "implement later" / "fill in details" / "similar to Task N" patterns. Every step has either complete code or a specific command + expected output.

**Type consistency:** `Feature.noisy: bool = False` and `Feature.warning: str | None = None` declared in Task 3, used identically in Task 3 tests and Task 5 backwards-compat assertions. `intrinsics_estimated` and `underside_unverified` flag strings match between Task 1, Task 2, and the existing `QUALITY_FLAGS` dict structure (parameterless = `False` value, matching the existing `intrinsics_suspect_high_anchor_rms` pattern).

**Scope check:** Single coherent PR. Six tasks, each independently committable, ~10-20 minutes per task. Total estimate: 90-120 minutes including the PR-creation step.

---

## Notes for the implementing subagent

- **Branch convention:** create a fresh branch off `main` named `feat/wizard-redesign-pr1-schema` before starting Task 1. Do not amend; each task = one new commit.
- **Git identity:** per-command env vars only (`GIT_AUTHOR_*` / `GIT_COMMITTER_*`). Never `git config --global`. The commit commands above already include this.
- **Trailing newline:** every file write should end with a single trailing newline. The atomic-write pattern in PR #18 demonstrates this for canonical artifacts; the same convention applies to source/test files via ruff format.
- **PEP 257 blank line:** any module-level docstring should be followed by a blank line before imports — `tests/test_schema_backwards_compat.py` in Task 5 follows this.
- **`from __future__ import annotations`** is already in both `models.py` and `validators.py` — keep it on any new test files too.
- **`encoding="utf-8"` on text file opens** — not relevant in PR-1 (no file I/O introduced) but a standing project convention.
- **Do NOT attempt to extend `FeatureMeasurement.method` enum** in this PR — that's PR-3's call. The existing values cover all current code paths.
- **If you encounter a test failure that's not in this plan**, stop and surface it; do not invent fixes outside this plan's scope.
