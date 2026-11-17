"""Socket-free resolver regressions; runnable with python -I <this file>."""

import importlib.util
import unittest
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace as NS

_spec = importlib.util.spec_from_file_location(
    "experiment_config_resolver",
    Path(__file__).resolve().parents[1] / "services/experiment_config_resolver.py",
)
resolver = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(resolver)


SAVED = {
    "temperature": 0,
    "max_tokens": 64,
    "top_p": 0.25,
    "presence_penalty": 0.5,
    "frequency_penalty": 0.75,
    "response_format": "json_object",
    "reasoning": {"dropdowns": {"reasoningEffort": "low"}},
    "tools": ["saved-tool"],
    "tool_choice": "required",
}


def epc(params=None, configuration=None, version=True):
    workspace = NS(id="workspace-a", is_default=False, organization_id="org-a")
    template = NS(
        id="prompt-a",
        organization_id="org-a",
        workspace_id=workspace.id,
        workspace=workspace,
        deleted=False,
    )
    snapshot = {
        "configuration": deepcopy(SAVED),
        "messages": [{"role": "user", "content": "hi"}],
    }
    record = NS(
        name="saved version",
        model="gpt-4.1",
        model_display_name=None,
        model_config={},
        model_params=params or {},
        configuration=configuration or {},
        output_format="string",
        voice_input_column_id=None,
        prompt_template_id=template.id,
        prompt_version_id="version-a" if version else None,
        prompt_version=NS(
            original_template=template,
            original_template_id=template.id,
            prompt_config_snapshot=snapshot,
        )
        if version
        else None,
        experiment_dataset=NS(
            experiment=NS(
                dataset=NS(
                    organization_id="org-a",
                    workspace_id=workspace.id,
                    workspace=workspace,
                )
            )
        ),
        get_messages=lambda: snapshot if version else [],
    )
    return record


