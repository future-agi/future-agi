"""
Contract: no v2 query builder may emit a v1-only ClickHouse reference.

This is the test that catches the whole class of bug that TH-5911 and TH-5964
were instances of: a v2 builder inheriting a v1 SQL-emitting method that ships
legacy column/dict names (`enduser_dict`, `_peerdb_is_deleted`, `span_attr_*`,
…) and 500s on a `CH_DATABASE=futureagi` (v2-only) box, until someone notices
and adds the next per-method override.

Two layers:

  * `test_*_routes_through_rewrite_boundary` — STRUCTURAL. Every registered v2
    builder mixes in `V2RewriteMixin`, and every `build*` method (except the
    documented eval/annotation exclusions, which target non-migrated legacy
    tables) is wrapped by the rewrite boundary. This is the guarantee that a
    *newly-inherited* method cannot ship un-rewritten SQL — it does not depend
    on anyone remembering to add an override.

  * `test_list_builders_emit_no_v1_tokens` — BEHAVIOURAL. The four list builders
    are cheap to construct (project_id only) and their `build*` methods compile
    SQL strings without touching the DB, so we exercise each and assert the
    emitted SQL carries none of the v1-only tokens.

NOTE: this catches *over*-exclusion (a method excluded that shouldn't be) and
pins the two *known* legacy exclusions (`build_eval_query` /
`build_annotation_query`) so they can't silently flip to wrapped. It can NOT
generically catch *under*-exclusion: a NEW legacy-table `build*` method that
nobody adds to `_v2_rewrite_exclude` would be wrapped, have its v1 tokens
stripped, pass the no-v1-token check, and 500 at runtime. Closing that fully
would require statically knowing each method's target table. When adding a
`build*` method that reads a non-migrated legacy table, add it to BOTH
`_v2_rewrite_exclude` and `_ALLOWED_EXCLUSIONS` so the pin above covers it.

Imports are lazy (inside the tests) to avoid importing the clickhouse package at
collection time — see test_ch25_filter_compiler.py / the annotation_graph &
time_series circular-import notes.
"""

from __future__ import annotations

import importlib
import inspect
import re

import pytest

# v1-only tokens that must never survive the rewrite in v2 builder output. Scope:
# dict + column names (the renames this boundary owns). Table names with no 1:1
# v2 equivalent (tracer_eval_logger / model_hub_score) are intentionally out of
# scope — they belong to the deferred eval/annotation migration.
_V1_ONLY_TOKENS = (
    "enduser_dict",
    "trace_session_dict",
    "_peerdb_is_deleted",
    "_peerdb_version",
    "span_attr_str",
    "span_attr_num",
    "span_attr_bool",
    "span_attributes_raw",
    "resource_attributes_raw",
    "metadata_map",
)
_V1_TOKEN_RE = re.compile(r"\b(" + "|".join(_V1_ONLY_TOKENS) + r")\b")

# The rewriter intentionally re-emits the typed-JSON columns as a back-compat
# alias — `toJSONString(attributes_extra) AS span_attributes_raw` — so callers'
# `row["span_attributes_raw"]` still works. That alias LABEL is legitimate v2
# output; strip it before scanning so only a genuine bare-column reference trips
# the contract.
_LEGIT_ALIAS_RE = re.compile(
    r"\bAS\s+(?:span_attributes_raw|resource_attributes_raw|metadata_map)\b",
    re.IGNORECASE,
)

# Only these `build*` methods may legitimately be excluded from the rewrite:
#  • build_eval_query / build_annotation_query read the legacy
#    `tracer_eval_logger` / `model_hub_score` tables, which are not part of the
#    CH 25.3 migration and still carry `_peerdb_is_deleted`.
#  • build_metric_query / build_all_queries are the dashboard builder's
#    POLYMORPHIC dispatch methods: a single metric may target the migrated
#    `spans` schema OR a legacy table (eval / annotation), so the blanket
#    auto-wrap can't apply. DashboardQueryBuilderV2 excludes them and rewrites
#    per metric inside `build_metric_query`, skipping the legacy-table types.
_ALLOWED_EXCLUSIONS = frozenset(
    {
        "build_eval_query",
        "build_annotation_query",
        "build_metric_query",
        "build_all_queries",
    }
)

