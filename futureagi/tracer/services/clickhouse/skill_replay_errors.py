"""The size refusal shared by paged and embedded skill replay readers."""

import re

from clickhouse_connect.driver.exceptions import DatabaseError

from tracer.services.clickhouse.read_budget import _clickhouse_error_code

SKILL_REPLAY_OVERFLOW_MESSAGE = "Skill load replay key budget exceeded"

# The marker must be the diagnostic, not a quoted literal in a failing
# statement echoed by the server alongside an unrelated throwIf error.
_OVERFLOW_DIAGNOSTIC = re.compile(
    rf"(?:^|[.:\n]\s*){re.escape(SKILL_REPLAY_OVERFLOW_MESSAGE)}(?=$|[:.(\s])"
)


class SkillReplayBudgetExceeded(RuntimeError):
    """An exact skill read exceeded its replay budget; no prefix is usable."""


def is_skill_replay_overflow(exc: Exception) -> bool:
    """Recognize the typed refusal or our throwIf, not unrelated CH errors."""
    if isinstance(exc, SkillReplayBudgetExceeded):
        return True
    message = str(exc)
    if not _OVERFLOW_DIAGNOSTIC.search(message):
        return False
    code = _clickhouse_error_code(exc)
    if code is not None:
        return code == 395
    return (
        isinstance(exc, DatabaseError)
        and "(FUNCTION_THROW_IF_VALUE_IS_NON_ZERO)" in message
    )
