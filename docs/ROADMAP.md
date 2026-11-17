# Ardoka development milestones

## Pilot v0.1 (implemented)
- No-login Seller/Client role selector; shared local SQLite.
- Seller product details, image uploads, real dimensions and prices.
- Client room uploads, manual room dimensions, simple rectangular Three.js shell.
- Select/move/rotate/remove catalog furniture; save and reload scene.
- WaveSpeedAI Qwen image-edit and TRELLIS.2 jobs; async local worker; polling; no automatic billable POST retry.
- Lightweight client-side placement and seller data ingestion.

## Next technical gates
1. Replace simple rectangular shell with segmentation/depth-based architectural topology reconstruction, plus measurement calibration, uncertainty marking and manual corrections. Prevent architecture hallucination.
2. Validate generated seller meshes (model-to-photo resemblance, bounding size, axes, polygon count, textures, platform licensing). Persist optimized GLB in owned storage; add seller approval.
3. Create proper collision + clearance solver, true category filtering, style ranking, budget constraints and multiple scene proposals. Add local deterministic validation.
4. Add scene undo/redo, autosave, revisions, snapping, surface mounting, door/window operations, 2D floor-plan view.
5. Durable queue, provider results reconciliation, download and store results, task-level accounting, quotas, provider cost metrics, background cleanup.
6. Add identity, role-based permissions, tenant security, payments, privacy/retention policy, accessibility, logging, backups, browser/device test matrix.
7. Add WebXR headset mode only after exact room scale, scene persistence, and multi-device performance are measured.

## Acceptance testing
- Seller posts image + measured sizes; client sees asset in catalog and preview dimensions.
- Client creates room, places object, moves/rotates it, saves, reloads and obtains identical transforms.
- Out-of-bounds save is rejected by API.
- Image validation blocks renamed non-image files and oversized uploads.
- No key: UI works but AI submit reports configuration failure (without paid API calls).
- Valid API key: provider image upload succeeds, model prediction ID persisted, polling succeeds, output link displayed.
- Any ambiguous model-submit error must not automatically create a second billed request.

## Cost baseline (not a quote)
Image edit and 3D generation are metered per request. Use live WaveSpeed model catalog for rates; do not infer a fixed project cost from one call. Count actual submissions, failures, storage, processing and re-generation separately.