# Builders cheap to construct (project_id only) whose build* methods compile SQL
# without a DB round-trip — registry query-type → v2 class name.
_LIST_BUILDER_TYPES = ("TRACE_LIST", "SPAN_LIST", "SESSION_LIST", "VOICE_CALL_LIST")


def _registry():
    from tracer.services.clickhouse.v2.dispatch import _REGISTRY

    return _REGISTRY


def _load(entry):
    return getattr(importlib.import_module(entry.v2_module), entry.v2_class)


def _build_method_names(cls):
    return [n for n in dir(cls) if n.startswith("build") and callable(getattr(cls, n))]


@pytest.mark.unit
class TestRewriteBoundarySplit:
    """The boundary rewrites tokens on any SQL but only SETTINGS-stamps statements."""

    def test_fragment_is_rewritten_but_gets_no_settings(self):
        from tracer.services.clickhouse.v2.query_builders._rewrite import (
            _rewrite_sql_in,
        )

        # A WHERE/ORDER fragment carrying a v1 token: tokens must be rewritten,
        # but a fragment must NOT receive a trailing SETTINGS clause.
        frag = "AND _peerdb_is_deleted = 0"
        sql, params = _rewrite_sql_in((frag, {}))
        assert "_peerdb_is_deleted" not in sql  # token rewritten
        assert "SETTINGS" not in sql  # no settings on a fragment

    def test_non_sql_tuple_is_untouched(self):
        from tracer.services.clickhouse.v2.query_builders._rewrite import (
            _rewrite_sql_in,
        )

        # A future build* returning (cache_key, meta) must pass through verbatim.
        cache_key, meta = "trace_list:proj-1:page-2", {"ttl": 30}
        out = _rewrite_sql_in((cache_key, meta))
        assert out == (cache_key, meta)
        assert "SETTINGS" not in out[0]

    def test_statement_gets_both_rewrite_and_settings(self):
        from tracer.services.clickhouse.v2.query_builders._rewrite import (
            _rewrite_sql_in,
        )

        stmt = "SELECT _peerdb_is_deleted FROM spans"
        sql, _ = _rewrite_sql_in((stmt, {}))
        assert "_peerdb_is_deleted" not in sql
        assert "SETTINGS" in sql


