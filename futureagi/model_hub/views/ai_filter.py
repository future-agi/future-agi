"""
General-purpose AI Filter endpoint.

POST /model-hub/ai-filter/

Takes a natural language query + a filter schema (available fields, operators, values)
and returns structured filter JSON. All model calls go through the in-house
`agentic_eval.core.llm.llm.LLM` wrapper, which routes through the Agentcc
gateway with litellm fallback.

Three modes:
  - build_filters (default): one-shot. Caller passes schema with optional
    `choices` per field; LLM picks fields/operators/values constrained to
    the schema. Used by evals.
  - select_fields: returns just the relevant field ids for the query.
    Used as step 1 of frontend-orchestrated multi-step flows.
  - smart: agentic. Caller passes schema + project_id + source. Backend
    runs a Gemini tool-use loop where the LLM autonomously calls
    `get_field_values(field_id)` for the fields it needs to ground its
    answer, then submits the final filter via `submit_filter`. One HTTP
    round trip — LLM does the orchestration. Used by the trace filter.
"""

import json
from contextlib import ExitStack, contextmanager

import structlog
from django.conf import settings
from django.db import connection, transaction
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.views import APIView

from agentic_eval.core.utils.json_utils import strip_code_fence
from model_hub.serializers.ai_filter import (
    AIFilterRequestSerializer,
    AIFilterResponseSerializer,
)
from tfc.utils.api_contracts import validated_request
from tfc.utils.api_serializers import ApiTextErrorResponseSerializer
from tfc.utils.general_methods import GeneralMethods

logger = structlog.get_logger(__name__)

ERROR_RESPONSES = {
    400: ApiTextErrorResponseSerializer,
    422: ApiTextErrorResponseSerializer,
    503: ApiTextErrorResponseSerializer,
    500: ApiTextErrorResponseSerializer,
}

SMART_FILTER_REQUEST_WALL_MS = settings.SMART_FILTER_REQUEST_WALL_MS
SMART_FILTER_VALUE_READ_WALL_MS = settings.SMART_FILTER_VALUE_READ_WALL_MS
SMART_FILTER_VALUE_LIMIT = settings.SMART_FILTER_VALUE_LIMIT
SMART_FILTER_SEARCH_MAX_BYTES = settings.SMART_FILTER_SEARCH_MAX_BYTES
SMART_FILTER_PROJECT_SCOPE_LIMIT = settings.SMART_FILTER_PROJECT_SCOPE_LIMIT


class SmartFilterGroundingError(Exception):
    """Sanitized exact-grounding refusal returned at the HTTP boundary."""

    def __init__(self, *, status_code: int, code: str, public_message: str):
        super().__init__(public_message)
        self.status_code = status_code
        self.code = code
        self.public_message = public_message


class AIFilterUnavailableError(Exception):
    """Sanitized request-wall or external-completion failure."""

    status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    code = "ai_filter_unavailable"
    public_message = (
        "AI filtering is temporarily unavailable. Retry or add the filter manually."
    )


def _ai_filter_unavailable() -> AIFilterUnavailableError:
    return AIFilterUnavailableError(AIFilterUnavailableError.public_message)


def _remaining_request_ms(deadline) -> int:
    """Return the one request-owned wall remaining, or fail typed/closed."""

    from tracer.services.clickhouse.read_budget import ReadDeadlineExceeded

    try:
        return deadline.remaining_ms(SMART_FILTER_REQUEST_WALL_MS)
    except ReadDeadlineExceeded as exc:
        raise _ai_filter_unavailable() from exc


def _execute_ai_filter_query_with_deadline(
    deadline, execute, sql, params, many, context
):
    """Shrink every PostgreSQL statement to the shared HTTP request wall."""

    remaining_ms = _remaining_request_ms(deadline)
    context["cursor"].cursor.execute(
        "SELECT set_config('statement_timeout', %s, true)",
        (str(remaining_ms),),
    )
    result = execute(sql, params, many, context)
    _remaining_request_ms(deadline)
    return result


@contextmanager
def _bounded_ai_filter_postgres(deadline):
    """Bound smart-mode authorization reads without spanning the LLM call."""

    if connection.vendor != "postgresql":
        yield
        _remaining_request_ms(deadline)
        return

    transaction_started = False

    def execute_with_remaining_timeout(execute, sql, params, many, context):
        nonlocal transaction_started
        if not connection.in_atomic_block and not transaction_started:
            stack.enter_context(transaction.atomic())
            transaction_started = True
        return _execute_ai_filter_query_with_deadline(
            deadline, execute, sql, params, many, context
        )

    # Keep connection acquisition lazy: mocked and validation-only paths do not
    # open PostgreSQL, while the first real ORM statement starts the SET LOCAL
    # transaction before the application query executes.
    with ExitStack() as stack:
        stack.enter_context(connection.execute_wrapper(execute_with_remaining_timeout))
        yield
        _remaining_request_ms(deadline)


def _bounded_completion_content(llm, messages, *, deadline) -> str:
    """Run gateway/litellm completion inside the caller's single wall.

    The existing bounded tool-completion transport owns gateway retries and
    litellm fallback under ``timeout_ms``.  Supplying no tools gives the
    build/select modes the same deadline behavior without entering the legacy
    unbounded completion/final-fallback path.
    """

    try:
        response = llm._get_completion_with_tools(
            messages,
            [],
            timeout_ms=_remaining_request_ms(deadline),
        )
        _remaining_request_ms(deadline)
        choices = getattr(response, "choices", None) or []
        if not choices:
            raise ValueError("empty completion")
        message = getattr(choices[0], "message", None)
        content = getattr(message, "content", None)
        if not isinstance(content, str):
            raise ValueError("completion content is unavailable")
        return content.strip()
    except AIFilterUnavailableError:
        raise
    except Exception as exc:
        raise _ai_filter_unavailable() from exc


def _grounding_too_broad() -> SmartFilterGroundingError:
    return SmartFilterGroundingError(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        code="ai_filter_grounding_too_broad",
        public_message=(
            "AI value grounding needs a more specific value. Refine the query "
            "and retry."
        ),
    )


def _grounding_unavailable() -> SmartFilterGroundingError:
    return SmartFilterGroundingError(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        code="ai_filter_grounding_unavailable",
        public_message=(
            "AI value grounding is temporarily unavailable. Retry or add the "
            "filter manually."
        ),
    )


SYSTEM_PROMPT = """You are a filter assistant. Given a user's natural language query and a schema of available filter fields, return a JSON array of filter conditions.

Each condition is an object with:
- "field": the field identifier (from the schema)
- "operator": the operator to use (from the allowed operators for that field)
- "value": the value to filter by

CORE RULES:
- Only use fields and operators from the provided schema.
- If the user mentions multiple conditions, return multiple objects in the array.
- Return ONLY the JSON array, no explanation or markdown.

VALUE-GROUNDING RULES (most important):

When a field has a "choices" list, you MUST pick the value from that list. Use these rules to do it well:

1. **Exact synonym match.** Pick the value whose meaning best matches what the user said, case-insensitively. Examples:
   - user says "english", choices include "English" → use "English"
   - user says "OK", choices include "ok" → use "ok"

1a. **Label-aware matching.** Some fields have a `choice_labels` map alongside `choices`. This maps the canonical API value to a user-facing label — e.g. `choices: ["voice", "text"]` with `choice_labels: {"voice": "Voice", "text": "Chat"}` means `"text"` is displayed as `"Chat"`. When the user's word matches a label (e.g. "chat"), emit the CANONICAL choice value (`"text"`), not the label. The backend will also resolve label → value server-side, so if in doubt pick the closest value.

2. **Negation via is_not.** If the user asks for the opposite of an existing value (e.g. "show errors" but the only choice is "OK"), invert with `is_not`. Do NOT return an empty filter just because the user's word isn't in the list.
   - "show errors" with choices ["OK"] → emit `{status, is_not, "OK"}`
   - "failed" with choices ["success"] → emit `{status, is_not, "success"}`

3. **Multi-value matching.** If the user named multiple things (e.g. "Spanish or French", "rmse or psnr", "Toxicity and Bias"), emit one filter row PER value, all using `is`. The application will OR them together. Always emit at least one row per named value.

3a. **Separator-tolerant matching.** Choices often use snake_case or kebab-case while users speak in plain English. Treat `_`, `-`, and space as equivalent when looking for a match. Examples:
   - user says "spearman correlation", choices include "spearman_correlation" → use "spearman_correlation"
   - user says "word error rate", choices include "word_error_rate" → use "word_error_rate"
   - user says "regex pii detection", choices include "regex_pii_detection" → use "regex_pii_detection"
   The backend will also normalize this server-side, so when in doubt, pick the closest choice and emit it.

4. **Substring fallback.** If the user named a substring (e.g. "production" but the only choice is "production-eu"), use `contains` instead of `is`.

5. **Partial-result rule.** If you can ground SOME but not ALL fields the user mentioned, STILL return the filters for the fields you grounded successfully. Never throw away the whole answer because one field couldn't be matched. An incomplete filter is better than no filter.

For fields WITHOUT a "choices" list (free text or numeric), use the user's literal value directly.

**Long choices lists (`choices_sample` + `choices_total`):** If a field has a `choices_sample` instead of `choices`, only a sample of values is shown — the actual list has `choices_total` entries. ALWAYS emit a filter for these fields when the user names something. If the user's word is in the sample, use it. If not, STILL emit the user's literal phrase as the value (use operator `is` for an exact name like "spearman correlation" or `contains` for a partial match like "regex"). The backend will fuzzy-resolve your value to the real choice — your job is to capture the user's intent, NOT to scan a million options. Never return an empty filter just because the value isn't in the sample.

If the user's query is ambiguous and doesn't map to any field at all, return an empty array [].

Example schema:
[{"field": "status", "type": "enum", "operators": ["is", "is_not"], "choices": ["active", "inactive"]}]

Example query: "show me active items"
Example response: [{"field": "status", "operator": "is", "value": "active"}]

Example query: "show me items that aren't active" with same schema
Example response: [{"field": "status", "operator": "is_not", "value": "active"}]"""

