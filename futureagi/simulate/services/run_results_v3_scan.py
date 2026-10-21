"""A run's per-call values for the unfiltered v3 calls page, read in one scan.

``run_calls_queryset`` derives every value in SQL, with correlated subqueries
per call. This reads the stored fields once and applies the same rules in
Python, including PostgreSQL's JSON text and NULL semantics, so its rows equal
``_call_values`` over that queryset.
"""

from __future__ import annotations

import json
import math
import re
import uuid
from collections.abc import Iterable
from decimal import Decimal
from fractions import Fraction
from typing import Any

from django.db import connections, router
from django.db.models import TextField
from django.db.models.fields.json import KeyTextTransform
from django.db.models.functions import Cast

from simulate.models import CallExecution, TestExecution
from simulate.models.hosted_harness import HostedHarnessJob
from simulate.models.scenarios import Scenarios
from simulate.services.harness_scenarios import AuthoredPick, authored_candidates
from simulate.services.run_results_v3 import _row_dimensions
from simulate.services.run_results_v3_expressions import (
    NUMERIC_JSON_PATTERN,
    NUMERIC_TEXT_PATTERN,
)
from simulate.services.run_results_v3_queries import UNGROUPED
from simulate.services.run_results_v3_scoring import (
    ERRORED_CALL_STATUSES,
    ERRORED_EVAL_STATUSES,
    HARNESS_ERROR,
    HARNESS_FAILED,
    HARNESS_INCONCLUSIVE,
    HARNESS_PASSED,
    IN_PROGRESS_CALL_STATUSES,
    NATIVE_FAILED,
    NATIVE_PASSED,
    QUEUED_CALL_STATUSES,
    UNMEASURED_EVAL_STATUSES,
    EvalScoringSpec,
    LiveEvals,
    live_eval_specs,
    score_tokens,
)
from simulate.services.scoring_status import SCORING_ROW_FIELDS, scoring_values

# The orderings the scan reproduces, as ``apply_run_call_query`` writes them.
ORDERINGS = {
    "-started_at": "ce.started_at DESC, ce.id ASC",
    "started_at": "ce.started_at ASC, ce.id ASC",
}


class _Number(str):
    """A JSON number, kept as the text PostgreSQL printed."""


# A JSON path that does not exist: SQL NULL, unlike JSON ``null``.
MISSING: Any = object()
Truth = bool | None


def _loads(text: str) -> Any:
    return json.loads(text, parse_int=_Number, parse_float=_Number)


def _get(value: Any, *keys: str) -> Any:
    for key in keys:
        if not isinstance(value, dict) or key not in value:
            return MISSING
        value = value[key]
    return value


def _dump(value: Any) -> str:
    """jsonb's text output for a parsed value."""
    if isinstance(value, _Number):
        return str(value)
    if value is True:
        return "true"
    if value is False:
        return "false"
    if value is None:
        return "null"
    if isinstance(value, str):
        return json.dumps(value, ensure_ascii=False)
    if isinstance(value, list):
        return "[" + ", ".join(_dump(item) for item in value) + "]"
    return (
        "{"
        + ", ".join(
            f"{json.dumps(key, ensure_ascii=False)}: {_dump(item)}"
            for key, item in value.items()
        )
        + "}"
    )


def _text(value: Any) -> str | None:
    """``->>``: SQL NULL for a missing path or JSON null."""
    if value is MISSING or value is None:
        return None
    if isinstance(value, str):
        return str(value)
    return _dump(value)


def _type(value: Any) -> str | None:
    """``jsonb_typeof``."""
    if value is MISSING:
        return None
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, _Number):
        return "number"
    if isinstance(value, str):
        return "string"
    if isinstance(value, list):
        return "array"
    return "object"


def _is_string(value: Any) -> bool:
    return isinstance(value, str) and not isinstance(value, _Number)


