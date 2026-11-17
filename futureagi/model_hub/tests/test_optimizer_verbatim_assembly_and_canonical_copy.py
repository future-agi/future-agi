"""Verbatim template assembly and canonical copies through real ORM entrypoints.

The adjacent canonical_worker fixture retains production normalization and the
real activity, stubbing only optimizer/provider and external-service boundaries.
"""

from copy import deepcopy
from unittest.mock import call
from uuid import UUID

import pytest

from ee.agent_opt.utils.template_variables import normalize_prompt_text
from model_hub.models.choices import SourceChoices
from model_hub.models.dataset_optimization_trial import DatasetOptimizationTrial
from model_hub.models.develop_dataset import Column
from model_hub.models.run_prompt import PromptTemplate, PromptVersion, RunPrompter
from model_hub.tests import test_optimizer_canonical_reference_check as canonical_cases
from model_hub.tests import test_optimizer_copy_writers_and_worker_refusal as copy_cases
from model_hub.tests.test_optimizer_canonical_reference_check import (
    canonical_worker as canonical_worker,
)
from model_hub.tests.test_optimizer_copy_writers_and_worker_refusal import (
    copy_writer as copy_writer,
)
from model_hub.tests.test_optimizer_copy_writers_and_worker_refusal import (
    graph as graph,
)
from model_hub.utils.optimizer_prompt_text import (
    COLUMN_REFERENCE_PATTERN,
    optimizer_column_references,
    remap_optimizer_messages,
)
from model_hub.views import run_prompt

pytestmark = pytest.mark.django_db


def _fullwidth(text):
    return "".join(chr(ord(char) + 0xFEE0) for char in text)


def _messages(column_id, layout, spelling, suffix="", destination=None):
    source = str(column_id)
    target = str(destination) if destination is not None else source
    if spelling == "compatibility":
        token = (
            _fullwidth("{{")
            + (target if destination is not None else _fullwidth(source))
            + _fullwidth(suffix + "}}")
        )
    else:
        token = "{{" + target[:12] + "​‌‍‎‏﻿" + target[12:] + suffix + "}}"
    assert (
        normalize_prompt_text.__module__ == "ee.agent_opt.utils.template_variables"
    ), "SETUP_AQ-XWS-W5H must use the production normalizer"
    assert normalize_prompt_text(token) == "{{" + target + suffix + "}}", (
        "SETUP_AQ-XWS-W5H spelling must canonicalize to the intended reference"
    )
    if destination is None:
        assert COLUMN_REFERENCE_PATTERN.search(token) is None, (
            "SETUP_AQ-XWS-W5H source token must not match the old raw regex"
        )
    # Include both length-changing normalization and composition outside the token.
    prefix = " \tKeep Å​ é text  "
    text = prefix + token + "\n  end\t "
    if layout == "whole":
        return [{"role": "user", "content": text}]
    split = len(prefix) + 21
    left, right = text[:split], text[split:]
    media = {"type": "image_url", "image_url": {"url": "w5h-media"}}
    if layout == "parts":
        return [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": left},
                    media,
                    {"type": "text", "text": right},
                ],
            }
        ]
    return [
        {"role": "system", "content": left},
        {"role": "user", "content": [media]},
        {"role": "assistant", "content": right},
    ]


def _template_source(graph, messages):
    template = PromptTemplate.no_workspace_objects.create(
        name="W5H same-workspace template",
        organization=graph.organization,
        workspace=graph.workspace,
    )
    PromptVersion.objects.create(
        original_template=template,
        template_version="1",
        is_default=True,
        prompt_config_snapshot=[{"messages": messages}],
    )
    graph.output.source = SourceChoices.OTHERS.value
    graph.output.source_id = template.id
    graph.output.save(update_fields=["source", "source_id"])


@pytest.mark.parametrize("layout", ["strings", "parts"])
@pytest.mark.parametrize("reference", [False, True])
def test_template_preserves_verbatim_join_at_provider_and_persistence(
    graph, canonical_worker, layout, reference
):
    contents = [" \tdef f():  ", "    return 1\t "]
    if reference:
        contents[1] = (
            "    return "
            + _fullwidth("{{" + str(graph.columns["same"].id) + "}}")
            + "\t "
        )
    messages = [
        {
            "role": role,
            "content": (
                content if layout == "strings" else [{"type": "text", "text": content}]
            ),
        }
        for role, content in zip(("system", "user"), contents, strict=True)
    ]
    _template_source(graph, messages)
    result = canonical_worker.execute()
    trial = DatasetOptimizationTrial.objects.filter(optimization_run=result.run).first()
    expected = normalize_prompt_text("\n".join(contents)).replace(
        str(graph.columns["same"].id), graph.columns["same"].name
    )
    observed = (
        result.result["best_prompt"] if result.result else None,
        trial.prompt if trial else None,
        canonical_worker.provider.call_args_list,
    )
    assert observed == (expected, expected, [call(expected)]), (
        "AQ-XWS-W5H: template messages must reach provider and persistence with the verbatim join before canonical reference resolution"
    )


