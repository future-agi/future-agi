from types import SimpleNamespace

import pytest

from evaluations.engine.instance import resolve_binding_model
from model_hub.models.choices import OwnerChoices
from model_hub.models.evals_metric import EvalTemplate
from model_hub.models.run_prompt import PromptEvalConfig, PromptTemplate
from model_hub.utils.eval_list import _RUN_CONFIG_DEFAULTS, build_run_config_view


@pytest.mark.parametrize(
    "eval_config, template_config, expected",
    [
        (
            {"run_config": {"model": "claude-3-5-sonnet-latest"}},
            {"model": "turing_large"},
            "claude-3-5-sonnet-latest",
        ),
        ({"model": "gpt-4.1"}, {"model": "turing_large"}, "gpt-4.1"),
        (
            {"model": "gpt-4.1", "run_config": {"model": "claude-3-5"}},
            {"model": "turing_large"},
            "claude-3-5",
        ),
        ({}, {"model": "turing_large"}, "turing_large"),
        (None, {"model": "turing_large"}, "turing_large"),
        (
            {"run_config": {"model": ""}, "model": "gpt-4.1"},
            {"model": "turing_large"},
            "gpt-4.1",
        ),
        ({"model": ""}, {"model": "turing_large"}, "turing_large"),
        ({"run_config": None}, {"model": "turing_large"}, "turing_large"),
        ({}, {}, None),
    ],
    ids=[
        "run_config-nested-wins-alone",
        "top-level-model-when-no-nested",
        "run_config-nested-wins-over-top-level",
        "template-default-when-runtime-empty",
        "template-default-when-runtime-none",
        "empty-nested-falls-through-to-top-level",
        "empty-string-top-level-falls-through",
        "none-run-config-falls-through-to-top-level-or-template",
        "no-model-anywhere-returns-none",
    ],
)
def test_resolve_binding_model_precedence(eval_config, template_config, expected):
    template = SimpleNamespace(config=template_config)
    assert resolve_binding_model(eval_config, template) == expected


def test_resolve_binding_model_template_config_none():
    template = SimpleNamespace(config=None)
    assert resolve_binding_model({"model": "gpt-4.1"}, template) == "gpt-4.1"
    assert resolve_binding_model({}, template) is None


def _fake_binding(config=None, error_localizer=False):
    return SimpleNamespace(config=config, error_localizer=error_localizer)


def test_build_run_config_view_shape_defaults():
    result = build_run_config_view(_fake_binding())
    assert set(result.keys()) == set(_RUN_CONFIG_DEFAULTS)
    assert result["agent_mode"] == "agent"
    assert result["check_internet"] is False
    assert result["summary"] == "concise"
    assert result["pass_threshold"] == 0.5
    assert result["error_localizer_enabled"] is False
    assert result["data_injection"] == {}
    assert result["knowledge_bases"] == []
    assert result["tools"] == {}


def test_build_run_config_view_error_localizer_column_wins_over_json():
    binding = _fake_binding(
        config={"run_config": {"error_localizer_enabled": False}}, error_localizer=True
    )
    assert build_run_config_view(binding)["error_localizer_enabled"] is True


def test_build_run_config_view_error_localizer_falls_back_to_nested_run_config():
    binding = _fake_binding(
        config={"run_config": {"error_localizer_enabled": True}},
        error_localizer=False,
    )
    assert build_run_config_view(binding)["error_localizer_enabled"] is True


def test_build_run_config_view_error_localizer_ignores_legacy_top_level_flag():
    binding = _fake_binding(
        config={"error_localizer_enabled": True}, error_localizer=False
    )
    assert build_run_config_view(binding)["error_localizer_enabled"] is False


def test_build_run_config_view_summary_dict_normalized_to_type_string():
    binding = _fake_binding(
        config={"run_config": {"summary": {"type": "detailed", "extra": 1}}}
    )
    assert build_run_config_view(binding)["summary"] == "detailed"


