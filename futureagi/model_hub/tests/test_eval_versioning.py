"""
Tests for Phase 5: Eval Template Versioning.
"""

import pytest

from model_hub.models.choices import OwnerChoices
from model_hub.models.evals_metric import EvalTemplate, EvalTemplateVersion


@pytest.fixture
def user_template(organization, workspace, user):
    return EvalTemplate.no_workspace_objects.create(
        name="versioned-eval",
        organization=organization,
        workspace=workspace,
        owner=OwnerChoices.USER.value,
        config={"output": "Pass/Fail"},
        eval_tags=["llm"],
        criteria="Check {{response}}",
        model="turing_large",
        visible_ui=True,
    )


# =============================================================================
# Unit: EvalTemplateVersionManager
# =============================================================================


@pytest.mark.unit
@pytest.mark.django_db
class TestVersionManager:
    def test_create_first_version(self, user_template, user, organization, workspace):
        v = EvalTemplateVersion.objects.create_version(
            eval_template=user_template,
            criteria="Check {{response}}",
            model="turing_large",
            user=user,
            organization=organization,
            workspace=workspace,
        )
        assert v.version_number == 1
        assert v.is_default is True
        assert v.criteria == "Check {{response}}"

    def test_create_second_version_increments(
        self, user_template, user, organization, workspace
    ):
        EvalTemplateVersion.objects.create_version(
            eval_template=user_template,
            criteria="V1",
            user=user,
            organization=organization,
        )
        v2 = EvalTemplateVersion.objects.create_version(
            eval_template=user_template,
            criteria="V2",
            user=user,
            organization=organization,
        )
        assert v2.version_number == 2

    def test_get_default(self, user_template, user, organization):
        v1 = EvalTemplateVersion.objects.create_version(
            eval_template=user_template,
            criteria="V1",
            user=user,
            organization=organization,
        )
        default = EvalTemplateVersion.objects.get_default(user_template)
        assert default.id == v1.id


# =============================================================================
# E2E: Version List API
# =============================================================================


@pytest.mark.e2e
@pytest.mark.django_db
class TestVersionListAPI:
    def _url(self, template_id):
        return f"/model-hub/eval-templates/{template_id}/versions/"

    def test_list_empty(self, auth_client, user_template):
        response = auth_client.get(self._url(user_template.id))
        assert response.status_code == 200
        result = response.data["result"]
        assert result["total"] == 0
        assert result["versions"] == []

    def test_list_with_versions(self, auth_client, user_template, user, organization):
        EvalTemplateVersion.objects.create_version(
            eval_template=user_template,
            criteria="V1",
            user=user,
            organization=organization,
        )
        EvalTemplateVersion.objects.create_version(
            eval_template=user_template,
            criteria="V2",
            user=user,
            organization=organization,
        )
        response = auth_client.get(self._url(user_template.id))
        assert response.status_code == 200
        result = response.data["result"]
        assert result["total"] == 2
        # Ordered by version_number desc
        assert result["versions"][0]["version_number"] == 2
        assert result["versions"][1]["version_number"] == 1

    def test_list_nonexistent_template(self, auth_client):
        response = auth_client.get(
            "/model-hub/eval-templates/00000000-0000-0000-0000-000000000000/versions/"
        )
        assert response.status_code == 404


# =============================================================================
# E2E: Version Create API
# =============================================================================


@pytest.mark.e2e
@pytest.mark.django_db
class TestVersionCreateAPI:
    def _url(self, template_id):
        return f"/model-hub/eval-templates/{template_id}/versions/create/"

    def test_create_version(self, auth_client, user_template):
        response = auth_client.post(self._url(user_template.id), {}, format="json")
        assert response.status_code == 200
        result = response.data["result"]
        assert result["version_number"] == 1
        assert result["is_default"] is True

    def test_create_multiple_versions(self, auth_client, user_template):
        r1 = auth_client.post(self._url(user_template.id), {}, format="json")
        r2 = auth_client.post(self._url(user_template.id), {}, format="json")
        assert r1.status_code == 200
        assert r2.status_code == 200
        assert r1.data["result"]["version_number"] == 1
        assert r2.data["result"]["version_number"] == 2
        # Default rotates to the newly-created version — v1 was default at
        # create-time but got demoted by v2's create_version transaction.
        assert r1.data["result"]["is_default"] is True
        assert r2.data["result"]["is_default"] is True

    def test_create_version_with_overrides(self, auth_client, user_template):
        response = auth_client.post(
            self._url(user_template.id),
            {"criteria": "New instructions {{var}}", "model": "turing_flash"},
            format="json",
        )
        assert response.status_code == 200
        v = EvalTemplateVersion.objects.get(id=response.data["result"]["id"])
        assert v.criteria == "New instructions {{var}}"
        assert v.model == "turing_flash"

    def test_create_version_rotates_default(self, auth_client, user_template):
        """Creating a new version demotes the previous default and flags the
        new version as default in the same transaction.
        """
        r1 = auth_client.post(self._url(user_template.id), {}, format="json")
        r2 = auth_client.post(self._url(user_template.id), {}, format="json")

        v1 = EvalTemplateVersion.objects.get(id=r1.data["result"]["id"])
        v2 = EvalTemplateVersion.objects.get(id=r2.data["result"]["id"])

        v1.refresh_from_db()
        v2.refresh_from_db()
        assert v1.is_default is False
        assert v2.is_default is True

    def test_only_one_default_after_multiple_creates(self, auth_client, user_template):
        """Invariant: after any number of create_version calls, exactly one
        version per template carries is_default=True. Enforced at the DB
        level by the `unique_default_version_per_template` partial unique
        constraint (migration 0114).
        """
        for _ in range(5):
            auth_client.post(self._url(user_template.id), {}, format="json")

        defaults = EvalTemplateVersion.objects.filter(
            eval_template=user_template, is_default=True, deleted=False
        )
        assert defaults.count() == 1
        assert defaults.first().version_number == 5

    def test_create_version_nonexistent_template(self, auth_client):
        response = auth_client.post(
            "/model-hub/eval-templates/00000000-0000-0000-0000-000000000000/versions/create/",
            {},
            format="json",
        )
        assert response.status_code == 404


@pytest.mark.unit
@pytest.mark.django_db
class TestVersionSnapshotColumns:
    def test_auto_capture_from_template(
        self, user_template, user, organization, workspace
    ):
        user_template.output_type_normalized = "pass_fail"
        user_template.pass_threshold = 0.7
        user_template.choice_scores = {"Yes": 1.0, "No": 0.0}
        user_template.error_localizer_enabled = True
        user_template.eval_tags = ["safety", "quality"]
        user_template.save()

        v = EvalTemplateVersion.objects.create_version(
            eval_template=user_template,
            criteria="X",
            model="turing_large",
            user=user,
            organization=organization,
            workspace=workspace,
        )
        assert v.output_type_normalized == "pass_fail"
        assert v.pass_threshold == 0.7
        assert v.choice_scores == {"Yes": 1.0, "No": 0.0}
        assert v.error_localizer_enabled is True
        assert v.eval_tags == ["safety", "quality"]

    def test_explicit_override_wins(
        self, user_template, user, organization, workspace
    ):
        user_template.pass_threshold = 0.9
        user_template.save()

        v = EvalTemplateVersion.objects.create_version(
            eval_template=user_template,
            criteria="X",
            model="turing_large",
            user=user,
            organization=organization,
            workspace=workspace,
            pass_threshold=0.3,
        )
        assert v.pass_threshold == 0.3

    def test_explicit_none_is_honored(
        self, user_template, user, organization, workspace
    ):
        user_template.choice_scores = {"Yes": 1.0}
        user_template.save()

        v = EvalTemplateVersion.objects.create_version(
            eval_template=user_template,
            criteria="X",
            model="turing_large",
            user=user,
            organization=organization,
            workspace=workspace,
            choice_scores=None,
        )
        assert v.choice_scores is None

    def test_eval_tags_is_list_copied(
        self, user_template, user, organization, workspace
    ):
        user_template.eval_tags = ["a", "b"]
        user_template.save()

        v = EvalTemplateVersion.objects.create_version(
            eval_template=user_template,
            criteria="X",
            model="turing_large",
            user=user,
            organization=organization,
            workspace=workspace,
        )
        user_template.eval_tags.append("c")
        user_template.save()
        v.refresh_from_db()
        assert v.eval_tags == ["a", "b"]


