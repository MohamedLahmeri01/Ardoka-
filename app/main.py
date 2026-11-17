"""Ardoka single-machine pilot: SQLite, static UI, WaveSpeedAI integration.

Security scope: local development only, WITHOUT authentication/tenancy. Do not
publish this service to the internet until authentication and authorization exist.
"""
from __future__ import annotations

import json
import os
import sqlite3
import threading
import time
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal
from urllib.parse import urlparse

import httpx
from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, field_validator

ROOT = Path(__file__).resolve().parent.parent
DATA = Path(os.getenv("ARDOKA_DATA_DIR", str(ROOT / "data"))).resolve()
MEDIA = DATA / "media"
DB = DATA / "ardoka.sqlite3"
MEDIA.mkdir(parents=True, exist_ok=True)
API = "https://api.wavespeed.ai/api/v3"
EDIT_MODEL = "wavespeed-ai/qwen-image/edit-plus"
MESH_MODEL = "wavespeed-ai/trellis-2/image-to-3d"
MAX_IMAGE_BYTES = 12 * 1024 * 1024
MAX_GLTF_BYTES = 45 * 1024 * 1024
FINISHED = {"completed", "failed", "cancelled", "timeout", "deleted"}

app = FastAPI(title="Ardoka Pilot", version="0.1.0", description="Local, no-login interior design prototype")
app.mount("/media", StaticFiles(directory=str(MEDIA)), name="media")


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


@contextmanager
def database():
    conn = sqlite3.connect(DB, timeout=15)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=15000")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def initialize() -> None:
    with database() as db:
        db.execute("PRAGMA journal_mode=WAL")
        db.executescript("""
        CREATE TABLE IF NOT EXISTS products (
          id TEXT PRIMARY KEY, name TEXT NOT NULL, seller TEXT NOT NULL,
          description TEXT NOT NULL DEFAULT '', style TEXT NOT NULL DEFAULT '',
          price_dzd REAL NOT NULL DEFAULT 0, width REAL NOT NULL,
          depth REAL NOT NULL, height REAL NOT NULL,
          image_path TEXT NOT NULL, model_path TEXT,
          status TEXT NOT NULL DEFAULT 'image_only', created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS projects (
          id TEXT PRIMARY KEY, name TEXT NOT NULL, prompt TEXT NOT NULL DEFAULT '',
          image_path TEXT NOT NULL, empty_image_path TEXT,
          width REAL NOT NULL DEFAULT 4, depth REAL NOT NULL DEFAULT 5,
          height REAL NOT NULL DEFAULT 2.8,
          scene_json TEXT NOT NULL DEFAULT '[]', created_at TEXT NOT NULL,
          updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS jobs (
          id TEXT PRIMARY KEY, kind TEXT NOT NULL, project_id TEXT,
          product_id TEXT, status TEXT NOT NULL, prediction_id TEXT,
          output_url TEXT, error TEXT, created_at TEXT NOT NULL,
          updated_at TEXT NOT NULL,
          FOREIGN KEY(project_id) REFERENCES projects(id),
          FOREIGN KEY(product_id) REFERENCES products(id)
        );
        """)


initialize()


def one(table: str, item_id: str) -> dict:
    if table not in {"projects", "products", "jobs"}:
        raise ValueError("Invalid table")
    with database() as db:
        row = db.execute(f"SELECT * FROM {table} WHERE id=?", (item_id,)).fetchone()
    if not row:
        raise HTTPException(404, f"{table[:-1].capitalize()} not found")
    result = dict(row)
    if table == "projects":
        result["scene"] = json.loads(result.pop("scene_json"))
    return result


def all_rows(table: str) -> list[dict]:
    if table not in {"projects", "products", "jobs"}:
        raise ValueError("Invalid table")
    with database() as db:
        rows = db.execute(f"SELECT * FROM {table} ORDER BY created_at DESC").fetchall()
    results = [dict(r) for r in rows]
    if table == "projects":
        for result in results:
            result["scene"] = json.loads(result.pop("scene_json"))
    return results


