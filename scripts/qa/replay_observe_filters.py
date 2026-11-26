#!/usr/bin/env python3
"""Bounded, read-only HTTP qualification of Observe filters. No Django/DB imports.

A generated case is NOT a test pass;
an exact HTTP completion without an independent oracle is still UNVERIFIED.
"""

from __future__ import annotations

import argparse
import collections
import copy
import datetime as dt
import hashlib
import http.client
import json
import math
import os
from pathlib import Path
import socket
import sys
import threading
import time
from urllib.parse import urlencode, urlsplit

VERSION = 6
REQUIRED_PROPERTY_FAMILIES = (
    "SPAN_ATTRIBUTE",
    "SYSTEM_METRIC",
    "EVAL_METRIC",
    "ANNOTATION",
)
LISTS = {
    "traces": "/tracer/trace/list_traces_of_session/",
    "spans": "/tracer/observation-span/list_spans_observe/",
    "sessions": "/tracer/trace-session/list_sessions/",
    "users_project": "/tracer/users/",
    "users_workspace": "/tracer/users/",
    "user_traces": "/tracer/trace/list_traces_of_session/",
    "user_sessions": "/tracer/trace-session/list_sessions/",
}
for _prefix in ("task", "eval"):
    for _entity in ("spans", "traces", "sessions"):
        LISTS[f"{_prefix}_{_entity}"] = LISTS[_entity]
GRAPHS = {
    "trace_graph": "/tracer/trace/get_graph_methods/",
    "span_graph": "/tracer/observation-span/get_graph_methods/",
    "session_graph": "/tracer/trace-session/get_session_graph_data/",
    "users_graph": "/tracer/project/get_users_aggregate_graph_data/",
}
DASHBOARD = "/tracer/dashboard/query/"
CATALOG = "/tracer/dashboard/metrics/"
VALUES = "/tracer/dashboard/filter_values/"
SURFACES = (
    tuple(LISTS)
    + tuple(GRAPHS)
    + (
        "dashboard_filter",
        "dashboard_breakdown",
        "dashboard_metric",
    )
)
ALLOWED = {("GET", p) for p in (*LISTS.values(), CATALOG, VALUES)} | {
    ("POST", p) for p in (*GRAPHS.values(), DASHBOARD)
}
TYPES = {
    "string": "text",
    "text": "text",
    "number": "number",
    "boolean": "boolean",
    "array": "array",
    "map": "map",
    "json": "map",
    "categorical": "categorical",
    "thumbs": "thumbs",
    "annotator": "annotator",
    "datetime": "datetime",
}