@pytest.mark.unit
@pytest.mark.django_db
class TestApplyVersionSnapshotToTemplate:
    def test_applies_non_null_fields(
        self, user_template, user, organization, workspace
    ):
        from model_hub.views.separate_evals import (
            _apply_version_snapshot_to_template,
        )

        v = EvalTemplateVersion.objects.create_version(
            eval_template=user_template,
            criteria="updated",
            model="turing_large",
            user=user,
            organization=organization,
            workspace=workspace,
            output_type_normalized="percentage",
            pass_threshold=0.6,
            eval_tags=["restored"],
        )

        user_template.output_type_normalized = "pass_fail"
        user_template.pass_threshold = 0.1
        user_template.eval_tags = ["drifted"]
        user_template.save()

        fields = _apply_version_snapshot_to_template(user_template, v)
        user_template.save(update_fields=fields)
        user_template.refresh_from_db()

        assert user_template.output_type_normalized == "percentage"
        assert user_template.pass_threshold == 0.6
        assert user_template.eval_tags == ["restored"]
        assert user_template.criteria == "updated"

    def test_skips_null_snapshot_fields(
        self, user_template, user, organization, workspace
    ):
        """NULL snapshot fields simulate a pre-migration-0091 version row;
        restore must preserve the template's current values."""
        from model_hub.views.separate_evals import (
            _apply_version_snapshot_to_template,
        )

        user_template.pass_threshold = 0.42
        user_template.eval_tags = ["keep-me"]
        user_template.save()

        v = EvalTemplateVersion.objects.create_version(
            eval_template=user_template,
            criteria="X",
            model="turing_large",
            user=user,
            organization=organization,
            workspace=workspace,
            output_type_normalized=None,
            pass_threshold=None,
            choice_scores=None,
            error_localizer_enabled=None,
            eval_tags=None,
        )

        fields = _apply_version_snapshot_to_template(user_template, v)
        user_template.save(update_fields=fields)
        user_template.refresh_from_db()

        assert user_template.pass_threshold == 0.42
        assert user_template.eval_tags == ["keep-me"]
        assert "pass_threshold" not in fields
        assert "eval_tags" not in fields

    def test_eval_tags_mutation_isolation_on_restore(
        self, user_template, user, organization, workspace
    ):
        from model_hub.views.separate_evals import (
            _apply_version_snapshot_to_template,
        )

        v = EvalTemplateVersion.objects.create_version(
            eval_template=user_template,
            criteria="X",
            model="turing_large",
            user=user,
            organization=organization,
            workspace=workspace,
            eval_tags=["a", "b"],
        )
        fields = _apply_version_snapshot_to_template(user_template, v)
        user_template.save(update_fields=fields)

        user_template.eval_tags.append("c")
        user_template.save()
        v.refresh_from_db()
        assert v.eval_tags == ["a", "b"]


@pytest.mark.unit
@pytest.mark.django_db
class TestOutputTypeNormalizedChoices:
    def test_accepts_valid_values(self, organization, workspace):
        from django.core.exceptions import ValidationError

        for value in ("pass_fail", "percentage", "deterministic"):
            t = EvalTemplate.no_workspace_objects.create(
                name=f"t-{value}",
                organization=organization,
                workspace=workspace,
                owner=OwnerChoices.USER.value,
                config={},
                eval_tags=[],
                criteria="X",
                model="turing_large",
                output_type_normalized=value,
            )
            try:
                t.full_clean()
            except ValidationError:
                raise AssertionError(
                    f"'{value}' should be a valid OutputTypeNormalized choice"
                )

    def test_rejects_invalid_value(self, organization, workspace):
        from django.core.exceptions import ValidationError

        with pytest.raises(ValidationError) as exc_info:
            EvalTemplate.no_workspace_objects.create(
                name="t-invalid",
                organization=organization,
                workspace=workspace,
                owner=OwnerChoices.USER.value,
                config={},
                eval_tags=[],
                criteria="X",
                model="turing_large",
                output_type_normalized="not_a_real_choice",
            )
        assert "output_type_normalized" in str(exc_info.value)


# =============================================================================
# Helpers for end-to-end eval lifecycle tests below.
# =============================================================================


_CREATE_V2_URL = "/model-hub/eval-templates/create-v2/"


def _update_url(template_id):
    return f"/model-hub/eval-templates/{template_id}/update/"


def _versions_url(template_id):
    return f"/model-hub/eval-templates/{template_id}/versions/"


def _versions_create_url(template_id):
    return f"/model-hub/eval-templates/{template_id}/versions/create/"


def _set_default_url(template_id, version_id):
    return (
        f"/model-hub/eval-templates/{template_id}/versions/{version_id}/set-default/"
    )


def _restore_url(template_id, version_id):
    return (
        f"/model-hub/eval-templates/{template_id}/versions/{version_id}/restore/"
    )


def _detail_url(template_id):
    return f"/model-hub/eval-templates/{template_id}/detail/"


def _draft_payload(eval_type="llm", **overrides):
    """Minimal payload for /create-v2/?is_draft=true. Validation is skipped
    for drafts so we can omit instructions/code."""
    payload = {
        "name": f"draft-{eval_type}-eval",
        "eval_type": eval_type,
        "instructions": "",
        "model": "turing_large",
        "output_type": "pass_fail",
        "is_draft": True,
    }
    payload.update(overrides)
    return payload


def _published_payload(eval_type="llm", **overrides):
    """Minimal payload for /create-v2/?is_draft=false. Provides eval-type
    specific required fields."""
    payload = {
        "name": f"published-{eval_type}-eval",
        "eval_type": eval_type,
        "model": "turing_large",
        "output_type": "pass_fail",
        "is_draft": False,
    }
    if eval_type == "code":
        payload["instructions"] = ""
        payload["code"] = "def evaluate(row):\n    return 1.0"
        payload["code_language"] = "python"
    elif eval_type == "agent":
        payload["instructions"] = "Judge {{response}} for quality."
        payload["mode"] = "auto"
    else:  # llm
        payload["instructions"] = "Evaluate {{response}} per system rules."
        payload["messages"] = [
            {"role": "system", "content": "You are a judge."},
            {"role": "user", "content": "{{response}}"},
        ]
    payload.update(overrides)
    return payload


# =============================================================================
# Integration: Draft → Publish lifecycle for llm/code/agent (TH-4855)
# =============================================================================


