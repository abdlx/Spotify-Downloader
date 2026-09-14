from __future__ import annotations

import importlib.metadata
import logging
import os
import shutil
import signal
import subprocess
import sys
import time
from dataclasses import dataclass

from app import __version__
from app.config import MAX_ATTEMPTS, RETRY_DELAYS, WORKER_ID
from app.db import connect, emit_event, get_settings, init_db, transaction, utcnow
from app.logging_config import configure_logging
from app.repository import update_job_counts


logger = logging.getLogger("worker")


@dataclass
class Child:
    process: subprocess.Popen
    kind: str
    resource_id: str
    job_id: str


def validate_runtime() -> None:
    missing = [name for name in ("ffmpeg", "ffprobe", "deno") if not shutil.which(name)]
    for package in ("spotdl", "yt-dlp", "yt-dlp-ejs"):
        try:
            importlib.metadata.version(package)
        except importlib.metadata.PackageNotFoundError:
            missing.append(package)
    if missing:
        raise RuntimeError(f"Mandatory runtime dependencies are missing: {', '.join(missing)}")


def recover() -> None:
    now = utcnow()
    with transaction(immediate=True) as conn:
        conn.execute("UPDATE jobs SET status='QUEUED', updated_at=? WHERE kind='RESOLVE' AND status='RESOLVING'", (now,))
        conn.execute(
            """UPDATE job_items SET status='QUEUED', progress=0, next_attempt_at=NULL, updated_at=?
               WHERE status IN ('SEARCHING','MATCHED','DOWNLOADING','TRANSCODING','TAGGING')""", (now,)
        )
        conn.execute("UPDATE jobs SET status='DOWNLOADING', updated_at=? WHERE kind='DOWNLOAD' AND status='QUEUED'", (now,))


def heartbeat() -> None:
    with transaction(immediate=True) as conn:
        conn.execute(
            """INSERT INTO worker_heartbeat(worker_id, pid, version, updated_at) VALUES(?, ?, ?, ?)
               ON CONFLICT(worker_id) DO UPDATE SET pid=excluded.pid, version=excluded.version, updated_at=excluded.updated_at""",
            (WORKER_ID, os.getpid(), __version__, utcnow()),
        )


def launch(module: str, resource_id: str) -> subprocess.Popen:
    kwargs = {}
    if os.name == "nt":
        kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        kwargs["start_new_session"] = True
    return subprocess.Popen([sys.executable, "-m", module, resource_id], **kwargs)


def terminate_tree(process: subprocess.Popen) -> None:
    if process.poll() is not None:
        return
    try:
        if os.name == "nt":
            process.send_signal(signal.CTRL_BREAK_EVENT)
        else:
            os.killpg(os.getpgid(process.pid), signal.SIGTERM)
        process.wait(timeout=8)
    except Exception:
        try:
            if os.name == "nt": process.kill()
            else: os.killpg(os.getpgid(process.pid), signal.SIGKILL)
        except Exception:
            pass


def mark_abnormal(item_id: str, job_id: str, returncode: int) -> None:
    conn = connect()
    try:
        item = conn.execute("SELECT status, attempt FROM job_items WHERE id=?", (item_id,)).fetchone()
        job = conn.execute("SELECT status FROM jobs WHERE id=?", (job_id,)).fetchone()
    finally:
        conn.close()
    if not item or item["status"] in ("QUEUED", "COMPLETE", "COMPLETE_WITH_WARNINGS", "FAILED", "CANCELLED"):
        return
    now = utcnow()
    if job and job["status"] in ("CANCEL_REQUESTED", "CANCELLED"):
        with transaction(immediate=True) as conn:
            conn.execute("UPDATE job_items SET status='CANCELLED', progress=0, updated_at=? WHERE id=?", (now, item_id))
    elif item["attempt"] < MAX_ATTEMPTS:
        from datetime import UTC, datetime, timedelta
        delay = RETRY_DELAYS[min(item["attempt"] - 1, len(RETRY_DELAYS) - 1)]
        next_at = (datetime.now(UTC) + timedelta(seconds=delay)).isoformat()
        with transaction(immediate=True) as conn:
            conn.execute("UPDATE job_items SET status='QUEUED', progress=0, error_code='WORKER_CHILD_EXIT', error=?, next_attempt_at=?, updated_at=? WHERE id=?", (f"Download process exited with code {returncode}.", next_at, now, item_id))
    else:
        with transaction(immediate=True) as conn:
            conn.execute("UPDATE job_items SET status='FAILED', progress=100, error_code='WORKER_CHILD_EXIT', error=?, updated_at=? WHERE id=?", (f"Download process exited with code {returncode}.", now, item_id))
    update_job_counts(job_id)