def test_build_run_config_view_summary_dict_without_type_falls_back():
    binding = _fake_binding(config={"run_config": {"summary": {"other": "value"}}})
    assert build_run_config_view(binding)["summary"] == "concise"


def test_build_run_config_view_reads_all_saved_keys():
    binding = _fake_binding(
        config={
            "run_config": {
                "agent_mode": "protect",
                "check_internet": True,
                "summary": "detailed",
                "pass_threshold": 0.75,
                "data_injection": {"full_row": True},
                "knowledge_bases": ["kb-1", "kb-2"],
                "tools": {"web": {"enabled": True}},
            }
        },
        error_localizer=True,
    )
    assert build_run_config_view(binding) == {
        "agent_mode": "protect",
        "check_internet": True,
        "summary": "detailed",
        "pass_threshold": 0.75,
        "error_localizer_enabled": True,
        "data_injection": {"full_row": True},
        "knowledge_bases": ["kb-1", "kb-2"],
        "tools": {"web": {"enabled": True}},
    }


def test_build_run_config_view_ignores_top_level_run_config_none():
    binding = _fake_binding(config={"run_config": None})
    result = build_run_config_view(binding)
    assert result["agent_mode"] == "agent"
    assert result["pass_threshold"] == 0.5


@pytest.mark.django_db
def test_evaluation_configs_endpoint_returns_template_id_and_eval_type(
    auth_client, user, workspace
):
    template = EvalTemplate.objects.create(
        name="workbench-fixture-llm",
        description="",
        owner=OwnerChoices.USER.value,
        organization=user.organization,
        workspace=workspace,
        eval_type="llm",
        config={"eval_type_id": "CustomPromptEvaluator", "output": "Pass/Fail"},
        eval_tags=["llm"],
    )
    prompt_template = PromptTemplate.objects.create(
        name="Workbench Prompt",
        organization=user.organization,
        workspace=workspace,
        created_by=user,
    )
    PromptEvalConfig.objects.create(
        name="toxicity_binding",
        eval_template=template,
        prompt_template=prompt_template,
        mapping={"output": "model_output"},
        config={},
    )

    response = auth_client.get(
        f"/model-hub/prompt-templates/{prompt_template.id}/evaluation-configs/"
    )
    assert response.status_code == 200
    row = response.json()["result"]["evaluation_configs"][0]
    assert row["template_id"] == str(template.id)
    assert row["eval_type"] == "llm"


@pytest.mark.django_db
def test_evaluation_configs_endpoint_surfaces_run_config(
    auth_client, user, workspace
):
    template = EvalTemplate.objects.create(
        name="workbench-fixture-runtime",
        description="",
        owner=OwnerChoices.SYSTEM.value,
        organization=None,
        workspace=None,
        eval_type="llm",
        config={"eval_type_id": "CustomPromptEvaluator", "output": "Pass/Fail"},
        eval_tags=["llm"],
    )
    prompt_template = PromptTemplate.objects.create(
        name="Workbench Prompt",
        organization=user.organization,
        workspace=workspace,
        created_by=user,
    )
    PromptEvalConfig.objects.create(
        name="toxicity_binding",
        eval_template=template,
        prompt_template=prompt_template,
        mapping={"output": "model_output"},
        config={
            "params": {},
            "run_config": {"model": "gpt-4.1", "agent_mode": "agent"},
        },
    )

    response = auth_client.get(
        f"/model-hub/prompt-templates/{prompt_template.id}/evaluation-configs/"
    )
    row = response.json()["result"]["evaluation_configs"][0]
    assert row["run_config"]["agent_mode"] == "agent"
    assert row["run_config"]["pass_threshold"] == 0.5
    assert set(row["run_config"].keys()) == set(_RUN_CONFIG_DEFAULTS)


