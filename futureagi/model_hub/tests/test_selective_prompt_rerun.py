"""Prompt edit scoping reaches the asynchronous runner without clearing peers."""

import json
from unittest.mock import patch

import pytest

from model_hub.models.choices import CellStatus, SourceChoices, StatusType
from model_hub.models.develop_dataset import Cell, Column
from model_hub.models.run_prompt import RunPrompter
from model_hub.services.selective_rerun import (
    input_fingerprints,
    save_fingerprints,
    selected_rows,
)
from model_hub.tests import test_selective_rerun as fixtures

datasets = fixtures.datasets
state = fixtures.state


@pytest.fixture
def prompt(datasets):
    a, _, _ = datasets
    prompt = RunPrompter.objects.create(
        dataset=a["dataset"],
        organization=a["org"],
        workspace=a["workspace"],
        name="answer",
        model="gpt-4o",
        messages=[{"role": "user", "content": "{{question}}"}],
        status=StatusType.COMPLETED.value,
    )
    Column.objects.filter(id=a["output"].id).update(
        source=SourceChoices.RUN_PROMPT.value, source_id=prompt.id
    )
    a["output"].refresh_from_db()
    return prompt


def edit(client, a, scope, config=None):
    return client.post(
        "/model-hub/develops/edit_run_prompt_column/",
        {
            "dataset_id": str(a["dataset"].id),
            "column_id": str(a["output"].id),
            "name": "answer",
            "config": config or {},
            "scope": scope,
        },
        format="json",
    )


@pytest.fixture
def json_prompt(datasets, prompt):
    """A completed JSON-output prompt whose stored inputs are acknowledged."""
    a, _, _ = datasets
    prompt.response_format = {"type": "json_object"}
    prompt.output_format = "object"
    # As stored by add_run_prompt_column: the field mirrors the editor setting.
    prompt.temperature = 0.3
    prompt.run_prompt_config = {"model_type": "llm", "temperature": 0.3}
    prompt.save(
        update_fields=[
            "response_format",
            "output_format",
            "temperature",
            "run_prompt_config",
        ]
    )
    for index, row in enumerate(a["rows"]):
        Cell.objects.filter(column=a["output"], row=row).update(
            value=json.dumps({"answer": f"stable-{index}"})
        )
    save_fingerprints(a["output"], input_fingerprints(a["output"]))
    return prompt


@pytest.mark.django_db
class TestPromptFingerprintsIgnoreOutputCaches:
    """Saving an unchanged prompt must never mark its unchanged rows as changed."""

    # The editor sends back what it loaded, without the derived output cache.
    EDITOR_SETTINGS = {"model_type": "llm", "temperature": 0.3}

    def test_repeated_unchanged_json_saves_select_nothing(self, datasets, json_prompt):
        a, _, clients = datasets
        before = state(a)
        with patch(
            "model_hub.tasks.run_prompt.process_prompts_single.apply_async"
        ) as dispatch:
            for config in ({}, {"run_prompt_config": dict(self.EDITOR_SETTINGS)}) * 2:
                response = edit(clients["a"], a, "changed", config)
                assert response.status_code == 200, response.content
                json_prompt.refresh_from_db()
                # The save itself repopulates the output-derived cache.
                derived = json_prompt.run_prompt_config["derived_variables"]
                assert derived["is_json"] is True
                assert selected_rows(a["output"], "changed") == []
        dispatch.assert_not_called()
        assert state(a) == before

    def test_changed_input_after_unchanged_saves_selects_only_that_row(
        self, datasets, json_prompt
    ):
        a, _, clients = datasets
        with patch(
            "model_hub.tasks.run_prompt.process_prompts_single.apply_async"
        ) as dispatch:
            assert edit(clients["a"], a, "changed").status_code == 200
            dispatch.assert_not_called()
            # A completed worker acknowledges inputs after the cache is present.
            save_fingerprints(a["output"], input_fingerprints(a["output"]))
            Cell.objects.filter(id=a["cells"][1].id).update(value="edited input")
            before = state(a)
            response = edit(
                clients["a"],
                a,
                "changed",
                {"run_prompt_config": dict(self.EDITOR_SETTINGS)},
            )
        assert response.status_code == 200, response.content
        assert dispatch.call_args.kwargs["args"][0]["row_ids"] == [str(a["rows"][1].id)]
        assert state(a)[0] == before[0]

    @pytest.mark.parametrize(
        "cache",
        [
            {"derived_variables": {"is_json": True, "paths": ["answer.changed"]}},
            {"logoUrl": "/assets/other-logo.svg"},
            {"isAvailable": False},
        ],
    )
    def test_cache_only_changes_keep_worker_fingerprints(
        self, datasets, json_prompt, cache
    ):
        a, _, _ = datasets
        before = input_fingerprints(a["output"])
        RunPrompter.objects.filter(id=json_prompt.id).update(
            run_prompt_config={**json_prompt.run_prompt_config, **cache}
        )
        assert input_fingerprints(a["output"]) == before
        assert selected_rows(a["output"], "changed") == []

    @pytest.mark.parametrize(
        "setting",
        [
            {"temperature": 0.9},
            {"reasoning": {"sliders": {"effort": "high"}}},
            {"template_format": "jinja"},
        ],
    )
    def test_execution_settings_still_select_every_row(
        self, datasets, json_prompt, setting
    ):
        a, _, _ = datasets
        RunPrompter.objects.filter(id=json_prompt.id).update(
            run_prompt_config={**json_prompt.run_prompt_config, **setting}
        )
        assert sorted(selected_rows(a["output"], "changed")) == sorted(
            str(row.id) for row in a["rows"]
        )


