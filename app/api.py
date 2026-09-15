from __future__ import annotations

import asyncio
import json
from contextlib import asynccontextmanager
from typing import AsyncIterator

from fastapi import FastAPI, Header, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse

from app.core import InputError, parse_spotify_url
from app.db import connect, get_settings, init_db, json_value, transaction, utcnow
from app.failure_report import build_failure_report
from app.health import health_snapshot
from app.logging_config import configure_logging
from app.models import ResolveRequest, SettingsPatch
from app.repository import (
    create_download_job,
    create_or_restart_resolution,
    get_collection,
    get_job,
    list_collection_tracks,
    list_collections,
    list_jobs,
    retry_failed,
    retry_item,
    set_job_action,
)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    configure_logging()
    init_db()
    yield


app = FastAPI(title="Local Music Library", version="1.0.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "http://127.0.0.1:3000"],
    allow_credentials=False,
    allow_methods=["GET", "POST", "PATCH"],
    allow_headers=["Content-Type", "Last-Event-ID"],
)


def not_found(resource: str) -> HTTPException:
    code = resource.upper().replace(" ", "_")
    return HTTPException(status_code=404, detail={"code": f"{code}_NOT_FOUND", "message": f"{resource.title()} not found."})


@app.get("/api/health")
def health():
    return health_snapshot()


@app.post("/api/resolve", status_code=202)
def resolve(body: ResolveRequest):
    try:
        ref = parse_spotify_url(body.url)
        collection, job = create_or_restart_resolution(ref)
        return {"collection": collection, "job": job}
    except InputError as exc:
        raise HTTPException(status_code=422, detail={"code": exc.code, "message": str(exc)}) from exc


@app.get("/api/collections")
def collections(limit: int = Query(100, ge=1, le=250)):
    return {"items": list_collections(limit)}


@app.get("/api/collections/{collection_id}")
def collection(collection_id: str):
    value = get_collection(collection_id)
    if not value:
        raise not_found("collection")
    return value


@app.get("/api/collections/{collection_id}/tracks")
def collection_tracks(
    collection_id: str,
    offset: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=500),
    job_id: str | None = Query(default=None),
):
    if not get_collection(collection_id):
        raise not_found("collection")
    return list_collection_tracks(collection_id, offset, limit, job_id)


@app.post("/api/collections/{collection_id}/download", status_code=202)
def download_collection(collection_id: str):
    try:
        return create_download_job(collection_id)
    except KeyError as exc:
        raise not_found("collection") from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail={"code": "COLLECTION_NOT_READY", "message": str(exc)}) from exc


@app.get("/api/diagnostics/latest")
def latest_download_diagnostics(examples: int = Query(15, ge=1, le=25)):
    report = build_failure_report(examples=examples)
    if report is None:
        raise not_found("download job")
    return JSONResponse(report, headers={"Cache-Control": "no-store"})


@app.get("/api/jobs/{job_id}/diagnostics")
def download_diagnostics(job_id: str, examples: int = Query(15, ge=1, le=25)):
    report = build_failure_report(job_id, examples=examples)
    if report is None:
        raise not_found("download job")
    return JSONResponse(report, headers={"Cache-Control": "no-store"})


@app.get("/api/jobs")
def jobs(limit: int = Query(100, ge=1, le=250)):
    return {"items": list_jobs(limit)}


@app.get("/api/jobs/{job_id}")
def job(job_id: str, include_items: bool = True):
    value = get_job(job_id, include_items=include_items)
    if not value:
        raise not_found("job")
    return value


def job_action(job_id: str, action: str):
    try:
        value = set_job_action(job_id, action)
        if not value:
            raise not_found("job")
        return value
    except ValueError as exc:
        raise HTTPException(status_code=409, detail={"code": "INVALID_JOB_STATE", "message": str(exc)}) from exc


@app.post("/api/jobs/{job_id}/pause")
def pause_job(job_id: str):
    return job_action(job_id, "pause")


@app.post("/api/jobs/{job_id}/resume")
def resume_job(job_id: str):
    return job_action(job_id, "resume")


@app.post("/api/jobs/{job_id}/cancel")
def cancel_job(job_id: str):
    return job_action(job_id, "cancel")


@app.post("/api/jobs/{job_id}/retry-failed")
def retry_job(job_id: str):
    try:
        value = retry_failed(job_id)
        if not value:
            raise not_found("job")
        return value
    except ValueError as exc:
        raise HTTPException(status_code=409, detail={"code": "NO_FAILED_TRACKS", "message": str(exc)}) from exc


@app.post("/api/job-items/{item_id}/retry")
def retry_track(item_id: str):
    try:
        value = retry_item(item_id)
        if not value:
            raise not_found("job item")
        return value
    except ValueError as exc:
        raise HTTPException(status_code=409, detail={"code": "TRACK_NOT_FAILED", "message": str(exc)}) from exc


@app.get("/api/settings")
def settings():
    return get_settings()


@app.patch("/api/settings")
def patch_settings(body: SettingsPatch):
    changes = body.changes()
    now = utcnow()
    with transaction(immediate=True) as conn:
        for key, value in changes.items():
            conn.execute(
                """INSERT INTO settings(key, value_json, updated_at) VALUES(?, ?, ?)
                   ON CONFLICT(key) DO UPDATE SET value_json=excluded.value_json, updated_at=excluded.updated_at""",
                (key, json_value(value), now),
            )
    return get_settings()


async def event_stream(last_id: int, request: Request) -> AsyncIterator[str]:
    cursor = last_id
    while not await request.is_disconnected():
        conn = connect()
        try:
            rows = conn.execute("SELECT * FROM events WHERE id>? ORDER BY id LIMIT 200", (cursor,)).fetchall()
        finally:
            conn.close()
        if rows:
            for row in rows:
                cursor = row["id"]
                data = {
                    "id": row["id"], "type": row["type"], "collection_id": row["collection_id"],
                    "job_id": row["job_id"], "track_id": row["track_id"],
                    "payload": json.loads(row["payload_json"]), "created_at": row["created_at"],
                }
                yield f"id: {cursor}\nevent: {row['type']}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"
        else:
            yield ": keep-alive\n\n"
        await asyncio.sleep(1)


@app.get("/api/events")
async def events(request: Request, since: int | None = Query(default=None, ge=0), last_event_id: str | None = Header(default=None)):
    if last_event_id and last_event_id.isdigit():
        cursor = int(last_event_id)
    elif since is not None:
        cursor = since
    else:
        conn = connect()
        try:
            cursor = int(conn.execute("SELECT COALESCE(MAX(id), 0) FROM events").fetchone()[0])
        finally:
            conn.close()
    return StreamingResponse(event_stream(cursor, request), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})
