DEFAULT_COST_TRACKING = {"enabled": True}

DEFAULT_CACHE = {
    "enabled": False,
    "default_ttl": 300,
    "max_entries": 10000,
}

_DURATION_UNITS = {"s": 1, "m": 60, "h": 3600, "d": 86400}


def _coerce_ttl_seconds(value):
    if isinstance(value, bool):
        return DEFAULT_CACHE["default_ttl"]
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    if isinstance(value, str):
        v = value.strip().lower()
        if v.isdigit():
            return int(v)
        if len(v) >= 2 and v[-1] in _DURATION_UNITS and v[:-1].isdigit():
            return int(v[:-1]) * _DURATION_UNITS[v[-1]]
    return DEFAULT_CACHE["default_ttl"]


def default_cost_tracking_config():
    return DEFAULT_COST_TRACKING.copy()


def normalize_cost_tracking_config(value):
    if not isinstance(value, dict):
        value = {}
    normalized = value.copy()
    normalized.setdefault("enabled", True)
    return normalized


def default_cache_config():
    return DEFAULT_CACHE.copy()


def normalize_cache_config(value):
    if not isinstance(value, dict):
        value = {}
    normalized = value.copy()
    for key, default in DEFAULT_CACHE.items():
        normalized.setdefault(key, default)
    normalized["default_ttl"] = _coerce_ttl_seconds(normalized.get("default_ttl"))
    return normalized
