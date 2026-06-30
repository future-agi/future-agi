"""Unit tests for the unresolved-{{token}} warning in run_prompt (PR #329, issue #321).

Pre-fix: populate_placeholders did not warn when a {{token}} survived substitution.
Post-fix: `_warn_unresolved_placeholders` logs the structured structlog event
`populate_placeholders_unresolved_tokens` listing the surviving tokens, on both the
normal and the exception-fallback path.

These import the production helper directly and assert against the (patched) module
logger, so they exercise the real warning logic with no DB. Pre-fix the symbol does
not exist -> ImportError -> fails; post-fix -> passes.

Run:  cd futureagi && make test-unit   (or: pytest model_hub/tests/test_run_prompt_unresolved_placeholders.py)
"""

import pytest

pytestmark = pytest.mark.unit

from unittest.mock import patch

from model_hub.views.run_prompt import _warn_unresolved_placeholders


@patch("model_hub.views.run_prompt.logger")
def test_warns_on_unresolved_string_token(mock_logger):
    _warn_unresolved_placeholders(
        [{"role": "user", "content": "Hello {{missing_column}}, welcome"}]
    )
    mock_logger.warning.assert_called_once()
    args, kwargs = mock_logger.warning.call_args
    assert args[0] == "populate_placeholders_unresolved_tokens"
    assert kwargs["unresolved"] == ["{{missing_column}}"]
    assert kwargs["role"] == "user"


@patch("model_hub.views.run_prompt.logger")
def test_warns_on_unresolved_tokens_in_list_content(mock_logger):
    _warn_unresolved_placeholders(
        [{"role": "system", "content": [{"type": "text", "text": "x {{a}} and {{b}}"}]}]
    )
    mock_logger.warning.assert_called_once()
    _, kwargs = mock_logger.warning.call_args
    assert kwargs["unresolved"] == ["{{a}}", "{{b}}"]
    assert kwargs["role"] == "system"


@patch("model_hub.views.run_prompt.logger")
def test_no_warning_when_all_tokens_resolved(mock_logger):
    _warn_unresolved_placeholders(
        [{"role": "user", "content": "Fully resolved content, no tokens here"}]
    )
    mock_logger.warning.assert_not_called()
