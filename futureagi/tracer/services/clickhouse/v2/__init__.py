"""
ClickHouse 25.3 (`v2`) service layer for FutureAGI.

This package is the production home for everything that talks to the new
typed-Map + typed-JSON spans schema. Imports cleanly from Django so management
commands, migrations, signal handlers, and the eval runner can all use it.

Layout:
    schema/             Versioned .sql files (idempotent via apply_schema.py)
    apply_schema.py     Hash-tracked DDL runner with drift detection
    adapter.py          Pure-Python PG-row → CH-row converter
    span_reader.py      CHSpanReader for read paths (eval runner, future dashboards)

Companion management commands live in `tracer/management/commands/ch25_*`.

Configuration: pulled from `settings.CLICKHOUSE_V2` (env-backed). See the
package docstring of `apply_schema.py` and the README at the bottom of
this package for the operator-facing wiring.

Migration provenance: this code originated in
planning/clickhouse-rearch/migration/ where it was test-driven and codex-
reviewed; the validated implementation files were copied here as a single
atomic move once the migration tooling was production-ready. The original
planning directory keeps the docs (DECISIONS, RUNBOOK, REVIEWS) as the
permanent historical record.
"""

from __future__ import annotations

import os
from typing import Any

from django.conf import settings


def get_v2_config() -> dict[str, Any]:
    """Read the CH 25.3 connection config, falling back to the legacy
    `CLICKHOUSE` dict's host (so a single-cluster deployment Just Works).

    Settings precedence:
      1. Explicit `settings.CLICKHOUSE_V2[...]` if defined
      2. Env vars `CH25_*`
      3. Legacy `settings.CLICKHOUSE[...]` values (re-used to point at the same host)
    """
    legacy = getattr(settings, "CLICKHOUSE", {}) or {}
    cfg = getattr(settings, "CLICKHOUSE_V2", {}) or {}
    return {
        "host":      cfg.get("CH25_HOST")      or os.getenv("CH25_HOST")      or legacy.get("CH_HOST", "127.0.0.1"),
        "http_port": int(cfg.get("CH25_HTTP_PORT") or os.getenv("CH25_HTTP_PORT") or 8123),
        "tcp_port":  int(cfg.get("CH25_TCP_PORT")  or os.getenv("CH25_TCP_PORT")  or legacy.get("CH_PORT", 9000)),
        "user":      cfg.get("CH25_USER")      or os.getenv("CH25_USER")      or legacy.get("CH_USERNAME", "default"),
        "password":  cfg.get("CH25_PASSWORD")  or os.getenv("CH25_PASSWORD")  or legacy.get("CH_PASSWORD", ""),
        "database":  cfg.get("CH25_DATABASE")  or os.getenv("CH25_DATABASE")  or legacy.get("CH_DATABASE", "futureagi"),
    }


def get_reader():
    """Returns a CHSpanReader bound to the v2 cluster, configured from settings.

    Used by `tracer/utils/eval.py` (post-cutover read path) and by management
    commands that need to inspect spans during validation.
    """
    from .span_reader import CHSpanReader
    cfg = get_v2_config()
    return CHSpanReader(
        host=cfg["host"], port=cfg["http_port"],
        username=cfg["user"], password=cfg["password"],
        database=cfg["database"],
    )


__all__ = ["get_v2_config", "get_reader"]
