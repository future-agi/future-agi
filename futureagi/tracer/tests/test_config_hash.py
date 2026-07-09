"""Tests for the transitive eval config hash (the "did the eval change?" axis)."""

import pytest

from model_hub.models.develop_dataset import KnowledgeBaseFile
from model_hub.models.evals_metric import (
    CompositeEvalChild,
    EvalTemplate,
    EvalTemplateVersion,
)
from tracer.models.custom_eval_config import CustomEvalConfig
from tracer.services.eval_tasks.config_hash import resolved_config_hash


def _hash(config):
    config.refresh_from_db()
    return resolved_config_hash(config)


def _single_template(organization, workspace, **overrides):
    kwargs = {
        "name": f"tmpl-{EvalTemplate.objects.count()}",
        "organization": organization,
        "workspace": workspace,
        "config": {"type": "pass_fail", "criteria": "base"},
    }
    kwargs.update(overrides)
    return EvalTemplate.objects.create(**kwargs)


def _config_for(project, template, **overrides):
    kwargs = {
        "name": f"cfg-{CustomEvalConfig.objects.count()}",
        "project": project,
        "eval_template": template,
        "config": {"threshold": 0.8},
        "mapping": {"input": "input", "output": "output"},
        "filters": {},
    }
    kwargs.update(overrides)
    return CustomEvalConfig.objects.create(**kwargs)


@pytest.mark.integration
@pytest.mark.django_db
class TestDeterminismAndCosmetic:
    def test_same_config_same_hash(self, custom_eval_config):
        assert resolved_config_hash(custom_eval_config) == resolved_config_hash(
            custom_eval_config
        )

    def test_hash_is_64_char_hex(self, custom_eval_config):
        digest = resolved_config_hash(custom_eval_config)
        assert len(digest) == 64
        int(digest, 16)  # raises if not hex

    def test_json_key_order_does_not_matter(self, project, organization, workspace):
        t1 = _single_template(organization, workspace, config={"a": 1, "b": 2})
        t2 = _single_template(organization, workspace, config={"b": 2, "a": 1})
        c1 = _config_for(
            project, t1, config={"x": 1, "y": 2}, mapping={"i": "a", "o": "b"}
        )
        c2 = _config_for(
            project, t2, config={"y": 2, "x": 1}, mapping={"o": "b", "i": "a"}
        )
        assert _hash(c1) == _hash(c2)

    @pytest.mark.parametrize(
        "field,value",
        [
            ("name", "renamed"),
            ("description", "totally different prose"),
            ("eval_tags", ["a", "b"]),
            ("visible_ui", False),
            ("allow_edit", False),
            ("allow_copy", False),
        ],
    )
    def test_cosmetic_template_edits_do_not_change_hash(
        self, custom_eval_config, field, value
    ):
        before = _hash(custom_eval_config)
        template = custom_eval_config.eval_template
        setattr(template, field, value)
        template.save()
        assert _hash(custom_eval_config) == before

    @pytest.mark.parametrize("field,value", [("eval_id", 999), ("proxy_agi", False)])
    def test_non_output_template_fields_excluded(
        self, custom_eval_config, field, value
    ):
        # eval_id (seed/lookup) and proxy_agi (routing) don't determine per-row
        # output, so editing them must not change the hash.
        before = _hash(custom_eval_config)
        template = custom_eval_config.eval_template
        setattr(template, field, value)
        template.save()
        assert _hash(custom_eval_config) == before


@pytest.mark.integration
@pytest.mark.django_db
class TestConfigInstanceFields:
    @pytest.mark.parametrize(
        "field,value",
        [
            ("config", {"threshold": 0.1}),
            ("mapping", {"input": "different"}),
            ("model", "protect"),
            ("error_localizer", True),
        ],
    )
    def test_output_determining_config_fields_change_hash(
        self, custom_eval_config, field, value
    ):
        before = _hash(custom_eval_config)
        setattr(custom_eval_config, field, value)
        custom_eval_config.save()
        assert _hash(custom_eval_config) != before

    def test_config_filters_excluded(self, custom_eval_config):
        # filters scope WHICH rows, not per-row output — they feed the sampler.
        before = _hash(custom_eval_config)
        custom_eval_config.filters = {"some": "filter"}
        custom_eval_config.save()
        assert _hash(custom_eval_config) == before

    def test_identity_fields_excluded(self, custom_eval_config):
        before = _hash(custom_eval_config)
        custom_eval_config.name = "a brand new name"
        custom_eval_config.save()
        assert _hash(custom_eval_config) == before


