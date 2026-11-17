# Ardoka — AI Interior Design Studio (local pilot v0.1)

Ardoka is a browser-based prototype combining a seller furniture catalog, an editable Three.js room scene, and **WaveSpeedAI-only** optional generation jobs. This is a new initial implementation for an otherwise empty repository.

**Current scope:** local Windows development, **no login**, SQLite, no Docker, no account required. **Not production-ready**: the role selector is not authentication, the API exposes all local records, and paid AI inference requires your own WaveSpeedAI key.

## Fast start — Windows 11 / Git Bash

Prerequisites: Python **3.11–3.13**, network access (Three.js CDN + paid WaveSpeedAPI models), a recent Chrome/Edge browser.

```bash
git clone https://github.com/MohamedLahmeri01/Ardoka-.git
cd Ardoka-
py -3 -m venv .venv
source .venv/Scripts/activate
python -m pip install -r requirements.txt
python run.py
```

Open **http://127.0.0.1:8765**. The expected server log contains `Uvicorn running on http://127.0.0.1:8765`.

**Existing clone:** `git switch main && git pull --ff-only origin main`.

**Enable paid AI calls:** create a WaveSpeedAI key at https://wavespeed.ai/accesskey, then in **Git Bash before starting `python run.py`**:

```bash
export WAVESPEED_API_KEY='paste-your-own-key-here'
python run.py
```

Do **not** paste API keys into the browser or into the Git repo. Without a key, manual product catalog, room creation, 3D previews, editing and saving all work; AI generation buttons return an explicit configuration error.

### Two user flows

- **Seller:** enter shop/product information, upload a product image, and set actual width/depth/height and price. The item appears immediately as a precisely-sized placeholder in client rooms. Optionally request **WaveSpeed TRELLIS.2** image-to-3D (a charged task). Generated meshes are marked **unverified** until manually checked for resemblance, dimensions and orientation.
- **Client:** upload an original room photo, enter room dimensions and a design prompt, create a simplified rectangular architectural room shell, and place/catalog products in the Three.js editor. Orbit/zoom, select, translate, rotate, remove, save and reopen. Optionally request **WaveSpeed Qwen Image Edit Plus** to remove furniture from the image. The edited image is a visual reference **only**: it is not an accurate depth/mesh reconstruction.

**Important:** A single photo does not reveal hidden architectural features. Windows, doors, stairs and true geometry reconstruction are **not yet implemented**. The 3D room shell is an editable manually-dimensioned box. The furniture recommender is an explicitly labeled **local keyword heuristic**, not an AI spatial planner. Placement collision detection is basic; validate before commercial use.

### Tests

```bash
python -m pytest -q
```

`/api/health` returns `auth_enabled: false`; `/api/docs` exposes FastAPI's interactive documentation.

## WaveSpeedAI API paths used

- Upload: `POST /api/v3/media/uploads`, then signed `PUT` to the returned storage URL (without your API key).
- Image editing: `POST /api/v3/wavespeed-ai/qwen-image/edit-plus` with `images` and `prompt`.
- Furniture model: `POST /api/v3/wavespeed-ai/trellis-2/image-to-3d` with `image`, resolution `512`.
- Poll: `GET /api/v3/predictions/{id}/result`.

**Billing safety:** The worker does **not retry model submit POSTs** after an ambiguous error. In such cases, jobs are marked `needs_recheck`: review the provider's Prediction History before starting another. Jobs are processed in daemon threads, which are **not crash-safe**. You can inspect persistent jobs at `/api/jobs`.

**Asset retention:** WaveSpeed upload inputs have seven-day retention; output URLs may also expire and models can fail browser CORS requests. The v0.1 prototype stores external output URLs, not durable copies. For production, download provider results into object storage, validate file types, and persist safe local/owned URLs. The frontend falls back to an accurately dimensioned placeholder when generated GLB cannot be loaded.

## Local structure

```
app/main.py          FastAPI + SQLite + file uploads + WaveSpeed workers
app/static/          Browser app + Three.js scene editor
run.py               Start local server on 127.0.0.1:8765
requirements.txt     Python dependencies
/tests               Local API tests
/docs                Gaps and implementation roadmap
/data                Runtime SQLite and uploaded images (gitignored)
```

## Constraints & production gaps

No user sessions or isolation; no verified 3D room reconstruction; no automatic accurate placement; no manual door/window editing; no undo-redo; no crash-safe durable job queue; no asset download/hosting; no payments; no rate limits. These are **next milestones**, not completed features.

**Only run locally** until identity, access control, tenant boundaries, upload isolation, operational monitoring, and queue durability are implemented.

## Official docs

- https://wavespeed.ai/docs/upload-files-api
- https://wavespeed.ai/docs/docs-api/wavespeed-ai/qwen-image/edit-plus
- https://wavespeed.ai/docs/docs-api/wavespeed-ai/trellis-2/image-to-3d
- https://wavespeed.ai/docs/rest-api