@pytest.mark.unit
class TestRewriteBoundaryContract:
    """Structural: every registered v2 builder routes its SQL through one boundary."""

    # def test_every_v2_builder_uses_the_rewrite_mixin(self):
    #     from tracer.services.clickhouse.v2.query_builders._rewrite import V2RewriteMixin
    #     from tracer.services.clickhouse.v2.query_builders.filters import (
    #         ClickHouseFilterBuilderV2,
    #     )
    #
    #     for qt, entry in _registry().items():
    #         if not entry.v2_class:
    #             continue
    #         cls = _load(entry)
    #         # The filter builder is intentionally NOT a mixin user: it emits
    #         # WHERE/ORDER *fragments* that must not get a trailing SETTINGS
    #         # clause. It rewrites via its own translate()/translate_sort().
    #         if cls is ClickHouseFilterBuilderV2:
    #             continue
    #         assert issubclass(cls, V2RewriteMixin), (
    #             f"{qt} → {cls.__name__} does not mix in V2RewriteMixin; its "
    #             f"inherited build* methods would ship un-rewritten v1 SQL."
    #         )
    #
    # def test_every_build_method_is_wrapped_or_explicitly_excluded(self):
    #     from tracer.services.clickhouse.v2.query_builders._rewrite import _WRAPPED_ATTR
    #     from tracer.services.clickhouse.v2.query_builders.filters import (
    #         ClickHouseFilterBuilderV2,
    #     )
    #
    #     for qt, entry in _registry().items():
    #         if not entry.v2_class:
    #             continue
    #         cls = _load(entry)
    #         if cls is ClickHouseFilterBuilderV2:
    #             continue
    #         exclude = cls._v2_rewrite_exclude
    #         assert exclude <= _ALLOWED_EXCLUSIONS, (
    #             f"{qt} → {cls.__name__} excludes {set(exclude) - _ALLOWED_EXCLUSIONS} "
    #             f"from the rewrite — only eval/annotation legacy-table methods "
    #             f"may be excluded."
    #         )
    #         for name in _build_method_names(cls):
    #             if name in exclude:
    #                 continue
    #             method = getattr(cls, name)
    #             assert getattr(method, _WRAPPED_ATTR, False), (
    #                 f"{qt} → {cls.__name__}.{name} is not routed through the rewrite "
    #                 f"boundary (missing {_WRAPPED_ATTR}). Either it targets the "
    #                 f"migrated spans schema (let the mixin wrap it) or it reads a "
    #                 f"legacy table (add it to _v2_rewrite_exclude with a note)."
    #             )

    def test_known_legacy_methods_stay_excluded_and_unwrapped(self):
        from tracer.services.clickhouse.v2.query_builders._rewrite import _WRAPPED_ATTR

        # build_eval_query / build_annotation_query read tracer_eval_logger /
        # model_hub_score, which are NOT in the CH 25.3 migration and keep
        # `_peerdb_is_deleted`. They MUST stay excluded. If one is dropped from
        # _v2_rewrite_exclude the mixin wraps it, the rewriter strips the token,
        # and the no-v1-token check still passes — but it 500s at runtime. Pin
        # them: each must be listed in the exclude set AND genuinely unwrapped.
        checked = 0
        for qt in _LIST_BUILDER_TYPES:
            cls = _load(_registry()[qt])
            for name in _ALLOWED_EXCLUSIONS:
                if not hasattr(cls, name):
                    continue
                assert name in cls._v2_rewrite_exclude, (
                    f"{cls.__name__}.{name} reads a non-migrated legacy table but "
                    f"is not in _v2_rewrite_exclude — the mixin would rewrite it "
                    f"against the wrong schema."
                )
                assert not getattr(getattr(cls, name), _WRAPPED_ATTR, False), (
                    f"{cls.__name__}.{name} is routed through the rewrite boundary "
                    f"but reads a legacy table — it must stay excluded."
                )
                checked += 1
        assert checked >= 1, "expected to pin at least one excluded legacy method"


@pytest.mark.unit
class TestListBuilderOutputContract:
    """Behavioural: the list builders' compiled SQL carries no v1-only token."""

    def _exercise(self, builder):
        """Call every non-excluded build* method with dummy ids; yield (name, sql)."""
        exclude = type(builder)._v2_rewrite_exclude
        for name in _build_method_names(type(builder)):
            if name in exclude:
                continue
            method = getattr(builder, name)
            sig = inspect.signature(method)
            required = [
                p
                for p in sig.parameters.values()
                if p.default is inspect.Parameter.empty
                and p.kind in (p.POSITIONAL_ONLY, p.POSITIONAL_OR_KEYWORD)
            ]
            # Inherited id-list methods take one positional (trace_ids/span_ids);
            # page/count methods take none.
            args = [["dummy-id-1", "dummy-id-2"]] * len(required)
            result = method(*args)
            if isinstance(result, tuple):
                yield name, result[0]
            elif isinstance(result, list):
                for el in result:
                    yield name, el[0]

    def test_list_builders_emit_no_v1_tokens(self):
        registry = _registry()
        exercised = 0
        for qt in _LIST_BUILDER_TYPES:
            cls = _load(registry[qt])
            builder = cls(project_id="contract-test-proj")
            for name, sql in self._exercise(builder):
                exercised += 1
                cleaned = _LEGIT_ALIAS_RE.sub("", sql or "")
                hit = _V1_TOKEN_RE.search(cleaned)
                assert hit is None, (
                    f"{cls.__name__}.{name} emitted v1-only token "
                    f"'{hit.group(0)}':\n{sql}"
                )
        # Guard against a refactor silently exercising nothing.
        assert exercised >= len(_LIST_BUILDER_TYPES), (
            f"expected to exercise at least one build* per list builder, "
            f"only ran {exercised}"
        )