SELECT_FIELDS_PROMPT = """You are a filter assistant. Given a user's natural language query and a list of available filter fields, pick the fields that are relevant to the query.

Each field in the schema has:
- "field": the field identifier
- "label": a human-readable name
- "category": one of system/eval/annotation/attribute
- "type": string/number/date/boolean

Rules:
- Return ONLY a JSON object of the form {"fields": ["field_id_1", "field_id_2"]}
- Only include field identifiers that appear in the schema
- Include a field if the user's query references it by name, label, or a synonym
- Prefer precision — if unsure, omit the field
- If nothing matches, return {"fields": []}
- Return ONLY the JSON object, no explanation or markdown"""


SMART_AGENT_PROMPT = """You are a filter-building assistant for an LLM observability product.

You will be given a user's natural language query and a list of filter fields available for the current project. Your job is to translate the query into a structured filter that the application can apply.

Each field in the schema may already have its complete configured values inlined as a `v` array — in that case, pick straight from that list and do NOT call any tool. A string field marked `v_searchable: true` requires exact server grounding before you may use it. Call the value tool with a specific `search_query`; never invent a value or fall back to the user's literal text for such a field.

You have two tools:
1. get_field_values(field_id, search_query) — performs an exact bounded search of real values for a field. SKIP this entirely for fields whose `v` is already inlined. For fields with `v_searchable: true`, you MUST pass a specific search_query. The request fails rather than returning sampled or incomplete values. The backend ranks the complete matching result by exact > prefix > substring > token overlap > fuzzy n-gram and returns the top matches. Example: get_field_values("model", search_query="gpt-4") returns exact stored values from the gpt-4 family.
2. submit_filter(filters) — your final answer. `filters` is a JSON array of filter conditions. Each condition has `field`, `operator`, and `value`. The operator must come from the type-appropriate operator list (see the legend above the field schema). For string fields, you may use any of: is, is_not, contains, not_contains.

VALUE-GROUNDING RULES (most important):

When you have a list of real values for a string field — whether from the inlined `v` array or from a get_field_values call — pick a value using these rules:

a. **Exact synonym match.** If the user's word matches a returned value (case-insensitive, including substring/prefix), use that exact value. Examples:
   - user says "english", returned values include "English" → use "English"
   - user says "gpt-4o", returned values include "gpt-4o-mini-2024-07-18" → use "gpt-4o-mini-2024-07-18" with operator `contains` or `is`
   - user says "OK", returned values include "ok" → use "ok"

b. **Negation via is_not.** If the user asks for the OPPOSITE of an existing value (and that's the only thing that exists), invert with `is_not`. This is critical — DO NOT return an empty filter just because the user's word isn't in the list. Examples:
   - user: "show errors", returned status values: ["OK"] → emit `{status, is_not, "OK"}` (everything not OK is an error)
   - user: "non-test calls", returned test_execution values: ["test_run_a", "test_run_b"] → if the user wants to EXCLUDE all, use `is_not` for each, OR use `is_empty` if available
   - user: "failed traces", returned status values: ["success"] → emit `{status, is_not, "success"}`

c. **Multi-value matching.** If the user named multiple things (e.g. "Spanish or French", "voicemail or busy"), emit one filter row per value, all using `is`. The application will OR them together.

d. **Substring fallback.** If no exact value matches but the user clearly named a substring (e.g. "production" but the only tag is "production-eu-west"), use `contains` instead of `is`.

e. **Partial-result rule.** If you can ground SOME but not ALL fields the user mentioned, STILL return the filters for the fields you grounded successfully. Never throw away the whole answer because one field couldn't be matched. An incomplete filter is better than no filter.

f. **No literal fallback for searchable fields.** If exact grounding returns no values, omit that condition. Never substitute the user's literal phrase for a `v_searchable` field. A broad, incomplete, or unavailable grounding read is rejected by the server instead of being shown to you as an empty or sampled result.

For numeric/date fields:
- Don't call get_field_values — those are continuous values.
- Pick the operator from the user's wording: "more than"/"over"/">" → greater_than, "less than"/"under"/"<" → less_than, "between X and Y" → between with value [X, Y], "at least"/"≥" → greater_than_or_equal, etc.

Multi-field queries:
- Read the query carefully — extract every distinct constraint.
- A query like "english female personas with success rate above 80" has THREE constraints: persona_language, persona_gender, success_rate. Emit one filter for each.

When to give up:
- Only return an empty filter if NO field in the schema is relevant to the query at all. If even one field maps cleanly, return that.
- Always finish by calling submit_filter exactly once. Do not return free-form text."""


# ---------------------------------------------------------------------------
# Smart agent helpers
# ---------------------------------------------------------------------------


def _normalize_grounding_search(search_query):
    value = str(search_query or "").strip()
    if not value or len(value.encode("utf-8")) > SMART_FILTER_SEARCH_MAX_BYTES:
        raise _grounding_too_broad()
    return value


def _fetch_trace_field_values(
    project_ids,
    metric_name,
    metric_type,
    *,
    search_query,
    deadline=None,
):
    """Return an exact query-scoped value vocabulary from direct-write CH25.

    A finite result is usable only when the underlying selector proves the
    entire searched 12-month window complete. A cap, timeout, or replay gap is
    a typed refusal; it is never converted to an empty list that lets the LLM
    invent a literal value.
    """
    from tracer.services.clickhouse.attribute_reads import AttributeReadSelector
    from tracer.services.clickhouse.filter_value_reads import (
        SYSTEM_FILTER_VALUE_METRICS,
        read_span_system_filter_values,
    )
    from tracer.services.clickhouse.read_budget import ReadDeadline
    from tracer.services.clickhouse.v2.query_service import V2AnalyticsQueryService

    if not project_ids:
        return []
    search = _normalize_grounding_search(search_query)
    deadline = deadline or ReadDeadline.start(SMART_FILTER_VALUE_READ_WALL_MS)

    try:
        if metric_type == "system_metric":
            if metric_name not in SYSTEM_FILTER_VALUE_METRICS:
                raise _grounding_too_broad()
            read = read_span_system_filter_values(
                V2AnalyticsQueryService(),
                project_ids=[str(project_id) for project_id in project_ids],
                metric_name=metric_name,
                search=search,
                limit=SMART_FILTER_VALUE_LIMIT,
                lookback_days=365,
                deadline=deadline,
            )
            if not read.query_complete:
                logger.warning(
                    "smart_filter_values_incomplete",
                    metric_name=metric_name,
                    metric_type=metric_type,
                    error_code=read.query_error_code,
                )
                if read.query_error_code == "sample_limit":
                    raise _grounding_too_broad()
                raise _grounding_unavailable()
            return list(read.values)
        elif metric_type == "custom_attribute":
            read = AttributeReadSelector(
                typed_only=True,
                json_attribute_mode="arrays",
                wall_timeout_ms=deadline.remaining_ms(SMART_FILTER_VALUE_READ_WALL_MS),
            ).read_values(
                project_ids,
                metric_name,
                search=search,
                max_values=SMART_FILTER_VALUE_LIMIT,
                horizon_days=365,
            )
            if not read.metadata.query_complete:
                logger.warning(
                    "smart_filter_values_incomplete",
                    metric_name=metric_name,
                    metric_type=metric_type,
                    error_code=read.metadata.query_error_code,
                )
                if read.metadata.query_error_code == "sample_limit":
                    raise _grounding_too_broad()
                raise _grounding_unavailable()

            values = []
            seen = set()
            for row in read.rows:
                raw_values = row.value if isinstance(row.value, tuple) else (row.value,)
                for raw_value in raw_values:
                    value = (
                        "true"
                        if raw_value is True
                        else "false"
                        if raw_value is False
                        else str(raw_value)
                    )
                    if value and value not in seen:
                        seen.add(value)
                        values.append(value)
            if len(values) > SMART_FILTER_VALUE_LIMIT:
                raise _grounding_too_broad()
            return values
        else:
            raise _grounding_too_broad()
    except SmartFilterGroundingError:
        raise
    except Exception as exc:
        logger.warning(
            "smart_filter_values_failed",
            metric_name=metric_name,
            metric_type=metric_type,
            error_type=type(exc).__name__,
        )
        raise _grounding_unavailable() from exc


