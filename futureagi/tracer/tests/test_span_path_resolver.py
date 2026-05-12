"""Tests for ``_resolve_span_path`` model-column branch.

Every concrete ObservationSpan column is reachable by bare name; the
model branch wins over span_attributes on a name collision.
"""

import pytest

# Cycle-breaker — same rationale as the other resolver tests.
import model_hub.tasks  # noqa: F401, E402

from tracer.models.observation_span import ObservationSpan  # noqa: E402
from tracer.models.trace import Trace  # noqa: E402
from tracer.utils.eval import (  # noqa: E402
    _MISSING,
    _SPAN_PUBLIC_FIELDS,
    _resolve_span_path,
)
from tracer.utils.eval_helpers import evalable_field_names  # noqa: E402


@pytest.mark.integration
@pytest.mark.django_db
class TestResolveSpanPathModelColumns:
    """Bare-name model column reachability."""

    def _make_span(self, observe_project, **overrides):
        trace = Trace.objects.create(project=observe_project)
        defaults = dict(
            id="span-resolver-1",
            project=observe_project,
            trace=trace,
            name="resolver-target",
            observation_type="llm",
            model="gpt-4o",
            provider="openai",
            latency_ms=1234,
            prompt_tokens=10,
            completion_tokens=20,
            total_tokens=30,
            input={"messages": [{"role": "user", "content": "hi"}]},
            output={"choices": [{"text": "hello"}]},
            metadata={"customer_id": "cust_42"},
            # ``model`` here collides with the model column — see
            # test_model_column_wins_over_span_attributes_collision.
            span_attributes={
                "model": "gpt-3.5-from-attrs",
                "nested": {"deep": "v"},
            },
        )
        defaults.update(overrides)
        return ObservationSpan.objects.create(**defaults)

    def test_scalar_model_column(self, observe_project):
        span = self._make_span(observe_project)
        assert _resolve_span_path(span, "provider") == "openai"
        assert _resolve_span_path(span, "latency_ms") == 1234
        assert _resolve_span_path(span, "prompt_tokens") == 10
        assert _resolve_span_path(span, "total_tokens") == 30

    def test_json_model_column_root_and_dotted(self, observe_project):
        span = self._make_span(observe_project)
        # Bare name returns the whole JSON value.
        assert _resolve_span_path(span, "metadata") == {"customer_id": "cust_42"}
        # Dotted walk into the JSON column.
        assert _resolve_span_path(span, "metadata.customer_id") == "cust_42"

    def test_model_column_wins_over_span_attributes_collision(self, observe_project):
        """Model column wins; span_attributes still reachable via the
        ``span_attributes.<name>`` head."""
        span = self._make_span(observe_project)
        assert _resolve_span_path(span, "model") == "gpt-4o"
        assert _resolve_span_path(span, "span_attributes.model") == "gpt-3.5-from-attrs"

    def test_span_attributes_fallback_still_works(self, observe_project):
        """Bare key fallback (legacy ``_resolve_attr`` surface)."""
        span = self._make_span(observe_project)
        assert _resolve_span_path(span, "nested.deep") == "v"

    def test_missing_dotted_returns_missing_sentinel(self, observe_project):
        """Missing key in a dotted walk returns ``_MISSING`` (caller writes
        an error row)."""
        span = self._make_span(observe_project, metadata={"x": 1})
        assert _resolve_span_path(span, "metadata.does_not_exist") is _MISSING

    def test_null_model_column_returns_none(self, observe_project):
        """Top-level null is a legitimate resolution — not ``_MISSING``."""
        span = self._make_span(observe_project, metadata=None)
        assert _resolve_span_path(span, "metadata") is None


@pytest.mark.django_db
class TestSpanPublicFieldsWhitelist:
    """``_SPAN_PUBLIC_FIELDS`` tracks the model schema."""

    def test_whitelist_matches_evalable_field_names(self):
        assert _SPAN_PUBLIC_FIELDS == evalable_field_names(ObservationSpan)

    def test_includes_representative_columns(self):
        for col in (
            "input",
            "output",
            "model",
            "provider",
            "prompt_tokens",
            "completion_tokens",
            "total_tokens",
            "latency_ms",
            "metadata",
            "tags",
            "status",
            "span_attributes",
        ):
            assert col in _SPAN_PUBLIC_FIELDS

    def test_excludes_soft_delete_columns(self):
        assert "deleted" not in _SPAN_PUBLIC_FIELDS
        assert "deleted_at" not in _SPAN_PUBLIC_FIELDS
