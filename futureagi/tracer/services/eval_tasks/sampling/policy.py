"""Policy validation and portable deterministic sampling primitives.

Closed windows use an exact, frozen bottom-k sample. Live windows use a stable
Bernoulli decision on first admission, never a repeated first-N page.
"""

from __future__ import annotations

import hashlib
import json
import math
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from decimal import Decimal

MAX_STRATA = 100
MAX_WINDOW = timedelta(days=31)
UNITS = ("span", "trace", "session", "user", "attribute")
SOURCES = ("span", "resource", "metadata")


class InvalidSamplingPolicy(ValueError):
    pass


def _keys(value: dict, allowed: set[str], context: str) -> None:
    if not isinstance(value, dict) or set(value) - allowed:
        raise InvalidSamplingPolicy(f"Invalid or unknown {context} fields.")


def attribute(value: dict) -> dict:
    _keys(value, {"source", "key"}, "attribute")
    key = value.get("key")
    if not isinstance(key, str) or not key or len(key) > 200 or "\x00" in key:
        raise InvalidSamplingPolicy("Attribute keys must contain 1–200 characters.")
    source = value.get("source", "span")
    if source not in SOURCES:
        raise InvalidSamplingPolicy(
            "Attribute source must be span, resource or metadata."
        )
    return {"source": source, "key": key}


def _integer(value: object, field: str) -> int:
    if type(value) is not int or not 0 <= value <= 1_000_000:
        raise InvalidSamplingPolicy(f"{field} must be an integer from 0 to 1000000.")
    return value


def _rule(value: dict, *, live: bool, named: bool) -> dict:
    _keys(
        value,
        {"name", "filters", "rate", "target_n", "min_n", "max_n", "rare_below"},
        "stratum",
    )
    result = deepcopy(value)
    if named:
        name = result.get("name")
        if not isinstance(name, str) or not name or len(name) > 80 or name == "default":
            raise InvalidSamplingPolicy(
                "Strata require unique names (1–80 characters, not default)."
            )
    if "rate" in result and "target_n" in result:
        raise InvalidSamplingPolicy("Choose rate or target_n, not both.")
    if "target_n" not in result:
        rate = result.get("rate", 100)
        if (
            isinstance(rate, bool)
            or not isinstance(rate, (int, float))
            or not math.isfinite(rate)
            or not 0 <= rate <= 100
        ):
            raise InvalidSamplingPolicy(
                "rate must be a finite percentage from 0 to 100."
            )
        result["rate"] = rate
    for key in ("target_n", "min_n", "max_n", "rare_below"):
        if key in result:
            result[key] = _integer(result[key], key)
    if result.get("min_n", 0) > result.get("max_n", 1_000_000):
        raise InvalidSamplingPolicy("min_n cannot exceed max_n.")
    if live and any(
        key in result for key in ("target_n", "min_n", "max_n", "rare_below")
    ):
        raise InvalidSamplingPolicy(
            "Quotas, floors, caps and rare-case census require a closed window."
        )
    filters = result.get("filters", [])
    if not isinstance(filters, list) or len(filters) > 20:
        raise InvalidSamplingPolicy(
            "A stratum supports at most 20 attribute predicates."
        )
    clean = []
    for predicate in filters:
        _keys(predicate, {"source", "key", "op", "values"}, "predicate")
        item = attribute({k: predicate[k] for k in ("source", "key") if k in predicate})
        op = predicate.get("op", "in")
        if op not in ("in", "not_in", "exists", "missing", "gt", "gte", "lt", "lte"):
            raise InvalidSamplingPolicy("Unsupported stratum predicate operator.")
        values = predicate.get("values", [])
        if (
            not isinstance(values, list)
            or len(values) > 100
            or any(
                not isinstance(v, (str, bool, int, float))
                or (isinstance(v, float) and not math.isfinite(v))
                or len(str(v)) > 500
                for v in values
            )
        ):
            raise InvalidSamplingPolicy(
                "Predicate values must be bounded scalar lists."
            )
        if op not in ("exists", "missing") and not values:
            raise InvalidSamplingPolicy("This predicate requires values.")
        if op in ("gt", "gte", "lt", "lte") and (
            len(values) != 1 or type(values[0]) not in (int, float)
        ):
            raise InvalidSamplingPolicy("Numeric predicates require one numeric value.")
        clean.append({**item, "op": op, "values": values})
    result["filters"] = clean
    return result