class VersionParamsTests(unittest.TestCase):
    def test_empty_and_null_overrides_preserve_saved_zero_and_all_params(self):
        for params in ({}, {"temperature": None, "maxTokens": None, "top_p": None}):
            with self.subTest(params=params):
                result = resolver.resolve_prompt_config(epc(params))
                expected = {
                    k: v for k, v in SAVED.items() if k not in ("tools", "tool_choice")
                }
                self.assertEqual(result["model_params"], expected)
                self.assertEqual(result["configuration"]["tools"], SAVED["tools"])
                self.assertEqual(result["configuration"]["tool_choice"], "required")

    def test_list_of_messages_passes_through_without_settings(self):
        record = epc()
        snapshot = [{"role": "user", "content": "legacy message"}]
        record.prompt_version.prompt_config_snapshot = snapshot
        record.get_messages = lambda: snapshot
        result = resolver.resolve_prompt_config(record)
        self.assertEqual(result["model_params"], {})
        self.assertIs(result["messages"], snapshot)

    def test_list_of_configs_uses_first_configuration_without_changing_messages(self):
        record = epc()
        snapshot = [
            {"configuration": {"temperature": 0, "max_tokens": 64}},
            {"configuration": {"temperature": 1}},
        ]
        record.prompt_version.prompt_config_snapshot = snapshot
        record.get_messages = lambda: snapshot
        result = resolver.resolve_prompt_config(record)
        self.assertEqual(result["model_params"], {"temperature": 0, "max_tokens": 64})
        self.assertIs(result["messages"], snapshot)

    def test_non_dict_snapshot_or_configuration_has_no_settings(self):
        for snapshot in (["legacy"], "legacy", 1, [], {"configuration": [1]}):
            with self.subTest(snapshot=snapshot):
                record = epc()
                record.prompt_version.prompt_config_snapshot = snapshot
                self.assertEqual(
                    resolver.resolve_prompt_config(record)["model_params"], {}
                )

    def test_camel_case_overrides_win_and_nulls_do_not(self):
        result = resolver.resolve_prompt_config(
            epc(
                {
                    "temperature": None,
                    "maxTokens": 128,
                    "topP": 0,
                    "presencePenalty": 0,
                    "frequencyPenalty": 0,
                    "responseFormat": "text",
                    "providers": "openai",
                },
                {"temperature": 1, "max_tokens": 256},
            )
        )
        self.assertEqual(result["model_params"]["max_tokens"], 128)
        self.assertEqual(result["model_params"]["top_p"], 0)
        self.assertEqual(result["model_params"]["presence_penalty"], 0)
        self.assertEqual(result["model_params"]["response_format"], "text")
        self.assertNotIn("maxTokens", result["model_params"])
        # The direct RunPrompt args must not shadow the request override.
        self.assertEqual(result["configuration"]["max_tokens"], 128)

    def test_legacy_empty_tools_inherit_but_explicit_none_disables(self):
        inherited = resolver.resolve_prompt_config(
            epc(configuration={"tools": [], "toolChoice": "auto"})
        )
        self.assertEqual(inherited["configuration"]["tools"], ["saved-tool"])
        self.assertEqual(inherited["configuration"]["tool_choice"], "required")
        disabled = resolver.resolve_prompt_config(
            epc(configuration={"tools": [], "toolChoice": "none"})
        )
        self.assertEqual(disabled["configuration"]["tools"], [])
        self.assertEqual(disabled["configuration"]["tool_choice"], "none")
        override = resolver.resolve_prompt_config(
            epc(configuration={"tools": ["new-tool"], "toolChoice": "auto"})
        )
        self.assertEqual(override["configuration"]["tools"], ["new-tool"])
        self.assertEqual(override["configuration"]["tool_choice"], "auto")

    def test_cross_tenant_or_workspace_version_refused_before_messages(self):
        for field, value in (
            ("organization_id", "foreign-org"),
            ("workspace_id", "foreign-workspace"),
        ):
            record = epc()
            setattr(record.prompt_version.original_template, field, value)
            record.get_messages = lambda: self.fail("foreign snapshot must not be read")
            with self.subTest(field=field), self.assertRaises(ValueError):
                resolver.resolve_prompt_config(record)

    def test_default_workspace_accepts_unassigned_same_org_prompt(self):
        record = epc()
        record.experiment_dataset.experiment.dataset.workspace.is_default = True
        record.prompt_version.original_template.workspace_id = None
        record.prompt_version.original_template.workspace = None
        self.assertEqual(
            resolver.resolve_prompt_config(record)["model_params"]["temperature"], 0
        )

    def test_inline_media_config_is_not_replaced_by_version(self):
        record = epc({"maxTokens": 32, "temperature": None}, version=False)
        record.model_config = {"voice": "alloy"}
        result = resolver.resolve_prompt_config(record)
        self.assertEqual(result["model_params"], {"max_tokens": 32})
        self.assertEqual(result["model_config"], {"voice": "alloy"})

    def test_edit_compares_resolved_settings_without_mutating_audit(self):
        record = epc(configuration={"toolChoice": "auto", "tools": []})
        effective = resolver.resolve_prompt_config(record)
        record.model_params = effective["model_params"]
        record.configuration = effective["configuration"]
        before = deepcopy((record.model_params, record.configuration))
        self.assertFalse(
            resolver.prompt_settings_changed(
                record, {}, {}, {"toolChoice": "auto", "tools": []}
            )
        )
        echoed = {**record.model_params, "temperature": 0.0}
        self.assertFalse(
            resolver.prompt_settings_changed(record, {}, echoed, record.configuration)
        )
        self.assertTrue(
            resolver.prompt_settings_changed(
                record, {}, {"maxTokens": 128}, {"toolChoice": "auto", "tools": []}
            )
        )
        self.assertTrue(
            resolver.prompt_settings_changed(
                record, {}, {}, {"tools": [], "toolChoice": "none"}
            )
        )
        self.assertEqual((record.model_params, record.configuration), before)

    def test_inline_edit_normalizes_aliases_and_empty_auto_tools(self):
        record = epc({"temperature": 0, "max_tokens": 64}, version=False)
        self.assertFalse(
            resolver.prompt_settings_changed(
                record,
                {},
                {"temperature": 0.0, "maxTokens": 64},
                {"tools": [], "toolChoice": "auto"},
            )
        )
        self.assertTrue(
            resolver.prompt_settings_changed(
                record,
                {},
                {"temperature": 0.0, "maxTokens": 64},
                {"tools": [], "toolChoice": "none"},
            )
        )

    def test_no_mutation_and_canonical_key_wins_alias_collision(self):
        record = epc({"max_tokens": 12, "maxTokens": 99, "temperature": None})
        before = deepcopy(record.prompt_version.prompt_config_snapshot)
        params = deepcopy(record.model_params)
        result = resolver.resolve_prompt_config(record)
        self.assertEqual(result["model_params"]["max_tokens"], 12)
        self.assertEqual(record.model_params, params)
        self.assertEqual(record.prompt_version.prompt_config_snapshot, before)


if __name__ == "__main__":
    unittest.main()
