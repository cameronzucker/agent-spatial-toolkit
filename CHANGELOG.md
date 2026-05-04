# Changelog

All notable changes to agent-spatial-toolkit will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
- Initial v1 design specification (`docs/specs/2026-05-03-design.md`)
- Implementation plan (`docs/plans/2026-05-03-v1-implementation.md`)
- Calibration chessboard PDFs (A4 + US Letter, 7×5 internal corners @ 25mm)
- GitHub Actions CI matrix workflow (Python 3.10–3.13, uv, pytest + ruff)
- pytest + ruff dev tooling configuration
- Project scaffolding: pyproject.toml, README, LICENSE (MIT), .gitignore

## [0.1.0-alpha] — Unreleased

CLI-only proof of the pipeline. Single-photo planar pose mode (β-mode) only.
FOV-class intrinsics fallback only (no chessboard calibration yet).
JPEG and PNG photo formats only.

## [0.1.0] — Unreleased

Full v1: multi-view triangulation (γ-mode), chessboard calibration, agent
skill primitive, HEIC support, cross-model adversarial validation gate.