@pytest.mark.parametrize("operation", ["create", "add"])
@pytest.mark.parametrize("layout", ["whole", "strings", "parts"])
@pytest.mark.parametrize("spelling", ["compatibility", "invisible"])
@pytest.mark.parametrize("suffix", ["", ".value", "[0]"])
def test_canonical_same_scope_copy_preserves_text_and_boundaries(
    graph, copy_writer, operation, layout, spelling, suffix
):
    source_id = graph.columns["legacy"].id
    messages = _messages(source_id, layout, spelling, suffix)
    original = deepcopy(messages)
    response, _ = copy_writer.post(operation, messages)
    copied = list(
        RunPrompter.no_workspace_objects.exclude(
            dataset_id__in=[dataset.id for dataset in graph.datasets.values()]
        )
    )
    prompt = copied[0] if len(copied) == 1 else None
    local = (
        list(
            Column.no_workspace_objects.filter(
                dataset_id=prompt.dataset_id, source=SourceChoices.OTHERS.value
            )
        )
        if prompt
        else []
    )
    destination = local[0].id if len(local) == 1 else None
    expected = (
        _messages(source_id, layout, spelling, suffix, destination)
        if destination is not None
        else None
    )
    observed = (
        response.status_code,
        len(copied),
        len(local),
        prompt.messages if prompt else None,
        optimizer_column_references(prompt.messages) if prompt else None,
        prompt.organization_id if prompt else None,
        prompt.workspace_id if prompt else None,
        messages == original,
    )
    assert observed == (
        200,
        1,
        1,
        expected,
        {destination},
        graph.organization.id,
        graph.workspace.id,
        True,
    ), (
        "AQ-XWS-W5H: both copy endpoints must remap canonical same-scope UUIDs without changing unrelated text roles media or boundaries"
    )


@pytest.mark.parametrize("operation", ["create", "add"])
@pytest.mark.parametrize("layout", ["whole", "strings", "parts"])
@pytest.mark.parametrize("spelling", ["compatibility", "invisible"])
@pytest.mark.parametrize("kind", ["other_dataset", "other_workspace", "foreign_org"])
def test_canonical_foreign_and_missing_copy_have_identical_atomic_refusal(
    graph, copy_writer, operation, layout, spelling, kind
):
    observed = []
    for reference in (graph.columns[kind].id, graph.missing):
        messages = _messages(reference, layout, spelling) + _messages(
            graph.columns["legacy"].id, "whole", spelling
        )
        response, before = copy_writer.post(operation, messages)
        observed.append((copy_cases._shape(response), copy_cases._snapshot() == before))
    expected = copy_cases._shape(
        run_prompt.GeneralMethods().not_found("Column or dataset not found")
    )
    assert observed == [(expected, True), (expected, True)], (
        "SETUP_AQ-XWS-W5H canonical foreign and nonexistent copies must have the same refusal and zero committed writes even alongside an admitted reference"
    )


@pytest.mark.parametrize("spelling", ["compatibility", "invisible"])
def test_template_foreign_and_missing_canonical_references_still_refuse(
    graph, canonical_worker, spelling
):
    observed = []
    for reference in (graph.columns["other_workspace"].id, graph.missing):
        _template_source(graph, _messages(reference, "whole", spelling))
        observed.append(canonical_worker.execute().refusal)
    assert observed == [
        canonical_cases._expected_worker_refusal(),
        canonical_cases._expected_worker_refusal(),
    ], (
        "SETUP_AQ-XWS-W5H template foreign and missing canonical references must fail identically before execution or trial writes"
    )


def test_copy_expanding_uuid_glyph_stays_in_its_original_message():
    source = UUID("ff000000-0000-4000-8000-000000000001")
    destination = UUID("ab000000-0000-4000-8000-000000000002")
    messages = [
        {"role": "system", "content": "  é {{ﬀ"},
        {"role": "user", "content": str(source)[2:] + "}}\t tail "},
    ]
    original = deepcopy(messages)
    assert optimizer_column_references(messages) == {source}, (
        "SETUP_AQ-XWS-W5H expanding glyph must be recognized by production extraction"
    )
    remapped = remap_optimizer_messages(messages, {source: destination})
    assert (remapped, messages) == (
        [
            {"role": "system", "content": "  é {{ab"},
            {"role": "user", "content": str(destination)[2:] + "}}\t tail "},
        ],
        original,
    ), (
        "AQ-XWS-W5H: expanded UUID glyph replacement must not shift neighboring text across message boundaries"
    )