async def save_image(file: UploadFile) -> str:
    ext = Path(file.filename or "").suffix.lower()
    if ext not in {".png", ".jpg", ".jpeg", ".webp"}:
        raise HTTPException(422, "Use PNG, JPG or WebP images")
    raw = await file.read(MAX_IMAGE_BYTES + 1)
    if not raw or len(raw) > MAX_IMAGE_BYTES:
        raise HTTPException(413, "Image must be 1 byte to 12 MB")
    # Validate actual file data, not the MIME type supplied by the browser.
    from io import BytesIO
    from PIL import Image, UnidentifiedImageError
    try:
        with Image.open(BytesIO(raw)) as img:
            img.verify()
        with Image.open(BytesIO(raw)) as img:
            if img.width * img.height > 36_000_000:
                raise HTTPException(413, "Image pixel dimensions are too large")
            fmt = img.format
    except (UnidentifiedImageError, ValueError, OSError):
        raise HTTPException(422, "Invalid image data") from None
    expected = {".png": "PNG", ".jpg": "JPEG", ".jpeg": "JPEG", ".webp": "WEBP"}
    if fmt != expected[ext]:
        raise HTTPException(422, "File extension does not match image format")
    name = f"{uuid.uuid4().hex}{ext}"
    (MEDIA / name).write_bytes(raw)
    return f"/media/{name}"


class ProductCreate(BaseModel):
    name: str = Field(min_length=2, max_length=100)
    seller: str = Field(default="Local seller", min_length=1, max_length=100)
    description: str = Field(default="", max_length=1000)
    style: str = Field(default="modern", max_length=80)
    price_dzd: float = Field(ge=0, le=100_000_000, default=0)
    width: float = Field(gt=0, le=15)
    depth: float = Field(gt=0, le=15)
    height: float = Field(gt=0, le=10)
    image_path: str

    @field_validator("image_path")
    @classmethod
    def validate_image(cls, path: str) -> str:
        if not path.startswith("/media/") or not (MEDIA / Path(path).name).is_file():
            raise ValueError("Upload the image first")
        return path


class ProjectCreate(BaseModel):
    name: str = Field(min_length=2, max_length=100)
    prompt: str = Field(default="", max_length=1000)
    width: float = Field(gt=1, le=30, default=4)
    depth: float = Field(gt=1, le=30, default=5)
    height: float = Field(gt=1.5, le=8, default=2.8)
    image_path: str

    @field_validator("image_path")
    @classmethod
    def validate_image(cls, path: str) -> str:
        return ProductCreate.validate_image(path)


class RoomUpdate(BaseModel):
    width: float = Field(gt=1, le=30)
    depth: float = Field(gt=1, le=30)
    height: float = Field(gt=1.5, le=8)


class SceneItem(BaseModel):
    id: str = Field(min_length=1, max_length=100)
    product_id: str
    x: float = Field(ge=-30, le=30)
    z: float = Field(ge=-30, le=30)
    rotation: float = Field(ge=-1000, le=1000, default=0)


class SceneSave(BaseModel):
    items: list[SceneItem] = Field(max_length=300)


@app.get("/api/health")
def health():
    return {"status": "ok", "database": "sqlite", "ai_configured": bool(os.getenv("WAVESPEED_API_KEY")), "auth_enabled": False}


@app.post("/api/uploads")
async def upload(file: UploadFile = File(...)):
    path = await save_image(file)
    return {"path": path}


@app.get("/api/products")
def products():
    return all_rows("products")


@app.post("/api/products", status_code=201)
def add_product(info: ProductCreate):
    item_id = uuid.uuid4().hex
    with database() as db:
        db.execute("""INSERT INTO products (id,name,seller,description,style,price_dzd,width,depth,height,image_path,created_at)
                      VALUES (?,?,?,?,?,?,?,?,?,?,?)""", (item_id, info.name, info.seller, info.description,
                      info.style, info.price_dzd, info.width, info.depth, info.height, info.image_path, utcnow()))
    return one("products", item_id)


@app.get("/api/projects")
def projects():
    return all_rows("projects")


@app.post("/api/projects", status_code=201)
def add_project(info: ProjectCreate):
    item_id = uuid.uuid4().hex
    with database() as db:
        db.execute("""INSERT INTO projects (id,name,prompt,width,depth,height,image_path,created_at,updated_at)
                      VALUES (?,?,?,?,?,?,?,?,?)""", (item_id, info.name, info.prompt, info.width, info.depth, info.height,
                      info.image_path, utcnow(), utcnow()))
    return one("projects", item_id)


@app.get("/api/projects/{project_id}")
def get_project(project_id: str):
    return one("projects", project_id)