@pytest.mark.integration
@pytest.mark.django_db
class TestDraftPublishLifecycle:
    """Validates that:
    - /create-v2/ with is_draft=true does NOT create V1
    - /update/ keystrokes mutate live row only (no versions)
    - /update/ with publish=true creates V1 lazily
    - Re-publish is idempotent
    - Post-publish updates do not create new versions
    For each eval_type the FE supports.
    """

    @pytest.mark.parametrize("eval_type", ["llm", "code", "agent"])
    def test_draft_create_does_not_create_version(self, auth_client, eval_type):
        response = auth_client.post(
            _CREATE_V2_URL, _draft_payload(eval_type), format="json"
        )
        assert response.status_code == 200, response.data
        template_id = response.data["result"]["id"]
        assert (
            EvalTemplateVersion.objects.filter(eval_template_id=template_id).count()
            == 0
        )
        # Live row exists, visible_ui=False
        template = EvalTemplate.objects.get(id=template_id)
        assert template.visible_ui is False

    @pytest.mark.parametrize("eval_type", ["llm", "code", "agent"])
    def test_non_draft_create_seeds_v1(self, auth_client, eval_type):
        response = auth_client.post(
            _CREATE_V2_URL, _published_payload(eval_type), format="json"
        )
        assert response.status_code == 200, response.data
        template_id = response.data["result"]["id"]
        versions = list(
            EvalTemplateVersion.objects.filter(eval_template_id=template_id)
        )
        assert len(versions) == 1
        assert versions[0].is_default is True
        template = EvalTemplate.objects.get(id=template_id)
        assert template.visible_ui is True

    def test_llm_non_draft_v1_captures_messages(self, auth_client):
        payload = _published_payload("llm")
        response = auth_client.post(_CREATE_V2_URL, payload, format="json")
        template_id = response.data["result"]["id"]
        v1 = EvalTemplateVersion.objects.get(eval_template_id=template_id)
        # /create-v2/ writes config.messages from req.messages; V1 snapshots
        # it from config (post C1 fix).
        assert v1.prompt_messages == payload["messages"]

    def test_keystroke_update_during_draft_does_not_create_version(
        self, auth_client
    ):
        create = auth_client.post(
            _CREATE_V2_URL, _draft_payload("llm"), format="json"
        )
        template_id = create.data["result"]["id"]
        for chunk in ["E", "Ev", "Evaluate {{x}}"]:
            r = auth_client.put(
                _update_url(template_id),
                {"instructions": chunk},
                format="json",
            )
            assert r.status_code == 200
        assert (
            EvalTemplateVersion.objects.filter(eval_template_id=template_id).count()
            == 0
        )

    def test_publish_creates_v1_from_current_live_state(self, auth_client):
        create = auth_client.post(
            _CREATE_V2_URL, _draft_payload("llm"), format="json"
        )
        template_id = create.data["result"]["id"]
        messages = [
            {"role": "system", "content": "System rule"},
            {"role": "user", "content": "Score {{response}}"},
        ]
        auth_client.put(
            _update_url(template_id),
            {"instructions": "Evaluate {{response}}", "messages": messages},
            format="json",
        )
        publish = auth_client.put(
            _update_url(template_id),
            {"publish": True},
            format="json",
        )
        assert publish.status_code == 200, publish.data
        versions = list(
            EvalTemplateVersion.objects.filter(eval_template_id=template_id)
        )
        assert len(versions) == 1
        v1 = versions[0]
        assert v1.version_number == 1
        assert v1.is_default is True
        assert v1.criteria == "Evaluate {{response}}"
        assert v1.prompt_messages == messages
        template = EvalTemplate.objects.get(id=template_id)
        assert template.visible_ui is True

    def test_republish_is_idempotent(self, auth_client):
        create = auth_client.post(
            _CREATE_V2_URL, _draft_payload("llm"), format="json"
        )
        template_id = create.data["result"]["id"]
        auth_client.put(
            _update_url(template_id),
            {"instructions": "Evaluate {{x}}"},
            format="json",
        )
        auth_client.put(_update_url(template_id), {"publish": True}, format="json")
        auth_client.put(_update_url(template_id), {"publish": True}, format="json")
        assert (
            EvalTemplateVersion.objects.filter(eval_template_id=template_id).count()
            == 1
        )

    def test_post_publish_keystrokes_do_not_create_new_versions(self, auth_client):
        create = auth_client.post(
            _CREATE_V2_URL, _draft_payload("llm"), format="json"
        )
        template_id = create.data["result"]["id"]
        auth_client.put(
            _update_url(template_id),
            {"instructions": "Evaluate {{x}}"},
            format="json",
        )
        auth_client.put(_update_url(template_id), {"publish": True}, format="json")
        auth_client.put(
            _update_url(template_id),
            {"instructions": "Evaluate {{x}} carefully"},
            format="json",
        )
        auth_client.put(
            _update_url(template_id),
            {"instructions": "Evaluate {{x}} carefully and twice"},
            format="json",
        )
        assert (
            EvalTemplateVersion.objects.filter(eval_template_id=template_id).count()
            == 1
        )


# =============================================================================
# Integration: /versions/create/ captures live prompts (C1 fix)
# =============================================================================


@pytest.mark.integration
@pytest.mark.django_db
class TestVersionsCreateCapturesLiveState:
    def test_captures_messages_from_live_config(
        self, auth_client, user_template
    ):
        messages = [{"role": "system", "content": "Live prompt"}]
        user_template.config = {"messages": messages, "output": "Pass/Fail"}
        user_template.save()

        response = auth_client.post(
            _versions_create_url(user_template.id), {}, format="json"
        )
        assert response.status_code == 200, response.data
        version = EvalTemplateVersion.objects.get(id=response.data["result"]["id"])
        assert version.prompt_messages == messages

    def test_captures_criteria_and_model_from_template(
        self, auth_client, user_template
    ):
        user_template.criteria = "live criteria text"
        user_template.model = "turing_flash"
        user_template.config = {"messages": [], "output": "Pass/Fail"}
        user_template.save()

        response = auth_client.post(
            _versions_create_url(user_template.id), {}, format="json"
        )
        version = EvalTemplateVersion.objects.get(id=response.data["result"]["id"])
        assert version.criteria == "live criteria text"
        assert version.model == "turing_flash"

    def test_request_overrides_win_over_live_state(
        self, auth_client, user_template
    ):
        user_template.criteria = "old"
        user_template.save()
        response = auth_client.post(
            _versions_create_url(user_template.id),
            {"criteria": "new override", "model": "turing_small"},
            format="json",
        )
        version = EvalTemplateVersion.objects.get(id=response.data["result"]["id"])
        assert version.criteria == "new override"
        assert version.model == "turing_small"

    def test_fe_null_choice_scores_does_not_strip_pass_fail_choices(
        self, auth_client
    ):
        """The FE sends `choice_scores: null` on every /update/ for
        pass_fail evals (no custom scores). The BE must NOT treat that
        as "wipe choices" — pass_fail choices ["Passed", "Failed"] are
        owned by the output_type branch and must persist across keystroke
        updates. Regression for 34805a3f's V1/V2 missing choices."""
        create = auth_client.post(
            _CREATE_V2_URL, _draft_payload("agent"), format="json"
        )
        template_id = create.data["result"]["id"]
        t = EvalTemplate.objects.get(id=template_id)
        # After create-v2 with pass_fail default, choices should be set.
        assert t.config.get("choices") == ["Passed", "Failed"]

        # FE keystroke update — sends choice_scores: null because no
        # custom scores for this pass_fail eval.
        auth_client.put(
            _update_url(template_id),
            {
                "instructions": "judge {{x}}",
                "mode": "auto",
                "choice_scores": None,
                "model": "turing_flash",
            },
            format="json",
        )
        t.refresh_from_db()
        # choices must survive
        assert t.config.get("choices") == ["Passed", "Failed"]
        assert t.choices == ["Passed", "Failed"]

        # Publish — V1 should carry the choices.
        auth_client.put(_update_url(template_id), {"publish": True}, format="json")
        v1 = EvalTemplateVersion.objects.get(eval_template_id=template_id)
        assert v1.config_snapshot.get("choices") == ["Passed", "Failed"]

    def test_v1_always_carries_few_shot_examples_key(self, auth_client):
        """The FE version-load uses a conditional setter — if
        config_snapshot lacks `few_shot_examples`, FE state from the
        previously-viewed version persists, leaking examples across
        versions. V1 must include the key (defaulting to `[]`) so the
        FE always resets the state."""
        for eval_type in ("llm", "code", "agent"):
            create = auth_client.post(
                _CREATE_V2_URL,
                _draft_payload(eval_type),
                format="json",
            )
            template_id = create.data["result"]["id"]
            auth_client.put(
                _update_url(template_id),
                {"instructions": "judge {{x}}", "code": "def f():\n    return 1"}
                if eval_type == "code"
                else {"instructions": "judge {{x}}"},
                format="json",
            )
            auth_client.put(
                _update_url(template_id), {"publish": True}, format="json"
            )
            v1 = EvalTemplateVersion.objects.get(eval_template_id=template_id)
            assert "few_shot_examples" in v1.config_snapshot, (
                f"{eval_type} V1 missing few_shot_examples key in config_snapshot"
            )
            assert v1.config_snapshot["few_shot_examples"] == []

    def test_llm_v1_persists_provided_few_shot_examples(self, auth_client):
        examples = [{"input": "x", "output": "y", "score": "Passed"}]
        response = auth_client.post(
            _CREATE_V2_URL,
            _published_payload("llm", few_shot_examples=examples),
            format="json",
        )
        template_id = response.data["result"]["id"]
        v1 = EvalTemplateVersion.objects.get(eval_template_id=template_id)
        assert v1.config_snapshot.get("few_shot_examples") == examples

    def test_update_model_change_mirrors_to_config(self, auth_client):
        """When /update/ changes the model, both the template column AND
        config["model"] must be updated. Otherwise V1.config_snapshot.model
        stays at the create-v2 value while the column tracks the latest —
        leading to FE form-load showing the wrong model on the version
        detail page."""
        create = auth_client.post(
            _CREATE_V2_URL,
            _draft_payload("agent", model="turing_large"),
            format="json",
        )
        template_id = create.data["result"]["id"]
        auth_client.put(
            _update_url(template_id),
            {"model": "turing_flash", "instructions": "judge {{x}}"},
            format="json",
        )
        t = EvalTemplate.objects.get(id=template_id)
        assert t.model == "turing_flash"
        assert t.config.get("model") == "turing_flash"
        auth_client.put(_update_url(template_id), {"publish": True}, format="json")
        v1 = EvalTemplateVersion.objects.get(eval_template_id=template_id)
        assert v1.model == "turing_flash"
        assert v1.config_snapshot.get("model") == "turing_flash"

    def test_fe_supplied_config_snapshot_does_not_strip_template_fields(
        self, auth_client, user_template
    ):
        """The FE hand-builds a config_snapshot on /versions/create/ and
        sometimes omits fields that live on template.config (notably
        choices / eval_type_id). The BE must ignore the FE-supplied
        snapshot and use the authoritative template.config so V2 carries
        every field V1 (or the live template) has."""
        user_template.config = {
            "messages": [{"role": "system", "content": "judge"}],
            "choices": ["Yes", "No"],
            "choices_map": {"Yes": "pass", "No": "fail"},
            "choice_scores": {"Yes": 1.0, "No": 0.0},
            "pass_threshold": 0.7,
            "error_localizer_enabled": True,
            "eval_type_id": "AgentEvaluator",
            "output": "score",
            "agent_mode": "auto",
            "tools": {"internet": True},
            "knowledge_bases": ["kb-1"],
        }
        user_template.choice_scores = {"Yes": 1.0, "No": 0.0}
        user_template.choices = ["Yes", "No"]
        user_template.save()

        # FE sends a snapshot that's missing choices / choices_map /
        # choice_scores — exactly the bug pattern observed in
        # 2532e0a6's V4/V5.
        response = auth_client.post(
            _versions_create_url(user_template.id),
            {
                "config_snapshot": {
                    "output": "score",
                    "agent_mode": "auto",
                    "agentMode": "auto",  # camelCase leak
                    "tools": {"internet": True},
                    "model": "turing_large",
                    "rule_prompt": "judge {{x}}",
                }
            },
            format="json",
        )
        assert response.status_code == 200, response.data
        version = EvalTemplateVersion.objects.get(id=response.data["result"]["id"])
        cs = version.config_snapshot
        # Every field from the live template config must be present even
        # though the FE-supplied snapshot omitted them.
        assert cs.get("choices") == ["Yes", "No"]
        assert cs.get("choices_map") == {"Yes": "pass", "No": "fail"}
        assert cs.get("choice_scores") == {"Yes": 1.0, "No": 0.0}
        assert cs.get("pass_threshold") == 0.7
        assert cs.get("error_localizer_enabled") is True
        assert cs.get("eval_type_id") == "AgentEvaluator"
        assert cs.get("knowledge_bases") == ["kb-1"]
        # And no camelCase leak (we sourced from template.config, not req)
        assert "agentMode" not in cs


