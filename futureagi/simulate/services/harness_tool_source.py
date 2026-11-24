"""Each tool's code, cut from the agent version's stored files by the module and function its entrypoint names."""

from __future__ import annotations

import ast
from typing import Any

import structlog
from django.core.cache import cache

from simulate.models import HostedHarnessJob
from simulate.services import agent_code
from simulate.services.hosted_harness import HostedHarnessError

logger = structlog.get_logger(__name__)

CACHE_SECONDS = 24 * 3600
_MISSING = "missing"


def cut_definition(text: str, name: str) -> tuple[int, int, str] | None:
    """The lines of the first function or class named ``name``, its decorators included."""
    try:
        tree = ast.parse(text)
    except (SyntaxError, ValueError):
        return None
    for node in ast.walk(tree):
        if (
            isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
            and node.name == name
        ):
            start = min([node.lineno, *(one.lineno for one in node.decorator_list)])
            end = node.end_lineno or node.lineno
            return start, end, "\n".join(text.splitlines()[start - 1 : end])
    return None


def _candidates(module: str, subfolder: str) -> list[str]:
    base = module.replace(".", "/")
    names = [f"{base}.py", f"{base}/__init__.py"]
    within = [f"{subfolder}/{name}" for name in names] if subfolder else []
    return list(dict.fromkeys([*within, *names]))


def _wanted(entry: Any) -> tuple[str, str] | None:
    if not isinstance(entry, dict):
        return None
    source = entry.get("source")
    if isinstance(source, dict) and source.get("code"):
        return None
    module = str(entry.get("module") or "")
    name = str(entry.get("callable") or "").rsplit(".", 1)[-1]
    return (module, name) if module and name else None


class _Finder:
    def __init__(self, job: HostedHarnessJob) -> None:
        self.version = job.agent_version
        self.code: agent_code.VersionCode | None = None
        self.index: dict[str, Any] | None = None
        self.texts: dict[str, str] = {}

    def _open(self) -> bool:
        if self.index is not None:
            return True
        if self.version is None:
            return False
        self.code = agent_code.version_code(self.version)
        self.index = agent_code.archive_index(self.code)
        return True

    def _text(self, path: str) -> str:
        if path not in self.texts:
            body = agent_code.read_file(self.version, path)
            self.texts[path] = str(body.get("content") or "")
        return self.texts[path]

    def find(self, module: str, name: str) -> dict[str, Any] | None:
        if not self._open():
            return None
        key = f"harness_tool_source:{self.code.identity}:{module}:{name}"
        cached = cache.get(key)
        if cached is not None:
            return None if cached == _MISSING else cached
        found = None
        for path in _candidates(module, self.code.subfolder):
            if path not in self.index:
                continue
            cut = cut_definition(self._text(path), name)
            if cut is not None:
                start, end, text = cut
                found = {
                    "path": path,
                    "line_start": start,
                    "line_end": end,
                    "code": text,
                }
                break
        cache.set(key, found or _MISSING, CACHE_SECONDS)
        return found


def with_tool_sources(job: HostedHarnessJob, entries: list[Any]) -> list[Any]:
    """The entrypoints, each without code given the code of the function it names; unchanged when the
    agent's files cannot be read."""
    if not any(_wanted(entry) for entry in entries):
        return entries
    finder = _Finder(job)
    filled: list[Any] = []
    try:
        for entry in entries:
            wanted = _wanted(entry)
            source = finder.find(*wanted) if wanted else None
            filled.append({**entry, "source": source} if source else entry)
    except HostedHarnessError as exc:
        logger.info("harness_tool_source_unavailable", job=str(job.id), code=exc.code)
        return entries
    return filled
