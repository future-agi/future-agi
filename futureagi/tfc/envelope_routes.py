"""Exact method and route grammar for delegated gateway requests."""

import re


def valid_delegated_route(scope, method, route):
    if scope != "batch":
        return method == "POST" and route == "/-/delegated/v1/chat/completions"
    if route == "/-/batches":
        return method in {"GET", "POST"}
    if method == "GET":
        return re.fullmatch(r"/-/batches/batch-[0-9a-f]{12}", route) is not None
    if method == "POST":
        return re.fullmatch(r"/-/batches/batch-[0-9a-f]{12}/cancel", route) is not None
    return False