def _has_key(value: Any, key: str) -> Truth:
    """jsonb ``?``."""
    if value is MISSING:
        return None
    if isinstance(value, dict):
        return key in value
    if isinstance(value, list):
        return any(_is_string(item) and item == key for item in value)
    return _is_string(value) and value == key


def _and(*values: Truth) -> Truth:
    if any(value is False for value in values):
        return False
    return None if any(value is None for value in values) else True


def _or(*values: Truth) -> Truth:
    if any(value is True for value in values):
        return True
    return None if any(value is None for value in values) else False


def _not(value: Truth) -> Truth:
    return None if value is None else not value


def _equals(left: str | None, right: str) -> Truth:
    return None if left is None else left == right


def _normalized(text: str | None) -> str | None:
    """``lower(btrim(...))``."""
    # SQL lower() follows the database locale; this matches it for ASCII only.
    return None if text is None else text.strip(" ").lower()


def _not_empty(text: str | None) -> str | None:
    return None if text == "" else text


_STATUS_UNMEASURED = frozenset(UNMEASURED_EVAL_STATUSES)
_STATUS_ERRORED = frozenset(ERRORED_EVAL_STATUSES)


def _status(evals: Any, eval_id: str) -> str:
    return _normalized(_not_empty(_text(_get(evals, eval_id, "status"))) or "")


def _measured(evals: Any, eval_id: str) -> Truth:
    """``_eval_measured_q``."""
    return _and(
        _has_key(evals, eval_id), _status(evals, eval_id) not in _STATUS_UNMEASURED
    )


def _errored(evals: Any, eval_id: str) -> Truth:
    """``_eval_errored_q``."""
    return _and(_has_key(evals, eval_id), _status(evals, eval_id) in _STATUS_ERRORED)


_NUMERIC_TEXT = re.compile(NUMERIC_TEXT_PATTERN)
_JSON_NUMBER_TEXT = re.compile(NUMERIC_JSON_PATTERN)
# ``[[:space:]]`` under prod's en_US.UTF8 (PG 16); U+180E varies by glibc version.
_SPACE = "[ \t\n\r\f\v\u1680\u180e\u2000-\u2006\u2008-\u200a\u2028\u2029\u205f\u3000]*"


def _numeric_score(normalized: str | None, pass_fail: bool) -> float | None:
    """``NormalizedEvalNumber``."""
    if normalized is None or not _NUMERIC_TEXT.fullmatch(normalized):
        return None
    number = float(normalized)
    if pass_fail:
        return 1.0 if number > 0.0 else 0.0
    if number > 1.0:
        number /= 100.0
    return min(max(number, 0.0), 1.0)


def _safe_float(text: str | None) -> float | None:
    """``_safe_json_float``."""
    return (
        float(text) if text is not None and _JSON_NUMBER_TEXT.fullmatch(text) else None
    )


def _strings(value: Any, depth: int = 3) -> Iterable[Any]:
    """The items a lax ``$[*] ? (@ like_regex ...)`` tests.

    The accessor, the filter and the regex each unwrap one array level.
    """
    if isinstance(value, list) and depth:
        for item in value:
            yield from _strings(item, depth - 1)
    else:
        yield value


def _matches_any(value: Any, pattern: re.Pattern[str]) -> Truth:
    """``jsonb_path_exists(value, '$[*] ? (@ like_regex ...)')`` in lax mode."""
    if value is MISSING:
        return None
    return any(
        _is_string(item) and pattern.fullmatch(item) is not None
        for item in _strings(value)
    )


_ZERO, _ONE = Decimal("0.0"), Decimal("1.0")
_MIN_SIG_DIGITS, _MAX_DISPLAY_SCALE = 16, 1000


def _numeric_literal(value: float) -> Decimal:
    """A Python float as the numeric literal PostgreSQL receives for it."""
    return Decimal(repr(value))


def _scale(value: Decimal) -> int:
    return max(0, -int(value.as_tuple().exponent))