class ReplayError(Exception):
    """Only a static, non-sensitive diagnostic code is exposed to the ledger."""


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def digest(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def read_json(path):
    with open(path, encoding="utf-8") as stream:
        return json.load(stream)


def private_write(path, value):
    """Never overwrite an earlier plan, manifest or report."""
    with os.fdopen(
        os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "w"
    ) as out:
        json.dump(value, out, ensure_ascii=False, indent=2, allow_nan=False)
        out.write("\n")


def utc(value):
    parsed = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ReplayError("END_TIME_REQUIRES_TIMEZONE")
    return parsed.astimezone(dt.timezone.utc)


def window(end, name):
    if name == "12M":
        try:
            start = end.replace(year=end.year - 1)
        except ValueError:
            start = end.replace(year=end.year - 1, day=28)
    else:
        start = end - dt.timedelta(days=int(name[:-1]))
    return {"start": start.isoformat(), "end": end.isoformat()}


def checked_scope(scope):
    for key in ("organization_id", "workspace_id", "project_id"):
        if not isinstance(scope.get(key), str) or not scope[key].strip():
            raise ReplayError("MISSING_SCOPE_" + key.upper())
    return scope


class Client:
    def __init__(
        self, base_url, scope, authorization, build_header=None, expected_build=None
    ):
        self.base = urlsplit(base_url)
        if (
            self.base.scheme not in ("https", "http")
            or not self.base.hostname
            or self.base.username
            or self.base.password
            or self.base.query
            or self.base.fragment
            or ".." in self.base.path
            or "%" in self.base.path
            or (
                self.base.scheme == "http"
                and self.base.hostname not in ("localhost", "127.0.0.1", "::1")
            )
        ):
            raise ReplayError("HTTPS_REQUIRED_EXCEPT_LOOPBACK_NO_URL_CREDENTIALS")
        checked_scope(scope)
        self.headers = {
            "Accept": "application/json",
            "Content-Type": "application/json",
            "X-Workspace-Id": scope["workspace_id"],
            "X-Organization-Id": scope["organization_id"],
        }
        if not authorization or "\n" in authorization or "\r" in authorization:
            raise ReplayError("AUTHORIZATION_ENV_REQUIRED")
        self.headers["Authorization"] = authorization
        if bool(build_header) != bool(expected_build):
            raise ReplayError("BUILD_HEADER_AND_EXPECTED_BUILD_REQUIRED_TOGETHER")
        self.build_header = build_header
        self.expected_build = expected_build
        self.identity = {
            "base_url": base_url.rstrip("/"),
            "scope": scope,
            "build_header": build_header,
            "expected_build": expected_build,
        }

    def request(self, method, path, params, body, deadline):
        if (method, path) not in ALLOWED:
            raise ReplayError("ENDPOINT_NOT_READ_ALLOWLISTED")
        if any(k in params for k in ("refresh", "force_refresh")):
            raise ReplayError("FORCED_REFRESH_FORBIDDEN")
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise ReplayError("ACTION_DEADLINE")
        conn_type = (
            http.client.HTTPSConnection
            if self.base.scheme == "https"
            else http.client.HTTPConnection
        )
        conn = conn_type(self.base.hostname, self.base.port, timeout=remaining)
        target = self.base.path.rstrip("/") + path
        if params:
            target += "?" + urlencode(
                {
                    k: str(v).lower() if isinstance(v, bool) else v
                    for k, v in params.items()
                }
            )
        payload = canonical(body).encode() if body is not None else None
        active_socket = []

        def expire_socket():
            for sock in active_socket:
                try:
                    sock.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass

        timer = threading.Timer(remaining, expire_socket)
        timer.daemon = True
        timer.start()
        try:
            conn.connect()
            active_socket.append(conn.sock)
            conn.sock.settimeout(max(0.001, deadline - time.monotonic()))
            conn.request(method, target, body=payload, headers=self.headers)
            conn.sock.settimeout(max(0.001, deadline - time.monotonic()))
            response = conn.getresponse()
            if 300 <= response.status < 400:
                raise ReplayError("REDIRECT_REFUSED")
            if response.status != 200:
                raise ReplayError(f"HTTP_{response.status}")
            if "json" not in response.getheader("Content-Type", "").lower():
                raise ReplayError("NON_JSON_RESPONSE")
            build = response.getheader(self.build_header) if self.build_header else None
            if self.expected_build and build != self.expected_build:
                raise ReplayError("CANDIDATE_BUILD_MISMATCH_OR_MISSING")
            chunks, size = [], 0
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise ReplayError("ACTION_DEADLINE")
                # Also bound every body read, not just connect/header receipt.
                if conn.sock:
                    conn.sock.settimeout(remaining)
                chunk = response.read1(65536)
                if not chunk:
                    break
                size += len(chunk)
                if size > 16 * 1024 * 1024:
                    raise ReplayError("RESPONSE_EXCEEDS_16_MIB")
                chunks.append(chunk)
            if time.monotonic() > deadline:
                raise ReplayError("ACTION_DEADLINE")
            document = json.loads(b"".join(chunks))
            if not isinstance(document, dict) or document.get("status") is not True:
                raise ReplayError("API_ENVELOPE_ERROR")
            result = document.get("result")
            if not isinstance(result, dict):
                raise ReplayError("API_RESULT_NOT_OBJECT")
            return result, {"response_bytes": size, "build_verified": bool(build)}
        except (TimeoutError, socket.timeout) as exc:
            raise ReplayError("ACTION_DEADLINE") from exc
        except (json.JSONDecodeError, UnicodeError) as exc:
            raise ReplayError("INVALID_JSON") from exc
        except (OSError, http.client.HTTPException) as exc:
            raise ReplayError(
                "ACTION_DEADLINE" if time.monotonic() >= deadline else "TRANSPORT_ERROR"
            ) from exc
        finally:
            timer.cancel()
            conn.close()


def raw_leaf(attribute, kind, op, value=None):
    family = attribute.get("col_type", "SPAN_ATTRIBUTE")
    if family not in (*REQUIRED_PROPERTY_FAMILIES, "NORMAL"):
        raise ReplayError("UNKNOWN_PROPERTY_FAMILY")
    config = {"col_type": family, "filter_type": TYPES[kind], "filter_op": op}
    if op not in ("is_null", "is_not_null"):
        config["filter_value"] = value
    if op in ("in", "not_in") and family == "SPAN_ATTRIBUTE":
        config["attribute_value_types"] = [kind] * len(value)
    leaf = {
        "column_id": attribute.get("column_id", attribute["name"]),
        "filter_config": config,
    }
    if attribute.get("property_id"):
        leaf.update(
            property_id=attribute["property_id"],
            source=attribute.get("source", "traces"),
        )
    return leaf


def seed_valid(kind, value):
    if kind == "datetime":
        if not isinstance(value, str):
            return False
        try:
            utc(value)
        except (ReplayError, ValueError):
            return False
        return True
    if kind in ("thumbs", "annotator"):
        return isinstance(value, str) and bool(value)
    if kind == "categorical":
        return (isinstance(value, str) and bool(value)) or (
            isinstance(value, list)
            and bool(value)
            and all(isinstance(item, str) and bool(item) for item in value)
        )
    if kind in ("string", "text"):
        return isinstance(value, str) and bool(value)
    if kind == "number":
        return (
            isinstance(value, (int, float))
            and not isinstance(value, bool)
            and math.isfinite(value)
        )
    if kind == "boolean":
        return isinstance(value, bool)
    if kind == "array":
        return (
            isinstance(value, list)
            and bool(value)
            and all(
                seed_valid("string", x)
                or seed_valid("number", x)
                or isinstance(x, bool)
                for x in value
            )
        )
    return isinstance(value, dict) and bool(value)


def variants(attribute):
    """Real seeds only. Missing value coverage gets explicit blocked cases."""
    kinds = (
        attribute.get("observed_types")
        or attribute.get("attribute_types")
        or [attribute.get("resolved_type") or attribute.get("type", "string")]
    )
    seeds = attribute.get("seeds", [])
    for kind in sorted(set(kinds)):
        if kind not in TYPES:
            yield kind + ":unsupported", [], "UNSUPPORTED_CATALOG_TYPE"
            continue
        for op in ("is_not_null", "is_null"):
            yield f"{kind}:{op}", [raw_leaf(attribute, kind, op)], None
        valid = [
            s["value"]
            for s in seeds
            if s.get("type") == kind and seed_valid(kind, s.get("value"))
        ]
        if (
            kind == "boolean"
            and attribute.get("col_type", "SPAN_ATTRIBUTE") == "SPAN_ATTRIBUTE"
        ):
            values = list(dict.fromkeys(valid))
            selections = [(f"single:{i}", [v]) for i, v in enumerate(values)]
            if not values:
                selections.append(("single", None))
            selections.append(("multiple", values if len(values) > 1 else None))
            for label, operands in selections:
                for operation in ("in", "not_in"):
                    if operands is None:
                        reason = (
                            "OBSERVED_DISTINCT_BOOLEAN_VALUES_REQUIRED"
                            if label == "multiple"
                            else "OBSERVED_TYPED_VALUE_REQUIRED"
                        )
                        yield f"boolean:{operation}:{label}", [], reason
                        continue
                    leaf = raw_leaf(attribute, "boolean", operation, operands)
                    # Picker membership uses text's public operator envelope;
                    # explicit provenance retains Boolean values/map semantics.
                    leaf["filter_config"]["filter_type"] = "text"
                    yield f"boolean:{operation}:{label}", [leaf], None
        if not valid:
            yield kind + ":value", [], "OBSERVED_TYPED_VALUE_REQUIRED"
            continue
        if kind in ("categorical", "thumbs", "annotator"):
            values = list(
                dict.fromkeys(
                    item
                    for value in valid
                    for item in (value if isinstance(value, list) else [value])
                )
            )
            for op in ("equals", "not_equals", "in", "not_in") + (
                ("contains", "not_contains") if kind == "categorical" else ()
            ):
                value = (
                    values[:5]
                    if op in ("in", "not_in", "contains", "not_contains")
                    else values[0]
                )
                yield f"{kind}:{op}:short", [raw_leaf(attribute, kind, op, value)], None
            continue
        # Preserve short/long and >500-byte real string representatives, without truncation.
        selected = {}
        for value in valid:
            size = len(value.encode()) if isinstance(value, str) else 0
            bucket = "over500" if size > 500 else "long" if size > 128 else "short"
            selected.setdefault(bucket, value)
        for label, value in selected.items():
            op = "contains" if kind == "array" else "equals"
            yield f"{kind}:{op}:{label}", [raw_leaf(attribute, kind, op, value)], None
            negative = "not_contains" if kind == "array" else "not_equals"
            yield (
                f"{kind}:{negative}:{label}",
                [raw_leaf(attribute, kind, negative, value)],
                None,
            )
            if kind in ("string", "text"):
                for operation in (
                    "contains",
                    "not_contains",
                    "starts_with",
                    "ends_with",
                ):
                    yield (
                        f"{kind}:{operation}:{label}",
                        [raw_leaf(attribute, kind, operation, value)],
                        None,
                    )
                for operation in ("in", "not_in"):
                    yield (
                        f"{kind}:{operation}:{label}",
                        [raw_leaf(attribute, "string", operation, valid[:5])],
                        None,
                    )
            if kind in ("number", "datetime"):
                for operation in (
                    "greater_than",
                    "greater_than_or_equal",
                    "less_than",
                    "less_than_or_equal",
                ):
                    yield (
                        f"{kind}:{operation}",
                        [raw_leaf(attribute, kind, operation, value)],
                        None,
                    )
                for operation in ("between", "not_between"):
                    yield (
                        f"{kind}:{operation}",
                        [
                            raw_leaf(
                                attribute, kind, operation, [min(valid), max(valid)]
                            )
                        ],
                        None,
                    )
            if kind in ("map", "json"):
                for operation in ("contains", "not_contains"):
                    yield (
                        f"{kind}:{operation}:{label}",
                        [raw_leaf(attribute, kind, operation, value)],
                        None,
                    )


def request_for(surface, scope, selected, filters, bounds):
    if surface.startswith("user_"):
        if not scope.get("user_id"):
            return None, "PUBLIC_USER_ID_REQUIRED"
        filters = filters + [
            {
                "column_id": "user_id",
                "filter_config": {
                    "filter_type": "text",
                    "filter_op": "equals",
                    "filter_value": scope["user_id"],
                },
            }
        ]
    if surface in LISTS:
        users = surface.startswith("users_")
        params = {
            "cursor_mode": True,
            # Match the actual frontend list workload, not just the eventual
            # single row chosen for detail: Tasks asks for 1, Eval asks for 50.
            "page_size": 1
            if surface.startswith("task_")
            else 50
            if surface.startswith("eval_")
            else 25,
            "current_page_index" if users else "page_number": 0,
            "filters": canonical(filters),
        }
        if surface != "users_workspace":
            params["project_id"] = scope["project_id"]
        keys = [
            a["name"]
            for a in selected
            if a.get("col_type", "SPAN_ATTRIBUTE") == "SPAN_ATTRIBUTE"
        ]
        if "sessions" not in surface and "spans" not in surface:
            if sum(len(key.encode()) for key in keys) > 2048 or any(
                len(key) > 512 for key in keys
            ):
                return None, "ATTRIBUTE_KEYS_EXCEED_API_2048_BYTE_LIMIT"
            params["attribute_keys"] = canonical(keys)
        if users:
            params["requested_columns"] = canonical(
                [
                    "user_id",
                    "activated_at",
                    "last_active",
                    "num_traces",
                    "num_sessions",
                    "total_tokens",
                    "total_cost",
                    "actions",
                ]
            )
        return {
            "method": "GET",
            "path": LISTS[surface],
            "params": params,
            "body": None,
            "kind": "list",
            "target_rows": params["page_size"],
        }, None
    if surface in GRAPHS:
        namespace = (
            "users"
            if surface == "users_graph"
            else "sessions"
            if surface == "session_graph"
            else "traces"
        )
        body = {
            "project_id": scope["project_id"],
            "filters": filters,
            "interval": "day",
            "property": "average",
            "req_data_config": {
                "id": "latency",
                "type": "SYSTEM_METRIC",
                "property_id": f"system_attribute:{namespace}:latency",
                "source": "sessions"
                if namespace in ("users", "sessions")
                else "traces",
            },
        }
        return {
            "method": "POST",
            "path": GRAPHS[surface],
            "params": {},
            "body": body,
            "kind": "graph",
        }, None
    body = {
        "workflow": "observability",
        "project_ids": [scope["project_id"]],
        "time_range": {"custom_start": bounds["start"], "custom_end": bounds["end"]},
        "granularity": "day",
        "filters": filters,
        "allow_sampled": False,
        "metrics": [
            {
                "id": "latency",
                "name": "latency",
                "type": "system_metric",
                "source": "traces",
                "aggregation": "avg",
            }
        ],
        "breakdowns": [],
    }
    first = selected[0]
    if (
        surface in ("dashboard_breakdown", "dashboard_metric")
        and first.get("col_type", "SPAN_ATTRIBUTE") != "SPAN_ATTRIBUTE"
    ):
        # Do not silently turn an eval/annotation/system identity into a custom
        # span key. These projections need their own source-specific payload.
        return None, "SOURCE_SPECIFIC_DASHBOARD_PROJECTION_NOT_IMPLEMENTED"
    kind = first.get("resolved_type") or first.get("type") or "string"
    if surface == "dashboard_breakdown":
        if kind not in ("string", "number", "boolean"):
            return None, "STRUCTURED_BREAKDOWN_NOT_COVERED"
        body["breakdowns"] = [
            {
                "name": first["name"],
                "type": "custom_attribute",
                "source": "traces",
                "attribute_type": kind,
            }
        ]
    if surface == "dashboard_metric":
        numeric = "number" in (
            first.get("observed_types") or first.get("attribute_types") or [kind]
        )
        if not numeric and kind not in ("string", "text", "boolean"):
            return None, "STRUCTURED_METRIC_NOT_COVERED"
        # The real API supports exact count/count_distinct for text and bool.
        # A nonnumeric value is not a reason to skip the whole metric surface;
        # it only excludes numeric-only aggregations such as avg/percentiles.
        # Keep the original numeric workload (including mixed historical
        # attributes), and do not reinterpret structured values as strings.
        metric_kind = "number" if numeric else kind
        aggregations = (
            ("avg", "min", "max", "p25", "p50")
            if numeric
            else ("count", "count_distinct")
        )
        body["metrics"] = [
            {
                "id": "m" + str(i),
                "name": first["name"],
                "type": "custom_attribute",
                "source": "traces",
                "attribute_key": first["name"],
                "attribute_type": metric_kind,
                "aggregation": agg,
            }
            for i, agg in enumerate(aggregations)
        ]
    return {
        "method": "POST",
        "path": DASHBOARD,
        "params": {},
        "body": body,
        "kind": "dashboard",
    }, None


def make_plan(inventory, end, surfaces=SURFACES, combo_sets=6, reproductions=()):
    scope = checked_scope(inventory["scope"])
    attrs = list(inventory["attributes"])
    for prop in inventory.get("properties", []):
        family = prop.get("col_type")
        if (
            family not in (*REQUIRED_PROPERTY_FAMILIES, "NORMAL")
            or family == "SPAN_ATTRIBUTE"
        ):
            raise ReplayError("INVALID_ADDITIONAL_PROPERTY_FAMILY")
        column_id = prop.get("column_id")
        if not isinstance(column_id, str) or not column_id:
            raise ReplayError("PROPERTY_COLUMN_ID_REQUIRED")
        # A system metric and an attribute may share a displayed name. The
        # matrix identity includes source/family; the wire column id does not.
        attrs.append(
            {
                **prop,
                "display_name": prop.get("name", column_id),
                "name": f"{family}:{prop.get('source', 'traces')}:{column_id}",
            }
        )
    attrs.sort(key=lambda a: a["name"])
    if not attrs or len({a["name"] for a in attrs}) != len(attrs):
        raise ReplayError("EMPTY_OR_DUPLICATE_ATTRIBUTE_INVENTORY")
    if set(surfaces) - set(SURFACES):
        raise ReplayError("UNKNOWN_SURFACE")
    recipes = []
    # Customer-entered predicates are legitimate reproduction inputs, not
    # assertions that their values exist in the catalog. Keep their provenance
    # separate from discovered suggestion seeds and never widen tenant/time.
    by_name = {a["name"]: a for a in attrs}
    labels = set()
    for reproduction in reproductions:
        label = reproduction.get("label")
        filters = reproduction.get("filters")
        if (
            not isinstance(label, str)
            or not label
            or label in labels
            or not isinstance(filters, list)
            or not 1 <= len(filters) <= 10
        ):
            raise ReplayError("INVALID_REPRODUCTION_INPUT")
        labels.add(label)
        selected, leaves = [], []
        for item in filters:
            name, kind, op = item.get("name"), item.get("type"), item.get("op")
            attr = by_name.get(name)
            if attr is None or kind not in (
                attr.get("observed_types")
                or attr.get("attribute_types")
                or [attr.get("resolved_type") or attr.get("type", "string")]
            ):
                raise ReplayError("REPRODUCTION_ATTRIBUTE_TYPE_NOT_IN_INVENTORY")
            allowed_ops = {"equals", "not_equals", "is_null", "is_not_null"}
            if kind in ("string", "text"):
                allowed_ops |= {"in", "not_in", "contains", "not_contains"}
            elif kind == "number":
                allowed_ops |= {
                    "greater_than",
                    "less_than",
                    "greater_than_or_equal",
                    "less_than_or_equal",
                }
            if (
                kind not in ("string", "text", "number", "boolean")
                or op not in allowed_ops
            ):
                raise ReplayError("UNSUPPORTED_REPRODUCTION_FILTER")
            value = item.get("value")
            if op not in ("is_null", "is_not_null"):
                values = value if op in ("in", "not_in") else [value]
                if (
                    not isinstance(values, list)
                    or not values
                    or not all(seed_valid(kind, v) for v in values)
                ):
                    raise ReplayError("REPRODUCTION_VALUE_TYPE_MISMATCH")
            if attr not in selected:
                selected.append(attr)
            leaves.append(raw_leaf(attr, kind, op, value))
        recipes.append((selected, f"reported:{label}", leaves, None))
    for attr in attrs:
        for label, leaves, blocked in variants(attr):
            recipes.append(([attr], label, leaves, blocked))
    # Deterministic mixed-type round-robin. Not combinatorial exhaustive coverage.
    buckets = collections.defaultdict(list)
    for attr in attrs:
        buckets[
            (
                attr.get("col_type", "SPAN_ATTRIBUTE"),
                attr.get("resolved_type") or attr.get("type", "string"),
            )
        ].append(attr)
    mixed = []
    while any(buckets.values()):
        for bucket in sorted(buckets):
            if buckets[bucket]:
                mixed.append(buckets[bucket].pop(0))
    for width in (2, 5, 10):
        if len(mixed) < width:
            continue
        for n in range(min(combo_sets, len(mixed))):
            chosen = [mixed[(n * width + j) % len(mixed)] for j in range(width)]
            leaves, blocked = [], None
            for attr in chosen:
                choices = [
                    v
                    for v in variants(attr)
                    if not v[2]
                    and (
                        ":equals:" in v[0]
                        or (v[0].startswith("array:") and ":contains:" in v[0])
                    )
                ]
                if choices:
                    leaves.extend(choices[0][1])
                else:
                    blocked = "COMBINATION_OBSERVED_VALUES_REQUIRED"
            recipes.append((chosen, f"mixed:{width}:{n}", leaves, blocked))
            present = []
            for attr in chosen:
                choices = [
                    v
                    for v in variants(attr)
                    if not v[2] and v[0].endswith(":is_not_null")
                ]
                if choices:
                    present.extend(choices[0][1])
            recipes.append(
                (
                    chosen,
                    f"mixed_presence:{width}:{n}",
                    present,
                    None if len(present) == width else "COMBINATION_TYPES_UNSUPPORTED",
                )
            )
    cases = []
    for selected, label, leaves, blocked in recipes:
        for period in ("7D", "30D", "12M"):
            bounds = window(end, period)
            date = {
                "column_id": "created_at",
                "filter_config": {
                    "filter_type": "datetime",
                    "col_type": "SYSTEM_METRIC",
                    "filter_op": "between",
                    "filter_value": [bounds["start"], bounds["end"]],
                },
            }
            for surface in surfaces:
                req, request_blocked = request_for(
                    surface, scope, selected, [date] + leaves, bounds
                )
                case = {
                    "surface": surface,
                    "period": period,
                    "window": bounds,
                    "variant": label,
                    "attributes": [a["name"] for a in selected],
                    "property_families": sorted(
                        {a.get("col_type", "SPAN_ATTRIBUTE") for a in selected}
                    ),
                    "request": req,
                    "blocked": blocked or request_blocked,
                    "tier": "complex"
                    if period == "12M" or len(selected) >= 5
                    else "large"
                    if period == "30D" or len(selected) > 1
                    else "normal",
                }
                case["target_ms"] = (
                    5000 if surface in LISTS else 30000 if period == "12M" else 15000
                )
                case["id"] = digest(case)[:24]
                cases.append(case)
    # Different string buckets can yield the same membership request; IDs still describe each case.
    if len({c["id"] for c in cases}) != len(cases):
        raise ReplayError("DUPLICATE_CASE_IDS")
    plan = {
        "version": VERSION,
        "scope": scope,
        "end": end.isoformat(),
        "surfaces": list(surfaces),
        "inventory_sha256": digest(inventory),
        "attribute_count": len(inventory["attributes"]),
        "property_count": len(attrs),
        "property_family_inventory": {
            family: {
                "properties": sum(
                    a.get("col_type", "SPAN_ATTRIBUTE") == family for a in attrs
                ),
                "inventory_verified_complete": inventory.get(
                    "property_family_completeness", {}
                ).get(family)
                is True,
            }
            for family in REQUIRED_PROPERTY_FAMILIES
        },
        "combo_sets_per_width": combo_sets,
        "reported_input_count": len(reproductions),
        "cases": cases,
        "limitations": [
            "First-page HTTP actions, not browser rendering or complete entity hydration.",
            "Tasks/eval cases test preview-list stage only; do not execute evaluations or save tasks.",
            "Sparse/dense population labels need independent occurrence evidence.",
            "Discovered suggestions and explicit reported inputs are separate; missing observed seeds remain BLOCKED_INPUT.",
            "API completion is not correctness; independent oracle and candidate identity required.",
            "Custom attributes alone cannot qualify eval, annotation or system property families.",
        ],
    }
    plan["plan_id"] = digest(plan)
    return plan


def inspect_exact(metadata):
    if any(metadata.get(k) is True for k in ("query_sampled", "sampled", "is_sampled")):
        raise ReplayError("SAMPLED_RESPONSE")
    if metadata.get("query_status") == "sampled":
        raise ReplayError("SAMPLED_RESPONSE")
    if (
        metadata.get("query_exact") is False
        or metadata.get("ordering_exact") is False
        or metadata.get("approximate_fields")
    ):
        raise ReplayError("INEXACT_RESPONSE")


def payload_for(result, kind):
    if kind == "list":
        rows = result.get("table")
        if not isinstance(rows, list):
            raise ReplayError("TABLE_MISSING")
        return rows
    if kind == "graph":
        data = result.get("data")
        if not isinstance(data, list):
            raise ReplayError("GRAPH_DATA_MISSING")
        return {"metric_name": result.get("metric_name"), "data": data}
    metrics = result.get("metrics")
    if not isinstance(metrics, list) or not metrics:
        raise ReplayError("DASHBOARD_METRICS_MISSING")
    for metric in metrics:
        inspect_exact(metric)
        if metric.get("query_complete") is False or metric.get("query_status") in (
            "pending",
            "degraded",
        ):
            raise ReplayError("DASHBOARD_METRIC_INCOMPLETE")
    return [
        {k: m[k] for k in ("id", "name", "aggregation", "series") if k in m}
        for m in metrics
    ]


def run_case(client, case, seconds, oracle=None, poll_seconds=0.25):
    start = time.monotonic()
    result_row = {
        "case_id": case["id"],
        "surface": case["surface"],
        "period": case["period"],
        "attribute_count": len(case["attributes"]),
        "tier": case["tier"],
        "status": "BLOCKED_INPUT" if case["blocked"] else "RUNNING",
        "requests": 0,
        "correctness": "UNVERIFIED",
        "candidate_verified": False,
    }
    if case["blocked"]:
        return {**result_row, "reason": case["blocked"], "elapsed_ms": 0}
    req = copy.deepcopy(case["request"])
    deadline = start + seconds
    cursors, row_hashes, accumulated = set(), set(), []
    build_verified = True
    try:
        for _ in range(100):
            result_row["requests"] += 1
            result, transport = client.request(
                req["method"], req["path"], req["params"], req["body"], deadline
            )
            build_verified = build_verified and transport["build_verified"]
            meta = {**result, **result.get("metadata", {})}
            inspect_exact(meta)
            result_row["server_cached"] = meta.get("query_cached")
            result_row["server_status"] = meta.get("query_status")
            result_row["server_filter_sha256"] = meta.get("query_applied_filter_sha256")
            result_row["server_filter_count"] = meta.get("query_applied_filter_count")
            complete = (
                meta.get("query_complete") is True
                and meta.get("query_status") == "complete"
            )
            if req["kind"] == "list":
                rows = payload_for(result, "list")
                if len(rows) > req["target_rows"]:
                    raise ReplayError("PAGE_EXCEEDS_REQUESTED_SIZE")
                # A degraded response may expose a proven prefix. Require explicit exact evidence.
                if rows and not complete and meta.get("query_exact") is not True:
                    raise ReplayError("INCOMPLETE_ROWS_WITHOUT_EXACT_PREFIX_PROOF")
                for row in rows:
                    identity = digest(row)
                    if identity in row_hashes:
                        raise ReplayError("DUPLICATE_ROW_ACROSS_CONTINUATIONS")
                    row_hashes.add(identity)
                    accumulated.append(row)
                # Signed cursors keep their original transport page size. A
                # partial prefix followed by a full transport page can exceed
                # one visible page; the UI buffers that overflow, not errors.
                if len(accumulated) >= req["target_rows"] or (
                    complete and meta.get("has_more") is False
                ):
                    payload = accumulated[: req["target_rows"]]
                    overflow = accumulated[req["target_rows"] :]
                    result_row["buffered_overflow_rows"] = len(overflow)
                    result_row["buffered_overflow_sha256"] = digest(overflow)
                    result_row["rows"] = len(payload)
                    result_row["population_exhausted"] = (
                        complete and meta.get("has_more") is False and not overflow
                    )
                    result_row["count_is_lower_bound"] = meta.get(
                        "count_is_lower_bound", meta.get("total_rows_is_lower_bound")
                    )
                    break
                token = meta.get("next_cursor")
                identity = meta.get("next_cursor_fingerprint") or digest(token)
                if not isinstance(token, str) or not token:
                    raise ReplayError("INCOMPLETE_WITHOUT_CURSOR")
                if identity in cursors:
                    raise ReplayError("REPEATED_CURSOR")
                cursors.add(identity)
                req["params"].pop("page_number", None)
                req["params"].pop("current_page_index", None)
                req["params"]["cursor"] = token
                # Page size is part of signed cursor identity: keep original request size.
                req["params"]["page_size"] = case["request"]["params"]["page_size"]
            elif complete:
                payload = payload_for(result, req["kind"])
                break
            elif meta.get("query_status") != "pending":
                raise ReplayError("AGGREGATION_INCOMPLETE_OR_ERROR")
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise ReplayError("ACTION_DEADLINE")
            time.sleep(min(poll_seconds, remaining))
        else:
            raise ReplayError("CONTINUATION_LIMIT")
        if time.monotonic() > deadline:
            raise ReplayError("ACTION_DEADLINE")
        result_row["result_sha256"] = digest(payload)
        result_row["candidate_verified"] = build_verified
        if oracle:
            if not oracle.get("provenance") or not oracle.get("result_sha256"):
                raise ReplayError("INDEPENDENT_ORACLE_METADATA_REQUIRED")
            if oracle["result_sha256"] != result_row["result_sha256"]:
                raise ReplayError("ORACLE_MISMATCH")
            result_row["correctness"] = "ORACLE_MATCH"
        result_row["status"] = (
            "API_PASS" if oracle and build_verified else "COMPLETE_UNVERIFIED"
        )
    except ReplayError as exc:
        # Diagnostic safety stops do not establish an application failure or
        # an empty result. In particular, a latency target is never a timeout.
        result_row.update(
            status="SAFETY_STOP"
            if str(exc) in ("ACTION_DEADLINE", "CONTINUATION_LIMIT")
            else "FAIL",
            reason=str(exc),
        )
    except (TypeError, ValueError, KeyError, AttributeError):
        result_row.update(status="FAIL", reason="MALFORMED_RESPONSE")
    result_row["elapsed_ms"] = round((time.monotonic() - start) * 1000, 2)
    goal = case["target_ms"]
    result_row["target_ms"] = goal
    result_row["latency_met"] = result_row["elapsed_ms"] <= goal and result_row[
        "status"
    ] in ("API_PASS", "COMPLETE_UNVERIFIED")
    result_row["performance"] = (
        "TARGET_MET" if result_row["latency_met"] else "TARGET_NOT_MET"
    )
    return result_row


def activation(result):
    keys = ("catalog_epoch", "catalog_revision", "activation_fingerprint")
    if any(result.get(k) is None for k in keys):
        raise ReplayError("CATALOG_ACTIVATION_EVIDENCE_MISSING")
    return {k: result[k] for k in keys}


def discover(client, scope, limit_seconds=300):
    deadline = time.monotonic() + limit_seconds
    params = {
        "cursor_mode": True,
        "page_size": 100,
        "category": "custom_attribute",
        "source": "traces",
        "project_ids": scope["project_id"],
        "per_eval_config": True,
    }
    attrs, tokens, chosen_activation = [], set(), None
    while True:
        result, _ = client.request(
            "GET", CATALOG, params, None, min(deadline, time.monotonic() + 10)
        )
        inspect_exact(result)
        if (
            result.get("query_complete") is not True
            or result.get("query_exact") is not True
        ):
            raise ReplayError("CATALOG_INCOMPLETE")
        current = activation(result)
        chosen_activation = chosen_activation or current
        if current != chosen_activation:
            raise ReplayError("CATALOG_ACTIVATION_CHANGED")
        if not isinstance(result.get("metrics"), list):
            raise ReplayError("CATALOG_METRICS_MISSING")
        attrs.extend(result["metrics"])
        if result.get("has_more") is False:
            break
        token = result.get("next_cursor")
        if not token or token in tokens:
            raise ReplayError("CATALOG_CURSOR_MISSING_OR_REPEATED")
        tokens.add(token)
        params["cursor"] = token
    manifest = {
        "version": VERSION,
        "scope": scope,
        "activation": chosen_activation,
        "catalog_complete": True,
        "attributes": [],
        "values_policy": "First 100 suggestions per attribute; never source-row sampling.",
    }
    for attr in attrs:
        entry = {
            "name": attr["name"],
            "property_id": attr.get("property_id"),
            "resolved_type": attr.get("output_type") or attr.get("type"),
            "observed_types": attr.get("attribute_types")
            or [attr.get("output_type") or attr.get("type")],
            "seeds": [],
            "values_complete": False,
        }
        if time.monotonic() >= deadline:
            entry["seed_error"] = "DISCOVERY_RUN_DEADLINE"
        else:
            value_params = {
                "metric_name": attr["name"],
                "metric_type": "custom_attribute",
                "source": "traces",
                "project_ids": scope["project_id"],
                "page_size": 100,
            }
            if attr.get("property_id"):
                value_params["property_id"] = attr["property_id"]
            try:
                result, _ = client.request(
                    "GET",
                    VALUES,
                    value_params,
                    None,
                    min(deadline, time.monotonic() + 10),
                )
                inspect_exact(result)
                if result.get("query_complete") is not True:
                    raise ReplayError("VALUES_INCOMPLETE")
                if activation(result) != chosen_activation:
                    raise ReplayError("CATALOG_ACTIVATION_CHANGED")
                entry["seeds"] = [
                    {"type": v["type"], "value": v["value"]}
                    for v in result.get("values", [])
                    if isinstance(v, dict) and "value" in v and v.get("type") in TYPES
                ]
                entry["values_complete"] = result.get("has_more") is False
            except ReplayError as exc:
                if str(exc) == "CATALOG_ACTIVATION_CHANGED":
                    raise
                entry["seed_error"] = str(exc)
        manifest["attributes"].append(entry)
    return manifest


def summarize(plan, rows):
    latest = {}
    for row in rows:
        latest[row["case_id"]] = row
    counts = collections.Counter(r["status"] for r in latest.values())
    times = sorted(
        r["elapsed_ms"]
        for r in latest.values()
        if r["status"] in ("API_PASS", "COMPLETE_UNVERIFIED")
    )
    coverage = {}
    for surface in plan["surfaces"]:
        cases = [c for c in plan["cases"] if c["surface"] == surface]
        by_status = collections.Counter(
            latest[c["id"]]["status"] if c["id"] in latest else "NOT_RUN" for c in cases
        )
        coverage[surface] = dict(by_status)
    return {
        "plan_id": plan["plan_id"],
        "attributes": plan["attribute_count"],
        "planned": len(plan["cases"]),
        "attempted": len(latest),
        "not_run": len(plan["cases"]) - len(latest),
        "statuses": dict(counts),
        "completion_p50_ms": times[len(times) // 2] if times else None,
        "completion_p95_ms": times[
            min(len(times) - 1, math.ceil(len(times) * 0.95) - 1)
        ]
        if times
        else None,
        "slo_misses_among_completions": sum(
            not r.get("latency_met", False)
            for r in latest.values()
            if r["status"] in ("API_PASS", "COMPLETE_UNVERIFIED")
        ),
        "by_surface": coverage,
        "ui_e2e_verified": False,
        "qualification": "NOT_QUALIFIED"
        if len(latest) != len(plan["cases"])
        or counts.get("API_PASS", 0) != len(plan["cases"])
        or any(not r.get("latency_met") for r in latest.values())
        else "API_MATRIX_PASSED_UI_UNVERIFIED",
    }


def load_ledger(path, run_id):
    if not Path(path).exists():
        return []
    rows = []
    with open(path, encoding="utf-8") as stream:
        for line in stream:
            try:
                row = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ReplayError(
                    "INCOMPLETE_LEDGER_LINE_REVIEW_BEFORE_RESUMING"
                ) from exc
            if row.get("run_id") != run_id:
                raise ReplayError("LEDGER_PLAN_TARGET_OR_OPTIONS_MISMATCH")
            rows.append(row)
    return rows


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    discovery = sub.add_parser("discover")
    planner = sub.add_parser("plan")
    runner = sub.add_parser("run")
    for cmd in (discovery, runner):
        cmd.add_argument(
            "--base-url",
            required=True,
            help="Exact API root, including any /api prefix",
        )
        cmd.add_argument("--auth-env", default="OBSERVE_REPLAY_AUTHORIZATION")
        cmd.add_argument("--build-header")
        cmd.add_argument("--expected-build")
        cmd.add_argument("--run-seconds", type=float, default=300)
    for cmd in (discovery, planner):
        cmd.add_argument("--output", required=True)
    discovery.add_argument(
        "--scope",
        required=True,
        help="JSON containing organization_id/workspace_id/project_id and optional user_id",
    )
    planner.add_argument("--inventory", required=True)
    planner.add_argument("--end", required=True, help="Fixed ISO8601 UTC window end")
    planner.add_argument("--user-id", help="Actual public user ID for user-detail tabs")
    planner.add_argument("--surfaces", default=",".join(SURFACES))
    planner.add_argument("--combo-sets", type=int, default=6)
    planner.add_argument(
        "--reproductions",
        help="Private JSON list of explicitly reported typed filter inputs, not observed-value seeds",
    )
    runner.add_argument("--plan", required=True)
    runner.add_argument("--ledger", required=True)
    runner.add_argument("--summary", required=True)
    runner.add_argument(
        "--oracle", help="Independent oracle JSON: plan_id and cases keyed by case_id"
    )
    runner.add_argument(
        "--candidate-label",
        required=True,
        help="Human label only, not proof of deployed build",
    )
    runner.add_argument(
        "--action-seconds",
        type=float,
        default=60,
        help="Separate diagnostic safety ceiling (default 60s); NOT the 5/15/30s performance target",
    )
    runner.add_argument("--max-cases", type=int, default=25)
    runner.add_argument("--max-consecutive-failures", type=int, default=3)
    runner.add_argument("--delay-seconds", type=float, default=0.25)
    runner.add_argument("--surface", choices=SURFACES)
    runner.add_argument("--period", choices=("7D", "30D", "12M"))
    runner.add_argument(
        "--attribute", help="Exact name; matches single and mixed cases"
    )
    runner.add_argument("--retry-failed", action="store_true")
    args = parser.parse_args(argv)
    if args.command == "plan":
        if not 0 <= args.combo_sets <= 100:
            raise ReplayError("COMBO_SETS_MUST_BE_0_TO_100")
        inventory = read_json(args.inventory)
        if args.user_id:
            inventory["scope"]["user_id"] = args.user_id
        plan = make_plan(
            inventory,
            utc(args.end),
            tuple(args.surfaces.split(",")),
            args.combo_sets,
            read_json(args.reproductions) if args.reproductions else (),
        )
        private_write(args.output, plan)
        print(
            canonical(
                {
                    "plan_id": plan["plan_id"],
                    "attributes": plan["attribute_count"],
                    "planned": len(plan["cases"]),
                    "blocked": sum(bool(c["blocked"]) for c in plan["cases"]),
                    "executed": 0,
                }
            )
        )
        return 0
    if not 0 < args.run_seconds <= 3600:
        raise ReplayError("RUN_SECONDS_MUST_BE_0_TO_3600")
    document = read_json(args.scope if args.command == "discover" else args.plan)
    scope = document if args.command == "discover" else document["scope"]
    client = Client(
        args.base_url,
        scope,
        os.environ.get(args.auth_env),
        args.build_header,
        args.expected_build,
    )
    if args.command == "discover":
        manifest = discover(client, scope, args.run_seconds)
        private_write(args.output, manifest)
        print(
            canonical(
                {
                    "attributes": len(manifest["attributes"]),
                    "seed_errors": sum(
                        "seed_error" in a for a in manifest["attributes"]
                    ),
                    "executed_filter_cases": 0,
                }
            )
        )
        return 0
    if (
        (not 0 < args.action_seconds <= 300)
        or not 1 <= args.max_cases <= 100000
        or args.delay_seconds < 0.1
        or args.max_consecutive_failures < 1
    ):
        raise ReplayError("INVALID_REQUEST_BUDGET_OR_SERIAL_PACING")
    plan = document
    if plan["plan_id"] != digest({k: v for k, v in plan.items() if k != "plan_id"}):
        raise ReplayError("PLAN_CHANGED_REGENERATE_INSTEAD")
    oracles = read_json(args.oracle) if args.oracle else {}
    if oracles and oracles.get("plan_id") != plan["plan_id"]:
        raise ReplayError("ORACLE_PLAN_MISMATCH")
    run_id = digest(
        {
            "plan_id": plan["plan_id"],
            "target": client.identity,
            "label": args.candidate_label,
            "action_seconds": args.action_seconds,
            "oracle_sha256": digest(oracles),
        }
    )
    if Path(args.summary).exists():
        raise ReplayError("SUMMARY_EXISTS_CHOOSE_NEW_PATH_BEFORE_RUN")
    stop = time.monotonic() + args.run_seconds
    failures, attempted = 0, 0
    # Lock prevents a second local runner from doubling production load/resuming this ledger.
    lock = Path(args.ledger + ".lock")
    try:
        lock_fd = os.open(lock, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError as exc:
        raise ReplayError("LEDGER_LOCK_EXISTS_VERIFY_RUNNER_BEFORE_REMOVING") from exc
    os.close(lock_fd)
    try:
        rows = load_ledger(args.ledger, run_id)
        latest = {r["case_id"]: r for r in rows}
        with os.fdopen(
            os.open(args.ledger, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600), "a"
        ) as output:
            for case in plan["cases"]:
                old = latest.get(case["id"])
                if old and not (
                    args.retry_failed and old["status"] in ("FAIL", "SAFETY_STOP")
                ):
                    continue
                if (
                    (args.surface and case["surface"] != args.surface)
                    or (args.period and case["period"] != args.period)
                    or (args.attribute and args.attribute not in case["attributes"])
                ):
                    continue
                if (
                    attempted >= args.max_cases
                    or time.monotonic() >= stop
                    or failures >= args.max_consecutive_failures
                ):
                    break
                seconds = min(
                    args.action_seconds,
                    stop - time.monotonic(),
                )
                row = run_case(
                    client, case, seconds, oracles.get("cases", {}).get(case["id"])
                )
                row.update(
                    run_id=run_id,
                    observed_at=dt.datetime.now(dt.timezone.utc).isoformat(),
                )
                output.write(canonical(row) + "\n")
                output.flush()
                os.fsync(output.fileno())
                rows.append(row)
                attempted += 1
                if row["status"] in ("FAIL", "SAFETY_STOP"):
                    failures += 1
                elif row["status"] != "BLOCKED_INPUT":
                    failures = 0
                print(canonical(row), flush=True)
                time.sleep(min(args.delay_seconds, max(0, stop - time.monotonic())))
    except KeyboardInterrupt:
        print(
            "Interrupted; completed actions are checkpointed. In-flight action is not counted.",
            file=sys.stderr,
        )
    finally:
        lock.unlink()
    summary = summarize(plan, rows)
    private_write(args.summary, summary)
    print(canonical(summary))
    return 0 if summary["qualification"] == "API_MATRIX_PASSED_UI_UNVERIFIED" else 2


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (ReplayError, FileExistsError) as error:
        print(
            str(error) if isinstance(error, ReplayError) else "OUTPUT_ALREADY_EXISTS",
            file=sys.stderr,
        )
        raise SystemExit(2) from None