@pytest.mark.django_db
def test_evaluation_configs_endpoint_error_localizer_column_wins(
    auth_client, user, workspace
):
    template = EvalTemplate.objects.create(
        name="workbench-fixture-loc",
        description="",
        owner=OwnerChoices.SYSTEM.value,
        organization=None,
        workspace=None,
        eval_type="llm",
        config={"eval_type_id": "CustomPromptEvaluator", "output": "Pass/Fail"},
        eval_tags=["llm"],
    )
    prompt_template = PromptTemplate.objects.create(
        name="Workbench Prompt",
        organization=user.organization,
        workspace=workspace,
        created_by=user,
    )
    PromptEvalConfig.objects.create(
        name="loc_binding",
        eval_template=template,
        prompt_template=prompt_template,
        mapping={"output": "model_output"},
        config={"run_config": {"error_localizer_enabled": False}},
        error_localizer=True,
    )

    response = auth_client.get(
        f"/model-hub/prompt-templates/{prompt_template.id}/evaluation-configs/"
    )
    row = response.json()["result"]["evaluation_configs"][0]
    assert row["run_config"]["error_localizer_enabled"] is True


@pytest.mark.django_db
def test_update_evaluation_configs_persists_error_localizer_from_fe(
    auth_client, user, workspace
):
    template = EvalTemplate.objects.create(
        name="workbench-fixture-el-save",
        description="",
        owner=OwnerChoices.SYSTEM.value,
        organization=None,
        workspace=None,
        eval_type="llm",
        config={"eval_type_id": "CustomPromptEvaluator", "output": "Pass/Fail"},
        eval_tags=["llm"],
    )
    prompt_template = PromptTemplate.objects.create(
        name="Workbench Prompt",
        organization=user.organization,
        workspace=workspace,
        created_by=user,
    )

    payload = {
        "id": str(template.id),
        "name": "eval_with_localizer",
        "mapping": {"output": "model_output"},
        "config": {},
        "error_localizer": True,
    }
    save_response = auth_client.post(
        f"/model-hub/prompt-templates/{prompt_template.id}/update-evaluation-configs/",
        data=payload,
        format="json",
    )
    assert save_response.status_code == 200, save_response.content

    saved = PromptEvalConfig.objects.get(
        prompt_template=prompt_template, deleted=False
    )
    assert saved.error_localizer is True

    read_response = auth_client.get(
        f"/model-hub/prompt-templates/{prompt_template.id}/evaluation-configs/"
    )
    row = read_response.json()["result"]["evaluation_configs"][0]
    assert row["run_config"]["error_localizer_enabled"] is True


@pytest.mark.django_db
def test_update_evaluation_configs_persists_pinned_version_id(
    auth_client, user, workspace
):
    template = EvalTemplate.objects.create(
        name="workbench-fixture-pin",
        description="",
        owner=OwnerChoices.USER.value,
        organization=user.organization,
        workspace=workspace,
        eval_type="llm",
        config={"eval_type_id": "CustomPromptEvaluator", "output": "Pass/Fail"},
        eval_tags=["llm"],
        criteria="be good",
        model="turing_large",
    )
    from model_hub.models.evals_metric import EvalTemplateVersion

    version = EvalTemplateVersion.objects.create_version(
        eval_template=template,
        criteria="be good",
        model="turing_large",
        config_snapshot={
            **(template.config or {}),
            "model": "turing_large",
        },
        user=user,
        organization=user.organization,
        workspace=workspace,
    )
    prompt_template = PromptTemplate.objects.create(
        name="Workbench Prompt Pin",
        organization=user.organization,
        workspace=workspace,
        created_by=user,
    )

    payload = {
        "id": str(template.id),
        "name": "eval_with_pin",
        "mapping": {"output": "model_output"},
        "model": "turing_large",
        "config": {
            "config": {},
            "run_config": {"model": "turing_large"},
        },
        "pinned_version_id": str(version.id),
        "is_run": False,
    }
    save_response = auth_client.post(
        f"/model-hub/prompt-templates/{prompt_template.id}/update-evaluation-configs/",
        data=payload,
        format="json",
    )
    assert save_response.status_code == 200, save_response.content
    body = save_response.json()["result"]
    assert body["pinned_version_id"] == str(version.id)

    saved = PromptEvalConfig.objects.get(
        prompt_template=prompt_template, deleted=False
    )
    assert str(saved.pinned_version_id) == str(version.id)

    read_response = auth_client.get(
        f"/model-hub/prompt-templates/{prompt_template.id}/evaluation-configs/"
    )
    row = read_response.json()["result"]["evaluation_configs"][0]
    assert row["pinned_version_id"] == str(version.id)