# Configured choice lists are exact metadata, so small ones may be inlined.
# Dynamic trace/dataset values are never pre-fetched or sampled; the model must
# issue one query-scoped exact tool call for the field it actually selected.
_INLINE_VALUE_CAP = 30


def _resolve_choice(value, choices, choice_labels=None):
    """Resolve a user/LLM-provided value to one of the allowed choices.

    Used by the legacy build_filters validator to recover from minor
    LLM/user mistakes when picking from an enum list. Tries, in order:

      1. exact match against choices
      2. exact match against the human label in `choice_labels`
         (e.g. value="Chat", choice_labels={"text": "Chat"} → returns "text")
      3. case-insensitive match against choices and labels
      4. separator-tolerant match (treat space / underscore / hyphen as
         equivalent — so "spearman correlation" maps to "spearman_correlation")
      5. case-insensitive substring containment as a last resort

    Returns the canonical choice string or None if nothing matches.
    """
    if value is None:
        return None
    sval = str(value)
    choice_labels = choice_labels or {}
    if sval in choices:
        return sval
    # Exact match against a human label.
    for canonical, label in choice_labels.items():
        if str(label) == sval and canonical in choices:
            return canonical
    low = sval.lower()
    # Case-insensitive against choices.
    for c in choices:
        if str(c).lower() == low:
            return c
    # Case-insensitive against labels.
    for canonical, label in choice_labels.items():
        if str(label).lower() == low and canonical in choices:
            return canonical

    # Separator-tolerant: normalize " ", "_", "-" all to a single space.
    def norm(s):
        return (
            str(s).lower().replace("_", " ").replace("-", " ").replace("/", " ").strip()
        )

    n_val = norm(sval)
    for c in choices:
        if norm(c) == n_val:
            return c
    for canonical, label in choice_labels.items():
        if norm(label) == n_val and canonical in choices:
            return canonical
    # Substring containment as a final fallback.
    for c in choices:
        nc = norm(c)
        if n_val and (n_val in nc or nc in n_val):
            return c
    for canonical, label in choice_labels.items():
        nl = norm(label)
        if n_val and (n_val in nl or nl in n_val) and canonical in choices:
            return canonical
    return None


_QUERY_STOPWORDS = {
    "show",
    "me",
    "all",
    "any",
    "the",
    "with",
    "and",
    "or",
    "of",
    "by",
    "for",
    "in",
    "to",
    "a",
    "an",
    "is",
    "are",
    "not",
    "have",
    "evals",
    "eval",
    "filter",
    "filters",
    "give",
    "list",
    "find",
    "where",
    "that",
    "which",
    "anything",
    "this",
    "these",
    "those",
    "from",
}


def _query_token_phrases(query):
    """Extract plausible search phrases from a user query for fuzzy matching.

    Generates a mix of single tokens and 2-3-word adjacent phrases
    (e.g. "spearman correlation", "word error rate"), filtered through
    a small stopword list. Used as a last-resort fallback when the LLM
    returned no filters but the user clearly named something.
    """
    if not query:
        return []
    tokens = [t for t in _tokenize(query.lower()) if t and t not in _QUERY_STOPWORDS]
    phrases = []
    seen = set()

    def add(p):
        if p and p not in seen:
            seen.add(p)
            phrases.append(p)

    # Multi-word phrases first (more specific).
    for size in (3, 2):
        for i in range(len(tokens) - size + 1):
            add(" ".join(tokens[i : i + size]))
    # Then single tokens.
    for t in tokens:
        add(t)
    return phrases


def _smart_search_values(values, query, limit=None):
    """Rank a list of distinct values by how well each matches a query.

    Used for high-cardinality string fields where dumping every value
    into the LLM prompt would be wasteful. Ranking is purely lexical
    (no embeddings) so it stays fast and deterministic:

      1. exact case-insensitive match
      2. starts-with case-insensitive
      3. substring case-insensitive
      4. token overlap (split on non-alphanumerics)
      5. character n-gram overlap as a last-resort fuzzy fallback

    Returns at most `limit` values in descending relevance.
    """
    limit = settings.SMART_FILTER_GROUNDED_VALUE_LIMIT if limit is None else int(limit)
    if limit < 1:
        raise ValueError("limit must be positive")
    if not query:
        return values[:limit]
    q = str(query).strip().lower()
    if not q:
        return values[:limit]
    q_tokens = {t for t in _tokenize(q) if t}
    q_grams = _char_ngrams(q, 3)

    scored = []
    for v in values:
        if v is None:
            continue
        sv = str(v)
        lv = sv.lower()
        score = 0
        if lv == q:
            score = 1000
        elif lv.startswith(q):
            score = 800
        elif q in lv:
            score = 600
        else:
            v_tokens = {t for t in _tokenize(lv) if t}
            token_overlap = len(q_tokens & v_tokens)
            if token_overlap:
                score = 200 + token_overlap * 50
            else:
                v_grams = _char_ngrams(lv, 3)
                if q_grams and v_grams:
                    inter = len(q_grams & v_grams)
                    if inter:
                        score = int(100 * inter / max(len(q_grams), 1))
        if score > 0:
            # Tiebreaker: prefer shorter values (less noise around the match)
            scored.append((-score, len(sv), sv))
    scored.sort()
    return [v for _, _, v in scored[:limit]]


def _tokenize(s):
    """Split a string on non-alphanumeric runs for token overlap matching."""
    out = []
    buf = []
    for ch in s:
        if ch.isalnum():
            buf.append(ch)
        else:
            if buf:
                out.append("".join(buf))
                buf = []
    if buf:
        out.append("".join(buf))
    return out


def _char_ngrams(s, n):
    """Set of character n-grams for fuzzy ranking."""
    if len(s) < n:
        return {s}
    return {s[i : i + n] for i in range(len(s) - n + 1)}


def _fetch_dataset_column_values(
    dataset_id,
    column_id,
    *,
    search_query,
    deadline=None,
):
    """Exact searched cell values for a (dataset, column) pair.

    The LIMIT includes a sentinel. Filling it is a typed ``too broad`` refusal,
    never a sampled vocabulary. Array/JSON blobs are flattened only after the
    complete searched raw-value set has been proven finite.

    NOTE: ownership is validated by the caller (which resolves the
    dataset against the workspace before calling this).
    """
    import json as _json

    from tracer.services.clickhouse.client import is_clickhouse_enabled
    from tracer.services.clickhouse.query_service import (
        AnalyticsQueryService,
    )
    from tracer.services.clickhouse.read_budget import ReadDeadline

    if not dataset_id or not column_id:
        raise _grounding_too_broad()
    if not is_clickhouse_enabled():
        raise _grounding_unavailable()
    search = _normalize_grounding_search(search_query)
    deadline = deadline or ReadDeadline.start(SMART_FILTER_VALUE_READ_WALL_MS)

    # Look up the column's data_type so we know whether to flatten. This point
    # read occurs from inside the model's tool loop, so it must inherit the
    # original request wall instead of using PostgreSQL's process-wide default.
    try:
        from model_hub.models.develop_dataset import Column

        with _bounded_ai_filter_postgres(deadline):
            column = Column.objects.only("data_type").get(
                id=column_id, dataset_id=dataset_id, deleted=False
            )
        data_type = column.data_type
    except Column.DoesNotExist as exc:
        raise _grounding_too_broad() from exc

    analytics = AnalyticsQueryService()
    try:
        sql = (
            "SELECT DISTINCT value AS val "
            "FROM model_hub_cell FINAL "
            "WHERE _peerdb_is_deleted = 0 "
            "AND dataset_id = toUUID(%(dataset_id)s) "
            "AND column_id = toUUID(%(column_id)s) "
            "AND value != '' "
            "AND positionCaseInsensitiveUTF8(value, %(search)s) > 0 "
            "ORDER BY val "
            "LIMIT %(result_limit)s"
        )
        result = analytics.execute_ch_query(
            sql,
            {
                "dataset_id": str(dataset_id),
                "column_id": str(column_id),
                "search": search,
                "result_limit": SMART_FILTER_VALUE_LIMIT + 1,
            },
            timeout_ms=deadline.remaining_ms(SMART_FILTER_VALUE_READ_WALL_MS),
            settings={
                "max_result_rows": SMART_FILTER_VALUE_LIMIT + 1,
                "result_overflow_mode": "throw",
                "timeout_overflow_mode": "throw",
            },
        )
        raw = [row["val"] for row in result.data if row.get("val")]
        if len(raw) > SMART_FILTER_VALUE_LIMIT:
            raise _grounding_too_broad()
    except SmartFilterGroundingError:
        raise
    except Exception as exc:
        logger.warning(
            "dataset_column_values_query_failed",
            dataset_id=str(dataset_id),
            column_id=str(column_id),
            error_type=type(exc).__name__,
        )
        raise _grounding_unavailable() from exc

    if data_type not in ("array", "json"):
        return raw

    # Flatten list / dict blobs into their elements for better LLM grounding.
    seen = set()
    out = []
    for blob in raw:
        try:
            parsed = _json.loads(blob)
        except (ValueError, TypeError):
            parsed = None
        candidates = []
        if isinstance(parsed, list):
            for elem in parsed:
                if isinstance(elem, str | int | float | bool):
                    candidates.append(str(elem))
                elif isinstance(elem, dict):
                    for v in elem.values():
                        if isinstance(v, str | int | float):
                            candidates.append(str(v))
        elif isinstance(parsed, dict):
            for v in parsed.values():
                if isinstance(v, str | int | float):
                    candidates.append(str(v))
        else:
            candidates.append(blob)
        for c in candidates:
            s = c.strip()
            if search.casefold() in s.casefold() and s and s not in seen:
                seen.add(s)
                out.append(s)
                if len(out) > SMART_FILTER_VALUE_LIMIT:
                    raise _grounding_too_broad()
    return out