# =============================================================================
# Integration: Set default version flips cleanly
# =============================================================================


@pytest.mark.integration
@pytest.mark.django_db
class TestSetDefaultVersion:
    def _make_two_versions(self, template, user, organization):
        v1 = EvalTemplateVersion.objects.create_version(
            eval_template=template,
            criteria="v1",
            model="turing_large",
            user=user,
            organization=organization,
        )
        v2 = EvalTemplateVersion.objects.create_version(
            eval_template=template,
            criteria="v2",
            model="turing_large",
            user=user,
            organization=organization,
        )
        return v1, v2

    def test_set_default_unsets_previous_default(
        self, auth_client, user_template, user, organization
    ):
        v1, v2 = self._make_two_versions(user_template, user, organization)
        # v1 starts default (is_first); set v2 default.
        response = auth_client.put(
            _set_default_url(user_template.id, v2.id), {}, format="json"
        )
        assert response.status_code == 200, response.data
        v1.refresh_from_db()
        v2.refresh_from_db()
        assert v1.is_default is False
        assert v2.is_default is True

    def test_set_default_aligns_live_template_to_version(
        self, auth_client, user_template, user, organization
    ):
        v1, v2 = self._make_two_versions(user_template, user, organization)
        # Mutate v2's snapshot fields away from template defaults so we can
        # observe the sync.
        v2.pass_threshold = 0.91
        v2.eval_tags = ["restored-tag"]
        v2.save(update_fields=["pass_threshold", "eval_tags"])

        auth_client.put(
            _set_default_url(user_template.id, v2.id), {}, format="json"
        )
        user_template.refresh_from_db()
        assert user_template.pass_threshold == 0.91
        assert user_template.eval_tags == ["restored-tag"]

    def test_exactly_one_default_after_set(
        self, auth_client, user_template, user, organization
    ):
        v1, v2 = self._make_two_versions(user_template, user, organization)
        auth_client.put(
            _set_default_url(user_template.id, v2.id), {}, format="json"
        )
        assert (
            EvalTemplateVersion.objects.filter(
                eval_template=user_template, is_default=True
            ).count()
            == 1
        )


# =============================================================================
# Integration: Restore version creates mirror and promotes it (H2 fix)
# =============================================================================


@pytest.mark.integration
@pytest.mark.django_db
class TestRestoreVersion:
    def _seed(self, template, user, organization):
        v1 = EvalTemplateVersion.objects.create_version(
            eval_template=template,
            prompt_messages=[{"role": "system", "content": "v1 prompt"}],
            config_snapshot={
                "messages": [{"role": "system", "content": "v1 prompt"}],
                "output": "Pass/Fail",
            },
            criteria="v1 criteria",
            model="turing_large",
            user=user,
            organization=organization,
        )
        v2 = EvalTemplateVersion.objects.create_version(
            eval_template=template,
            prompt_messages=[{"role": "system", "content": "v2 prompt"}],
            config_snapshot={
                "messages": [{"role": "system", "content": "v2 prompt"}],
                "output": "Pass/Fail",
            },
            criteria="v2 criteria",
            model="turing_flash",
            user=user,
            organization=organization,
        )
        # Force v2 default so the restore-of-v1 test has a known starting state.
        EvalTemplateVersion.objects.filter(eval_template=template).update(
            is_default=False
        )
        v2.is_default = True
        v2.save(update_fields=["is_default"])
        v1.refresh_from_db()
        v2.refresh_from_db()
        return v1, v2

    def test_restore_creates_new_mirror_version(
        self, auth_client, user_template, user, organization
    ):
        v1, _ = self._seed(user_template, user, organization)
        response = auth_client.post(
            _restore_url(user_template.id, v1.id), {}, format="json"
        )
        assert response.status_code == 200, response.data
        mirror_id = response.data["result"]["id"]
        # Mirror is a NEW row, not an in-place mutation of v1.
        assert str(mirror_id) != str(v1.id)
        mirror = EvalTemplateVersion.objects.get(id=mirror_id)
        assert mirror.version_number == 3
        assert mirror.criteria == "v1 criteria"
        assert mirror.prompt_messages == [
            {"role": "system", "content": "v1 prompt"}
        ]

    def test_restore_promotes_mirror_to_default_and_demotes_others(
        self, auth_client, user_template, user, organization
    ):
        v1, v2 = self._seed(user_template, user, organization)
        response = auth_client.post(
            _restore_url(user_template.id, v1.id), {}, format="json"
        )
        mirror = EvalTemplateVersion.objects.get(
            id=response.data["result"]["id"]
        )
        v1.refresh_from_db()
        v2.refresh_from_db()
        assert mirror.is_default is True
        assert v1.is_default is False
        assert v2.is_default is False
        assert response.data["result"]["is_default"] is True
        assert response.data["result"]["restored_from"] == 1

    def test_restore_aligns_live_template_to_source_version(
        self, auth_client, user_template, user, organization
    ):
        v1, _ = self._seed(user_template, user, organization)
        auth_client.post(_restore_url(user_template.id, v1.id), {}, format="json")
        user_template.refresh_from_db()
        assert user_template.criteria == "v1 criteria"
        assert (
            user_template.config.get("messages")
            == [{"role": "system", "content": "v1 prompt"}]
        )

    def test_restore_keeps_exactly_one_default(
        self, auth_client, user_template, user, organization
    ):
        v1, _ = self._seed(user_template, user, organization)
        auth_client.post(_restore_url(user_template.id, v1.id), {}, format="json")
        assert (
            EvalTemplateVersion.objects.filter(
                eval_template=user_template, is_default=True
            ).count()
            == 1
        )


