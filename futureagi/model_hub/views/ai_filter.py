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
    runs a Haiku tool-use loop where the LLM autonomously calls
    `get_field_values(field_id)` for the fields it needs to ground its
    answer, then submits the final filter via `submit_filter`. One HTTP
    round trip — LLM does the orchestration. Used by the trace filter.
"""

import json
import traceback

import structlog
from drf_yasg.utils import swagger_auto_schema
from rest_framework.permissions import IsAuthenticated
from rest_framework.views import APIView

from model_hub.serializers.ai_filter import (
    AIFilterRequestSerializer,
    AIFilterResponseSerializer,
)
from tfc.utils.api_serializers import ApiErrorResponseSerializer
from tfc.utils.general_methods import GeneralMethods

logger = structlog.get_logger(__name__)

ERROR_RESPONSES = {
    400: ApiErrorResponseSerializer,
    500: ApiErrorResponseSerializer,
}

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

Each field in the schema may already have its real values inlined as a `v` array — in that case, pick straight from that list, do NOT call any tool. Only when a field is marked `v_searchable: true` (high-cardinality) do you need to fetch values, and you must pass a `search_query` to fuzzy-rank the values.

You have two tools:
1. get_field_values(field_id, search_query?) — fetches real values for a field. SKIP this entirely for fields whose `v` is already inlined. For fields with `v_searchable: true`, you MUST pass a search_query — the backend will rank values by exact > prefix > substring > token overlap > fuzzy n-gram and return the top 20 matches. Example: get_field_values("model", search_query="gpt-4") on a project with 200 model strings will return only the gpt-4 family.
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

f. **Empty-field fallback.** If a field is marked `v_empty: true`, use the user's literal value with operator `is` (or `contains` for partial matches). The user's intent is more important than whether the result set will be empty.

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

# CH column expressions for the system metric → spans table mapping.
# Mirrors tracer.views.dashboard.DashboardViewSet.filter_values to keep
# the smart agent self-contained without coupling to that viewset.
_TRACE_SYSTEM_COL_MAP = {
    "project": "toString(project_id)",
    "model": "model",
    "status": "status",
    "provider": "provider",
    "observation_type": "observation_type",
    "span_kind": "observation_type",
    "service_name": "name",
    "session": "trace_session_id",
    "user": "toString(end_user_id)",
    "tag": "arrayJoin(trace_tags)",
    "prompt_name": "dictGet('prompt_dict', 'prompt_name', prompt_version_id)",
    "prompt_version": "dictGet('prompt_dict', 'template_version', prompt_version_id)",
    "prompt_label": "dictGet('prompt_label_dict', 'name', prompt_label_id)",
}


def _fetch_trace_field_values(project_ids, metric_name, metric_type):
    """Distinct values for a field in the spans table.

    Returns a list of strings. Empty on miss (unknown field, query failure,
    or no rows). Capped at 100 to keep the LLM context small.
    """
    from tracer.services.clickhouse.client import is_clickhouse_enabled
    from tracer.services.clickhouse.query_service import (
        AnalyticsQueryService,
    )

    if not is_clickhouse_enabled() or not project_ids:
        return []

    analytics = AnalyticsQueryService()
    try:
        if metric_type == "system_metric":
            col_expr = _TRACE_SYSTEM_COL_MAP.get(metric_name)
            if not col_expr:
                return []
            sql = (
                f"SELECT DISTINCT {col_expr} AS val "
                f"FROM spans "
                f"WHERE project_id IN %(project_ids)s "
                f"AND _peerdb_is_deleted = 0 "
                f"AND {col_expr} != '' "
                f"ORDER BY val "
                f"LIMIT 100"
            )
            result = analytics.execute_ch_query(
                sql, {"project_ids": project_ids}, timeout_ms=5000
            )
        elif metric_type == "custom_attribute":
            sql = (
                "SELECT DISTINCT span_attr_str[%(attr_key)s] AS val "
                "FROM spans "
                "WHERE project_id IN %(project_ids)s "
                "AND _peerdb_is_deleted = 0 "
                "AND span_attr_str[%(attr_key)s] != '' "
                "ORDER BY val "
                "LIMIT 100"
            )
            result = analytics.execute_ch_query(
                sql,
                {"project_ids": project_ids, "attr_key": metric_name},
                timeout_ms=5000,
            )
        else:
            return []
        return [row["val"] for row in result.data if row.get("val")]
    except Exception as e:
        logger.warning(
            "smart_filter_values_failed",
            metric_name=metric_name,
            metric_type=metric_type,
            error=str(e)[:200],
        )
        return []


# Fields where the value list can grow into the thousands (user ids,
# free-form tags, model strings, span/service names). We never inline
# their values in the initial prompt — too many tokens. Instead the LLM
# can call `get_field_values` with a `search_query` to fuzzy-match.
_HIGH_CARDINALITY_FIELDS = {
    "user",
    "session",
    "tag",
    "model",
    "service_name",
    "prompt_name",
    "prompt_version",
    "prompt_label",
}

# Cap on values we inline per field. Larger lists get withheld and the
# LLM must use the search tool to retrieve relevant slices.
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


def _smart_search_values(values, query, limit=20):
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


def _fetch_dataset_column_values(dataset_id, column_id):
    """Distinct cell values for a (dataset, column) pair from ClickHouse.

    Mirrors `_fetch_trace_field_values` but reads `model_hub_cell`. For
    array / json columns the raw cell blob is parsed and its elements
    are emitted so the LLM can ground against e.g. "English" instead of
    '["English","French"]'. Returns up to 100 strings.

    NOTE: ownership is validated by the caller (which resolves the
    dataset against the workspace before calling this).
    """
    import json as _json

    from tracer.services.clickhouse.client import is_clickhouse_enabled
    from tracer.services.clickhouse.query_service import (
        AnalyticsQueryService,
    )

    if not is_clickhouse_enabled() or not dataset_id or not column_id:
        return []

    # Look up the column's data_type so we know whether to flatten.
    try:
        from model_hub.models.develop_dataset import Column

        column = Column.objects.only("data_type").get(
            id=column_id, dataset_id=dataset_id, deleted=False
        )
        data_type = column.data_type
    except Exception:
        data_type = "text"

    analytics = AnalyticsQueryService()
    try:
        sql = (
            "SELECT DISTINCT value AS val "
            "FROM model_hub_cell FINAL "
            "WHERE _peerdb_is_deleted = 0 "
            "AND dataset_id = toUUID(%(dataset_id)s) "
            "AND column_id = toUUID(%(column_id)s) "
            "AND value != '' "
            "ORDER BY val "
            "LIMIT 200"
        )
        result = analytics.execute_ch_query(
            sql,
            {"dataset_id": str(dataset_id), "column_id": str(column_id)},
            timeout_ms=5000,
        )
        raw = [row["val"] for row in result.data if row.get("val")]
    except Exception as e:
        logger.warning(
            "dataset_column_values_query_failed",
            dataset_id=str(dataset_id),
            column_id=str(column_id),
            error=str(e)[:200],
        )
        return []

    if data_type not in ("array", "json"):
        return raw[:100]

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
                if isinstance(elem, (str, int, float, bool)):
                    candidates.append(str(elem))
                elif isinstance(elem, dict):
                    for v in elem.values():
                        if isinstance(v, (str, int, float)):
                            candidates.append(str(v))
        elif isinstance(parsed, dict):
            for v in parsed.values():
                if isinstance(v, (str, int, float)):
                    candidates.append(str(v))
        else:
            candidates.append(blob)
        for c in candidates:
            s = c.strip()
            if s and s not in seen:
                seen.add(s)
                out.append(s)
            if len(out) >= 100:
                break
        if len(out) >= 100:
            break
    return out


def _resolve_project_ids(workspace, raw_project_id):
    """Validate that the requested project belongs to the workspace.

    Returns a list of project ids (single-element if the caller named one,
    or all workspace projects if none was given).
    """
    from tracer.models.project import Project

    workspace_ids = {
        str(pid)
        for pid in Project.objects.filter(workspace=workspace).values_list(
            "id", flat=True
        )
    }
    if raw_project_id and str(raw_project_id) in workspace_ids:
        return [str(raw_project_id)]
    if raw_project_id:
        return []  # caller asked for a project they don't own
    return list(workspace_ids)


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
    except Exception:
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


def _validate_smart_filters(parsed_filters, schema):
    """Apply field/operator/choice validation for smart-mode output.

    The smart-mode prompt drops per-field operator lists from the LLM
    payload (the operator legend is in the system prompt by type), so
    validation here checks against the type-default operator set rather
    than each field's declared list. Field choices, when supplied, still
    constrain the value with case-insensitive matching.
    """
    if not isinstance(parsed_filters, list):
        return []
    field_map = {s["field"]: s for s in schema if isinstance(s, dict)}
    out = []
    for f in parsed_filters:
        if not isinstance(f, dict):
            continue
        field = f.get("field")
        operator = f.get("operator") or "is"
        value = f.get("value")
        if field not in field_map:
            continue
        field_schema = field_map[field]
        ftype = field_schema.get("type") or "string"
        if ftype == "number":
            if operator not in _NUMBER_OPS_ALWAYS_ALLOWED:
                continue
        else:
            if operator not in _STRING_OPS_ALWAYS_ALLOWED:
                continue
        choices = field_schema.get("choices") or []
        if choices and value not in choices:
            match = next(
                (c for c in choices if str(c).lower() == str(value).lower()),
                None,
            )
            if match is not None:
                value = match
            # Note: for smart mode we do NOT drop the filter when the
            # value is missing from choices — the LLM may have been told
            # by `get_field_values` what the real values look like, and
            # is using `contains` or a substring on purpose.
        out.append({"field": field, "operator": operator, "value": value})
    return out


def _run_smart_agent(query, schema, fetch_values):
    """Run the Haiku tool-use loop. Returns a list of validated filters.

    `fetch_values(field_id) -> list[str]` is the source-specific value
    lookup. Traces pass a closure over `_fetch_trace_field_values`;
    datasets pass one over `_fetch_dataset_column_values`. The agent
    loop itself is shared and source-agnostic.

    Uses the in-house LLM wrapper (`agentic_eval.core.llm.llm.LLM`) which
    routes through the Agentcc gateway with litellm fallback, so we don't
    talk to Bedrock directly here.
    """
    from agentic_eval.core.llm.llm import LLM
    from agentic_eval.core.utils.model_config import ModelConfigs

    haiku_cfg = ModelConfigs.HAIKU_4_5_BEDROCK_ARN
    llm = LLM(
        provider=haiku_cfg.provider,
        model_name=haiku_cfg.model_name,
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
        label = s.get("label")
        cat = s.get("category") or "system"
        ftype = s.get("type") or "string"
        entry = {"f": fid, "t": ftype}
        if label and label != fid:
            entry["l"] = label
        if cat != "system":
            entry["c"] = cat
        compact_fields.append(entry)

    schema_by_id = {
        s["field"]: s for s in schema if isinstance(s, dict) and s.get("field")
    }

    # ------------------------------------------------------------------
    # Pre-fetch values for low-cardinality string fields and inline them
    # in the field schema. The LLM sees real values upfront and usually
    # doesn't need to call get_field_values at all.
    #
    # We skip:
    #   - non-string fields (numerics don't have enumerable values)
    #   - high-cardinality string fields (free-form id/tag/model namespaces
    #     where the value list can grow unboundedly)
    # ------------------------------------------------------------------
    inlined_value_count = 0
    for entry in compact_fields:
        fid = entry["f"]
        if entry["t"] != "string":
            continue
        if fid in _HIGH_CARDINALITY_FIELDS:
            continue
        try:
            vals = fetch_values(fid)
        except Exception:
            vals = []
        if not vals:
            # The field exists in the schema but has no rows in CH yet.
            # Tell the LLM explicitly so it can still emit a literal-value
            # filter (the user's intent matters even when the result set
            # would be empty — e.g. "show voicemails" on a fresh project).
            entry["v_empty"] = True
            continue
        if len(vals) <= _INLINE_VALUE_CAP:
            entry["v"] = vals
            inlined_value_count += len(vals)
        else:
            # Too many to inline — flag it so the LLM knows to use the
            # search tool for this field instead of guessing.
            entry["v_count"] = len(vals)
            entry["v_searchable"] = True

    tools = [
        {
            "type": "function",
            "function": {
                "name": "get_field_values",
                "description": (
                    "Return the distinct real values for a field in the project. "
                    "USE THIS when you need to ground a value against real data "
                    "and the field schema doesn't already inline its values "
                    "(check the `v` array on each field — if present, the field's "
                    "real values are already there and you don't need to call "
                    "this tool). For high-cardinality fields (those with "
                    "v_searchable: true), pass a search_query so the backend can "
                    "fuzzy-rank the values for you and return only the relevant "
                    "ones — never request the full list, it can be thousands."
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
                                "Optional substring/keyword to rank values by. "
                                "Required for high-cardinality fields. The "
                                "backend ranks by exact > prefix > substring > "
                                "token overlap > char n-gram fuzzy. Returns at "
                                "most 20 ranked results."
                            ),
                        },
                    },
                    "required": ["field_id"],
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
        "f=field id, "
        "t=type, "
        "l=human label (omitted when same as id), "
        "c=category (omitted when 'system'), "
        "v=array of all real distinct values for this field "
        "(already pre-fetched — pick straight from this list, no tool call needed), "
        "v_count=total distinct value count for high-cardinality fields, "
        "v_searchable=true means the value list is too big to inline, "
        "call get_field_values(field_id, search_query) to fuzzy-search it, "
        "v_empty=true means no rows exist yet for this field — you may "
        "still emit a literal-value filter from the user's wording (the "
        "result will be empty but the intent is preserved)."
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
        response = llm._get_completion_with_tools(messages, tools)
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
                    try:
                        vals = fetch_values(fid)
                    except Exception:
                        vals = []
                else:
                    vals = []
                if search_query and vals:
                    ranked = _smart_search_values(vals, search_query, limit=20)
                    tool_result = {
                        "field_id": fid,
                        "search_query": search_query,
                        "total_distinct": len(vals),
                        "values": ranked,
                    }
                else:
                    # No search query: return up to 50 values to keep the
                    # tool result small. Anything bigger should have been
                    # inlined upfront or queried with a search_query.
                    tool_result = {
                        "field_id": fid,
                        "total_distinct": len(vals),
                        "values": vals[:50],
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
    return _validate_smart_filters(submitted, schema)


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

    @swagger_auto_schema(
        request_body=AIFilterRequestSerializer,
        responses={200: AIFilterResponseSerializer, **ERROR_RESPONSES},
    )
    def post(self, request, *args, **kwargs):
        mode = "build_filters"  # default — referenced by except blocks below
        try:
            mode = request.data.get("mode", "build_filters")
            query = request.data.get("query", "").strip()
            schema = request.data.get("schema", [])

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
                source = request.data.get("source", "traces")
                if source == "traces":
                    project_id = request.data.get("project_id")
                    project_ids = _resolve_project_ids(request.workspace, project_id)
                    if project_id and not project_ids:
                        return self._gm.bad_request("project not found in workspace")
                    metric_type_by_id = {
                        s.get("field"): {
                            "system": "system_metric",
                            "eval": "eval_metric",
                            "annotation": "annotation_metric",
                            "attribute": "custom_attribute",
                        }.get(s.get("category") or "system", "system_metric")
                        for s in schema
                        if isinstance(s, dict) and s.get("field")
                    }

                    def fetch_values(field_id):
                        return _fetch_trace_field_values(
                            project_ids,
                            field_id,
                            metric_type_by_id.get(field_id, "system_metric"),
                        )

                elif source == "dataset":
                    # Smart mode for dataset rows: scope to one dataset and
                    # look up per-column distinct cell values so the LLM can
                    # fuzzy-match the user's wording against real data.
                    raw_dataset_id = request.data.get("dataset_id") or request.data.get(
                        "project_id"
                    )
                    dataset_id = _resolve_dataset_id(request.workspace, raw_dataset_id)
                    if not dataset_id:
                        return self._gm.bad_request("dataset_id not found in workspace")

                    def fetch_values(field_id):
                        return _fetch_dataset_column_values(dataset_id, field_id)

                else:
                    return self._gm.bad_request(
                        "smart mode supports source='traces' or 'dataset'"
                    )

                filters = _run_smart_agent(query, schema, fetch_values)
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
            # litellm fallback) so we don't talk to Bedrock directly.
            from agentic_eval.core.llm.llm import LLM
            from agentic_eval.core.utils.model_config import ModelConfigs

            haiku_cfg = ModelConfigs.HAIKU_4_5_BEDROCK_ARN
            llm = LLM(
                provider=haiku_cfg.provider,
                model_name=haiku_cfg.model_name,
                temperature=0.0,
                max_tokens=500,
            )
            raw_text = llm._get_completion_content(
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_message},
                ],
            ).strip()

            # Parse the JSON response
            # Handle cases where the model wraps in ```json ... ```
            if raw_text.startswith("```"):
                raw_text = raw_text.split("```")[1]
                if raw_text.startswith("json"):
                    raw_text = raw_text[4:]
                raw_text = raw_text.strip()

            parsed = json.loads(raw_text)

            if mode == "select_fields":
                fields_out = []
                if isinstance(parsed, dict):
                    raw_fields = parsed.get("fields", [])
                elif isinstance(parsed, list):
                    raw_fields = parsed
                else:
                    raw_fields = []
                schema_ids = {s.get("field") for s in schema if isinstance(s, dict)}
                for f in raw_fields:
                    if isinstance(f, str) and f in schema_ids and f not in fields_out:
                        fields_out.append(f)
                return self._gm.success_response({"fields": fields_out})

            filters = parsed if isinstance(parsed, list) else []

            # Validate each filter against the schema
            field_map = {s["field"]: s for s in schema}
            validated = []
            for f in filters:
                field = f.get("field")
                operator = f.get("operator")
                value = f.get("value")

                if field not in field_map:
                    continue

                field_schema = field_map[field]
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

                validated.append(
                    {
                        "field": field,
                        "operator": operator,
                        "value": value,
                    }
                )

            # Last-resort fallback: if the LLM returned nothing AND the
            # schema has at least one long-choices field, try to resolve
            # the user's query directly against each enum's choices using
            # the same fuzzy matcher. This catches cases where the LLM
            # was paralyzed by a long choices list and refused to emit.
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
                            validated.append(
                                {
                                    "field": f_schema.get("field"),
                                    "operator": "is",
                                    "value": match,
                                }
                            )

            return self._gm.success_response({"filters": validated})

        except json.JSONDecodeError as e:
            logger.warning(f"AI filter JSON parse error: {e}")
            if mode == "select_fields":
                return self._gm.success_response({"fields": []})
            # Run the same query-direct fallback as the empty-validated path
            # so the user still gets *some* answer when the LLM returned
            # malformed JSON for a long choices list.
            try:
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
                return self._gm.success_response({"filters": fallback})
            except Exception:
                return self._gm.success_response({"filters": []})
        except Exception as e:
            logger.error(f"Error in AIFilterView: {str(e)}\n{traceback.format_exc()}")
            return self._gm.bad_request(f"AI filter error: {str(e)}")