_SIMULATION_STRING_VALUE_EXPRESSIONS = {
    "simulation": "rt.name",
    "run_test": "rt.name",
    "test_execution": "toString(te.id)",
    "scenario": "s.name",
    "agent_definition": "ad.agent_name",
    "agent_version": ("concat(ad.agent_name, ' v', toString(av.version_number))"),
    "persona": (
        "if(JSONExtractString(replaceAll(JSONExtractString(c.call_metadata, "
        "'row_data', 'persona'), char(39), char(34)), 'name') != '', "
        "JSONExtractString(replaceAll(JSONExtractString(c.call_metadata, "
        "'row_data', 'persona'), char(39), char(34)), 'name'), "
        "JSONExtractString(c.call_metadata, 'row_data', 'persona'))"
    ),
    "call_type": "c.simulation_call_type",
    "status": "c.status",
    "ended_reason": "c.ended_reason",
    "scenario_type": "s.scenario_type",
    "persona_gender": (
        "JSONExtractString(replaceAll(JSONExtractString(c.call_metadata, "
        "'row_data', 'persona'), char(39), char(34)), 'gender')"
    ),
    "persona_age_group": (
        "JSONExtractString(replaceAll(JSONExtractString(c.call_metadata, "
        "'row_data', 'persona'), char(39), char(34)), 'age_group')"
    ),
    "persona_location": (
        "JSONExtractString(replaceAll(JSONExtractString(c.call_metadata, "
        "'row_data', 'persona'), char(39), char(34)), 'location')"
    ),
    "persona_profession": (
        "JSONExtractString(replaceAll(JSONExtractString(c.call_metadata, "
        "'row_data', 'persona'), char(39), char(34)), 'profession')"
    ),
    "persona_personality": (
        "JSONExtractString(replaceAll(JSONExtractString(c.call_metadata, "
        "'row_data', 'persona'), char(39), char(34)), 'personality')"
    ),
    "persona_communication_style": (
        "JSONExtractString(replaceAll(JSONExtractString(c.call_metadata, "
        "'row_data', 'persona'), char(39), char(34)), 'communication_style')"
    ),
    "persona_accent": (
        "JSONExtractString(replaceAll(JSONExtractString(c.call_metadata, "
        "'row_data', 'persona'), char(39), char(34)), 'accent')"
    ),
    "persona_language": (
        "JSONExtractString(replaceAll(JSONExtractString(c.call_metadata, "
        "'row_data', 'persona'), char(39), char(34)), 'language')"
    ),
    "persona_conversation_speed": (
        "JSONExtractString(replaceAll(JSONExtractString(c.call_metadata, "
        "'row_data', 'persona'), char(39), char(34)), 'conversation_speed')"
    ),
}


def _resolve_simulation_scope(
    workspace,
    *,
    agent_definition_id=None,
    run_test_id=None,
    test_execution_id=None,
):
    """Authorize and normalize one explicit simulation grounding scope.

    Smart grounding must never reinterpret an observability ``project_id`` as
    a simulation entity. At least one native simulation scope is required and
    every supplied id must resolve through the same organization/workspace.
    """

    if not any((agent_definition_id, run_test_id, test_execution_id)):
        raise _grounding_too_broad()

    from simulate.models import AgentDefinition, RunTest, TestExecution

    organization = workspace.organization
    scope = {
        "organization_id": str(organization.id),
        "workspace_id": str(workspace.id),
    }
    if agent_definition_id:
        if not AgentDefinition.objects.filter(
            id=agent_definition_id,
            organization=organization,
            workspace=workspace,
            deleted=False,
        ).exists():
            raise _grounding_too_broad()
        scope["agent_definition_id"] = str(agent_definition_id)

    run_test = None
    if run_test_id:
        run_tests = RunTest.objects.filter(
            id=run_test_id,
            organization=organization,
            workspace=workspace,
            deleted=False,
        )
        if agent_definition_id:
            run_tests = run_tests.filter(agent_definition_id=agent_definition_id)
        run_test = run_tests.only("id", "agent_definition_id").first()
        if not run_test:
            raise _grounding_too_broad()
        scope["run_test_id"] = str(run_test.id)
        if run_test.agent_definition_id:
            scope.setdefault("agent_definition_id", str(run_test.agent_definition_id))

    if test_execution_id:
        executions = TestExecution.objects.filter(
            id=test_execution_id,
            run_test__organization=organization,
            run_test__workspace=workspace,
            run_test__deleted=False,
            deleted=False,
        )
        if run_test_id:
            executions = executions.filter(run_test_id=run_test_id)
        if agent_definition_id:
            executions = executions.filter(
                run_test__agent_definition_id=agent_definition_id
            )
        execution = executions.select_related("run_test").first()
        if not execution:
            raise _grounding_too_broad()
        scope["test_execution_id"] = str(execution.id)
        scope.setdefault("run_test_id", str(execution.run_test_id))
        if execution.run_test.agent_definition_id:
            scope.setdefault(
                "agent_definition_id",
                str(execution.run_test.agent_definition_id),
            )
    return scope


def _fetch_simulation_field_values(
    scope,
    metric_name,
    metric_type,
    *,
    search_query,
    eval_key=None,
    deadline=None,
):
    """Read an exact, tenant-scoped simulation value vocabulary from CH.

    The query joins the CDC dimension tables rather than borrowing any trace
    reader or relying on eventually-refreshed dictionaries for authorization.
    A sentinel row proves whether the result is finite under the request wall.
    """

    from tracer.services.clickhouse.client import is_clickhouse_enabled
    from tracer.services.clickhouse.query_builders.simulation_dashboard import (
        _sanitize_key,
    )
    from tracer.services.clickhouse.query_service import AnalyticsQueryService
    from tracer.services.clickhouse.read_budget import ReadDeadline

    if not scope or not scope.get("organization_id") or not scope.get("workspace_id"):
        raise _grounding_too_broad()
    if not is_clickhouse_enabled():
        raise _grounding_unavailable()
    search = _normalize_grounding_search(search_query)
    deadline = deadline or ReadDeadline.start(SMART_FILTER_VALUE_READ_WALL_MS)

    if metric_type == "system_metric":
        value_expr = _SIMULATION_STRING_VALUE_EXPRESSIONS.get(metric_name)
        if not value_expr:
            raise _grounding_too_broad()
    elif metric_type == "eval_metric":
        try:
            safe_eval_key = _sanitize_key(str(eval_key or ""))
        except ValueError as exc:
            raise _grounding_too_broad() from exc
        result_expr = (
            f"nullIf(JSONExtractString(c.eval_outputs, '{safe_eval_key}', "
            "'result'), '')"
        )
        output_expr = (
            f"nullIf(JSONExtractString(c.eval_outputs, '{safe_eval_key}', "
            "'output'), '')"
        )
        value_expr = f"coalesce({result_expr}, {output_expr})"
    else:
        raise _grounding_too_broad()

    clauses = [
        "c._peerdb_is_deleted = 0",
        "c.deleted = 0",
        "te._peerdb_is_deleted = 0",
        "te.deleted = 0",
        "rt._peerdb_is_deleted = 0",
        "rt.deleted = 0",
        "rt.organization_id = toUUID(%(organization_id)s)",
        "rt.workspace_id = toUUID(%(workspace_id)s)",
        "c.created_at >= now() - INTERVAL 365 DAY",
        f"{value_expr} IS NOT NULL",
        f"toString({value_expr}) != ''",
        f"positionCaseInsensitiveUTF8(toString({value_expr}), %(search)s) > 0",
    ]
    params = {
        "organization_id": scope["organization_id"],
        "workspace_id": scope["workspace_id"],
        "search": search,
        "result_limit": SMART_FILTER_VALUE_LIMIT + 1,
    }
    if scope.get("agent_definition_id"):
        clauses.append("rt.agent_definition_id = toUUID(%(agent_definition_id)s)")
        params["agent_definition_id"] = scope["agent_definition_id"]
    if scope.get("run_test_id"):
        clauses.append("rt.id = toUUID(%(run_test_id)s)")
        params["run_test_id"] = scope["run_test_id"]
    if scope.get("test_execution_id"):
        clauses.append("te.id = toUUID(%(test_execution_id)s)")
        params["test_execution_id"] = scope["test_execution_id"]
    if metric_type == "eval_metric":
        clauses.append(f"JSONHas(c.eval_outputs, '{safe_eval_key}') = 1")

    query = (
        f"SELECT DISTINCT toString({value_expr}) AS value\n"
        "FROM simulate_call_execution AS c FINAL\n"
        "INNER JOIN simulate_test_execution AS te FINAL ON te.id = c.test_execution_id\n"
        "INNER JOIN simulate_run_test AS rt FINAL ON rt.id = te.run_test_id\n"
        "LEFT JOIN simulate_scenarios AS s FINAL ON s.id = c.scenario_id "
        "AND s._peerdb_is_deleted = 0 AND s.deleted = 0\n"
        "LEFT JOIN simulate_agent_version AS av FINAL ON av.id = c.agent_version_id "
        "AND av._peerdb_is_deleted = 0 AND av.deleted = 0\n"
        "LEFT JOIN simulate_agent_definition AS ad FINAL "
        "ON ad.id = rt.agent_definition_id "
        "AND ad._peerdb_is_deleted = 0 AND ad.deleted = 0\n"
        f"WHERE {' AND '.join(clauses)}\n"
        "ORDER BY value\n"
        "LIMIT %(result_limit)s"
    )
    try:
        result = AnalyticsQueryService().execute_ch_query(
            query,
            params,
            timeout_ms=deadline.remaining_ms(SMART_FILTER_VALUE_READ_WALL_MS),
            settings={
                "max_result_rows": SMART_FILTER_VALUE_LIMIT + 1,
                "result_overflow_mode": "throw",
                "timeout_overflow_mode": "throw",
            },
        )
        values = [row["value"] for row in result.data if row.get("value")]
        if len(values) > SMART_FILTER_VALUE_LIMIT:
            raise _grounding_too_broad()
        return values
    except SmartFilterGroundingError:
        raise
    except Exception as exc:
        logger.warning(
            "simulation_filter_values_query_failed",
            metric_name=metric_name,
            metric_type=metric_type,
            error_type=type(exc).__name__,
        )
        raise _grounding_unavailable() from exc