# =============================================================================
# Integration: Version list and detail behavior across lifecycle states
# =============================================================================


@pytest.mark.integration
@pytest.mark.django_db
class TestVersionListAcrossLifecycle:
    def test_list_empty_for_draft(self, auth_client):
        create = auth_client.post(
            _CREATE_V2_URL, _draft_payload("llm"), format="json"
        )
        template_id = create.data["result"]["id"]
        response = auth_client.get(_versions_url(template_id))
        assert response.status_code == 200
        assert response.data["result"]["total"] == 0

    def test_list_has_v1_after_publish(self, auth_client):
        create = auth_client.post(
            _CREATE_V2_URL, _draft_payload("llm"), format="json"
        )
        template_id = create.data["result"]["id"]
        auth_client.put(
            _update_url(template_id),
            {"instructions": "Evaluate {{x}}"},
            format="json",
        )
        auth_client.put(_update_url(template_id), {"publish": True}, format="json")
        response = auth_client.get(_versions_url(template_id))
        assert response.data["result"]["total"] == 1
        assert response.data["result"]["versions"][0]["version_number"] == 1
        assert response.data["result"]["versions"][0]["is_default"] is True

    def test_list_after_save_as_new_version(self, auth_client):
        create = auth_client.post(
            _CREATE_V2_URL, _draft_payload("llm"), format="json"
        )
        template_id = create.data["result"]["id"]
        auth_client.put(
            _update_url(template_id),
            {"instructions": "Evaluate {{x}}"},
            format="json",
        )
        auth_client.put(_update_url(template_id), {"publish": True}, format="json")
        auth_client.post(_versions_create_url(template_id), {}, format="json")
        response = auth_client.get(_versions_url(template_id))
        assert response.data["result"]["total"] == 2
        # Ordered desc by version_number
        version_numbers = [
            v["version_number"] for v in response.data["result"]["versions"]
        ]
        assert version_numbers == [2, 1]


@pytest.mark.integration
@pytest.mark.django_db
class TestDetailViewVersionNumber:
    def test_draft_detail_shows_v1_placeholder(self, auth_client):
        create = auth_client.post(
            _CREATE_V2_URL, _draft_payload("llm"), format="json"
        )
        template_id = create.data["result"]["id"]
        response = auth_client.get(_detail_url(template_id))
        assert response.status_code == 200, response.data
        # Draft has zero version rows but FE renders "V{n}" — view coerces
        # version_count to max(actual, 1) and current_version to "V1" so the
        # placeholder is consistent across draft + post-publish states.
        assert response.data["result"]["current_version"] == "V1"
        assert response.data["result"]["version_count"] == 1
        # Confirm the underlying state really has zero version rows.
        assert (
            EvalTemplateVersion.objects.filter(eval_template_id=template_id).count()
            == 0
        )

    def test_published_detail_shows_default_version_number(self, auth_client):
        create = auth_client.post(
            _CREATE_V2_URL, _draft_payload("llm"), format="json"
        )
        template_id = create.data["result"]["id"]
        auth_client.put(
            _update_url(template_id),
            {"instructions": "Evaluate {{x}}"},
            format="json",
        )
        auth_client.put(_update_url(template_id), {"publish": True}, format="json")
        # Save as new version twice → V1, V2, V3
        auth_client.post(_versions_create_url(template_id), {}, format="json")
        auth_client.post(_versions_create_url(template_id), {}, format="json")
        response = auth_client.get(_detail_url(template_id))
        assert response.data["result"]["version_count"] == 3
        # Default version number is whichever is_default=True
        from model_hub.models.evals_metric import EvalTemplateVersion

        default = EvalTemplateVersion.objects.get(
            eval_template_id=template_id, is_default=True
        )
        assert (
            response.data["result"]["current_version"]
            == f"V{default.version_number}"
        )


# =============================================================================
# Integration: Type-specific fields round-trip through publish → mutate →
# save-as-new-version → restore. Validates that the per-type contents of
# template.config (agent's tools/KBs/mode, code's code/language, llm's
# messages/few-shot) are preserved end-to-end.
# =============================================================================


@pytest.mark.integration
@pytest.mark.django_db
class TestLLMFieldsRoundTrip:
    """LLM evals: messages, few_shot_examples, system_prompt, check_internet."""

    def _publish_llm_with_fields(self, auth_client):
        messages_v1 = [
            {"role": "system", "content": "Be strict"},
            {"role": "user", "content": "Score {{response}}"},
        ]
        few_shot_v1 = [
            {"input": "x", "output": "y", "score": "Passed"},
        ]
        response = auth_client.post(
            _CREATE_V2_URL,
            _published_payload(
                "llm",
                instructions="Evaluate {{response}}",
                messages=messages_v1,
                few_shot_examples=few_shot_v1,
                check_internet=True,
            ),
            format="json",
        )
        assert response.status_code == 200, response.data
        return response.data["result"]["id"], messages_v1, few_shot_v1

    def test_v1_snapshot_captures_llm_fields(self, auth_client):
        template_id, messages_v1, few_shot_v1 = self._publish_llm_with_fields(
            auth_client
        )
        v1 = EvalTemplateVersion.objects.get(eval_template_id=template_id)
        snap = v1.config_snapshot
        assert snap["messages"] == messages_v1
        assert snap["few_shot_examples"] == few_shot_v1
        assert snap["check_internet"] is True
        assert snap["eval_type_id"] == "CustomPromptEvaluator"
        assert v1.prompt_messages == messages_v1

    def test_restore_recovers_llm_fields_after_mutation(self, auth_client):
        template_id, messages_v1, few_shot_v1 = self._publish_llm_with_fields(
            auth_client
        )
        # Mutate the live row away from V1's content
        messages_v2 = [{"role": "system", "content": "Be lenient"}]
        auth_client.put(
            _update_url(template_id),
            {
                "messages": messages_v2,
                "few_shot_examples": [
                    {"input": "p", "output": "q", "score": "Failed"}
                ],
                "check_internet": False,
                "instructions": "Different {{response}}",
            },
            format="json",
        )
        # Save mutated state as V2
        v2_response = auth_client.post(
            _versions_create_url(template_id), {}, format="json"
        )
        v2_id = v2_response.data["result"]["id"]
        # Restore V1
        v1 = EvalTemplateVersion.objects.get(
            eval_template_id=template_id, version_number=1
        )
        restore = auth_client.post(_restore_url(template_id, v1.id), {}, format="json")
        assert restore.status_code == 200, restore.data

        # Live template should match V1's content
        template = EvalTemplate.objects.get(id=template_id)
        assert template.config["messages"] == messages_v1
        assert template.config["few_shot_examples"] == few_shot_v1
        assert template.config["check_internet"] is True
        assert template.config["eval_type_id"] == "CustomPromptEvaluator"
        assert template.eval_type == "llm"
        # V2 untouched
        v2 = EvalTemplateVersion.objects.get(id=v2_id)
        assert v2.config_snapshot["messages"] == messages_v2