@app.put("/api/projects/{project_id}/room")
def edit_room(project_id: str, info: RoomUpdate):
    one("projects", project_id)
    with database() as db:
        db.execute("UPDATE projects SET width=?,depth=?,height=?,updated_at=? WHERE id=?",
                   (info.width, info.depth, info.height, utcnow(), project_id))
    return one("projects", project_id)


@app.put("/api/projects/{project_id}/scene")
def save_scene(project_id: str, payload: SceneSave):
    project = one("projects", project_id)
    if len({item.id for item in payload.items}) != len(payload.items):
        raise HTTPException(422, "Scene item IDs must be unique")
    with database() as db:
        by_id = {r["id"]: dict(r) for r in db.execute("SELECT * FROM products")}
    for item in payload.items:
        product = by_id.get(item.product_id)
        if not product:
            raise HTTPException(422, f"Unknown product: {item.product_id}")
        # Check bounding-box footprint after rotation, conservatively.
        import math
        c, s = abs(math.cos(item.rotation)), abs(math.sin(item.rotation))
        half_x = (product["width"] * c + product["depth"] * s) / 2
        half_z = (product["depth"] * c + product["width"] * s) / 2
        if abs(item.x) + half_x > project["width"] / 2 + 1e-6 or abs(item.z) + half_z > project["depth"] / 2 + 1e-6:
            raise HTTPException(422, f"{product['name']} is outside room boundaries")
    with database() as db:
        db.execute("UPDATE projects SET scene_json=?,updated_at=? WHERE id=?",
                   (json.dumps([p.model_dump() for p in payload.items]), utcnow(), project_id))
    return one("projects", project_id)


@app.get("/api/projects/{project_id}/recommendations")
def recommend(project_id: str):
    project = one("projects", project_id)
    prompt = project["prompt"].lower()
    candidates = all_rows("products")
    def score(p: dict) -> float:
        words = (p["name"] + " " + p["style"] + " " + p["description"]).lower().split()
        return sum(1 for w in words if w.strip(",.") in prompt.split())
    # Explicitly a heuristic, NOT a WaveSpeed LLM or spatial planning model.
    return {"method": "local_keyword_heuristic", "products": sorted(candidates, key=score, reverse=True)}


def update_job(job_id: str, **fields):
    if not fields:
        return
    keys = list(fields) + ["updated_at"]
    args = [fields[k] for k in fields] + [utcnow(), job_id]
    with database() as db:
        db.execute("UPDATE jobs SET " + ",".join(f"{k}=?" for k in keys) + " WHERE id=?", args)


def ws_key():
    key = os.getenv("WAVESPEED_API_KEY", "").strip()
    if not key:
        raise HTTPException(503, "Set WAVESPEED_API_KEY in your local environment first")
    return key


def media_file(local_path: str) -> Path:
    return MEDIA / Path(local_path).name


def wavespeed_upload(client: httpx.Client, file_path: Path, key: str) -> str:
    # Use WaveSpeed's short-lived direct-upload ticket. Never send API key to signed storage URL.
    size = file_path.stat().st_size
    content_type = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png", ".webp": "image/webp"}[file_path.suffix.lower()]
    ticket_resp = client.post(f"{API}/media/uploads", json={"filename": file_path.name, "size": size,
                                   "content_type": content_type}, headers={"Authorization": f"Bearer {key}"})
    ticket_resp.raise_for_status()
    ticket = ticket_resp.json()["data"]
    with open(file_path, "rb") as payload:
        upload_resp = client.request(ticket["upload"].get("method", "PUT"), ticket["upload"]["url"],
                                     headers=ticket["upload"].get("headers", {}), content=payload)
        upload_resp.raise_for_status()
    return ticket["download_url"]