def _resolve_project_ids(workspace, raw_project_id):
    """Validate that the requested project belongs to the workspace.

    Returns a list of project ids (single-element if the caller named one,
    or all workspace projects if none was given).
    """
    from tracer.models.project import Project

    if raw_project_id:
        owned = Project.objects.filter(
            workspace=workspace,
            id=raw_project_id,
        ).exists()
        return [str(raw_project_id)] if owned else []

    workspace_ids = list(
        Project.objects.filter(workspace=workspace).values_list("id", flat=True)[
            : SMART_FILTER_PROJECT_SCOPE_LIMIT + 1
        ]
    )
    if len(workspace_ids) > SMART_FILTER_PROJECT_SCOPE_LIMIT:
        raise _grounding_too_broad()
    return [str(project_id) for project_id in workspace_ids]


def _resolve_dataset_id(workspace, raw_dataset_id):
    """Return the dataset id iff it belongs to this workspace, else None.

    Smart mode against dataset rows MUST target a specific dataset —
    unlike the trace path, there's no meaningful "all workspace datasets"
    default (the LLM would be asked to ground filters against the union
    of every column in every dataset, which is meaningless).
    """
    if not raw_dataset_id:
        return None
    try:
        from model_hub.models.develop_dataset import Dataset

        Dataset.objects.only("id").get(
            id=raw_dataset_id, workspace=workspace, deleted=False
        )
        return str(raw_dataset_id)
    except Dataset.DoesNotExist:
        return None


_STRING_OPS_ALWAYS_ALLOWED = {"is", "is_not", "contains", "not_contains"}
_NUMBER_OPS_ALWAYS_ALLOWED = {
    "equal_to",
    "not_equal_to",
    "greater_than",
    "greater_than_or_equal",
    "less_than",
    "less_than_or_equal",
    "between",
    "not_between",
}

_NUMERIC_FIELD_TYPES = {"number", "integer", "float"}


def _ai_schema_identity(field_schema):
    """Return the logical property identity used inside the AI tool loop.

    ``field`` remains the native adapter column. New callers supply a stable
    ``property_id``; legacy non-smart schemas without one retain their old
    field identity until their owning surface is migrated.
    """

    if not isinstance(field_schema, dict):
        return ""
    return str(field_schema.get("property_id") or field_schema.get("field") or "")


def _validate_smart_filters(parsed_filters, schema, grounded_values_by_field):
    """Apply field/operator/choice validation for smart-mode output.

    The smart-mode prompt drops per-field operator lists from the LLM
    payload (the operator legend is in the system prompt by type), so
    validation here checks against the type-default operator set rather
    than each field's declared list. Every non-numeric value must also be
    witnessed by configured choices or an exact tool result from this request.
    """
    if not isinstance(parsed_filters, list):
        return []
    field_map = {
        _ai_schema_identity(s): s
        for s in schema
        if isinstance(s, dict) and _ai_schema_identity(s)
    }
    native_field_map = {}
    for field_schema in field_map.values():
        native_field_map.setdefault(str(field_schema.get("field") or ""), []).append(
            field_schema
        )
    out = []
    for f in parsed_filters:
        if not isinstance(f, dict):
            continue
        submitted_identity = str(f.get("property_id") or f.get("field") or "")
        operator = f.get("operator") or "is"
        value = f.get("value")
        field_schema = field_map.get(submitted_identity)
        if field_schema is None:
            # Legacy model responses may still echo the native field. Only
            # accept that form when it resolves to exactly one definition;
            # same-name definitions must never be guessed.
            native_matches = native_field_map.get(str(f.get("field") or ""), [])
            if len(native_matches) != 1:
                continue
            field_schema = native_matches[0]
        identity = _ai_schema_identity(field_schema)
        native_field = str(field_schema.get("field") or "")
        ftype = field_schema.get("type") or "string"
        if ftype in _NUMERIC_FIELD_TYPES:
            if operator not in _NUMBER_OPS_ALWAYS_ALLOWED:
                continue
        else:
            if operator not in _STRING_OPS_ALWAYS_ALLOWED:
                continue
        if ftype not in _NUMERIC_FIELD_TYPES:
            grounded_values = tuple(grounded_values_by_field.get(identity, ()))
            normalized_value = str(value or "").casefold()
            if not grounded_values or not normalized_value:
                raise _grounding_too_broad()
            exact_match = next(
                (
                    candidate
                    for candidate in grounded_values
                    if str(candidate).casefold() == normalized_value
                ),
                None,
            )
            if operator in {"is", "is_not"}:
                if exact_match is None:
                    raise _grounding_too_broad()
                value = exact_match
            elif not any(
                normalized_value in str(candidate).casefold()
                for candidate in grounded_values
            ):
                raise _grounding_too_broad()
        condition = {
            "field": native_field,
            "operator": operator,
            "value": value,
        }
        if field_schema.get("property_id"):
            condition["property_id"] = field_schema["property_id"]
        out.append(condition)
    return out