@pytest.mark.integration
@pytest.mark.django_db
class TestTemplateOutputFields:
    @pytest.mark.parametrize(
        "field,value",
        [
            ("config", {"type": "pass_fail", "criteria": "changed"}),
            ("criteria", "a different rubric"),
            ("choices", ["yes", "no", "maybe"]),
            ("multi_choice", True),
            ("model", "gpt-some-other"),
            ("pass_threshold", 0.9),
            ("choice_scores", {"yes": 1.0, "no": 0.0}),
            ("output_type_normalized", "percentage"),
            ("eval_type", "code"),
            ("template_type", "composite"),
            ("error_localizer_enabled", True),
            ("evaluator_id", "12345678-1234-5678-1234-567812345678"),
        ],
    )
    def test_template_output_field_changes_hash(self, custom_eval_config, field, value):
        before = _hash(custom_eval_config)
        template = custom_eval_config.eval_template
        setattr(template, field, value)
        template.save()
        assert _hash(custom_eval_config) != before


@pytest.mark.integration
@pytest.mark.django_db
class TestCompositeRecursion:
    def _composite_config(self, project, organization, workspace):
        parent = _single_template(
            organization,
            workspace,
            template_type="composite",
            aggregation_function="weighted_avg",
            composite_child_axis="pass_fail",
        )
        child_a = _single_template(organization, workspace, criteria="child a")
        child_b = _single_template(organization, workspace, criteria="child b")
        link_a = CompositeEvalChild.objects.create(
            parent=parent, child=child_a, order=0, weight=1.0
        )
        link_b = CompositeEvalChild.objects.create(
            parent=parent, child=child_b, order=1, weight=2.0
        )
        config = _config_for(project, parent)
        return config, parent, child_a, child_b, link_a, link_b

    def test_child_template_edit_changes_parent_hash(
        self, project, organization, workspace
    ):
        config, parent, child_a, *_ = self._composite_config(
            project, organization, workspace
        )
        before = _hash(config)
        child_a.criteria = "child a, revised"
        child_a.save()
        assert _hash(config) != before

    def test_child_weight_change_changes_hash(self, project, organization, workspace):
        config, parent, child_a, child_b, link_a, link_b = self._composite_config(
            project, organization, workspace
        )
        before = _hash(config)
        link_a.weight = 5.0
        link_a.save()
        assert _hash(config) != before

    def test_per_binding_config_change_changes_hash(
        self, project, organization, workspace
    ):
        config, parent, child_a, child_b, link_a, link_b = self._composite_config(
            project, organization, workspace
        )
        before = _hash(config)
        link_a.config = {"override": "x"}
        link_a.save()
        assert _hash(config) != before

    @pytest.mark.parametrize(
        "field,value",
        [
            ("aggregation_function", "min"),
            ("aggregation_enabled", False),
            ("composite_child_axis", "percentage"),
        ],
    )
    def test_aggregation_settings_change_hash(
        self, project, organization, workspace, field, value
    ):
        config, parent, *_ = self._composite_config(project, organization, workspace)
        before = _hash(config)
        setattr(parent, field, value)
        parent.save()
        assert _hash(config) != before

    def test_removing_a_child_changes_hash(self, project, organization, workspace):
        config, parent, child_a, child_b, link_a, link_b = self._composite_config(
            project, organization, workspace
        )
        before = _hash(config)
        link_b.delete()  # soft-delete
        assert _hash(config) != before

    def test_self_referential_composite_terminates(
        self, project, organization, workspace
    ):
        # A -> B -> A must not infinitely recurse (cycle guard).
        a = _single_template(organization, workspace, template_type="composite")
        b = _single_template(organization, workspace, template_type="composite")
        CompositeEvalChild.objects.create(parent=a, child=b, order=0)
        CompositeEvalChild.objects.create(parent=b, child=a, order=0)
        config = _config_for(project, a)
        assert len(resolved_config_hash(config)) == 64