def _leading_group(value: Fraction) -> tuple[int, int]:
    """Weight and value of the first non-zero base-10000 digit of a ``numeric``."""
    value = abs(value)
    if not value:
        return 0, 0
    weight = 0
    while value >= 10000:
        value /= 10000
        weight += 1
    while value < 1:
        value *= 10000
        weight -= 1
    return weight, int(value)


def _numeric_divide(dividend: Decimal, divisor: Decimal) -> Decimal:
    """``numeric / numeric`` at the scale PostgreSQL's ``select_div_scale`` picks."""
    # Mirrors select_div_scale in PostgreSQL's numeric.c, verified against 16.
    # TODO: drop if call scores are ever stored instead of computed.
    exact_dividend, exact_divisor = Fraction(dividend), Fraction(divisor)
    weight1, digit1 = _leading_group(exact_dividend)
    weight2, digit2 = _leading_group(exact_divisor)
    quotient_weight = weight1 - weight2 - (1 if digit1 <= digit2 else 0)
    scale = max(
        _MIN_SIG_DIGITS - quotient_weight * 4, _scale(dividend), _scale(divisor), 0
    )
    scale = min(scale, _MAX_DISPLAY_SCALE)
    scaled = exact_dividend / exact_divisor * 10**scale
    # Half away from zero, as numeric rounds.
    rounded = math.floor(abs(scaled) + Fraction(1, 2))
    return Decimal(-rounded if scaled < 0 else rounded).scaleb(-scale)


class _EvalScore:
    """``_expanded_eval_score`` for one evaluation."""

    def __init__(self, eval_id: str, spec: EvalScoringSpec | None):
        self.eval_id = eval_id
        self.spec = spec
        self.pass_fail = spec is not None and spec.output_type == "pass_fail"
        self.choice_scores = (
            spec.choice_scores
            if spec is not None and spec.output_type == "deterministic"
            else {}
        )
        positive, negative = score_tokens(spec)
        self.positive, self.negative = frozenset(positive), frozenset(negative)
        self.choice_patterns = {
            label: re.compile(_SPACE + re.escape(label) + _SPACE, re.IGNORECASE)
            for label in self.choice_scores
        }

    def __call__(self, evals: Any) -> float | None:
        if _not(_measured(evals, self.eval_id)) is True:
            return None
        output = _get(evals, self.eval_id, "output")
        raw_text = next(
            (
                text
                for key in ("score", "result", "output", "choice", "value")
                if (text := _text(_get(output, key))) is not None
            ),
            _text(output),
        )
        normalized = _normalized(raw_text)
        numeric = _numeric_score(normalized, self.pass_fail)
        if self.spec is None and _type(output) == "object":
            return numeric if _type(_get(output, "score")) == "number" else None
        if self.choice_scores:
            output_text = _normalized(_not_empty(_text(output)))
            choice_text = _normalized(_not_empty(_text(_get(output, "choice"))))
            no_choices = _not(_has_key(output, "choices"))
            for label, score in self.choice_scores.items():
                exact = _or(
                    _equals(output_text, label),
                    _and(_equals(choice_text, label), no_choices),
                )
                if exact is True:
                    return min(max(float(score), 0.0), 1.0)
        if normalized in self.positive:
            raw = 1.0
        elif normalized in self.negative:
            raw = 0.0
        else:
            raw = numeric
        if not self.choice_scores:
            return raw
        # The weights reach SQL as numeric literals, so this mean is numeric.
        weighted, count = _ZERO, _ZERO
        choices = _get(output, "choices")
        for label, weight in self.choice_scores.items():
            pattern = self.choice_patterns[label]
            if _or(_matches_any(output, pattern), _matches_any(choices, pattern)):
                weighted += _numeric_literal(weight)
                count += _ONE
        if count > _ZERO:
            return float(min(max(_numeric_divide(weighted, count), _ZERO), _ONE))
        return raw


Verdict = tuple[float | None, Truth, Truth]


