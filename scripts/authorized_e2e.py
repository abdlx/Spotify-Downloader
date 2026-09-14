from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

import requests
from mutagen.id3 import ID3


def request(method: str, url: str, **kwargs):
    response = requests.request(method, url, timeout=30, **kwargs)
    response.raise_for_status()
    return response.json()


def wait_until(api: str, path: str, accepted: set[str], timeout: int = 900):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = request("GET", f"{api}{path}")
        if value["status"] in accepted:
            return value
        time.sleep(2)
    raise TimeoutError(f"Timed out waiting for {path}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Authorized real-media acceptance test")
    parser.add_argument("url", help="A public Spotify track URL whose corresponding media you may download")
    parser.add_argument("--i-confirm-authorized", action="store_true", required=True)
    parser.add_argument("--api", default="http://127.0.0.1:8000/api")
    args = parser.parse_args()
    resolved = request("POST", f"{args.api}/resolve", json={"url": args.url})
    collection_id = resolved["collection"]["id"]
    collection = wait_until(args.api, f"/collections/{collection_id}", {"READY", "FAILED"})
    if collection["status"] != "READY":
        raise RuntimeError(collection.get("error") or "Collection resolution failed")
    job = request("POST", f"{args.api}/collections/{collection_id}/download")
    job = wait_until(args.api, f"/jobs/{job['id']}", {"COMPLETE", "COMPLETE_WITH_ERRORS"}, 1800)
    if job["status"] != "COMPLETE" or not job.get("items"):
        raise RuntimeError(f"Download job did not complete cleanly: {job['status']}")
    output = Path(job["items"][0]["output_path"])
    if not output.is_file():
        raise FileNotFoundError(output)
    tags = ID3(output)
    required = {"title": tags.getall("TIT2"), "artist": tags.getall("TPE1"), "album": tags.getall("TALB"), "artwork": tags.getall("APIC")}
    missing = [name for name, frames in required.items() if not frames]
    if missing:
        raise AssertionError(f"MP3 is missing: {', '.join(missing)}")
    print(f"PASS: {output}")
    print(f"title={required['title'][0]} artist={required['artist'][0]} album={required['album'][0]} artwork_frames={len(required['artwork'])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