def process_job(job_id: str):
    """Development-only worker thread. No POST retries (to avoid duplicate billing)."""
    try:
        job = one("jobs", job_id)
        key = ws_key()
        if job["kind"] == "empty_room":
            project = one("projects", job["project_id"])
            file_path = media_file(project["image_path"])
            model = EDIT_MODEL
        else:
            product = one("products", job["product_id"])
            file_path = media_file(product["image_path"])
            model = MESH_MODEL
        with httpx.Client(timeout=httpx.Timeout(100.0, connect=15.0), follow_redirects=False) as client:
            update_job(job_id, status="uploading")
            upload_url = wavespeed_upload(client, file_path, key)
            if job["kind"] == "empty_room":
                payload = {"prompt": "Remove ALL movable furniture, rugs, lighting accessories and decorations. Keep the EXACT original camera position and preserve walls, windows, doors, stairs, floors, ceilings, architecture and structural dimensions. A completely empty, clean room. Do not add new architecture.", "images": [upload_url], "output_format": "png"}
            else:
                payload = {"image": upload_url, "resolution": "512", "texture_size": 1024,
                           "target_face_count": 50000, "remove_background": True}
            update_job(job_id, status="submitting")
            # IMPORTANT: Never automatically retry a POST that can create a billed task.
            response = client.post(f"{API}/{model}", json=payload, headers={"Authorization": f"Bearer {key}"})
            response.raise_for_status()
            task = response.json().get("data", response.json())
            task_id = task.get("id")
            if not task_id:
                raise ValueError("Provider did not return prediction ID; inspect WaveSpeed History before resubmitting")
            update_job(job_id, status="running", prediction_id=task_id)
            for _ in range(75):  # up to approx 8 min, depending on request latencies
                time.sleep(4)
                result = client.get(f"{API}/predictions/{task_id}/result", headers={"Authorization": f"Bearer {key}"})
                if result.status_code in {429, 500, 502, 503, 504}:
                    continue
                result.raise_for_status()
                data = result.json().get("data", result.json())
                status = data.get("status", "running")
                if status == "completed":
                    outputs = data.get("outputs") or []
                    output = outputs[0] if outputs else None
                    if isinstance(output, dict):
                        output = output.get("url") or output.get("model") or output.get("glb")
                    if not isinstance(output, str) or not output.startswith("https://"):
                        raise ValueError("Provider returned no HTTPS output asset")
                    update_job(job_id, status="completed", output_url=output)
                    if job["kind"] == "empty_room":
                        with database() as db:
                            db.execute("UPDATE projects SET empty_image_path=?,updated_at=? WHERE id=?",
                                       (output, utcnow(), job["project_id"]))
                    else:
                        with database() as db:
                            db.execute("UPDATE products SET model_path=?,status='generated_unverified' WHERE id=?",
                                       (output, job["product_id"]))
                    return
                if status in FINISHED:
                    update_job(job_id, status="failed", error=str(data.get("error") or status)[:800])
                    return
            update_job(job_id, status="needs_recheck", error="Polling timed out. Prediction may still be running; do not resubmit blindly.")
    except Exception as exc:
        # Ambiguous submit errors must be checked in WaveSpeed History before retrying.
        update_job(job_id, status="needs_recheck", error=f"{type(exc).__name__}: {str(exc)[:700]}")


class JobStart(BaseModel):
    kind: Literal["empty_room", "furniture_3d"]
    project_id: str | None = None
    product_id: str | None = None


@app.post("/api/jobs", status_code=202)
def start_job(payload: JobStart):
    ws_key()
    if payload.kind == "empty_room":
        if not payload.project_id:
            raise HTTPException(422, "project_id required")
        one("projects", payload.project_id)
    else:
        if not payload.product_id:
            raise HTTPException(422, "product_id required")
        one("products", payload.product_id)
    item_id = uuid.uuid4().hex
    target_kind = "project_id" if payload.kind == "empty_room" else "product_id"
    target_id = payload.project_id if payload.kind == "empty_room" else payload.product_id
    with database() as db:
        running = db.execute(f"SELECT id FROM jobs WHERE {target_kind}=? AND kind=? AND status IN ('queued','uploading','submitting','running','needs_recheck')",
                             (target_id, payload.kind)).fetchone()
        if running:
            raise HTTPException(409, "A job already exists; inspect its status before creating another paid task")
        db.execute("""INSERT INTO jobs (id,kind,project_id,product_id,status,created_at,updated_at)
                      VALUES (?,?,?,?,?,?,?)""", (item_id, payload.kind, payload.project_id, payload.product_id,
                      "queued", utcnow(), utcnow()))
    thread = threading.Thread(target=process_job, args=(item_id,), daemon=True)
    thread.start()
    return one("jobs", item_id)


@app.get("/api/jobs")
def jobs():
    return all_rows("jobs")


@app.get("/api/jobs/{job_id}")
def get_job(job_id: str):
    return one("jobs", job_id)


@app.get("/")
def index():
    return FileResponse(ROOT / "app" / "static" / "index.html")


@app.get("/app.js")
def app_js():
    return FileResponse(ROOT / "app" / "static" / "app.js", media_type="application/javascript")


@app.get("/styles.css")
def styles():
    return FileResponse(ROOT / "app" / "static" / "styles.css", media_type="text/css")