class _ConfiguredVerdict:
    """``_configured_eval_verdict``: (score, passed, failed)."""

    def __init__(self, eval_id: str, spec: EvalScoringSpec):
        self.eval_id = eval_id
        self.spec = spec
        self.raw_score = (
            _EvalScore(eval_id, spec) if spec.threshold is not None else None
        )

    def __call__(self, evals: Any) -> Verdict:
        eval_id, spec = self.eval_id, self.spec
        output = _normalized(_not_empty(_text(_get(evals, eval_id, "output"))))
        failure = _get(evals, eval_id, "output", "failure")
        final_pass = _or(
            _equals(output, "passed"), None if failure is MISSING else failure is False
        )
        final_fail = _or(
            _equals(output, "failed"), None if failure is MISSING else failure is True
        )
        if spec.output_type != "pass_fail":
            stored_type = _normalized(
                _not_empty(_text(_get(evals, eval_id, "output_type")))
            )
            stored_verdict = (
                None
                if stored_type is None
                else stored_type in {"pass/fail", "pass_fail"}
            )
            final_pass = _and(final_pass, stored_verdict)
            final_fail = _and(final_fail, stored_verdict)
        measured = _measured(evals, eval_id)
        raw = self.raw_score(evals) if self.raw_score is not None else None
        if _not(measured) is True:
            score = None
        elif final_pass is True:
            score = 1.0
        elif final_fail is True:
            score = 0.0
        elif spec.reverse_output:
            score = None if raw is None else 1.0 - raw
        else:
            score = raw
        if spec.threshold is None:
            return score, _and(measured, final_pass), _and(measured, final_fail)
        if raw is None:
            raw_pass = raw_fail = None
        elif spec.output_type == "pass_fail":
            raw_pass, raw_fail = raw > 0.0, raw <= 0.0
        else:
            raw_pass, raw_fail = raw >= spec.threshold, raw < spec.threshold
        if final_pass is True:
            passed: Truth = True
        elif final_fail is True:
            passed = False
        else:
            passed = raw_fail if spec.reverse_output else raw_pass
        if final_fail is True:
            failed: Truth = True
        elif final_pass is True:
            failed = False
        else:
            failed = raw_pass if spec.reverse_output else raw_fail
        return score, _and(measured, passed), _and(measured, failed)


_NATIVE_PASSED = frozenset(NATIVE_PASSED)
_NATIVE_FAILED = frozenset(NATIVE_FAILED)


def _native_verdicts(entry: Any) -> tuple[bool, bool, bool]:
    """``_NativeHarnessVerdict`` for one unconfigured entry: (failed, errored, passed)."""
    if not isinstance(entry, dict) or _text(entry.get("source", MISSING)) != "harness":
        return False, False, False
    # lower() as in _normalized: equal to SQL's for ASCII only.
    status = (_text(entry.get("status", MISSING)) or "").strip(" ").lower()
    if status in _STATUS_ERRORED:
        return False, True, False
    output = entry.get("output", MISSING)
    if status in _STATUS_UNMEASURED or _type(output) not in {"string", "boolean"}:
        return False, False, False
    token = _normalized(_text(output))
    return token in _NATIVE_FAILED, False, token in _NATIVE_PASSED


_HARNESS_ERROR = frozenset(HARNESS_ERROR)
_HARNESS_FAILED = frozenset(HARNESS_FAILED)
_HARNESS_INCONCLUSIVE = frozenset(HARNESS_INCONCLUSIVE)
_HARNESS_PASSED = frozenset(HARNESS_PASSED)


def _outcome(
    status: str,
    harness_status: str | None,
    failed: Truth,
    errored: Truth,
    passed: Truth,
) -> str:
    """The ``result_outcome`` CASE, in its order."""
    if status in QUEUED_CALL_STATUSES:
        return "queued"
    if status in IN_PROGRESS_CALL_STATUSES:
        return "in_progress"
    if harness_status in _HARNESS_ERROR or status in ERRORED_CALL_STATUSES:
        return "error"
    if status != "completed":
        return "inconclusive"
    if harness_status in _HARNESS_FAILED or failed is True:
        return "failed"
    if errored is True or harness_status in _HARNESS_INCONCLUSIVE:
        return "inconclusive"
    if harness_status in _HARNESS_PASSED or passed is True:
        return "passed"
    return "inconclusive"


