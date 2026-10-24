"""Template format resolution for experiment prompt configs (issue #2661).

LLM experiments lock their messages from a prompt version, and the live
experiment form does not send configuration.template_format. The runner and
the eval prompt_chain read the format from the experiment prompt config, so
they have to fall back to the format the version was saved with, or a Jinja
prompt renders as mustache.
"""

from types import SimpleNamespace
from unittest import mock

import pytest

from model_hub.models.choices import DatasetSourceChoices, ModelTypes
from model_hub.models.develop_dataset import Cell, Column, Dataset, Row
from model_hub.models.experiments import ExperimentPromptConfig
from model_hub.models.run_prompt import PromptVersion
from model_hub.services.experiment_config_resolver import resolve_prompt_config
from model_hub.views import eval_runner
from model_hub.views.eval_runner import _resolve_special_value
from model_hub.views.run_prompt import populate_placeholders

JINJA_TEXT = "Hi {% if flag %}{{ name }}{% endif %}!"
MUSTACHE_TEXT = "Hi {{#flag}}{{name}}{{/flag}}!"
JSON_CELL = '{"q": "a < b & c"}'
# What the live LLM experiment form sends as configuration.
LLM_FORM_CONFIGURATION = {"toolChoice": "auto", "tools": []}


def _version(text, template_format=None, as_list=False):
    configuration = {"model": "gpt-4o"}
    if template_format is not None:
        configuration["template_format"] = template_format
    snapshot = {
        "messages": [{"role": "user", "content": [{"type": "text", "text": text}]}],
        "configuration": configuration,
        "placeholders": [],
    }
    return PromptVersion(
        template_version="v1",
        prompt_config_snapshot=[snapshot] if as_list else snapshot,
    )


def _epc(prompt_version=None, configuration=None, messages=None):
    return ExperimentPromptConfig(
        name="exp",
        model="gpt-4o",
        prompt_version=prompt_version,
        configuration=dict(configuration or {}),
        messages=messages,
    )


def _resolved_format(epc):
    return resolve_prompt_config(epc)["configuration"].get("template_format")


class TestResolvePromptConfigTemplateFormat:
    def test_llm_form_payload_uses_version_saved_as_jinja(self):
        epc = _epc(_version(JINJA_TEXT, "jinja"), LLM_FORM_CONFIGURATION)
        configuration = resolve_prompt_config(epc)["configuration"]
        assert configuration["template_format"] == "jinja"
        assert configuration["toolChoice"] == "auto"

    def test_version_saved_as_mustache(self):
        epc = _epc(_version(MUSTACHE_TEXT, "mustache"), LLM_FORM_CONFIGURATION)
        assert _resolved_format(epc) == "mustache"

    def test_list_snapshot_is_read(self):
        epc = _epc(_version(JINJA_TEXT, "jinja", as_list=True))
        assert _resolved_format(epc) == "jinja"

    def test_explicit_experiment_format_wins_over_version(self):
        epc = _epc(_version(JINJA_TEXT, "jinja"), {"template_format": "mustache"})
        assert _resolved_format(epc) == "mustache"

    def test_no_format_anywhere_uses_experiment_default(self):
        epc = _epc(_version(MUSTACHE_TEXT), LLM_FORM_CONFIGURATION)
        assert _resolved_format(epc) == "mustache"

    def test_inline_messages_keep_their_own_format(self):
        assert _resolved_format(_epc(None, {"template_format": "jinja"}, [])) == (
            "jinja"
        )
        assert _resolved_format(_epc(None, {}, [])) == "mustache"

    def test_stored_configuration_is_not_mutated(self):
        epc = _epc(_version(JINJA_TEXT, "jinja"), LLM_FORM_CONFIGURATION)
        resolve_prompt_config(epc)
        assert epc.configuration == LLM_FORM_CONFIGURATION


@pytest.fixture
def dataset_row(db, organization, user, workspace):
    dataset = Dataset.objects.create(
        name="template-format",
        organization=organization,
        user=user,
        source=DatasetSourceChoices.BUILD.value,
        model_type=ModelTypes.GENERATIVE_LLM.value,
        column_order=[],
        column_config={},
    )
    columns = {
        name: Column.objects.create(
            name=name, data_type="text", dataset=dataset, source="OTHERS"
        )
        for name in ("name", "flag", "ctx")
    }
    dataset.column_order = [str(c.id) for c in columns.values()]
    dataset.save()
    row = Row.objects.create(dataset=dataset, order=0)
    values = {"name": "Karthik", "flag": "yes", "ctx": JSON_CELL}
    for name, column in columns.items():
        Cell.objects.create(dataset=dataset, column=column, row=row, value=values[name])
    return dataset, row


