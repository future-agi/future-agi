#!/usr/bin/env python3
"""Opt-in API benchmark. Does not provision data or bypass workbench budgets."""

import argparse
import json
import os
import statistics
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import urlencode, urlparse
from urllib.request import Request, urlopen
from uuid import UUID


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--organization", type=UUID, required=True)
    parser.add_argument("--workspace", type=UUID, required=True)
    parser.add_argument("--project", type=UUID, required=True)
    parser.add_argument("--fixture-manifest", type=Path, required=True)
    parser.add_argument(
        "--profile", type=Path, default=Path(__file__).with_name("profile.json")
    )
    parser.add_argument("--allow-benchmark-requests", action="store_true")
    args = parser.parse_args()
    if not args.allow_benchmark_requests:
        parser.error(
            "Explicit opt-in required; run only on dedicated validation infrastructure."
        )
    if urlparse(args.base_url).scheme not in {"http", "https"}:
        parser.error("Use an HTTP(S) API URL.")
    token = os.environ.get("SQL_BENCHMARK_TOKEN")
    if not token:
        parser.error(
            "Set SQL_BENCHMARK_TOKEN; never put bearer credentials on the command line."
        )
    profile = json.loads(args.profile.read_text())
    manifest = json.loads(args.fixture_manifest.read_text())
    if (
        UUID(manifest["project_id"]) != args.project
        or manifest["span_count"] < profile["required_project_spans"]
    ):
        parser.error(
            "Fixture manifest must identify this project and satisfy the declared scale."
        )
    if (
        not 0 < profile["duration_seconds"] <= 86400
        or profile["interval_seconds"] < 10
        or not 0 < profile["window_hours"] <= 744
    ):
        parser.error(
            "Profile must be bounded to 24h, at most one submission per 10s, and a 31-day window."
        )
    headers = {
        "Authorization": f"Bearer {token}",
        "X-Organization-Id": str(args.organization),
        "X-Workspace-Id": str(args.workspace),
        "Content-Type": "application/json",
    }

    def api(method, path, body=None):
        request = Request(
            args.base_url.rstrip("/") + path,
            method=method,
            headers=headers,
            data=None if body is None else json.dumps(body).encode(),
        )
        with urlopen(request, timeout=10) as response:
            return json.load(response)

    started = time.monotonic()
    durations, errors, count = [], 0, 0
    while time.monotonic() - started < profile["duration_seconds"]:
        sample = time.monotonic()
        end = datetime.now(timezone.utc)
        job_id = None
        record = {"sample": count, "started_at": end.isoformat()}
        try:
            job = api(
                "POST",
                "/tracer/sql-query/run/",
                {
                    "project_id": str(args.project),
                    "sql": profile["sql"],
                    "start": (
                        end - timedelta(hours=profile["window_hours"])
                    ).isoformat(),
                    "end": end.isoformat(),
                },
            )
            job_id = job["id"]
            while (
                job["state"] in {"queued", "running"}
                and time.monotonic() - sample < 120
                and time.monotonic() - started < profile["duration_seconds"]
            ):
                time.sleep(1)
                job = api(
                    "GET",
                    f"/tracer/sql-query/{job_id}/?"
                    + urlencode({"project_id": str(args.project)}),
                )
            if (
                job["state"] != "succeeded"
                or job.get("result", {}).get("truncated")
                or job.get("result", {}).get("row_count", 0) == 0
            ):
                raise RuntimeError(
                    "Query did not complete with nonempty, exact bounded output."
                )
            duration = round((time.monotonic() - sample) * 1000)
            durations.append(duration)
            record.update(
                state="succeeded",
                wall_ms=duration,
                sql_ms=job["result"]["elapsed_ms"],
                rows=job["result"]["row_count"],
            )
        except Exception as exc:
            errors += 1
            record.update(
                state="failed",
                error_type=type(exc).__name__,
                http_status=exc.code if isinstance(exc, HTTPError) else None,
            )
            if job_id:
                try:
                    api(
                        "POST",
                        f"/tracer/sql-query/{job_id}/cancel/",
                        {"project_id": str(args.project)},
                    )
                except Exception:
                    pass
        print(json.dumps(record), flush=True)  # No SQL, result values or credentials.
        count += 1
        remaining = min(
            profile["interval_seconds"] - (time.monotonic() - sample),
            profile["duration_seconds"] - (time.monotonic() - started),
        )
        if remaining > 0:
            time.sleep(remaining)
    ordered = sorted(durations)
    print(
        json.dumps(
            {
                "summary": True,
                "samples": count,
                "failures": errors,
                "median_wall_ms": statistics.median(durations) if durations else None,
                "p95_wall_ms": (
                    ordered[min(len(ordered) - 1, int(len(ordered) * 0.95))]
                    if ordered
                    else None
                ),
                "fixture_manifest_span_count": manifest["span_count"],
                "fixture_count_independently_verified": False,
                "profile": profile["name"],
            }
        )
    )
    return int(errors > 0)


if __name__ == "__main__":
    raise SystemExit(main())