def main() -> None:
    configure_logging()
    init_db()
    validate_runtime()
    recover()
    children: dict[str, Child] = {}
    running = True

    def stop(_signum, _frame):
        nonlocal running
        running = False

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    logger.info("worker ready", extra={"event": "worker_ready"})
    while running:
        heartbeat()
        for key, child in list(children.items()):
            code = child.process.poll()
            if code is None:
                continue
            if child.kind == "download" and code != 0:
                mark_abnormal(child.resource_id, child.job_id, code)
            elif child.kind == "resolve" and code != 0:
                failed_collection_id = None
                with transaction(immediate=True) as conn:
                    job = conn.execute(
                        "SELECT collection_id, status FROM jobs WHERE id=?", (child.job_id,)
                    ).fetchone()
                    if job and job["status"] == "RESOLVING":
                        now = utcnow()
                        failed_collection_id = job["collection_id"]
                        message = f"Metadata worker exited unexpectedly with code {code}."
                        conn.execute(
                            """UPDATE jobs SET status='FAILED', error_code='WORKER_CHILD_EXIT',
                               error=?, completed_at=?, updated_at=? WHERE id=?""",
                            (message, now, now, child.job_id),
                        )
                        conn.execute(
                            """UPDATE collections SET status='FAILED', error_code='WORKER_CHILD_EXIT',
                               error='Metadata resolution stopped unexpectedly. Fetch the link again to retry.',
                               updated_at=? WHERE id=?""",
                            (now, failed_collection_id),
                        )
                if failed_collection_id:
                    emit_event(
                        "collection.failed",
                        collection_id=failed_collection_id,
                        job_id=child.job_id,
                        payload={
                            "code": "WORKER_CHILD_EXIT",
                            "message": "Metadata resolution stopped unexpectedly. Fetch the link again to retry.",
                        },
                    )
            del children[key]

        conn = connect()
        try:
            cancelling = conn.execute("SELECT id FROM jobs WHERE status='CANCEL_REQUESTED'").fetchall()
        finally:
            conn.close()
        for job in cancelling:
            active = [child for child in children.values() if child.job_id == job["id"]]
            for child in active:
                terminate_tree(child.process)
            if not active:
                with transaction(immediate=True) as conn:
                    conn.execute("UPDATE job_items SET status='CANCELLED', updated_at=? WHERE job_id=? AND status NOT IN ('COMPLETE','COMPLETE_WITH_WARNINGS','FAILED','SKIPPED')", (utcnow(), job["id"]))
                    conn.execute("UPDATE jobs SET status='CANCELLED', completed_at=?, updated_at=? WHERE id=?", (utcnow(), utcnow(), job["id"]))

        resolver_running = any(child.kind == "resolve" for child in children.values())
        if not resolver_running:
            with transaction(immediate=True) as conn:
                row = conn.execute("SELECT id FROM jobs WHERE kind='RESOLVE' AND status='QUEUED' ORDER BY created_at LIMIT 1").fetchone()
                if row:
                    conn.execute("UPDATE jobs SET status='RESOLVING', started_at=COALESCE(started_at, ?), updated_at=? WHERE id=?", (utcnow(), utcnow(), row["id"]))
            if row:
                children[f"resolve:{row['id']}"] = Child(launch("app.resolve_child", row["id"]), "resolve", row["id"], row["id"])

        concurrency = int(get_settings(include_secret=True).get("concurrency", 2))
        active_downloads = sum(child.kind == "download" for child in children.values())
        while active_downloads < concurrency:
            with transaction(immediate=True) as conn:
                row = conn.execute(
                    """SELECT ji.id, ji.job_id FROM job_items ji JOIN jobs j ON j.id=ji.job_id
                       WHERE ji.status='QUEUED' AND j.status IN ('QUEUED','DOWNLOADING')
                       AND (ji.next_attempt_at IS NULL OR ji.next_attempt_at<=?)
                       AND NOT EXISTS (
                         SELECT 1 FROM job_items active
                         WHERE active.track_id=ji.track_id AND active.id<>ji.id
                         AND active.status IN ('SEARCHING','MATCHED','DOWNLOADING','TRANSCODING','TAGGING')
                       )
                       ORDER BY ji.created_at, ji.position LIMIT 1""", (utcnow(),)
                ).fetchone()
                if row:
                    now = utcnow()
                    conn.execute("UPDATE job_items SET status='SEARCHING', attempt=attempt+1, progress=1, next_attempt_at=NULL, updated_at=? WHERE id=?", (now, row["id"]))
                    conn.execute("UPDATE jobs SET status='DOWNLOADING', started_at=COALESCE(started_at, ?), updated_at=? WHERE id=?", (now, now, row["job_id"]))
            if not row:
                break
            children[f"download:{row['id']}"] = Child(launch("app.download_child", row["id"]), "download", row["id"], row["job_id"])
            active_downloads += 1

        time.sleep(0.75)

    for child in children.values():
        terminate_tree(child.process)
    logger.info("worker stopped", extra={"event": "worker_stopped"})


if __name__ == "__main__":
    main()