@pytest.mark.django_db
def test_update_evaluation_configs_version_only_switch_dedups(
    auth_client, user, workspace
):
    """Re-picking an existing version with matching config must not mint a new one."""
    template = EvalTemplate.objects.create(
        name="workbench-fixture-pin-dedup",
        description="",
        owner=OwnerChoices.USER.value,
        organization=user.organization,
        workspace=workspace,
        eval_type="llm",
        config={"eval_type_id": "CustomPromptEvaluator", "output": "Pass/Fail"},
        eval_tags=["llm"],
        criteria="be good",
        model="turing_large",
    )
    from model_hub.models.evals_metric import EvalTemplateVersion

    version = EvalTemplateVersion.objects.create_version(
        eval_template=template,
        criteria="be good",
        model="turing_large",
        config_snapshot={
            **(template.config or {}),
            "model": "turing_large",
        },
        user=user,
        organization=user.organization,
        workspace=workspace,
    )
    prompt_template = PromptTemplate.objects.create(
        name="Workbench Prompt Dedup",
        organization=user.organization,
        workspace=workspace,
        created_by=user,
    )
    binding = PromptEvalConfig.objects.create(
        name="eval_dedup",
        eval_template=template,
        prompt_template=prompt_template,
        mapping={"output": "model_output"},
        config={"run_config": {"model": "turing_large"}},
        pinned_version=version,
        user=user,
    )

    before = EvalTemplateVersion.objects.filter(
        eval_template=template, deleted=False
    ).count()
    save_response = auth_client.post(
        f"/model-hub/prompt-templates/{prompt_template.id}/update-evaluation-configs/",
        data={
            "id": str(template.id),
            "name": "eval_dedup",
            "mapping": {"output": "model_output"},
            "config": {
                "config": {},
                "run_config": {"model": "turing_large"},
            },
            "model": "turing_large",
            "user_eval_id": str(binding.id),
            "pinned_version_id": str(version.id),
            "is_run": False,
        },
        format="json",
    )
    assert save_response.status_code == 200, save_response.content
    after = EvalTemplateVersion.objects.filter(
        eval_template=template, deleted=False
    ).count()
    assert after == before
    binding.refresh_from_db()
    assert str(binding.pinned_version_id) == str(version.id)