def _run_smart_agent(query, schema, fetch_values, *, deadline=None):
    """Run the Haiku tool-use loop. Returns a list of validated filters.

    `fetch_values(field_id, search_query=...) -> list[str]` is the source-specific value
    lookup. Traces pass a closure over `_fetch_trace_field_values`;
    datasets pass one over `_fetch_dataset_column_values`. The agent
    loop itself is shared and source-agnostic.

    Uses the in-house LLM wrapper (`agentic_eval.core.llm.llm.LLM`) which
    routes through the Agentcc gateway with litellm fallback, so we don't
    talk to Bedrock directly here.
    """
    from agentic_eval.core.llm.llm import LLM
    from agentic_eval.core.utils.model_config import ModelConfigs
    from tracer.services.clickhouse.read_budget import (
        ReadDeadline,
        ReadDeadlineExceeded,
    )

    deadline = deadline or ReadDeadline.start(SMART_FILTER_REQUEST_WALL_MS)

    def remaining_request_ms():
        try:
            return deadline.remaining_ms(SMART_FILTER_REQUEST_WALL_MS)
        except ReadDeadlineExceeded as exc:
            raise _grounding_unavailable() from exc

    cfg = ModelConfigs.VERTEX_GEMINI_2_5_FLASH
    llm = LLM(
        provider=cfg.provider,
        model_name=cfg.model_name,
        temperature=0.0,
        max_tokens=800,
    )

    # Compact field list — only the bits the LLM needs to pick fields.
    # Drop operators (uniform per type, explained in the system prompt),
    # drop labels when they're identical to the id, drop category for
    # system fields (the default). Saves ~60% of input tokens vs the
    # full per-field object.
    compact_fields = []
    for s in schema:
        if not isinstance(s, dict) or not s.get("field"):
            continue
        fid = s["field"]
        property_id = _ai_schema_identity(s)
        label = s.get("label")
        cat = s.get("category") or "system"
        ftype = s.get("type") or "string"
        entry = {"f": property_id, "t": ftype}
        if property_id != fid:
            entry["n"] = fid
        if label and label != fid:
            entry["l"] = label
        if cat != "system":
            entry["c"] = cat
        compact_fields.append(entry)

    schema_by_id = {
        _ai_schema_identity(s): s
        for s in schema
        if isinstance(s, dict) and s.get("field") and _ai_schema_identity(s)
    }

    # Configured choices are exact metadata and can be inlined. Dynamic values
    # are never pre-fetched: only a field the model actually selects may issue
    # one query-scoped exact search. This avoids serially sampling every string
    # dimension before the first model turn.
    grounded_values_by_field = {}
    static_choices_by_field = {}
    for entry in compact_fields:
        fid = entry["f"]
        configured = schema_by_id[fid].get("choices") or []
        if configured:
            values = []
            for configured_value in configured:
                if configured_value not in values:
                    values.append(configured_value)
            static_choices_by_field[fid] = values
            grounded_values_by_field[fid] = tuple(values)
            if len(values) <= _INLINE_VALUE_CAP:
                entry["v"] = values
            else:
                entry["v_count"] = len(values)
                entry["v_searchable"] = True
            continue
        if entry["t"] not in _NUMERIC_FIELD_TYPES:
            entry["v_searchable"] = True

    tools = [
        {
            "type": "function",
            "function": {
                "name": "get_field_values",
                "description": (
                    "Return the complete bounded set of stored values matching "
                    "one specific search for a field. Use this when the field "
                    "schema has `v_searchable: true`; fields with an inline `v` "
                    "array are already grounded. The request refuses broad or "
                    "incomplete results instead of returning a sample."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "field_id": {
                            "type": "string",
                            "description": "The field identifier from the schema.",
                        },
                        "search_query": {
                            "type": "string",
                            "description": (
                                "Specific substring/keyword used by the exact "
                                "bounded value query. Required for every "
                                "searchable field. The "
                                "backend ranks by exact > prefix > substring > "
                                "token overlap > char n-gram fuzzy. Returns at "
                                "most 20 ranked results."
                            ),
                        },
                    },
                    "required": ["field_id", "search_query"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "submit_filter",
                "description": (
                    "Submit the final filter. This must be your last action."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "filters": {
                            "type": "array",
                            "description": (
                                "List of filter conditions. Empty list if the "
                                "query cannot be translated."
                            ),
                            "items": {
                                "type": "object",
                                "properties": {
                                    "field": {"type": "string"},
                                    "operator": {"type": "string"},
                                    "value": {},
                                },
                                "required": ["field", "operator", "value"],
                            },
                        }
                    },
                    "required": ["filters"],
                },
            },
        },
    ]

    # Inline the operator legend so we don't have to list operators per field.
    operator_legend = (
        "Operators by field type:\n"
        "- string: is, is_not, contains, not_contains\n"
        "- number: equal_to, not_equal_to, greater_than, "
        "greater_than_or_equal, less_than, less_than_or_equal, "
        "between, not_between\n"
        "Field schema entries: "
        "f=stable property id (use this exact value in tools and submit_filter), "
        "n=native adapter field (display context only; omitted when equal to f), "
        "t=type, "
        "l=human label (omitted when same as id), "
        "c=category (omitted when 'system'), "
        "v=array of all real distinct values for this field "
        "(already pre-fetched — pick straight from this list, no tool call needed), "
        "v_count=exact configured choice count, "
        "v_searchable=true means you must call "
        "get_field_values(field_id, search_query) before using the field. "
        "Never invent or substitute a literal value for a searchable field."
    )
    user_payload = (
        f"{operator_legend}\n\n"
        f"Available fields ({len(compact_fields)}):\n"
        f"{json.dumps(compact_fields)}\n\n"
        f"User query: {query}"
    )
    messages = [
        {"role": "system", "content": SMART_AGENT_PROMPT},
        {"role": "user", "content": user_payload},
    ]

    submitted = None
    for _ in range(5):  # cap iterations
        # _get_completion_with_tools handles gateway routing, retries, and
        # litellm fallback internally. It uses the temperature/max_tokens
        # configured on the LLM instance.
        try:
            response = llm._get_completion_with_tools(
                messages,
                tools,
                timeout_ms=remaining_request_ms(),
            )
        except TimeoutError as exc:
            raise _grounding_unavailable() from exc
        remaining_request_ms()
        msg = response.choices[0].message
        tool_calls = getattr(msg, "tool_calls", None) or []

        if not tool_calls:
            break  # model gave a free-form reply with no tool call — give up

        # Append the assistant message first (required by chat protocol).
        messages.append(
            {
                "role": "assistant",
                "content": msg.content or "",
                "tool_calls": [
                    {
                        "id": tc.id,
                        "type": "function",
                        "function": {
                            "name": tc.function.name,
                            "arguments": tc.function.arguments,
                        },
                    }
                    for tc in tool_calls
                ],
            }
        )

        terminated = False
        for tc in tool_calls:
            name = tc.function.name
            try:
                args = json.loads(tc.function.arguments or "{}")
            except json.JSONDecodeError:
                args = {}

            if name == "get_field_values":
                fid = args.get("field_id")
                search_query = args.get("search_query")
                if fid in schema_by_id:
                    search_query = _normalize_grounding_search(search_query)
                    if fid in static_choices_by_field:
                        vals = static_choices_by_field[fid]
                    else:
                        vals = fetch_values(fid, search_query=search_query)
                else:
                    raise _grounding_too_broad()
                ranked = _smart_search_values(vals, search_query)
                grounded_values_by_field[fid] = tuple(vals)
                tool_result = {
                    "field_id": fid,
                    "search_query": search_query,
                    "query_complete": True,
                    "total_distinct": len(vals),
                    "values": ranked,
                }
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": tc.id,
                        "content": json.dumps(tool_result),
                    }
                )
            elif name == "submit_filter":
                submitted = args.get("filters", [])
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": tc.id,
                        "content": json.dumps({"ok": True}),
                    }
                )
                terminated = True
            else:
                # Unknown tool — tell the model and let it try again.
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": tc.id,
                        "content": json.dumps({"error": f"unknown tool {name}"}),
                    }
                )

        if terminated:
            break

    if submitted is None:
        return []
    return _validate_smart_filters(submitted, schema, grounded_values_by_field)