_ENTRY_KEYS = ("status", "output", "output_type", "source")
# Entry text a memo may hold before it starts over; it only saves re-judging.
_MEMO_TEXT_LIMIT = 16 * 1024 * 1024


class _Memo(dict[Any, Any]):
    """A memo keyed by entry text that empties once it holds too much text."""

    def __init__(self) -> None:
        super().__init__()
        self.text_size = 0

    def remember(self, key: Any, text: str | None, value: Any) -> Any:
        size = len(text) if text is not None else 0
        if self.text_size + size > _MEMO_TEXT_LIMIT:
            self.clear()
            self.text_size = 0
        self[key] = value
        self.text_size += size
        return value


class _Judge:
    """Scores and verdicts for a run's calls, memoised per stored entry.

    Calls of a run repeat the same few eval entries, so each distinct entry is
    judged once.
    """

    def __init__(self, specs: dict[str, EvalScoringSpec], column_ids: list[str]):
        self.verdicts = {
            eval_id: _ConfiguredVerdict(eval_id, spec)
            for eval_id, spec in specs.items()
        }
        self.column_ids = column_ids
        self.scores = {
            eval_id: _EvalScore(eval_id, None)
            for eval_id in column_ids
            if eval_id not in specs
        }
        self._parsed = _Memo()
        self._verdicts = _Memo()
        self._scores = _Memo()
        self._natives = _Memo()

    def _entry(self, text: str) -> Any:
        """One entry from its ``[status, output, output_type, source]`` text."""
        if (entry := self._parsed.get(text, MISSING)) is MISSING:
            values = _loads(text)
            # Absent keys read as JSON null here; no rule below tells them apart.
            entry = self._parsed.remember(
                text, text, dict(zip(_ENTRY_KEYS, values, strict=True))
            )
        return entry

    def _single(self, eval_id: str, text: str | None) -> dict[str, Any]:
        return {} if text is None else {eval_id: self._entry(text)}

    def _verdict(self, eval_id: str, text: str | None) -> tuple[Verdict, Truth]:
        key = (eval_id, text)
        if (judged := self._verdicts.get(key)) is None:
            evals = self._single(eval_id, text)
            judged = self._verdicts.remember(
                key, text, (self.verdicts[eval_id](evals), _errored(evals, eval_id))
            )
        return judged

    def _score(self, eval_id: str, text: str | None) -> float | None:
        key = (eval_id, text)
        if key not in self._scores:
            return self._scores.remember(
                key, text, self.scores[eval_id](self._single(eval_id, text))
            )
        return self._scores[key]

    def _native(self, text: str) -> tuple[bool, bool, bool]:
        if (judged := self._natives.get(text)) is None:
            judged = self._natives.remember(
                text, text, _native_verdicts(self._entry(text))
            )
        return judged

    def judge_entries(
        self, entries: dict[str, str]
    ) -> tuple[dict[str, float | None], Truth, Truth, Truth]:
        """Judge an object of entries: (scores, failed, errored, passed)."""
        judged = {
            eval_id: self._verdict(eval_id, entries.get(eval_id))
            for eval_id in self.verdicts
        }
        natives = [
            self._native(text)
            for key, text in entries.items()
            if key not in self.verdicts
        ]
        scores = {
            eval_id: (
                judged[eval_id][0][0]
                if eval_id in judged
                else self._score(eval_id, entries.get(eval_id))
            )
            for eval_id in self.column_ids
        }
        return (
            scores,
            _or(
                *(verdict[2] for verdict, _ in judged.values()),
                any(n[0] for n in natives),
            ),
            _or(
                *(errored for _, errored in judged.values()), any(n[1] for n in natives)
            ),
            _or(
                *(verdict[1] for verdict, _ in judged.values()),
                any(n[2] for n in natives),
            ),
        )

    def judge_value(
        self, evals: Any
    ) -> tuple[dict[str, float | None], Truth, Truth, Truth]:
        """Judge eval outputs that are not an object, or SQL NULL as ``MISSING``."""
        judged = {eval_id: verdict(evals) for eval_id, verdict in self.verdicts.items()}
        scores = {
            eval_id: (
                judged[eval_id][0] if eval_id in judged else self.scores[eval_id](evals)
            )
            for eval_id in self.column_ids
        }
        # Only an object holds native checks.
        return (
            scores,
            _or(*(verdict[2] for verdict in judged.values()), False),
            _or(*(_errored(evals, eval_id) for eval_id in self.verdicts), False),
            _or(*(verdict[1] for verdict in judged.values()), False),
        )