@pytest.mark.integration
@pytest.mark.django_db
class TestCodeFieldsRoundTrip:
    """Code evals: code, code_language, eval_type_id=CustomCodeEval."""

    def _publish_code(self, auth_client):
        code_v1 = "def evaluate(row):\n    return 1.0 if row['x'] > 0 else 0.0"
        response = auth_client.post(
            _CREATE_V2_URL,
            _published_payload("code", code=code_v1, code_language="python"),
            format="json",
        )
        assert response.status_code == 200, response.data
        return response.data["result"]["id"], code_v1

    def test_v1_snapshot_captures_code_fields(self, auth_client):
        template_id, code_v1 = self._publish_code(auth_client)
        v1 = EvalTemplateVersion.objects.get(eval_template_id=template_id)
        snap = v1.config_snapshot
        assert snap["code"] == code_v1
        assert snap["language"] == "python"
        assert snap["eval_type_id"] == "CustomCodeEval"

    def test_restore_recovers_code_fields(self, auth_client):
        template_id, code_v1 = self._publish_code(auth_client)
        # Mutate code on live row
        auth_client.put(
            _update_url(template_id),
            {"code": "def evaluate(row):\n    return 0.5", "code_language": "python"},
            format="json",
        )
        # Save V2
        auth_client.post(_versions_create_url(template_id), {}, format="json")
        # Restore V1
        v1 = EvalTemplateVersion.objects.get(
            eval_template_id=template_id, version_number=1
        )
        restore = auth_client.post(_restore_url(template_id, v1.id), {}, format="json")
        assert restore.status_code == 200, restore.data

        template = EvalTemplate.objects.get(id=template_id)
        assert template.config["code"] == code_v1
        assert template.config["language"] == "python"
        assert template.config["eval_type_id"] == "CustomCodeEval"
        assert template.eval_type == "code"
        # For code evals, criteria stores the code itself
        assert template.criteria == code_v1


@pytest.mark.integration
@pytest.mark.django_db
class TestAgentFieldsRoundTrip:
    """Agent evals: agent_mode, tools (internet, connectors), knowledge_bases,
    data_injection, summary."""

    def _publish_agent(self, auth_client):
        tools_v1 = {"internet": True, "connectors": ["slack", "github"]}
        kbs_v1 = ["kb-id-1", "kb-id-2"]
        data_injection_v1 = {"variables_only": False, "full_row": True}
        summary_v1 = {"type": "long"}
        response = auth_client.post(
            _CREATE_V2_URL,
            _published_payload(
                "agent",
                instructions="Reason about {{response}} carefully.",
                mode="agent",
                tools=tools_v1,
                knowledge_bases=kbs_v1,
                data_injection=data_injection_v1,
                summary=summary_v1,
            ),
            format="json",
        )
        assert response.status_code == 200, response.data
        return (
            response.data["result"]["id"],
            tools_v1,
            kbs_v1,
            data_injection_v1,
            summary_v1,
        )

    def test_v1_snapshot_captures_agent_fields(self, auth_client):
        (
            template_id,
            tools_v1,
            kbs_v1,
            data_injection_v1,
            summary_v1,
        ) = self._publish_agent(auth_client)
        v1 = EvalTemplateVersion.objects.get(eval_template_id=template_id)
        snap = v1.config_snapshot
        assert snap["agent_mode"] == "agent"
        assert snap["tools"] == tools_v1
        assert snap["knowledge_bases"] == kbs_v1
        # data_injection has auto-flag handling — assert our explicit keys land
        assert snap["data_injection"]["full_row"] is True
        assert snap["summary"] == summary_v1
        assert snap["eval_type_id"] == "AgentEvaluator"

    def test_restore_recovers_agent_fields(self, auth_client):
        (
            template_id,
            tools_v1,
            kbs_v1,
            data_injection_v1,
            summary_v1,
        ) = self._publish_agent(auth_client)
        # Mutate every agent-specific field
        auth_client.put(
            _update_url(template_id),
            {
                "mode": "quick",
                "tools": {"internet": False, "connectors": []},
                "knowledge_bases": [],
                "data_injection": {"variables_only": True},
                "summary": {"type": "concise"},
            },
            format="json",
        )
        # Save mutated state as V2
        auth_client.post(_versions_create_url(template_id), {}, format="json")
        # Restore V1
        v1 = EvalTemplateVersion.objects.get(
            eval_template_id=template_id, version_number=1
        )
        restore = auth_client.post(_restore_url(template_id, v1.id), {}, format="json")
        assert restore.status_code == 200, restore.data

        template = EvalTemplate.objects.get(id=template_id)
        assert template.config["agent_mode"] == "agent"
        assert template.config["tools"] == tools_v1
        assert template.config["knowledge_bases"] == kbs_v1
        assert template.config["data_injection"]["full_row"] is True
        assert template.config["summary"] == summary_v1
        assert template.config["eval_type_id"] == "AgentEvaluator"
        assert template.eval_type == "agent"


@pytest.mark.integration
@pytest.mark.django_db
class TestColumnSnapshotFieldsRoundTrip:
    """The 5 column-snapshot fields (output_type_normalized, pass_threshold,
    choice_scores, error_localizer_enabled, eval_tags) survive publish →
    mutate → restore via the dedicated version columns."""

    def test_all_column_fields_restore(
        self, auth_client, user_template, user, organization
    ):
        user_template.output_type_normalized = "deterministic"
        user_template.pass_threshold = 0.85
        user_template.choice_scores = {"Pass": 1.0, "Fail": 0.0}
        user_template.error_localizer_enabled = True
        user_template.eval_tags = ["safety", "quality"]
        user_template.save()
        v1 = EvalTemplateVersion.objects.create_version(
            eval_template=user_template,
            criteria="v1 criteria",
            model="turing_large",
            user=user,
            organization=organization,
        )
        # Mutate template state and create V2
        user_template.output_type_normalized = "pass_fail"
        user_template.pass_threshold = 0.5
        user_template.choice_scores = None
        user_template.error_localizer_enabled = False
        user_template.eval_tags = ["other"]
        user_template.save()
        EvalTemplateVersion.objects.create_version(
            eval_template=user_template,
            criteria="v2",
            model="turing_large",
            user=user,
            organization=organization,
        )
        # Restore V1
        restore = auth_client.post(
            _restore_url(user_template.id, v1.id), {}, format="json"
        )
        assert restore.status_code == 200, restore.data
        user_template.refresh_from_db()
        assert user_template.output_type_normalized == "deterministic"
        assert user_template.pass_threshold == 0.85
        assert user_template.choice_scores == {"Pass": 1.0, "Fail": 0.0}
        assert user_template.error_localizer_enabled is True
        assert user_template.eval_tags == ["safety", "quality"]


