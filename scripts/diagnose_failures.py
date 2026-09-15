from __future__ import annotations

import argparse
import json

from app.failure_report import build_failure_report


def main() -> int:
    parser = argparse.ArgumentParser(description="Read-only downloader failure audit")
    parser.add_argument("--job-id", help="Download job ID; defaults to the latest download job")
    parser.add_argument("--examples", type=int, default=15)
    args = parser.parse_args()
    report = build_failure_report(args.job_id, examples=max(0, min(args.examples, 25)))
    if report is None:
        raise SystemExit("Download job not found")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