# Each eval entry is cut down to the keys the rules read, as one JSON array per
# entry, keyed and separated by control characters that JSON text escapes.
_ENTRIES_START, _ENTRY_SEPARATOR, _KEY_SEPARATOR = "\x1d", "\x1e", "\x1f"
_SCAN_BATCH_SIZE = 1000
_SCAN_SQL = """
SELECT ce.id, ce.status, ce.duration_seconds,
  ce.avg_agent_latency_ms::float8, ce.customer_cost_cents::float8,
  ce.avg_stop_time_after_interruption_ms, ce.ai_interruption_count, ce.row_id,
  CASE WHEN jsonb_typeof(ce.call_metadata -> 'harness_outcome_status') = 'string'
    THEN ce.call_metadata ->> 'harness_outcome_status' END,
  NULLIF(ce.call_metadata ->> 'harness_scenario_key', ''),
  NULLIF(ce.call_metadata ->> 'use_case', ''),
  NULLIF(ce.call_metadata ->> 'goal', ''),
  NULLIF(ce.call_metadata #>> '{{row_data,use_case}}', ''),
  NULLIF(ce.call_metadata #>> '{{row_data,goal}}', ''),
  ce.conversation_metrics_data ->> 'avg_latency_ms',
  ce.conversation_metrics_data ->> 'turn_count',
  ce.conversation_metrics_data ->> 'bot_message_count',
  ce.conversation_metrics_data ->> 'total_tokens',
  ce.conversation_metrics_data ->> 'csat_score',
  CASE WHEN jsonb_typeof(ce.eval_outputs) = 'object' THEN chr(29) || COALESCE((
    SELECT string_agg(
      to_json(e.key)::text || chr(31) || '['
      || COALESCE((e.value -> 'status')::text, 'null') || ','
      || COALESCE((e.value -> 'output')::text, 'null') || ','
      || COALESCE((e.value -> 'output_type')::text, 'null') || ','
      || COALESCE((e.value -> 'source')::text, 'null') || ']',
      chr(30))
    FROM jsonb_each(ce.eval_outputs) e), '')
  ELSE ce.eval_outputs::text END,
  NULLIF(s.metadata ->> 'use_case', ''), NULLIF(s.metadata ->> 'goal', ''), s.name
FROM (
  SELECT id, status, duration_seconds, avg_agent_latency_ms, customer_cost_cents,
    avg_stop_time_after_interruption_ms, ai_interruption_count, row_id,
    scenario_id, started_at, conversation_metrics_data,
    -- Decompress each large JSON column once per row, not once per reference.
    jsonb_path_query_first(call_metadata, '$') AS call_metadata,
    jsonb_path_query_first(eval_outputs, '$') AS eval_outputs
  FROM {calls_table}
  WHERE test_execution_id = %s AND NOT deleted{call_filter}
  -- Keeps the planner from inlining this subquery and undoing the single read.
  OFFSET 0
) ce
JOIN {scenarios_table} s ON s.id = ce.scenario_id
ORDER BY {ordering}
"""
_CALL_FILTER = " AND id = ANY(%s::uuid[])"
_CALLS_TABLE = CallExecution._meta.db_table
_SCENARIO_FIELDS = (
    "job_id",
    "scenario_key",
    "call_execution_id",
    "created_at",
    "use_case",
    "sub_goals_text",
    "accent",
    "age_group",
    "overlay",
    "task",
)