def _authorize_smart_property_schema(
    schema,
    *,
    source,
    workspace,
    project_ids=(),
    dataset_id=None,
    simulation_scope=None,
):
    """Bind every smart-filter schema row to one authorized definition.

    The browser supplies labels and native adapter fields for display, but it
    may not invent a logical property definition or reuse one definition for a
    same-name property from another source. Dynamic value reads remain scoped
    by the already-authorized project/dataset ids.
    """

    from tracer.utils.property_registry import (
        parse_property_registry_id,
        validate_property_filter_binding,
    )

    category_by_kind = {
        "system_attribute": "system",
        "custom_attribute": "attribute",
        "eval": "eval",  # explicit rolling legacy only
        "eval_config": "eval",
        "eval_template": "eval",
        "annotation": "annotation",
        "dataset_column": "dataset_column",
    }
    trace_kinds = {
        "system_attribute",
        "custom_attribute",
        "eval_config",
        "annotation",
    }
    filter_column_type_by_category = {
        "system": "SYSTEM_METRIC",
        "attribute": "SPAN_ATTRIBUTE",
        "eval": "EVAL_METRIC",
        "annotation": "ANNOTATION",
        "dataset_column": "CUSTOM_COLUMN",
    }
    normalized = []
    for raw in schema:
        field_schema = dict(raw)
        property_id = str(field_schema.get("property_id") or "")
        if not property_id:
            raise _grounding_too_broad()
        try:
            decoded = parse_property_registry_id(property_id)
        except ValueError as exc:
            raise _grounding_too_broad() from exc
        kind = decoded["property_kind"]
        native_field = str(field_schema.get("field") or "")
        declared_category = str(field_schema.get("category") or "system")
        expected_category = category_by_kind.get(kind)
        if expected_category is None or (
            declared_category != expected_category
            and not (source == "dataset" and kind == "dataset_column")
        ):
            raise _grounding_too_broad()
        try:
            validate_property_filter_binding(
                property_id,
                column_id=native_field,
                column_type=filter_column_type_by_category[declared_category],
                source=source,
            )
        except (KeyError, ValueError) as exc:
            raise _grounding_too_broad() from exc

        if source == "dataset":
            if kind != "dataset_column" or not dataset_id:
                raise _grounding_too_broad()
            # The exact value reader performs the authoritative
            # (dataset_id, column_id, deleted=false) lookup before CH.
            normalized.append(field_schema)
            continue

        if source == "simulation":
            if not simulation_scope or kind not in {
                "system_attribute",
                "eval_config",
            }:
                raise _grounding_too_broad()
            if (
                kind == "system_attribute"
                and decoded.get("definition_source") != "simulation"
            ):
                raise _grounding_too_broad()
            if kind == "eval_config":
                from simulate.models import SimulateEvalConfig

                configs = SimulateEvalConfig.objects.filter(
                    id=decoded["metric_name"],
                    run_test__organization=workspace.organization,
                    run_test__workspace=workspace,
                    run_test__deleted=False,
                    deleted=False,
                )
                if simulation_scope.get("agent_definition_id"):
                    configs = configs.filter(
                        run_test__agent_definition_id=simulation_scope[
                            "agent_definition_id"
                        ]
                    )
                if simulation_scope.get("run_test_id"):
                    configs = configs.filter(
                        run_test_id=simulation_scope["run_test_id"]
                    )
                if simulation_scope.get("test_execution_id"):
                    configs = configs.filter(
                        run_test__executions__id=simulation_scope["test_execution_id"],
                        run_test__executions__deleted=False,
                    )
                config = configs.select_related("eval_template").first()
                if (
                    not config
                    or not config.eval_template
                    or config.eval_template.deleted
                ):
                    raise _grounding_too_broad()
                output_type = str(
                    (config.eval_template.config or {}).get("output") or ""
                )
                normalized_output_type = output_type.casefold().replace("/", "_")
                if normalized_output_type == "pass_fail":
                    field_schema["choices"] = ["Passed", "Failed"]
                elif normalized_output_type in {"choice", "choices"}:
                    field_schema["choices"] = list(config.eval_template.choices or [])
                mapping = config.mapping if isinstance(config.mapping, dict) else {}
                field_schema["_simulation_eval_key"] = str(
                    mapping.get("key") or config.id
                )
                field_schema["_simulation_eval_output_type"] = output_type
            normalized.append(field_schema)
            continue

        if source not in {"traces", "sessions"} or kind not in trace_kinds:
            raise _grounding_too_broad()
        if kind == "system_attribute" and decoded.get("definition_source") in {
            "datasets",
            "dataset_column",
        }:
            raise _grounding_too_broad()
        if kind == "eval_config":
            from tracer.models.custom_eval_config import CustomEvalConfig

            config = (
                CustomEvalConfig.objects.filter(
                    id=native_field,
                    project_id__in=project_ids,
                    deleted=False,
                )
                .select_related("eval_template")
                .first()
            )
            if not config or not config.eval_template or config.eval_template.deleted:
                raise _grounding_too_broad()
            output_type = str((config.eval_template.config or {}).get("output") or "")
            if output_type.casefold().replace("/", "_") == "pass_fail":
                field_schema["choices"] = ["Passed", "Failed"]
            elif output_type.casefold() in {"choice", "choices"}:
                field_schema["choices"] = list(config.eval_template.choices or [])
        elif kind == "annotation" and native_field != "annotator":
            from django.db.models import Q

            from model_hub.models.develop_annotations import AnnotationsLabels

            label = (
                AnnotationsLabels.objects.filter(
                    Q(project_id__in=project_ids)
                    | Q(project__isnull=True, workspace=workspace),
                    id=native_field,
                    organization=workspace.organization,
                    deleted=False,
                )
                .order_by("id")
                .first()
            )
            if not label:
                raise _grounding_too_broad()
            options = (label.settings or {}).get("options") or []
            authoritative_choices = [
                option.get("label")
                for option in options
                if isinstance(option, dict) and option.get("label") is not None
            ]
            if authoritative_choices:
                field_schema["choices"] = authoritative_choices
        normalized.append(field_schema)
    return normalized