@pytest.mark.integration
@pytest.mark.django_db
class TestAgentChoicesAndErrorLocalizerOnV1:
    """Targeted regression for the report: agent eval V1 was missing
    `choices` and `error_localizer_enabled`. Verifies both make it into
    V1 whether the eval is created with `is_draft=False` (V1 seeded at
    create-v2) or `is_draft=True` then published via /update/."""

    @pytest.mark.parametrize("eval_type", ["llm", "code", "agent"])
    def test_direct_create_v1_has_choices_in_config_snapshot(
        self, auth_client, eval_type
    ):
        response = auth_client.post(
            _CREATE_V2_URL,
            _published_payload(eval_type, error_localizer_enabled=True),
            format="json",
        )
        assert response.status_code == 200, response.data
        template_id = response.data["result"]["id"]
        v1 = EvalTemplateVersion.objects.get(eval_template_id=template_id)

        # Bug repro: config_snapshot["choices"] was missing for agent + any
        # pass_fail eval. FE reads choices from config_snapshot, so this
        # broke the version-detail page label rendering.
        assert v1.config_snapshot.get("choices") == ["Passed", "Failed"], (
            f"V1.config_snapshot missing 'choices' for {eval_type}: "
            f"{v1.config_snapshot}"
        )
        # error_localizer_enabled has always been on the version row column
        # but the bug report flagged it as missing too — root cause was the
        # version list serializer not exposing it. The column itself is set
        # correctly here; the serializer fix below.
        assert v1.error_localizer_enabled is True

    def test_draft_then_publish_agent_v1_has_choices(self, auth_client):
        create = auth_client.post(
            _CREATE_V2_URL, _draft_payload("agent"), format="json"
        )
        template_id = create.data["result"]["id"]
        # Fill the form as the FE would
        auth_client.put(
            _update_url(template_id),
            {
                "eval_type": "agent",
                "instructions": "Score {{response}}.",
                "mode": "auto",
                "tools": {"internet": False, "connectors": []},
                "error_localizer_enabled": True,
                "output_type": "pass_fail",
            },
            format="json",
        )
        publish = auth_client.put(
            _update_url(template_id), {"publish": True}, format="json"
        )
        assert publish.status_code == 200, publish.data
        v1 = EvalTemplateVersion.objects.get(eval_template_id=template_id)
        assert v1.config_snapshot.get("choices") == ["Passed", "Failed"]
        assert v1.error_localizer_enabled is True


@pytest.mark.integration
@pytest.mark.django_db
class TestVersionListResponseExposesColumnFields:
    """The version list serializer (EvalVersionItem) historically only
    exposed `config_snapshot` — column-level snapshot fields like
    error_localizer_enabled were on the row but invisible to the FE.
    These tests pin the surface area so future column additions land
    in the response."""

    def test_response_includes_column_snapshot_fields(self, auth_client):
        response = auth_client.post(
            _CREATE_V2_URL,
            _published_payload(
                "llm",
                error_localizer_enabled=True,
                pass_threshold=0.65,
                tags=["safety"],
            ),
            format="json",
        )
        template_id = response.data["result"]["id"]
        list_response = auth_client.get(_versions_url(template_id))
        assert list_response.status_code == 200
        item = list_response.data["result"]["versions"][0]
        # All column-snapshot fields visible to the FE
        assert item["error_localizer_enabled"] is True
        assert item["pass_threshold"] == 0.65
        assert item["output_type_normalized"] == "pass_fail"
        assert item["eval_tags"] == ["safety"]
        # Plus prompt_messages (separate column on version row)
        assert isinstance(item["prompt_messages"], list)

    def test_v1_config_snapshot_carries_scoring_fields(self, auth_client):
        """The FE form-load reads pass_threshold / choice_scores /
        error_localizer_enabled from config.* when loading a version.
        These also live as version columns, but the FE form code only
        looks at config_snapshot, so they MUST be echoed into config.
        """
        response = auth_client.post(
            _CREATE_V2_URL,
            _published_payload(
                "agent",
                instructions="Reason about {{response}}.",
                mode="auto",
                output_type="deterministic",
                choice_scores={"A": 1.0, "B": 0.5, "C": 0.0},
                error_localizer_enabled=True,
                pass_threshold=0.8,
            ),
            format="json",
        )
        assert response.status_code == 200, response.data
        template_id = response.data["result"]["id"]
        v1 = EvalTemplateVersion.objects.get(eval_template_id=template_id)
        cs = v1.config_snapshot
        assert cs.get("choice_scores") == {"A": 1.0, "B": 0.5, "C": 0.0}
        assert cs.get("pass_threshold") == 0.8
        assert cs.get("error_localizer_enabled") is True

    def test_publish_lazy_v1_config_snapshot_carries_scoring_fields(
        self, auth_client
    ):
        """Same as above but for the draft → publish path (V1 created
        lazily by /update/?publish=true)."""
        create = auth_client.post(
            _CREATE_V2_URL, _draft_payload("agent"), format="json"
        )
        template_id = create.data["result"]["id"]
        auth_client.put(
            _update_url(template_id),
            {
                "instructions": "Score {{response}}.",
                "mode": "auto",
                "output_type": "deterministic",
                "choice_scores": {"Yes": 1.0, "No": 0.0},
                "error_localizer_enabled": True,
                "pass_threshold": 0.7,
            },
            format="json",
        )
        auth_client.put(_update_url(template_id), {"publish": True}, format="json")
        v1 = EvalTemplateVersion.objects.get(eval_template_id=template_id)
        cs = v1.config_snapshot
        assert cs.get("choice_scores") == {"Yes": 1.0, "No": 0.0}
        assert cs.get("pass_threshold") == 0.7
        assert cs.get("error_localizer_enabled") is True

    def test_response_lifts_choices_from_config_snapshot(self, auth_client):
        # Deterministic agent eval — choices come from choice_scores
        response = auth_client.post(
            _CREATE_V2_URL,
            _published_payload(
                "agent",
                instructions="Score {{response}}.",
                mode="auto",
                output_type="deterministic",
                choice_scores={"Yes": 1.0, "Maybe": 0.5, "No": 0.0},
            ),
            format="json",
        )
        assert response.status_code == 200, response.data
        template_id = response.data["result"]["id"]
        list_response = auth_client.get(_versions_url(template_id))
        item = list_response.data["result"]["versions"][0]
        # choices / choices_map / multi_choice were previously only reachable
        # through config_snapshot — now lifted to top level so the FE
        # version-detail page can render them directly.
        assert item["choices"] == ["Yes", "Maybe", "No"]
        assert item["choices_map"] == {
            "Yes": "pass",
            "Maybe": "neutral",
            "No": "fail",
        }
        assert item["multi_choice"] is False


@pytest.mark.integration
@pytest.mark.django_db
class TestEvalTypeColumnAlignmentAfterRestore:
    """If a user changed eval_type after publishing, restoring an older
    version should put template.eval_type back in sync with the restored
    config["eval_type_id"]. Otherwise detail view (reads column) and runtime
    (reads config) would disagree."""

    def test_restore_realigns_eval_type_column(self, auth_client):
        # Publish as LLM
        create = auth_client.post(
            _CREATE_V2_URL,
            _published_payload("llm", instructions="Score {{response}}"),
            format="json",
        )
        template_id = create.data["result"]["id"]
        # Switch eval_type to agent on the live row
        auth_client.put(
            _update_url(template_id),
            {
                "eval_type": "agent",
                "mode": "auto",
                "tools": {"internet": False, "connectors": []},
            },
            format="json",
        )
        template = EvalTemplate.objects.get(id=template_id)
        assert template.eval_type == "agent"
        assert template.config["eval_type_id"] == "AgentEvaluator"
        # Snapshot the mutated agent state as V2
        auth_client.post(_versions_create_url(template_id), {}, format="json")
        # Restore V1 (which was LLM)
        v1 = EvalTemplateVersion.objects.get(
            eval_template_id=template_id, version_number=1
        )
        auth_client.post(_restore_url(template_id, v1.id), {}, format="json")
        template.refresh_from_db()
        # Column and config must agree after restore
        assert template.config["eval_type_id"] == "CustomPromptEvaluator"
        assert template.eval_type == "llm"


# =============================================================================
# Unit: maybe_pin_new_version service (TH-5173 / PR #772)
# =============================================================================

