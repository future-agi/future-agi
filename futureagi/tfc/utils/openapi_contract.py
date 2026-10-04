"""Contract hygiene helpers for the generated Management API OpenAPI document.

Standard library only: ``scripts/check_openapi_contract.py`` imports this module
without Django, so keep Django and drf-yasg out of here. The generator that uses
these rules lives in :mod:`tfc.utils.api_contracts`.
"""

from __future__ import annotations

import re
from collections import OrderedDict
from typing import Iterable, Mapping
from urllib.parse import urlsplit, urlunsplit

HTTP_METHODS = ("get", "put", "post", "delete", "options", "head", "patch", "trace")

_PATH_PARAMETER = re.compile(r"\{([^}]+)\}")
_UNSAFE_ID_CHARS = re.compile(r"[^A-Za-z0-9_.-]+")


def iter_operations(document: Mapping) -> Iterable[tuple[str, str, Mapping]]:
    """Yield ``(path, method, operation)`` for every operation in a Swagger 2.0
    or OpenAPI 3 ``paths`` object, in document order."""
    paths = document.get("paths") or {}
    for path, path_item in paths.items():
        if not isinstance(path_item, Mapping):
            continue
        for method in HTTP_METHODS:
            operation = path_item.get(method)
            if isinstance(operation, Mapping):
                yield path, method, operation


def find_duplicate_operation_ids(document: Mapping) -> OrderedDict[str, list[dict]]:
    """Every operationId used by more than one operation, with all its members.

    Unlike a strict validator, which stops at the first duplicate, this reports
    the complete inventory so a CI failure names every collision at once.
    """
    members: OrderedDict[str, list[dict]] = OrderedDict()
    for path, method, operation in iter_operations(document):
        operation_id = operation.get("operationId")
        if not operation_id:
            continue
        members.setdefault(operation_id, []).append({"method": method, "path": path})
    return OrderedDict(
        (operation_id, group) for operation_id, group in members.items() if len(group) > 1
    )


def format_duplicate_operation_ids(duplicates: Mapping[str, list[dict]]) -> str:
    lines = [f"{len(duplicates)} duplicate operationId group(s):"]
    for operation_id, group in duplicates.items():
        pairs = ", ".join(f"{m['method'].upper()} {m['path']}" for m in group)
        lines.append(f"  {operation_id}: {pairs}")
    return "\n".join(lines)


def path_parameters(path: str) -> list[str]:
    """Path parameter names in template order: ``/a/{x}/b/{y}/`` -> ``['x', 'y']``."""
    return _PATH_PARAMETER.findall(path)


def _named_segments(path: str) -> list[str]:
    return [
        segment
        for segment in path.strip("/").split("/")
        if segment and not _PATH_PARAMETER.fullmatch(segment)
    ]


def _slug(value: str) -> str:
    return _UNSAFE_ID_CHARS.sub("_", value).strip("_") or "alt"


def collision_rank(path: str, siblings: Iterable[str] = ()) -> tuple[int, int, int, str]:
    """Sort key deciding which member of an operationId collision keeps the
    historic ID.

    Fewest path parameters first, so the collection route wins over its detail
    route. Among routes with the same parameters, a spelling that omits the
    trailing slash loses: the router always mounts its route with one, and the
    slash-less path is an extra alias (``supported-models`` beside
    ``supported_models/``). Length, then lexicographic order, break whatever is
    left, so the result never depends on urlpatterns order.
    """
    missing_trailing_slash = not path.endswith("/")
    return (
        len(path_parameters(path)),
        1 if missing_trailing_slash else 0,
        len(path),
        path,
    )


def disambiguated_operation_id(operation_id: str, winner_path: str, path: str) -> str:
    """The deterministic replacement ID for a route that lost a collision.

    * A route with path parameters the winner lacks is named after them:
      ``accounts_appsmith_users_create`` on ``/accounts/appsmith/users/{user_id}/``
      becomes ``accounts_appsmith_users_create_by_user_id``.
    * Otherwise (two spellings of one route) the suffix is the slug of the path
      segments the winner does not have, so the same route always yields the same
      ID regardless of URL pattern order.
    """
    winner_params = set(path_parameters(winner_path))
    extra_params = [p for p in path_parameters(path) if p not in winner_params]
    if extra_params:
        return f"{operation_id}_by_{'_'.join(_slug(p) for p in extra_params)}"
    winner_segments = set(_named_segments(winner_path))
    extra_segments = [s for s in _named_segments(path) if s not in winner_segments]
    suffix = "_".join(_slug(s) for s in extra_segments) if extra_segments else "alt"
    return f"{operation_id}_{suffix}"


def plan_operation_id_renames(document: Mapping) -> OrderedDict[tuple[str, str], str]:
    """Map ``(path, method)`` to the operationId each colliding operation should
    carry so that every operationId in ``document`` is unique.

    Only collision losers appear in the result; every operationId that is unique
    already is left untouched. The plan is deterministic for a given set of
    routes and never depends on urlpatterns order.
    """
    taken = {
        operation.get("operationId")
        for _, _, operation in iter_operations(document)
        if operation.get("operationId")
    }
    renames: OrderedDict[tuple[str, str], str] = OrderedDict()
    for operation_id, group in find_duplicate_operation_ids(document).items():
        siblings = [member["path"] for member in group]
        ordered = sorted(group, key=lambda member: collision_rank(member["path"], siblings))
        winner = ordered[0]
        for loser in ordered[1:]:
            candidate = disambiguated_operation_id(
                operation_id, winner["path"], loser["path"]
            )
            unique = candidate
            counter = 2
            while unique in taken:
                unique = f"{candidate}_{counter}"
                counter += 1
            taken.add(unique)
            renames[(loser["path"], loser["method"])] = unique
    return renames


def normalize_public_base_url(value: str | None) -> str | None:
    """``scheme://host[:port]`` for an absolute http(s) URL, else ``None``.

    drf-yasg raises at schema-generation time (an HTTP 500 for every visitor of
    ``/docs/``) when ``DEFAULT_API_URL`` is not an absolute http(s) URL, so a
    misconfigured value must degrade to request-derived host detection instead
    of being passed through. Any path, query or fragment is dropped: Swagger 2.0
    carries only host and scheme here, and drf-yasg ignores the path anyway.
    """
    if not value:
        return None
    parts = urlsplit(value.strip())
    if parts.scheme not in ("http", "https") or not parts.netloc:
        return None
    return urlunsplit((parts.scheme, parts.netloc, "", "", ""))