@pytest.mark.integration
@pytest.mark.django_db
class TestPinnedVersion:
    def _pinned_composite(self, project, organization, workspace):
        parent = _single_template(organization, workspace, template_type="composite")
        child = _single_template(organization, workspace, criteria="live child")
        version = EvalTemplateVersion.objects.create(
            eval_template=child,
            version_number=1,
            criteria="pinned v1",
            model="m1",
            organization=organization,
            workspace=workspace,
        )
        link = CompositeEvalChild.objects.create(
            parent=parent, child=child, order=0, pinned_version=version
        )
        config = _config_for(project, parent)
        return config, child, version, link

    def test_live_child_edit_does_not_change_pinned_hash(
        self, project, organization, workspace
    ):
        config, child, version, link = self._pinned_composite(
            project, organization, workspace
        )
        before = _hash(config)
        child.criteria = "live child edited after pin"
        child.save()
        assert _hash(config) == before

    def test_repinning_changes_hash(self, project, organization, workspace):
        config, child, version, link = self._pinned_composite(
            project, organization, workspace
        )
        before = _hash(config)
        v2 = EvalTemplateVersion.objects.create(
            eval_template=child,
            version_number=2,
            criteria="pinned v2",
            model="m2",
            organization=organization,
            workspace=workspace,
        )
        link.pinned_version = v2
        link.save()
        assert _hash(config) != before

    def test_repin_error_localizer_only_changes_hash(
        self, project, organization, workspace
    ):
        # v1/v2 are identical except error_localizer_enabled, which the pinned
        # snapshot omits — repinning must still flip the hash.
        parent = _single_template(organization, workspace, template_type="composite")
        child = _single_template(organization, workspace, criteria="live child")
        common = {
            "eval_template": child,
            "criteria": "same",
            "model": "same-model",
            "organization": organization,
            "workspace": workspace,
        }
        v1 = EvalTemplateVersion.objects.create(
            version_number=1, error_localizer_enabled=False, **common
        )
        v2 = EvalTemplateVersion.objects.create(
            version_number=2, error_localizer_enabled=True, **common
        )
        link = CompositeEvalChild.objects.create(
            parent=parent, child=child, order=0, pinned_version=v1
        )
        config = _config_for(project, parent)
        before = _hash(config)
        link.pinned_version = v2
        link.save()
        assert _hash(config) != before


@pytest.mark.integration
@pytest.mark.django_db
class TestSharedTemplateAndKB:
    def test_identical_resolved_configs_hash_equal(
        self, project, organization, workspace
    ):
        template = _single_template(organization, workspace)
        c1 = _config_for(project, template)
        c2 = _config_for(project, template)
        assert _hash(c1) == _hash(c2)

    def test_shared_template_edit_flips_all_dependents(
        self, project, organization, workspace
    ):
        template = _single_template(organization, workspace)
        c1 = _config_for(project, template)
        c2 = _config_for(project, template)
        b1, b2 = _hash(c1), _hash(c2)
        template.criteria = "edited once, affects both"
        template.save()
        assert _hash(c1) != b1
        assert _hash(c2) != b2

    def test_kb_attach_changes_hash(self, project, organization, workspace):
        template = _single_template(organization, workspace)
        config = _config_for(project, template)
        before = _hash(config)
        kb = KnowledgeBaseFile.objects.create(name="kb1", organization=organization)
        config.kb_id = kb
        config.save()
        assert _hash(config) != before

    def test_kb_swap_changes_hash(self, project, organization, workspace):
        template = _single_template(organization, workspace)
        kb1 = KnowledgeBaseFile.objects.create(name="kb1", organization=organization)
        kb2 = KnowledgeBaseFile.objects.create(name="kb2", organization=organization)
        config = _config_for(project, template, kb_id=kb1)
        before = _hash(config)
        config.kb_id = kb2
        config.save()
        assert _hash(config) != before