def _render_like_experiment_run(epc, dataset_row):
    """Mirror _setup_prompt_v2_sync and the experiment runner's render call."""
    dataset, row = dataset_row
    config = resolve_prompt_config(epc)
    messages = config["messages"]
    if isinstance(messages, dict) and "messages" in messages:
        messages = messages["messages"]
    model_config = config.get("configuration", {})
    out = populate_placeholders(
        messages,
        dataset.id,
        row.id,
        None,
        model_name="gpt-4o",
        template_format=model_config.get("template_format") or "mustache",
        escape_mustache=False,
    )
    return "".join(seg.get("text", "") for seg in out[0]["content"])


def _prompt_chain(epc, dataset_row):
    dataset, row = dataset_row
    runner = SimpleNamespace(
        experiment_dataset=SimpleNamespace(prompt_config=epc),
        dataset=dataset,
        column=None,
    )
    return _resolve_special_value("prompt_chain", row, runner)


def _agent_prompt_chain(node_snapshot, dataset_row):
    """prompt_chain for an agent experiment whose LLM node has this snapshot."""
    dataset, row = dataset_row
    runner = SimpleNamespace(
        experiment_dataset=SimpleNamespace(
            id="edt", prompt_config=None, agent_config=object()
        ),
        dataset=dataset,
        column=None,
    )
    with mock.patch.object(
        eval_runner, "_get_agent_node_messages", return_value=node_snapshot
    ):
        return _resolve_special_value("prompt_chain", row, runner)


@pytest.mark.django_db
class TestExperimentRendersInSavedFormat:
    def test_jinja_saved_prompt_renders_as_jinja(self, dataset_row):
        epc = _epc(_version(JINJA_TEXT, "jinja"), LLM_FORM_CONFIGURATION)
        assert _render_like_experiment_run(epc, dataset_row) == "Hi Karthik!"

    def test_mustache_saved_prompt_renders_as_mustache(self, dataset_row):
        epc = _epc(_version(MUSTACHE_TEXT, "mustache"), LLM_FORM_CONFIGURATION)
        assert _render_like_experiment_run(epc, dataset_row) == "Hi Karthik!"

    def test_version_without_format_renders_as_mustache(self, dataset_row):
        # Versions saved before the format existed use the experiment default.
        epc = _epc(_version(MUSTACHE_TEXT), LLM_FORM_CONFIGURATION)
        assert _render_like_experiment_run(epc, dataset_row) == "Hi Karthik!"

    def test_eval_prompt_chain_uses_saved_jinja_format(self, dataset_row):
        epc = _epc(_version(JINJA_TEXT, "jinja"), LLM_FORM_CONFIGURATION)
        assert _prompt_chain(epc, dataset_row) == "user: Hi Karthik!"

    def test_mustache_saved_prompt_sends_cell_values_raw(self, dataset_row):
        # The Workbench saves "mustache" by default and sends values raw; an
        # experiment on that version must not HTML-escape a JSON cell.
        epc = _epc(_version("In: {{ctx}}", "mustache"), LLM_FORM_CONFIGURATION)
        assert _render_like_experiment_run(epc, dataset_row) == f"In: {JSON_CELL}"

    def test_inline_mustache_config_sends_cell_values_raw(self, dataset_row):
        # TTS/image experiment cards save inline messages with "mustache".
        messages = [{"role": "user", "content": [{"type": "text", "text": "{{ctx}}"}]}]
        epc = _epc(None, {"template_format": "mustache"}, messages)
        assert _render_like_experiment_run(epc, dataset_row) == JSON_CELL

    def test_agent_prompt_chain_uses_node_saved_jinja_format(self, dataset_row):
        # The agent runtime renders a Jinja-saved LLM node with Jinja.
        snapshot = _version(JINJA_TEXT, "jinja").prompt_config_snapshot
        assert _agent_prompt_chain(snapshot, dataset_row) == "user: Hi Karthik!"

    def test_agent_prompt_chain_node_without_format_uses_default(self, dataset_row):
        snapshot = _version(MUSTACHE_TEXT).prompt_config_snapshot
        assert _agent_prompt_chain(snapshot, dataset_row) == "user: Hi Karthik!"

    def test_eval_prompt_chain_keeps_mustache_cell_values_raw(self, dataset_row):
        epc = _epc(_version("In: {{ctx}}", "mustache"), LLM_FORM_CONFIGURATION)
        assert _prompt_chain(epc, dataset_row) == f"user: In: {JSON_CELL}"

    def test_agent_prompt_chain_keeps_mustache_cell_values_raw(self, dataset_row):
        snapshot = _version("In: {{ctx}}", "mustache").prompt_config_snapshot
        assert _agent_prompt_chain(snapshot, dataset_row) == f"user: In: {JSON_CELL}"

    def test_run_prompt_explicit_mustache_still_escapes(self, dataset_row):
        dataset, row = dataset_row
        messages = [{"role": "user", "content": "In: {{ctx}}"}]
        out = populate_placeholders(
            messages, dataset.id, row.id, None, "gpt-4o", template_format="mustache"
        )
        assert out[0]["content"][0]["text"] == (
            "In: {&quot;q&quot;: &quot;a &lt; b &amp; c&quot;}"
        )