def parse_time(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            raise ValueError
        return parsed.astimezone(UTC)
    except (TypeError, ValueError, AttributeError) as exc:
        raise InvalidSamplingPolicy(
            "Window bounds must be timezone-aware ISO timestamps."
        ) from exc


def validate_policy(
    value: dict | None,
    *,
    row_type: str = "spans",
    now: datetime | None = None,
    contact_attribute: str = "",
) -> dict | None:
    if value is None:
        return None
    _keys(
        value,
        {
            "version",
            "sampling_unit",
            "unit_attribute",
            "missing_id",
            "mode",
            "window",
            "strata",
            "group_by",
            "default",
        },
        "policy",
    )
    if len(json.dumps(value, default=str).encode("utf-8")) > 65536:
        raise InvalidSamplingPolicy("Sampling policies must not exceed 64 KiB.")
    policy = deepcopy(value)
    if policy.get("version") != 1:
        raise InvalidSamplingPolicy("sampling policy version must be 1.")
    unit = policy.get("sampling_unit")
    if unit not in UNITS or row_type == "voiceCalls":
        raise InvalidSamplingPolicy("Unsupported sampling unit or evaluation row type.")
    if (
        row_type == "traces"
        and unit == "span"
        or row_type == "sessions"
        and unit in ("span", "trace")
    ):
        raise InvalidSamplingPolicy(
            "Sampling units cannot be finer than evaluation rows."
        )
    if unit == "attribute":
        policy["unit_attribute"] = attribute(
            policy.get("unit_attribute") or {"key": contact_attribute}
        )
    elif "unit_attribute" in policy:
        raise InvalidSamplingPolicy(
            "unit_attribute is only valid for attribute sampling."
        )
    policy["missing_id"] = policy.get("missing_id", "exclude")
    if policy["missing_id"] not in ("exclude", "row"):
        raise InvalidSamplingPolicy(
            "missing_id must be exclude or row (independent row fallback)."
        )
    mode = policy.get("mode", "closed")
    if mode not in ("closed", "live"):
        raise InvalidSamplingPolicy("mode must be closed or live.")
    policy["mode"] = mode
    window = policy.get("window")
    _keys(window, {"start", "end"}, "window")
    start, end = parse_time(window.get("start")), parse_time(window.get("end"))
    if not timedelta(0) < end - start <= MAX_WINDOW:
        raise InvalidSamplingPolicy(
            "Use a positive event-time window of at most 31 days."
        )
    if mode == "closed" and end > (now or datetime.now(UTC)):
        raise InvalidSamplingPolicy(
            "Exact quotas require a closed window ending in the past."
        )
    policy["window"] = {"start": start.isoformat(), "end": end.isoformat()}
    strata = policy.get("strata", [])
    if not isinstance(strata, list) or len(strata) > MAX_STRATA:
        raise InvalidSamplingPolicy(f"Use at most {MAX_STRATA} ordered strata.")
    policy["strata"] = [_rule(rule, live=mode == "live", named=True) for rule in strata]
    if len({rule["name"] for rule in policy["strata"]}) != len(strata):
        raise InvalidSamplingPolicy("Stratum names must be unique.")
    group_by = policy.get("group_by", [])
    if not isinstance(group_by, list) or len(group_by) > 4:
        raise InvalidSamplingPolicy("Use at most four group-by attributes.")
    policy["group_by"] = [attribute(item) for item in group_by]
    policy["default"] = _rule(
        policy.get("default", {"rate": 100}), live=mode == "live", named=False
    )
    if policy["default"]["filters"] or "name" in policy["default"]:
        raise InvalidSamplingPolicy(
            "The default stratum cannot have filters or a name."
        )
    return policy


def policy_version(
    policy: dict, *, project_id: str, row_type: str, filters: dict
) -> str:
    payload = {
        "policy": policy,
        "project": str(project_id),
        "row_type": row_type,
        "filters": filters,
    }
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode()
    ).hexdigest()


def unit_hash(seed: str, unit_id: str) -> str:
    return hashlib.sha256(f"{seed}:{unit_id}".encode()).hexdigest().upper()


def threshold(rate: float) -> str:
    numerator, denominator = Decimal(str(rate)).as_integer_ratio()
    return f"{(numerator * (1 << 256)) // (denominator * 100):065X}"


def rate_selects(seed: str, unit_id: str, rate: float) -> bool:
    return "0" + unit_hash(seed, unit_id) < threshold(rate)


def target_size(rule: dict, population: int) -> int:
    target = rule.get("target_n", math.ceil(population * rule.get("rate", 100) / 100))
    if population <= rule.get("rare_below", -1):
        target = population
    return min(
        population, rule.get("max_n", population), max(rule.get("min_n", 0), target)
    )