@pytest.mark.django_db
class TestSelectivePromptRerun:
    def test_failed_ids_reach_the_queue_and_other_cells_are_untouched(
        self, datasets, prompt
    ):
        a, _, clients = datasets
        Cell.objects.filter(column=a["output"], row=a["rows"][0]).update(
            status=CellStatus.ERROR.value
        )
        before = state(a)
        with patch(
            "model_hub.tasks.run_prompt.process_prompts_single.apply_async"
        ) as dispatch:
            response = edit(clients["a"], a, "failed")
        assert response.status_code == 200, response.content
        assert dispatch.call_args.kwargs["args"][0]["row_ids"] == [str(a["rows"][0].id)]
        assert state(a)[1] == before[1]

    @pytest.mark.parametrize("reference", ["named", "uuid", "sanitized"])
    def test_changed_uses_persisted_inputs(self, datasets, prompt, reference):
        a, _, clients = datasets
        if reference != "named":
            name = str(a["column"].id)
            if reference == "sanitized":
                name = name.replace("-", "_")
                if name[0].isdigit():
                    name = "_" + name
            prompt.messages = [{"role": "user", "content": "{{" + name + "}}"}]
            prompt.save(update_fields=["messages"])
        save_fingerprints(a["output"], input_fingerprints(a["output"]))
        Cell.objects.filter(id=a["cells"][1].id).update(value="changed prompt input")
        before = state(a)
        with patch(
            "model_hub.tasks.run_prompt.process_prompts_single.apply_async"
        ) as dispatch:
            response = edit(clients["a"], a, "changed")
        assert response.status_code == 200, response.content
        assert dispatch.call_args.kwargs["args"][0]["row_ids"] == [str(a["rows"][1].id)]
        assert state(a)[0] == before[0]

    @pytest.mark.parametrize("scope", ["all", "changed", "failed"])
    def test_foreign_prompt_edit_never_writes(self, datasets, prompt, scope):
        a, _, clients = datasets
        before = state(a)
        with patch(
            "model_hub.tasks.run_prompt.process_prompts_single.apply_async"
        ) as dispatch:
            response = edit(clients["b"], a, scope)
        assert response.status_code == 404, response.content
        dispatch.assert_not_called()
        assert state(a) == before

    def test_running_prompt_cannot_change_selected_outputs(self, datasets, prompt):
        a, _, clients = datasets
        RunPrompter.objects.filter(id=prompt.id).update(status=StatusType.RUNNING.value)
        before = state(a)
        response = edit(clients["a"], a, "failed")
        assert response.status_code == 400, response.content
        assert state(a) == before

    def test_runner_filters_rows_before_submission(self, datasets, prompt):
        from model_hub.views.run_prompt import RunPrompts

        a, _, _ = datasets
        runner = RunPrompts(str(prompt.id))
        # This unit check isolates dispatch, not the external model implementation.
        with patch.object(runner, "process_row") as process:
            runner.run_prompt(edit_mode=True, row_ids=[str(a["rows"][1].id)])
        assert [str(call.args[0].id) for call in process.call_args_list] == [
            str(a["rows"][1].id)
        ]