def _level(value: str | None) -> str:
    return UNGROUPED if value is None or value == "" else value


def _entries(text: str, keys: dict[str, str]) -> dict[str, str]:
    """An eval outputs object's entries by key, each as its entry text.

    ``keys`` memoises decoded keys, which repeat on every call of a run.
    """
    entries = {}
    if len(text) > 1:
        for part in text[1:].split(_ENTRY_SEPARATOR):
            key, _, entry = part.partition(_KEY_SEPARATOR)
            if (decoded := keys.get(key)) is None:
                decoded = keys[key] = json.loads(key)
            entries[decoded] = entry
    return entries


def scan_call_values(
    execution: TestExecution,
    columns: list[dict[str, str]],
    ordering: str = "-started_at",
    *,
    live_evals: LiveEvals | None = None,
    call_ids: list[str] | None = None,
    scoring: bool = True,
) -> list[dict[str, Any]]:
    """``_call_values`` over ``run_calls_queryset(execution)``, in ``ordering``.

    ``call_ids`` narrows the scan to those of the run's calls. ``scoring=False``
    leaves out the scoring state, for a caller that reads no scoring field.
    """
    if ordering not in ORDERINGS:
        raise ValueError(
            f"Unknown ordering {ordering!r}; expected one of {sorted(ORDERINGS)}"
        )
    if live_evals is None:
        live_evals = live_eval_specs(execution.run_test_id)
    live_evals.warn_invalid_thresholds()
    judge = _Judge(
        {eval_id: live.spec for eval_id, live in live_evals.items()},
        [str(column["id"]) for column in columns],
    )
    jobs = list(
        HostedHarnessJob.all_objects.filter(test_execution_id=execution.id).values_list(
            "id", "environment_id"
        )
    )
    own_jobs = {job_id for job_id, _ in jobs}
    scenario_jobs = own_jobs | {env_id for _, env_id in jobs if env_id}
    params: list[Any] = [str(execution.id)]
    if call_ids is not None:
        params.append([str(uuid.UUID(str(call_id))) for call_id in call_ids])
    with connections[router.db_for_read(CallExecution)].cursor() as cursor:
        cursor.execute(
            _SCAN_SQL.format(
                ordering=ORDERINGS[ordering],
                calls_table=_CALLS_TABLE,
                scenarios_table=Scenarios._meta.db_table,
                call_filter="" if call_ids is None else _CALL_FILTER,
            ),
            params,
        )
        rows = []
        keys: dict[str, str] = {}
        # Each row with what its authored scenario and goal still need; the
        # scenarios are read once every call's scenario key is known.
        unresolved = []
        # The driver holds the whole result; batches keep Python's copy small.
        while calls := cursor.fetchmany(_SCAN_BATCH_SIZE):
            for (
                call_id,
                status,
                duration,
                latency,
                cost,
                stop_latency,
                ai_interruptions,
                row_id,
                harness_status,
                scenario_key,
                *metadata_goals,
                metric_latency,
                metric_turns,
                metric_bot_messages,
                metric_tokens,
                metric_csat,
                eval_text,
                scenario_use_case,
                scenario_goal,
                scenario_name,
            ) in calls:
                scores, failed, errored, passed = (
                    judge.judge_entries(_entries(eval_text, keys))
                    if eval_text is not None and eval_text.startswith(_ENTRIES_START)
                    else judge.judge_value(
                        MISSING if eval_text is None else _loads(eval_text)
                    )
                )
                turns = _safe_float(metric_turns)
                csat = _safe_float(metric_csat)
                row = {
                    "id": str(call_id),
                    "duration_seconds": duration,
                    "result_latency_ms": (
                        latency if latency is not None else _safe_float(metric_latency)
                    ),
                    "result_turn_count": (
                        turns if turns is not None else _safe_float(metric_bot_messages)
                    ),
                    "result_tokens": _safe_float(metric_tokens),
                    "result_cost_cents": cost,
                    "result_csat": (
                        csat if csat is not None and 0.0 <= csat <= 10.0 else None
                    ),
                    "avg_stop_time_after_interruption_ms": stop_latency,
                    "ai_interruption_count": ai_interruptions,
                    # Set from the authored scenario below.
                    "result_goal": None,
                    "result_sub_goal": [],
                    "result_accent": UNGROUPED,
                    "result_age": UNGROUPED,
                    "result_attack": UNGROUPED,
                    "result_task": UNGROUPED,
                    "result_outcome": _outcome(
                        status, harness_status, failed, errored, passed
                    ),
                    "scores": scores,
                }
                unresolved.append(
                    (
                        row,
                        call_id,
                        scenario_key,
                        metadata_goals,
                        row_id,
                        (scenario_use_case, scenario_goal, scenario_name),
                    )
                )
                rows.append(row)

    scenarios = (
        authored_candidates(
            execution.id,
            # Every call of the run, as the SQL read model's subquery sees them.
            call_ids=CallExecution._base_manager.filter(
                test_execution_id=execution.id
            ).values("id"),
            keys={key for _, _, key, *_ in unresolved if key is not None},
        )
        .using(router.db_for_read(CallExecution))
        .annotate(
            sub_goals_text=Cast("sub_goals", TextField()),
            accent=KeyTextTransform("accent", "persona"),
            age_group=KeyTextTransform("age_group", "persona"),
            overlay=KeyTextTransform("overlay", "coverage"),
            task=KeyTextTransform("task", "coverage"),
        )
        .values_list(*_SCENARIO_FIELDS, named=True)
        if unresolved
        else []
    )
    authored = AuthoredPick(scenarios, own_jobs, scenario_jobs)

    # Rows still without a goal, with their dataset row and scenario fallbacks.
    pending_goals = []
    for row, call_id, scenario_key, metadata_goals, row_id, fallbacks in unresolved:
        scenario = authored.pick(call_id, scenario_key)
        if scenario is not None:
            row["result_sub_goal"] = (
                []
                if scenario.sub_goals_text is None
                else json.loads(scenario.sub_goals_text)
            )
            row["result_accent"] = _level(scenario.accent)
            row["result_age"] = _level(scenario.age_group)
            row["result_attack"] = _level(scenario.overlay)
            row["result_task"] = _level(scenario.task)
        # Twin: ``result_goal``'s Coalesce in run_results_v3_queries; change both.
        row["result_goal"] = next(
            (
                value
                for value in (
                    None if scenario is None else _not_empty(scenario.use_case),
                    *metadata_goals,
                )
                if value is not None
            ),
            None,
        )
        if row["result_goal"] is None:
            pending_goals.append((row, row_id, fallbacks))

    dimensions = _row_dimensions(
        [],
        {str(row_id) for _, row_id, _ in pending_goals if row_id},
        names=("use_case", "goal"),
    )
    for row, row_id, fallbacks in pending_goals:
        cells = dimensions.get(str(row_id), {})
        # ``dataset_goal`` takes the use_case cell even when its value is NULL.
        dataset_goal = cells["use_case"] if "use_case" in cells else cells.get("goal")
        row["result_goal"] = next(
            (value for value in (dataset_goal, *fallbacks) if value is not None),
            None,
        )
    if rows and scoring:
        # The scoring state comes from the projection ``_call_values`` reads,
        # so the two paths agree on it by construction.
        scoring = {
            str(values["id"]): values
            for values in scoring_values(
                CallExecution.all_objects.filter(id__in=[row["id"] for row in rows])
            )
        }
        for row in rows:
            values = scoring[row["id"]]
            row.update({field: values[field] for field in SCORING_ROW_FIELDS})
    return rows