class AIFilterView(APIView):
    """
    POST /model-hub/ai-filter/

    Request body:
    {
        "query": "show me LLM evals that are pass/fail",
        "schema": [
            {
                "field": "eval_type",
                "label": "Eval Type",
                "type": "enum",
                "operators": ["is", "is_not"],
                "choices": ["llm", "code", "agent"]
            },
            ...
        ]
    }

    Response:
    {
        "status": true,
        "result": {
            "filters": [
                {"field": "eval_type", "operator": "is", "value": "llm"},
                {"field": "output_type", "operator": "is", "value": "pass_fail"}
            ]
        }
    }
    """

    _gm = GeneralMethods()
    permission_classes = [IsAuthenticated]

    def dispatch(self, request, *args, **kwargs):
        """Own one wall from APIView dispatch through the finalized response."""

        from tracer.services.clickhouse.read_budget import ReadDeadline

        self._request_deadline = ReadDeadline.start(SMART_FILTER_REQUEST_WALL_MS)
        response = super().dispatch(request, *args, **kwargs)
        try:
            _remaining_request_ms(self._request_deadline)
        except AIFilterUnavailableError as exc:
            logger.warning(
                "ai_filter_dispatch_deadline_exceeded",
                status_code=exc.status_code,
            )
            replacement = self._gm.custom_error_response(
                exc.status_code,
                exc.public_message,
                code=exc.code,
            )
            # ``super().dispatch`` already finalized the DRF response. Replace
            # its body/status in place so the error keeps renderer context.
            response.status_code = replacement.status_code
            response.data = replacement.data
        return response

    @validated_request(
        request_serializer=AIFilterRequestSerializer,
        responses={200: AIFilterResponseSerializer, **ERROR_RESPONSES},
        reject_unknown_fields=True,
    )
    def post(self, request, *args, **kwargs):
        mode = "build_filters"  # default — referenced by except blocks below
        request_deadline = self._request_deadline
        try:
            payload = request.validated_data
            mode = payload.get("mode", "build_filters")
            query = payload.get("query", "").strip()
            schema = payload.get("schema", [])

            if not query:
                return self._gm.bad_request("Query is required")
            if not schema:
                return self._gm.bad_request("Schema is required")
            if mode not in ("build_filters", "select_fields", "smart"):
                return self._gm.bad_request("Invalid mode")

            # ------------------------------------------------------------
            # Smart mode — agentic tool-use loop
            # ------------------------------------------------------------
            if mode == "smart":
                grounding_deadline = request_deadline
                source = payload.get("source", "traces")
                if source in {"traces", "sessions"}:
                    project_id = payload.get("project_id")
                    with _bounded_ai_filter_postgres(grounding_deadline):
                        project_ids = _resolve_project_ids(
                            request.workspace, project_id
                        )
                        if project_id and not project_ids:
                            return self._gm.bad_request(
                                "project not found in workspace"
                            )
                        schema = _authorize_smart_property_schema(
                            schema,
                            source=source,
                            workspace=request.workspace,
                            project_ids=project_ids,
                        )
                    schema_by_identity = {_ai_schema_identity(s): s for s in schema}
                    metric_type_by_id = {
                        identity: {
                            "system": "system_metric",
                            "eval": "eval_metric",
                            "annotation": "annotation_metric",
                            "attribute": "custom_attribute",
                        }.get(field_schema.get("category") or "system", "system_metric")
                        for identity, field_schema in schema_by_identity.items()
                    }

                    def fetch_values(property_id, *, search_query):
                        field_schema = schema_by_identity.get(property_id)
                        if not field_schema:
                            raise _grounding_too_broad()
                        return _fetch_trace_field_values(
                            project_ids,
                            field_schema["field"],
                            metric_type_by_id.get(property_id, "system_metric"),
                            search_query=search_query,
                            deadline=grounding_deadline,
                        )

                elif source == "simulation":
                    with _bounded_ai_filter_postgres(grounding_deadline):
                        simulation_scope = _resolve_simulation_scope(
                            request.workspace,
                            agent_definition_id=payload.get("agent_definition_id"),
                            run_test_id=payload.get("run_test_id"),
                            test_execution_id=payload.get("test_execution_id"),
                        )
                        schema = _authorize_smart_property_schema(
                            schema,
                            source=source,
                            workspace=request.workspace,
                            simulation_scope=simulation_scope,
                        )
                    schema_by_identity = {_ai_schema_identity(s): s for s in schema}

                    def fetch_values(property_id, *, search_query):
                        field_schema = schema_by_identity.get(property_id)
                        if not field_schema:
                            raise _grounding_too_broad()
                        category = field_schema.get("category") or "system"
                        return _fetch_simulation_field_values(
                            simulation_scope,
                            field_schema["field"],
                            ("eval_metric" if category == "eval" else "system_metric"),
                            eval_key=field_schema.get("_simulation_eval_key"),
                            search_query=search_query,
                            deadline=grounding_deadline,
                        )

                elif source == "dataset":
                    # Smart mode for dataset rows: scope to one dataset and
                    # look up per-column distinct cell values so the LLM can
                    # fuzzy-match the user's wording against real data.
                    raw_dataset_id = payload.get("dataset_id") or payload.get(
                        "project_id"
                    )
                    with _bounded_ai_filter_postgres(grounding_deadline):
                        dataset_id = _resolve_dataset_id(
                            request.workspace, raw_dataset_id
                        )
                        if not dataset_id:
                            return self._gm.bad_request(
                                "dataset_id not found in workspace"
                            )
                        schema = _authorize_smart_property_schema(
                            schema,
                            source=source,
                            workspace=request.workspace,
                            dataset_id=dataset_id,
                        )
                    schema_by_identity = {_ai_schema_identity(s): s for s in schema}

                    def fetch_values(property_id, *, search_query):
                        field_schema = schema_by_identity.get(property_id)
                        if not field_schema:
                            raise _grounding_too_broad()
                        return _fetch_dataset_column_values(
                            dataset_id,
                            field_schema["field"],
                            search_query=search_query,
                            deadline=grounding_deadline,
                        )

                else:
                    return self._gm.bad_request(
                        "smart mode supports source='traces', 'sessions', "
                        "'simulation', or 'dataset'"
                    )

                filters = _run_smart_agent(
                    query,
                    schema,
                    fetch_values,
                    deadline=grounding_deadline,
                )
                _remaining_request_ms(request_deadline)
                return self._gm.success_response({"filters": filters})

            # Build the user message with schema context. Compact large
            # `choices` lists so the LLM doesn't get paralyzed when an enum
            # has hundreds of values — send a sample plus the total count,
            # and rely on the server-side fuzzy resolver to map whatever
            # the LLM emits to the real choices.
            CHOICES_SAMPLE_CAP = 30
            compact_schema = []
            for s in schema:
                if not isinstance(s, dict):
                    continue
                entry = dict(s)
                ch = entry.get("choices")
                if isinstance(ch, list) and len(ch) > CHOICES_SAMPLE_CAP:
                    entry["choices_sample"] = ch[:CHOICES_SAMPLE_CAP]
                    entry["choices_total"] = len(ch)
                    entry["choices_note"] = (
                        f"Only {CHOICES_SAMPLE_CAP} of {len(ch)} values shown. "
                        "If the user names something not in this sample, "
                        "still emit it as the value (with operator 'is' for "
                        "exact intent or 'contains' for substring). The "
                        "backend will fuzzy-match the real value."
                    )
                    entry.pop("choices", None)
                compact_schema.append(entry)
            schema_desc = json.dumps(compact_schema, indent=2)
            user_message = f"Filter schema:\n{schema_desc}\n\nUser query: {query}"

            system_prompt = (
                SELECT_FIELDS_PROMPT if mode == "select_fields" else SYSTEM_PROMPT
            )

            # Route through the in-house LLM wrapper (Agentcc gateway with
            # litellm fallback).
            from agentic_eval.core.llm.llm import LLM
            from agentic_eval.core.utils.model_config import ModelConfigs

            cfg = ModelConfigs.VERTEX_GEMINI_2_5_FLASH
            llm = LLM(
                provider=cfg.provider,
                model_name=cfg.model_name,
                temperature=0.0,
                max_tokens=500,
            )
            raw_text = _bounded_completion_content(
                llm,
                [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_message},
                ],
                deadline=request_deadline,
            )

            # Unwrap any ```json ... ``` fence the model added, then parse.
            try:
                parsed = json.loads(strip_code_fence(raw_text))
            except json.JSONDecodeError:
                logger.warning("ai_filter_json_parse_error", mode=mode)
                _remaining_request_ms(request_deadline)
                if mode == "select_fields":
                    return self._gm.success_response({"fields": []})

                # Preserve the query-direct recovery only while the shared
                # request wall is live. An expired request must fail closed.
                fallback = []
                tokens = _query_token_phrases(query)
                for f_schema in schema:
                    if not isinstance(f_schema, dict):
                        continue
                    choices = f_schema.get("choices") or []
                    if not choices:
                        continue
                    f_labels = f_schema.get("choice_labels") or {}
                    for tok in tokens:
                        match = _resolve_choice(tok, choices, f_labels)
                        if match is not None:
                            fallback.append(
                                {
                                    "field": f_schema.get("field"),
                                    "operator": "is",
                                    "value": match,
                                }
                            )
                _remaining_request_ms(request_deadline)
                return self._gm.success_response({"filters": fallback})

            if mode == "select_fields":
                fields_out = []
                if isinstance(parsed, dict):
                    raw_fields = parsed.get("fields", [])
                elif isinstance(parsed, list):
                    raw_fields = parsed
                else:
                    raw_fields = []
                schema_by_identity = {
                    _ai_schema_identity(s): s
                    for s in schema
                    if isinstance(s, dict) and _ai_schema_identity(s)
                }
                identities_by_native = {}
                for identity, field_schema in schema_by_identity.items():
                    identities_by_native.setdefault(
                        field_schema.get("field"), []
                    ).append(identity)
                for f in raw_fields:
                    if not isinstance(f, str):
                        continue
                    identity = f if f in schema_by_identity else None
                    if identity is None:
                        native_matches = identities_by_native.get(f, [])
                        identity = (
                            native_matches[0] if len(native_matches) == 1 else None
                        )
                    if identity and identity not in fields_out:
                        fields_out.append(identity)
                _remaining_request_ms(request_deadline)
                return self._gm.success_response({"fields": fields_out})

            filters = parsed if isinstance(parsed, list) else []

            # Validate each filter against the schema
            field_map = {
                _ai_schema_identity(s): s
                for s in schema
                if isinstance(s, dict) and _ai_schema_identity(s)
            }
            identities_by_native = {}
            for identity, field_schema in field_map.items():
                identities_by_native.setdefault(field_schema.get("field"), []).append(
                    identity
                )
            validated = []
            for f in filters:
                submitted_identity = str(f.get("property_id") or f.get("field") or "")
                operator = f.get("operator")
                value = f.get("value")

                field_schema = field_map.get(submitted_identity)
                if field_schema is None:
                    native_matches = identities_by_native.get(f.get("field"), [])
                    if len(native_matches) != 1:
                        continue
                    field_schema = field_map[native_matches[0]]
                allowed_ops = field_schema.get("operators", [])
                if allowed_ops and operator not in allowed_ops:
                    # Soft-allow `is_not` and `contains` on string fields even
                    # if the caller's per-field op list omitted them — the
                    # SYSTEM_PROMPT explicitly tells the LLM to use them for
                    # negation and substring fallback, and rejecting the
                    # filter here would re-introduce the empty-result bug.
                    ftype = field_schema.get("type") or "string"
                    if ftype in ("string", "enum") and operator in (
                        "is_not",
                        "contains",
                        "not_contains",
                        "is",
                    ):
                        pass
                    else:
                        continue

                choices = field_schema.get("choices", [])
                choice_labels = field_schema.get("choice_labels") or {}
                if choices:
                    if value not in choices:
                        match = _resolve_choice(value, choices, choice_labels)
                        if match is not None:
                            value = match
                        elif operator in ("contains", "not_contains"):
                            # Substring/fuzzy operators don't need the value
                            # to be in the enum list — the LLM is searching.
                            pass
                        else:
                            continue

                condition = {
                    "field": field_schema["field"],
                    "operator": operator,
                    "value": value,
                }
                if field_schema.get("property_id"):
                    condition["property_id"] = field_schema["property_id"]
                validated.append(condition)

            # Last-resort fallback: if the LLM returned nothing AND the
            # schema has at least one long-choices field, try to resolve
            # the user's query directly against each enum's choices using
            # the same fuzzy matcher. This catches cases where the LLM
            # was paralyzed by a long choices list and refused to emit.
            _remaining_request_ms(request_deadline)
            if not validated:
                tokens = _query_token_phrases(query)
                for f_schema in schema:
                    if not isinstance(f_schema, dict):
                        continue
                    choices = f_schema.get("choices") or []
                    if not choices:
                        continue
                    f_labels = f_schema.get("choice_labels") or {}
                    for tok in tokens:
                        match = _resolve_choice(tok, choices, f_labels)
                        if match is not None:
                            condition = {
                                "field": f_schema.get("field"),
                                "operator": "is",
                                "value": match,
                            }
                            if f_schema.get("property_id"):
                                condition["property_id"] = f_schema["property_id"]
                            validated.append(condition)

            _remaining_request_ms(request_deadline)
            return self._gm.success_response({"filters": validated})

        except AIFilterUnavailableError as exc:
            logger.warning(
                "ai_filter_unavailable",
                mode=mode,
                code=exc.code,
                status_code=exc.status_code,
            )
            return self._gm.custom_error_response(
                exc.status_code,
                exc.public_message,
                code=exc.code,
            )
        except SmartFilterGroundingError as exc:
            logger.warning(
                "ai_filter_grounding_refused",
                mode=mode,
                code=exc.code,
                status_code=exc.status_code,
            )
            return self._gm.custom_error_response(
                exc.status_code,
                exc.public_message,
                code=exc.code,
            )
        except Exception as exc:
            logger.error(
                "ai_filter_request_failed",
                mode=mode,
                error_type=type(exc).__name__,
            )
            unavailable = _ai_filter_unavailable()
            return self._gm.custom_error_response(
                unavailable.status_code,
                unavailable.public_message,
                code=unavailable.code,
            )