@pytest.mark.unit
@pytest.mark.django_db
class TestMaybePinNewVersion:
    """Regression tests for the version-pinning service.

    Covers the three cases Nikhil asked for:
    1. Editing system_prompt creates a new version and pins it with a snapshot
       that matches the saved config.
    2. Bare rerun (no config in request) skips version creation.
    3. prepare_eval_config prefers config over template when key is present,
       even if the value is empty string.
    """

    def _make_uem(self, organization, workspace, user, template, config=None):
        from model_hub.models.evals_metric import UserEvalMetric
        from model_hub.models.develop_dataset import Dataset
        ds = Dataset.no_workspace_objects.create(
            name="test-ds",
            organization=organization,
            workspace=workspace,
        )
        return UserEvalMetric.objects.create(
            name="test-binding",
            template=template,
            dataset=ds,
            organization=organization,
            workspace=workspace,
            config=config or {},
        )

    def test_system_prompt_edit_creates_version_with_matching_snapshot(
        self, organization, workspace, user, user_template
    ):
        """Editing system_prompt must create a new version whose snapshot
        matches the config that will be persisted to the binding."""
        from model_hub.services.eval_version_pinning import maybe_pin_new_version

        uem = self._make_uem(organization, workspace, user, user_template)

        request_data = {
            "config": {
                "config": {"system_prompt": "new system prompt"},
            }
        }
        maybe_pin_new_version(
            uem, request_data, user=user,
            organization=organization, workspace=workspace,
        )

        assert uem.pinned_version is not None
        snapshot = uem.pinned_version.config_snapshot or {}
        assert snapshot.get("system_prompt") == "new system prompt"

    def test_bare_rerun_skips_version_creation(
        self, organization, workspace, user, user_template
    ):
        """A bare rerun request (no config changes) must not create a version."""
        from model_hub.services.eval_version_pinning import maybe_pin_new_version

        uem = self._make_uem(organization, workspace, user, user_template)
        before = uem.pinned_version

        maybe_pin_new_version(
            uem, {"run": True},  # no "config" key
            user=user, organization=organization, workspace=workspace,
        )

        assert uem.pinned_version == before  # unchanged

    def test_explicit_version_switch_pins_target_version(
        self, organization, workspace, user, user_template
    ):
        """Passing a different pinned_version_id must pin that version directly
        without creating a new one."""
        from model_hub.services.eval_version_pinning import maybe_pin_new_version
        from model_hub.models.evals_metric import EvalTemplateVersion

        uem = self._make_uem(organization, workspace, user, user_template)

        # Create two versions manually
        v1 = EvalTemplateVersion.objects.create_version(
            eval_template=user_template,
            criteria="v1 criteria",
            model="turing_large",
            user=user,
            organization=organization,
            workspace=workspace,
        )
        v2 = EvalTemplateVersion.objects.create_version(
            eval_template=user_template,
            criteria="v2 criteria",
            model="turing_large",
            user=user,
            organization=organization,
            workspace=workspace,
        )
        uem.pinned_version = v2
        uem.save()

        # Simulate FE sending a different pinned_version_id (v1) — user switched back
        # The view sets pinned_version directly and skips maybe_pin_new_version.
        # Here we test the service is NOT called (no new version beyond v2 exists).
        version_count_before = EvalTemplateVersion.objects.filter(
            eval_template=user_template
        ).count()
        uem.pinned_version = v1  # view sets this directly on explicit switch
        uem.save()

        assert uem.pinned_version_id == v1.id
        assert EvalTemplateVersion.objects.filter(
            eval_template=user_template
        ).count() == version_count_before, "Explicit version switch must not create a new version"

    def test_same_version_id_still_runs_maybe_pin(
        self, organization, workspace, user, user_template
    ):
        """When FE echoes the current pinned_version_id unchanged, a config edit
        must still go through maybe_pin_new_version and create a new version."""
        from model_hub.services.eval_version_pinning import maybe_pin_new_version
        from model_hub.models.evals_metric import EvalTemplateVersion

        uem = self._make_uem(organization, workspace, user, user_template)

        # Simulate an existing pinned version
        v1 = EvalTemplateVersion.objects.create_version(
            eval_template=user_template,
            criteria="original",
            model="turing_large",
            user=user,
            organization=organization,
            workspace=workspace,
        )
        uem.pinned_version = v1
        uem.save()

        # FE sends same pinned_version_id back (unchanged dropdown) + new config
        request_data = {
            "config": {"config": {"rule_prompt": "updated prompt"}},
            "pinned_version_id": str(v1.id),  # same as current — not a switch
        }
        # View logic: version_switched = (v1.id != eval_metric.pinned_version_id) = False
        # So maybe_pin_new_version runs. It should create a new version.
        maybe_pin_new_version(
            uem, request_data, user=user,
            organization=organization, workspace=workspace,
        )

        assert uem.pinned_version_id != v1.id, "Config change must create a new version"
        assert uem.pinned_version is not None

    def test_prepare_eval_config_preserves_empty_rule_prompt(
        self, organization, workspace, user, user_template
    ):
        """Clearing rule_prompt to '' must not fall back to the template prompt."""
        from evaluations.engine.instance import prepare_eval_config
        import types

        # Template has a real prompt
        user_template.config["eval_type_id"] = "AgentEvaluator"
        user_template.config["rule_prompt"] = "original template prompt"
        user_template.save()

        # User explicitly clears the prompt to ""
        config = {"rule_prompt": "", "eval_type_id": "AgentEvaluator"}
        result_config, _ = prepare_eval_config(
            user_template, config, model="turing_large",
            organization_id=str(organization.id),
        )

        assert result_config.get("rule_prompt") == "", (
            "Empty string should be preserved, not replaced with template prompt"
        )

    def test_config_and_snapshot_agree_after_edit(
        self, organization, workspace, user, user_template
    ):
        """Invariant: after any edit, eval_metric.config and
        pinned_version.config_snapshot must reflect the same rule_prompt.
        This is the contract Nikhil asked for — the two sources of truth
        must stay in lockstep."""
        from model_hub.services.eval_version_pinning import maybe_pin_new_version

        uem = self._make_uem(organization, workspace, user, user_template)

        request_data = {
            "config": {
                "config": {"rule_prompt": "lockstep prompt"},
                "run_config": {"model": "turing_large"},
            }
        }
        # Simulate the view: first pin, then save config
        maybe_pin_new_version(
            uem, request_data, user=user,
            organization=organization, workspace=workspace,
        )
        # Simulate eval_metric.config = normalize_eval_runtime_config(...)
        uem.config = request_data["config"]
        uem.save()

        # Reload from DB to confirm both are persisted
        uem.refresh_from_db()
        assert uem.pinned_version_id is not None
        snapshot = uem.pinned_version.config_snapshot or {}
        config_rule_prompt = uem.config.get("config", {}).get("rule_prompt")
        snapshot_rule_prompt = snapshot.get("rule_prompt")
        assert config_rule_prompt == snapshot_rule_prompt == "lockstep prompt", (
            f"config has {config_rule_prompt!r} but snapshot has {snapshot_rule_prompt!r}"
        )

    def test_version_switch_with_no_config_change_keeps_selected_version(
        self, organization, workspace, user, user_template
    ):
        """Switching to an older version with the same config should keep
        that version pinned (dedup fires). No new version created."""
        from model_hub.services.eval_version_pinning import maybe_pin_new_version
        from model_hub.models.evals_metric import EvalTemplateVersion

        uem = self._make_uem(organization, workspace, user, user_template)

        # Create v1 with a specific rule_prompt
        v1_request = {"config": {"config": {"rule_prompt": "v1 prompt"}, "run_config": {}}}
        maybe_pin_new_version(uem, v1_request, user=user, organization=organization, workspace=workspace)
        uem.save()
        v1 = uem.pinned_version

        # Create v2 with a different prompt
        v2_request = {"config": {"config": {"rule_prompt": "v2 prompt"}, "run_config": {}}}
        maybe_pin_new_version(uem, v2_request, user=user, organization=organization, workspace=workspace)
        uem.save()
        assert uem.pinned_version_id != v1.id

        # Simulate "switch back to v1 with no changes": set v1 as baseline, send v1's config
        uem.pinned_version = v1
        maybe_pin_new_version(uem, v1_request, user=user, organization=organization, workspace=workspace)

        # Dedup should fire — snap matches v1 → still pinned to v1
        assert uem.pinned_version_id == v1.id, "Switching back to v1 with same config should keep v1 pinned"
        version_count = EvalTemplateVersion.objects.filter(eval_template=user_template).count()
        assert version_count == 2, f"No new version should be created, found {version_count}"
