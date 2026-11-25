from types import SimpleNamespace
from unittest.mock import patch

import pytest

from simulate.services.harness_tool_source import cut_definition, with_tool_sources
from simulate.services.hosted_harness import HostedHarnessError

TOOLS_PY = '''"""Tools."""
from langchain.tools import tool


@tool
def find_hotels(city: str) -> list:
    """Find hotels."""
    return [city]


async def cancel_booking(booking_id):
    return booking_id
'''

ENTRY = {"tool": "find_hotels", "mode": "import", "module": "agent.tools"}


def _entry(name, **extra):
    return {**ENTRY, "tool": name, "callable": name, **extra}


@pytest.fixture
def stored_code():
    code = SimpleNamespace(identity="archive:org:one", subfolder="")
    with (
        patch(
            "simulate.services.harness_tool_source.agent_code.version_code",
            return_value=code,
        ),
        patch(
            "simulate.services.harness_tool_source.agent_code.read_texts",
            side_effect=lambda _code, paths: {
                path: TOOLS_PY for path in paths if path == "agent/tools.py"
            },
        ) as read,
        patch("simulate.services.harness_tool_source.cache") as cache,
    ):
        cache.get_many.return_value = {}
        yield read


def _job():
    return SimpleNamespace(
        id="job", agent_version_id="version", agent_version=SimpleNamespace()
    )


class TestCutDefinition:
    def test_a_decorated_function_starts_at_its_decorator(self):
        start, end, code = cut_definition(TOOLS_PY, "find_hotels")

        assert (start, end) == (5, 8)
        assert code.splitlines()[0] == "@tool"
        assert code.splitlines()[-1] == "    return [city]"

    def test_an_async_function_is_found(self):
        assert cut_definition(TOOLS_PY, "cancel_booking")[:2] == (11, 12)

    def test_a_method_follows_its_class(self):
        text = "class Orders:\n    def refund(self):\n        return 1\n\n\nclass Billing:\n    def refund(self):\n        return 2\n"

        start, end, code = cut_definition(text, "Billing.refund")

        assert (start, end) == (7, 8)
        assert code.strip().endswith("return 2")

    def test_an_unknown_name_or_broken_file_is_none(self):
        assert cut_definition(TOOLS_PY, "missing") is None
        assert cut_definition("def broken(:", "broken") is None


class TestWithToolSources:
    def test_each_tool_gets_the_code_its_module_and_callable_name(self, stored_code):
        filled = with_tool_sources(
            _job(), [_entry("find_hotels"), _entry("cancel_booking")]
        )

        assert [entry["source"]["path"] for entry in filled] == [
            "agent/tools.py",
            "agent/tools.py",
        ]
        assert filled[1]["source"]["code"].startswith("async def cancel_booking")
        stored_code.assert_called_once()

    def test_a_tool_that_already_has_code_keeps_it(self, stored_code):
        given = {"path": "x.py", "line_start": 1, "line_end": 1, "code": "pass"}

        filled = with_tool_sources(_job(), [_entry("find_hotels", source=given)])

        assert filled[0]["source"] == given
        stored_code.assert_not_called()

    def test_a_tool_not_in_the_files_is_left_without_code(self, stored_code):
        filled = with_tool_sources(_job(), [_entry("lookup", module="agent.other")])

        assert "source" not in filled[0]

    def test_unreadable_files_leave_the_entries_unchanged(self):
        entries = [_entry("find_hotels")]
        with patch(
            "simulate.services.harness_tool_source.agent_code.version_code",
            side_effect=HostedHarnessError("no_code", "none", status_code=404),
        ):
            assert with_tool_sources(_job(), entries) is entries
