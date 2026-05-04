# agent-spatial-toolkit

> I can't CAD for shit and agents don't have good spatial reasoning, so this toolkit fixes both problems in a lightweight cross-vendor transferable package.

A skill + library for **converting reference photos of physical components into structured spatial data** that LLM agents can reason over. Designed for makers who need to author parametric models of off-the-shelf hardware without round-tripping through CAD tools.

## Status

🚧 **Design phase.** v0.1.0 not yet implemented. The full design specification lives in [`docs/specs/2026-05-03-design.md`](docs/specs/2026-05-03-design.md).

If you found this repo because you want the working tool: come back later, or follow along by reading the design doc and giving feedback.

## What this is, in one paragraph

You have photos of a Raspberry Pi HAT (or any other physical component). You want an LLM to design a 3D-printed case for it, but the LLM can't see the photos accurately enough to place mounting holes, port cutouts, or feature outlines correctly. You point the toolkit at your photos. It walks you through a browser-based wizard: click 4 reference frame anchors per photo, then click features and label them. The toolkit does the photogrammetry math (PnP + multi-view triangulation) and emits a structured JSON file with each labeled feature's `(X, Y, Z)` position in millimeters relative to the part's reference frame. The LLM reads that JSON and now has *verified* spatial data instead of trying to guess from pixels.

## What this is *not*

- A photogrammetry tool (we don't produce 3D meshes)
- A 3D scanner (we don't generate dense geometry)
- A CAD application (we emit data, not designs)
- A generative AI 3D tool (we measure, we don't hallucinate)

The mental model is closer to a **software CMM (Coordinate Measuring Machine)** than to a 3D scanner: an operator probes named points on a known part, and you get back a labeled list of measured features.

## How it works (TL;DR)

1. Take photos of your part with a real camera (your phone's telephoto lens is ideal).
2. Run the toolkit. It opens a local-only browser wizard.
3. Calibrate each photo by clicking known reference points (e.g., the four corners of the PCB).
4. Click features you care about and label them ("rj45_corner", "gpio_socket_center").
5. The toolkit triangulates 3D positions across photos and produces `annotations.json`.
6. Hand the JSON to your LLM agent (or downstream parametric CAD code) and let it reason.

For details, see the [design doc](docs/specs/2026-05-03-design.md).

## Roadmap (intent)

- **v0.1** — Single-page wizard, multi-view triangulation, chessboard calibration, generic-FOV fallback. Outputs `annotations.json` per session.
- **v0.5** — Templates layer: per-part-class (Pi-HAT, M.2 module, etc.) prescriptions for required photos and expected features.
- **v1.0** — Self-calibration via bundle adjustment; downstream emitters (parametric CAD YAML, OpenSCAD modules).
- **Beyond** — Distortion-aware reprojection-based feature refinement; agent-driven semi-automated annotation; mobile capture app.

## Why this exists

Brainstormed and designed in one extended session between cameronzucker and Claude (Sonnet 4.5 / Opus 4.7) while trying to design a 3D-printed case for a Raspberry Pi 5. After four failed iterations of the LLM hallucinating spatial relationships from photos, it became clear that the right move wasn't more iteration — it was building the missing primitive. This toolkit is that primitive.

## License

MIT. See [LICENSE](LICENSE).