@pytest.mark.django_db
def test_update_evaluation_configs_dirty_edit_creates_new_version(
    auth_client, user, workspace
):
    """Editing config against a pinned baseline mints and pins a new version."""
    from model_hub.models.evals_metric import EvalTemplateVersion

    template = EvalTemplate.objects.create(
        name="workbench-fixture-pin-dirty",
        description="",
        owner=OwnerChoices.USER.value,
        organization=user.organization,
        workspace=workspace,
        eval_type="llm",
        config={"eval_type_id": "CustomPromptEvaluator", "output": "Pass/Fail"},
        eval_tags=["llm"],
        criteria="be good",
        model="turing_large",
    )
    baseline = EvalTemplateVersion.objects.create_version(
        eval_template=template,
        criteria="be good",
        model="turing_large",
        config_snapshot={
            **(template.config or {}),
            "model": "turing_large",
        },
        user=user,
        organization=user.organization,
        workspace=workspace,
    )
    prompt_template = PromptTemplate.objects.create(
        name="Workbench Prompt Dirty",
        organization=user.organization,
        workspace=workspace,
        created_by=user,
    )
    binding = PromptEvalConfig.objects.create(
        name="eval_dirty",
        eval_template=template,
        prompt_template=prompt_template,
        mapping={"output": "model_output"},
        config={"run_config": {"model": "turing_large"}},
        pinned_version=baseline,
        user=user,
    )

    before = EvalTemplateVersion.objects.filter(
        eval_template=template, deleted=False
    ).count()
    save_response = auth_client.post(
        f"/model-hub/prompt-templates/{prompt_template.id}/update-evaluation-configs/",
        data={
            "id": str(template.id),
            "name": "eval_dirty",
            "mapping": {"output": "model_output"},
            "config": {
                # Nested template override differs from baseline snapshot.
                "config": {"rule_prompt": "be much stricter now"},
                "run_config": {"model": "turing_large"},
            },
            "model": "turing_large",
            "user_eval_id": str(binding.id),
            "pinned_version_id": str(baseline.id),
            "is_run": False,
        },
        format="json",
    )
    assert save_response.status_code == 200, save_response.content
    after = EvalTemplateVersion.objects.filter(
        eval_template=template, deleted=False
    ).count()
    assert after == before + 1

    binding.refresh_from_db()
    assert binding.pinned_version_id is not None
    assert str(binding.pinned_version_id) != str(baseline.id)
    assert save_response.json()["result"]["pinned_version_id"] == str(
        binding.pinned_version_id
    )
    assert "be much stricter now" in (
        binding.pinned_version.criteria
        or (binding.pinned_version.config_snapshot or {}).get("rule_prompt", "")
    )


@pytest.mark.django_db
def test_update_evaluation_configs_rejects_foreign_template_version(
    auth_client, user, workspace
):
    """pinned_version_id belonging to another eval template must 400."""
    from model_hub.models.evals_metric import EvalTemplateVersion

    template = EvalTemplate.objects.create(
        name="workbench-fixture-pin-self",
        description="",
        owner=OwnerChoices.USER.value,
        organization=user.organization,
        workspace=workspace,
        eval_type="llm",
        config={"eval_type_id": "CustomPromptEvaluator", "output": "Pass/Fail"},
        eval_tags=["llm"],
        criteria="self",
        model="turing_large",
    )
    other = EvalTemplate.objects.create(
        name="workbench-fixture-pin-other",
        description="",
        owner=OwnerChoices.USER.value,
        organization=user.organization,
        workspace=workspace,
        eval_type="llm",
        config={"eval_type_id": "CustomPromptEvaluator", "output": "Pass/Fail"},
        eval_tags=["llm"],
        criteria="other",
        model="turing_large",
    )
    foreign_version = EvalTemplateVersion.objects.create_version(
        eval_template=other,
        criteria="other",
        model="turing_large",
        config_snapshot=other.config or {},
        user=user,
        organization=user.organization,
        workspace=workspace,
    )
    prompt_template = PromptTemplate.objects.create(
        name="Workbench Prompt Foreign Pin",
        organization=user.organization,
        workspace=workspace,
        created_by=user,
    )

    save_response = auth_client.post(
        f"/model-hub/prompt-templates/{prompt_template.id}/update-evaluation-configs/",
        data={
            "id": str(template.id),
            "name": "eval_foreign_pin",
            "mapping": {"output": "model_output"},
            "config": {"run_config": {"model": "turing_large"}},
            "pinned_version_id": str(foreign_version.id),
            "is_run": False,
        },
        format="json",
    )
    assert save_response.status_code == 400, save_response.content
    assert "Selected version not found" in str(save_response.content)
    assert not PromptEvalConfig.objects.filter(
        prompt_template=prompt_template, deleted=False
    ).exists()


