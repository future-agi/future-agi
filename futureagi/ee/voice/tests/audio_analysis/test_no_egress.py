"""AC13 static guard: analysis sources contain no HTTP clients or URL literals."""

import ast
from pathlib import Path

import pytest

ANALYSIS_ROOT = Path(__file__).resolve().parents[2] / "services" / "audio_analysis"
SOURCES = sorted(ANALYSIS_ROOT.rglob("*.py"))
FORBIDDEN_CLIENTS = {"urllib", "requests", "httpx", "aiohttp"}


def test_analysis_sources_exist():
    assert SOURCES, f"No analysis sources found under {ANALYSIS_ROOT}"


@pytest.mark.parametrize(
    "path", SOURCES, ids=lambda path: str(path.relative_to(ANALYSIS_ROOT))
)
def test_no_http_imports_or_url_literals(path):
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    for node in ast.walk(tree):
        modules = []
        if isinstance(node, ast.Import):
            modules = [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom):
            modules = (
                [node.module] if node.module else [alias.name for alias in node.names]
            )
        for module in modules:
            assert module.split(".")[0] not in FORBIDDEN_CLIENTS, (
                f"{path}:{node.lineno}: forbidden HTTP import {module}"
            )
        if isinstance(node, ast.Constant) and isinstance(node.value, (str, bytes)):
            prefixes = (
                (b"http://", b"https://")
                if isinstance(node.value, bytes)
                else ("http://", "https://")
            )
            assert not any(prefix in node.value for prefix in prefixes), (
                f"{path}:{node.lineno}: forbidden HTTP URL literal"
            )
