"""Each tool's code, cut from the agent version's stored files by the module and function its entrypoint names."""

from __future__ import annotations

import ast
import tarfile
from typing import Any, TypedDict

import structlog
from django.core.cache import cache

from simulate.models import HostedHarnessJob
from simulate.services import agent_code
from simulate.services.hosted_harness import HostedHarnessError

logger = structlog.get_logger(__name__)

CACHE_SECONDS = 24 * 3600
_MISSING = "missing"
_DEFINITIONS = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)


class ToolSource(TypedDict):
    path: str
    line_start: int
    line_end: int
    code: str


def _named(body: list[ast.stmt], name: str) -> ast.stmt | None:
    return next(
        (node for node in body if isinstance(node, _DEFINITIONS) and node.name == name),
        None,
    )


def _definition(tree: ast.Module, dotted: str) -> ast.stmt | None:
    parts = dotted.split(".")
    node: Any = tree
    for part in parts:
        node = _named(node.body, part)
        if node is None:
            break
    if node is not None:
        return node
    if len(parts) > 1:
        return None
    return next(
        (
            one
            for one in ast.walk(tree)
            if isinstance(one, _DEFINITIONS) and one.name == dotted
        ),
        None,
    )


def cut_definition(text: str, dotted: str) -> tuple[int, int, str] | None:
    """The lines of the function or class ``dotted`` names (``Class.method`` follows the
    class), its decorators included."""
    try:
        tree = ast.parse(text)
    except (SyntaxError, ValueError):
        return None
    node = _definition(tree, dotted)
    if node is None:
        return None
    start = min([node.lineno, *(one.lineno for one in node.decorator_list)])
    end = node.end_lineno or node.lineno
    return start, end, "\n".join(text.splitlines()[start - 1 : end])


def _candidates(module: str, subfolder: str) -> list[str]:
    base = module.replace(".", "/")
    names = [f"{base}.py", f"{base}/__init__.py"]
    within = [f"{subfolder}/{name}" for name in names] if subfolder else []
    return list(dict.fromkeys([*within, *names]))


def _wanted(entry: Any) -> tuple[str, str] | None:
    if not isinstance(entry, dict) or entry.get("mode") == "unreachable":
        return None
    source = entry.get("source")
    if isinstance(source, dict) and source.get("code"):
        return None
    module = str(entry.get("module") or "")
    name = str(entry.get("callable") or "")
    return (module, name) if module and name else None


def _key(version_id: Any, module: str, name: str) -> str:
    return f"harness_tool_source:{version_id}:{module}:{name}"


def _find(
    code: agent_code.VersionCode, wanted: set[tuple[str, str]]
) -> dict[tuple[str, str], ToolSource | None]:
    paths = {
        (module, name): _candidates(module, code.subfolder) for module, name in wanted
    }
    texts = agent_code.read_texts(
        code, sorted({path for found in paths.values() for path in found})
    )
    sources: dict[tuple[str, str], ToolSource | None] = {}
    for (module, name), candidates in paths.items():
        sources[(module, name)] = None
        for path in candidates:
            cut = cut_definition(texts[path], name) if path in texts else None
            if cut is not None:
                start, end, text = cut
                sources[(module, name)] = {
                    "path": path,
                    "line_start": start,
                    "line_end": end,
                    "code": text,
                }
                break
    return sources


def with_tool_sources(job: HostedHarnessJob, entries: list[Any]) -> list[Any]:
    """The entrypoints, each without code given the code of the function it names; unchanged
    when the agent's files cannot be read."""
    version_id = job.agent_version_id
    wanted = {pair for pair in map(_wanted, entries) if pair}
    if version_id is None or not wanted:
        return entries
    keys = {pair: _key(version_id, *pair) for pair in wanted}
    cached = cache.get_many(list(keys.values()))
    sources = {pair: cached[key] for pair, key in keys.items() if key in cached}
    missing = wanted - set(sources)
    if missing:
        try:
            found = _find(agent_code.version_code(job.agent_version), missing)
        except (HostedHarnessError, tarfile.TarError, OSError) as exc:
            logger.warning(
                "harness_tool_source_unavailable",
                job=str(job.id),
                error=type(exc).__name__,
            )
            return entries
        cache.set_many(
            {keys[pair]: value or _MISSING for pair, value in found.items()},
            CACHE_SECONDS,
        )
        sources.update(found)
    filled: list[Any] = []
    for entry in entries:
        pair = _wanted(entry)
        source = sources.get(pair) if pair else None
        filled.append(
            {**entry, "source": source} if source and source != _MISSING else entry
        )
    return filled