@pytest.mark.django_db
def test_run_evaluation_overlays_pinned_config_snapshot(user, workspace, monkeypatch):
    """Workbench runtime must prefer pinned config_snapshot over live template config."""
    from model_hub.models.evals_metric import EvalTemplateVersion
    from model_hub.views.prompt_template import PromptTemplateViewSet

    template = EvalTemplate.objects.create(
        name="workbench-fixture-pin-runtime",
        description="",
        owner=OwnerChoices.USER.value,
        organization=user.organization,
        workspace=workspace,
        eval_type="llm",
        config={
            "eval_type_id": "CustomPromptEvaluator",
            "output": "Pass/Fail",
            "rule_prompt": "live template criteria",
        },
        eval_tags=["llm"],
        criteria="live template criteria",
        model="turing_large",
    )
    pinned = EvalTemplateVersion.objects.create_version(
        eval_template=template,
        criteria="pinned criteria",
        model="gpt-4.1",
        config_snapshot={
            "eval_type_id": "CustomPromptEvaluator",
            "output": "Pass/Fail",
            "rule_prompt": "pinned criteria",
            "model": "gpt-4.1",
            "marker": "from-pinned-snapshot",
        },
        user=user,
        organization=user.organization,
        workspace=workspace,
    )
    prompt_template = PromptTemplate.objects.create(
        name="Workbench Prompt Runtime",
        organization=user.organization,
        workspace=workspace,
        created_by=user,
    )
    evaluation = PromptEvalConfig.objects.create(
        name="eval_runtime",
        eval_template=template,
        prompt_template=prompt_template,
        mapping={"output": "model_output"},
        config={"run_config": {"model": "gpt-4.1"}},
        pinned_version=pinned,
        user=user,
    )

    captured = {}

    class _FakeEvalClass:
        pass

    def _fake_get_eval_class(eval_type_id):
        captured["eval_type_id"] = eval_type_id
        return _FakeEvalClass

    def _fake_create_eval_instance(self, config=None, eval_class=None, model=None, **kwargs):
        captured["config"] = config
        captured["model"] = model
        captured["eval_class"] = eval_class
        raise RuntimeError("stop-after-config-capture")

    monkeypatch.setattr(
        "evaluations.engine.registry.get_eval_class", _fake_get_eval_class
    )
    monkeypatch.setattr(
        "model_hub.views.eval_runner.EvaluationRunner._create_eval_instance",
        _fake_create_eval_instance,
    )

    view = PromptTemplateViewSet()
    with pytest.raises(RuntimeError, match="stop-after-config-capture"):
        view.run_evaluation(
            evaluation,
            response="hello",
            messages=[{"role": "user", "content": "hi"}],
            variable_combination={},
            organization_id=user.organization_id,
            template=prompt_template,
        )

    # Config was captured before the intentional stop — pinned snapshot wins.
    assert captured["config"]["marker"] == "from-pinned-snapshot"
    assert captured["config"]["rule_prompt"] == "pinned criteria"
    assert "live template criteria" not in (
        captured["config"].get("rule_prompt") or ""
    )
    assert captured["eval_type_id"] == "CustomPromptEvaluator"


def test_workbench_eval_payload_forwards_pinned_version_id():
    """Document the FE contract: workbench save body must carry pinned_version_id."""
    # Mirrors EvaluationDrawer workbench branch payload construction.
    eval_config = {
        "templateId": "tpl-1",
        "name": "toxicity",
        "mapping": {"output": "model_output"},
        "model": "gpt-4.1",
        "config": {"rule_prompt": "check toxicity"},
        "params": {},
        "versionId": "11111111-1111-4111-8111-111111111111",
    }
    run_config = {"model": eval_config["model"]}
    payload = {
        "id": eval_config["templateId"],
        "name": eval_config["name"],
        "mapping": eval_config["mapping"] or {},
        "model": eval_config["model"],
        "config": {
            "config": eval_config["config"] or {},
            "params": eval_config["params"],
            "run_config": run_config,
        },
        "is_run": True,
        **(
            {"pinned_version_id": eval_config["versionId"]}
            if eval_config.get("versionId")
            else {}
        ),
    }
    assert payload["pinned_version_id"] == eval_config["versionId"]
    assert payload["config"]["config"]["rule_prompt"] == "check toxicity"
    assert "run_config" in payload["config"]
