import ast
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
SOURCE_ROOT = REPO_ROOT / "futureagi"

RAW_ERROR_KEYS = {"error", "detail"}
MESSAGE_KEY = "message"

PROTOCOL_COMPATIBILITY_ALLOWLIST = {
    Path("futureagi/mcp_server/views/oauth.py"): (
        "OAuth 2.0 token endpoints must return RFC-compatible error bodies."
    ),
}

RAW_ERROR_DEBT_BY_FILE = {
    Path("futureagi/model_hub/views/dataset_optimization.py"): 8,
    Path("futureagi/tracer/views/annotation.py"): 1,
    Path("futureagi/tracer/views/charts.py"): 1,
}


def _iter_source_files():
    for path in SOURCE_ROOT.rglob("*.py"):
        relative = path.relative_to(REPO_ROOT)
        if (
            "tests" in relative.parts
            or "migrations" in relative.parts
            or any(part.startswith(".") for part in relative.parts)
        ):
            continue
        yield path


def _call_name(call):
    if isinstance(call.func, ast.Name):
        return call.func.id
    if isinstance(call.func, ast.Attribute):
        return call.func.attr
    return ""


def _dict_arg(call):
    if call.args and isinstance(call.args[0], ast.Dict):
        return call.args[0]
    for keyword in call.keywords:
        if keyword.arg == "data" and isinstance(keyword.value, ast.Dict):
            return keyword.value
    return None


def _literal_keys(dictionary):
    keys = set()
    for key in dictionary.keys:
        if isinstance(key, ast.Constant) and isinstance(key.value, str):
            keys.add(key.value)
    return keys


def _status_code(call):
    for keyword in call.keywords:
        if keyword.arg != "status":
            continue
        value = keyword.value
        if isinstance(value, ast.Constant) and isinstance(value.value, int):
            return value.value
        if isinstance(value, ast.Attribute) and value.attr.startswith("HTTP_"):
            _, code, *_ = value.attr.split("_", 2)
            if code.isdigit():
                return int(code)
    return None


def _is_raw_error_response(call):
    if _call_name(call) not in {"Response", "JsonResponse"}:
        return False
    dictionary = _dict_arg(call)
    if dictionary is None:
        return False

    keys = _literal_keys(dictionary)
    if {"type", "code", "detail"}.issubset(keys):
        return False
    status_code = _status_code(call)
    if keys & RAW_ERROR_KEYS:
        return status_code is None or status_code >= 400
    if MESSAGE_KEY in keys and status_code is not None and status_code >= 400:
        return True
    return False


def _raw_error_counts_by_file():
    counts = {}

    for path in _iter_source_files():
        relative = path.relative_to(REPO_ROOT)
        if relative in PROTOCOL_COMPATIBILITY_ALLOWLIST:
            continue
        tree = ast.parse(path.read_text(), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or not _is_raw_error_response(node):
                continue
            counts[relative] = counts.get(relative, 0) + 1
    return counts


def test_product_api_errors_use_common_envelope_or_tracked_burn_down_debt():
    current_counts = _raw_error_counts_by_file()

    new_debt = sorted(set(current_counts) - set(RAW_ERROR_DEBT_BY_FILE))
    increased_debt = {
        str(path): {
            "baseline": RAW_ERROR_DEBT_BY_FILE[path],
            "current": current_counts[path],
        }
        for path in current_counts
        if path in RAW_ERROR_DEBT_BY_FILE
        and current_counts[path] > RAW_ERROR_DEBT_BY_FILE[path]
    }

    assert new_debt == []
    assert increased_debt == {}
