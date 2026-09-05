"""
Pin the reader contract for ``ProjectVersion.eval_tags``.

``eval_tags`` is persisted from client payloads with no per-entry schema: the
SDK contract declares a bare ``JSONField(required=False, allow_null=True)``,
and ``tracer/utils/otel.py`` writes ``"type": eval_tag.get("type")`` /
``"value": eval_tag.get("value")`` straight through, so both land as ``None``
whenever the client omits the key.

Two readers then called ``.lower()`` on those values:

  1. ``tracer/views/observation_span.py::retrieve_loading`` built
     ``eval_config_mapping`` from tags FILTERED by type but
     ``custom_eval_config_ids`` from the UNFILTERED list. Any tag with a
     different type therefore produced a config row with no mapping entry,
     and ``eval_config_mapping.get(...).lower()`` raised AttributeError. The
     view's blanket ``except Exception`` turned that into a generic HTTP 400,
     so the eval panel never resolved for that project version — permanently,
     with the real cause only in the logs.

  2. ``tracer/utils/eval.py::eval_observation_span_runner`` called
     ``eval_tag.get("value").lower()``, which raised on any span-type tag
     whose ``value`` the client omitted — aborting the loop and skipping
     every remaining eval for that span.

These are pure-function tests over the shared filter; no DB, no services.
"""

from __future__ import annotations

import pytest

from tracer.utils.eval_tags import OBSERVATION_SPAN_TYPE, span_type_eval_tags

pytestmark = pytest.mark.unit


def _tag(**overrides):
    tag = {
        "custom_eval_config_id": "cfg-1",
        "type": OBSERVATION_SPAN_TYPE,
        "value": "LLM",
    }
    tag.update(overrides)
    return tag


class TestSpanTypeEvalTags:
    def test_keeps_well_formed_span_type_tags(self):
        tags = [_tag(), _tag(custom_eval_config_id="cfg-2", value="tool")]
        assert span_type_eval_tags(tags) == tags

    def test_drops_tags_addressing_another_target(self):
        # The regression: this tag used to reach CustomEvalConfig.objects
        # through the unfiltered id set while having no mapping entry.
        tags = [_tag(), _tag(custom_eval_config_id="cfg-2", type="TRACE_TYPE")]
        assert span_type_eval_tags(tags) == [tags[0]]

    def test_drops_tags_whose_type_the_client_omitted(self):
        # Exactly what otel.py persists when the payload has no "type".
        tags = [_tag(), _tag(custom_eval_config_id="cfg-2", type=None)]
        assert span_type_eval_tags(tags) == [tags[0]]

    def test_drops_tags_whose_value_the_client_omitted(self):
        # Callers lowercase value; None would raise inside the runner loop.
        tags = [_tag(), _tag(custom_eval_config_id="cfg-2", value=None)]
        assert span_type_eval_tags(tags) == [tags[0]]

    def test_drops_tags_without_a_config_id(self):
        tags = [_tag(), _tag(custom_eval_config_id=None)]
        assert span_type_eval_tags(tags) == [tags[0]]

    def test_drops_non_mapping_entries(self):
        # EvalTemplate.eval_tags is a plain list of category strings; if the
        # two shapes are ever crossed, a bare string must not crash a reader.
        tags = [_tag(), "RAG", None, 7]
        assert span_type_eval_tags(tags) == [tags[0]]

    @pytest.mark.parametrize("empty", [None, [], {}])
    def test_tolerates_empty_input(self, empty):
        assert span_type_eval_tags(empty) == []

    def test_every_surviving_tag_is_safe_to_index_and_lower(self):
        """The contract both call sites depend on.

        Callers do ``tag["custom_eval_config_id"]`` and ``tag["value"].lower()``
        without guards, so anything returned here must support both.
        """
        messy = [
            _tag(),
            _tag(custom_eval_config_id="cfg-2", type="TRACE_TYPE"),
            _tag(custom_eval_config_id="cfg-3", type=None),
            _tag(custom_eval_config_id="cfg-4", value=None),
            _tag(custom_eval_config_id=None),
            "RAG",
        ]
        for tag in span_type_eval_tags(messy):
            assert tag["custom_eval_config_id"]
            assert tag["value"].lower()


class TestRetrieveLoadingMappingConsistency:
    """The two structures in retrieve_loading must agree.

    The bug was a mismatch, not a bad value: the mapping was keyed by
    span-type tags while the id set came from every tag, so the view looked
    up configs it had no mapping for.
    """

    def test_config_ids_and_mapping_keys_stay_in_lockstep(self):
        eval_tags = [
            _tag(),
            _tag(custom_eval_config_id="cfg-2", type="TRACE_TYPE"),
            _tag(custom_eval_config_id="cfg-3", type=None),
            _tag(custom_eval_config_id="cfg-4", value=None),
        ]

        span_type_tags = span_type_eval_tags(eval_tags)
        eval_config_mapping = {
            t["custom_eval_config_id"]: t["value"] for t in span_type_tags
        }
        custom_eval_config_ids = {t["custom_eval_config_id"] for t in span_type_tags}

        assert custom_eval_config_ids == set(eval_config_mapping)

        # Every config the view would then load has a usable mapping entry,
        # so the .lower() call cannot land on None.
        for config_id in custom_eval_config_ids:
            assert eval_config_mapping.get(str(config_id)).lower()
