"""E2E-SMK-02: every system eval template has a version after seeding.

On a fresh database no system template had an ``EvalTemplateVersion``, so a run
that bound one was refused ("evaluator has no valid resolved version"). The
seeder now gives each system template a default version that matches it, adds
the next version when the seeded content changes, and changes nothing about how
an evaluation executes.
"""

import copy
import uuid

import pytest
from django.core.cache import cache
from django.urls import reverse
from rest_framework import status

from model_hub.management.commands import seed_system_evals
from model_hub.management.commands.seed_system_evals import (
    SYSTEM_EVALS_VERSION,
    load_yaml_evals,
    seed_evals,
    sync_system_template_versions,
)
from model_hub.models.dataset_versions import DatasetVersion, DatasetVersionItem
from model_hub.models.develop_dataset import Dataset
from model_hub.models.evals_metric import (
    EvalTemplate,
    EvalTemplateVersion,
    UserEvalMetric,
)

FUNCTION_EVAL = "is_json"
AGENT_EVAL = "action_confirmation_gating"


@pytest.fixture
def seeded(db):
    # The version cache is process-wide, not rolled back with the test
    # transaction: restore whatever was there so no later test sees ours.
    previous = cache.get("system_evals_version")
    try:
        seed_evals(force=True)
        yield
    finally:
        if previous is None:
            cache.delete("system_evals_version")
        else:
            cache.set("system_evals_version", previous, timeout=None)


def _system(name):
    return EvalTemplate.no_workspace_objects.get(
        name=name, owner="system", organization__isnull=True
    )


def _versions(template):
    return list(
        EvalTemplateVersion.objects.filter(
            eval_template=template, deleted=False
        ).order_by("version_number")
    )


def _system_templates():
    return EvalTemplate.no_workspace_objects.filter(
        owner="system", organization__isnull=True, workspace__isnull=True
    )


def _system_versions():
    return EvalTemplateVersion.objects.filter(
        eval_template__owner="system",
        eval_template__organization__isnull=True,
        eval_template__workspace__isnull=True,
        deleted=False,
    )


class _Log:
    """Stands in for the seeder's module logger and keeps what it logged."""

    def __init__(self):
        self.events = []

    def info(self, event, **fields):
        self.events.append((event, fields))

    warning = error = info

    def fields(self, event):
        (fields,) = [f for name, f in self.events if name == event]
        return fields


@pytest.mark.django_db
def test_every_seeded_system_template_has_one_default_version(seeded):
    names = {definition["name"] for definition in load_yaml_evals()}
    templates = list(
        EvalTemplate.no_workspace_objects.filter(
            owner="system", organization__isnull=True, name__in=names
        )
    )
    assert len(templates) == len(names)
    for template in templates:
        versions = _versions(template)
        assert len(versions) == 1, template.name
        version = versions[0]
        assert version.version_number == 1, template.name
        assert version.is_default, template.name
        assert version.organization_id is None, template.name
        assert version.workspace_id is None, template.name
        assert version.config_snapshot == template.config, template.name
        assert version.criteria == (template.criteria or ""), template.name
        assert version.model == "", template.name
        assert version.pass_threshold == template.pass_threshold, template.name
        assert version.output_type_normalized == template.output_type_normalized, (
            template.name
        )
        assert EvalTemplateVersion.objects.get_default(template) == version


@pytest.mark.django_db
def test_reseeding_unchanged_content_creates_no_version(seeded):
    before = EvalTemplateVersion.objects.filter(eval_template__owner="system").count()
    seed_evals(force=True)
    assert sync_system_template_versions() == 0
    after = EvalTemplateVersion.objects.filter(eval_template__owner="system").count()
    assert after == before


@pytest.mark.django_db
def test_changed_seed_content_adds_the_next_default_and_keeps_the_pinned_one(
    seeded, monkeypatch
):
    template = _system(FUNCTION_EVAL)
    (v1,) = _versions(template)
    changed = copy.deepcopy(load_yaml_evals())
    for definition in changed:
        if definition["name"] == FUNCTION_EVAL:
            definition["description"] = "unchanged field for versions"
            definition["pass_threshold"] = 0.75
    monkeypatch.setattr(seed_system_evals, "load_yaml_evals", lambda: changed)

    seed_evals(force=True)

    template.refresh_from_db()
    v1_after, v2 = _versions(template)
    assert v1_after.id == v1.id
    assert not v1_after.is_default
    assert v1_after.pass_threshold == v1.pass_threshold
    assert v2.version_number == 2
    assert v2.is_default
    assert v2.pass_threshold == 0.75 == template.pass_threshold
    # Every other template kept its single version.
    assert len(_versions(_system(AGENT_EVAL))) == 1


@pytest.mark.django_db
def test_a_template_with_an_undefaulted_current_version_is_flagged_not_duplicated(
    seeded,
):
    template = _system(FUNCTION_EVAL)
    (v1,) = _versions(template)
    EvalTemplateVersion.objects.filter(pk=v1.pk).update(is_default=False)

    assert sync_system_template_versions() == 0

    (v1_after,) = _versions(template)
    assert v1_after.id == v1.id
    assert v1_after.is_default


@pytest.mark.django_db
def test_the_seeded_version_changes_nothing_the_engine_executes(seeded):
    from evaluations.engine.instance import (
        apply_version_overrides,
        resolve_pass_threshold,
    )

    for name in (FUNCTION_EVAL, AGENT_EVAL):
        template = _system(name)
        version = EvalTemplateVersion.objects.get_default(template)
        config = copy.deepcopy(template.config)
        overridden, criteria = apply_version_overrides(
            copy.deepcopy(config), version, None
        )
        assert overridden == config, name
        assert criteria is None, name
        assert resolve_pass_threshold(template, None, version) == (
            resolve_pass_threshold(template, None, None)
        ), name


@pytest.mark.django_db
def test_the_runner_effective_config_is_the_same_with_the_seeded_version(seeded):
    """EvaluationRunner overlays the resolved version's config_snapshot onto
    the template config (``_get_effective_eval_config``); the seeded version
    must leave that config unchanged too (review S3)."""
    from model_hub.views.eval_runner import EvaluationRunner

    def effective(template, version):
        # Bypass __init__ (it loads a metric and writes its status); the
        # method reads only these attributes.
        runner = EvaluationRunner.__new__(EvaluationRunner)
        runner.source = None
        runner.eval_template = template
        runner._resolved_version = version
        runner.version_number = None
        runner.user_eval_metric = None
        return runner._get_effective_eval_config()

    for name in (FUNCTION_EVAL, AGENT_EVAL):
        template = _system(name)
        version = EvalTemplateVersion.objects.get_default(template)
        assert version is not None, name
        assert effective(template, version) == effective(template, None), name
        assert effective(template, version) == template.config, name


@pytest.mark.django_db
@pytest.mark.parametrize("counter_ahead_by", [0, 7])
def test_a_plain_seed_restores_versions_when_the_counter_is_already_current(
    seeded, counter_ahead_by
):
    """Review M1: the version sync does not depend on SYSTEM_EVALS_VERSION.

    A database seeded before system versions existed, with the cache at this
    branch's counter or at a later one (another branch's bump, e.g. dev's),
    skips the template upsert but still gains every version on the next plain
    ``seed_evals()``, which is what the deploy's bootstrap job runs.
    """
    templates = list(_system_templates())
    assert templates
    _system_versions().delete()
    assert not _system_versions().exists()
    cache.set(
        "system_evals_version", SYSTEM_EVALS_VERSION + counter_ahead_by, timeout=None
    )

    assert seed_evals() == (0, 0, 0)  # the template upsert was skipped

    for template in templates:
        (version,) = _versions(template)
        assert version.version_number == 1, template.name
        assert version.is_default, template.name
        assert version.config_snapshot == template.config, template.name
    # And again: idempotent under the same gate.
    before = _system_versions().count()
    assert seed_evals() == (0, 0, 0)
    assert _system_versions().count() == before == len(templates)


@pytest.mark.django_db
def test_a_current_cache_does_not_skip_seeding_a_database_with_no_system_template(
    seeded,
):
    """E2E-SMK-02's fresh-database re-check.

    The version counter lives in Redis, the templates in Postgres. After a
    database reset with Redis kept, the counter was current, so a plain
    ``seed_evals()`` (the bootstrap job's call) skipped the upsert and the new
    database ended with no system template at all. A current counter must not
    skip the seed when the database holds no system template.
    """
    expected = {e["eval_id"] for e in load_yaml_evals()}
    assert expected
    # A hard delete through the plain base manager, as a dropped database would
    # look (the versions go with their templates: CASCADE).
    EvalTemplate._base_manager.filter(
        owner="system", organization__isnull=True, workspace__isnull=True
    ).delete()
    assert not _system_templates().exists()
    cache.set("system_evals_version", SYSTEM_EVALS_VERSION, timeout=None)

    created, updated, unchanged = seed_evals()

    assert (created, updated, unchanged) == (len(expected), 0, 0)
    assert set(_system_templates().values_list("eval_id", flat=True)) == expected
    for template in _system_templates():
        (version,) = _versions(template)
        assert version.version_number == 1, template.name
        assert version.is_default, template.name
    # With templates present, the current counter is trusted again.
    assert seed_evals() == (0, 0, 0)
    assert _system_templates().count() == len(expected)


@pytest.mark.django_db
def test_a_dry_run_counts_the_versions_it_would_create_and_writes_none(
    seeded, monkeypatch
):
    """Review S7: the dry run reports versions too, without writing any."""
    templates = list(_system_templates())
    _system_versions().delete()
    log = _Log()
    monkeypatch.setattr(seed_system_evals, "logger", log)

    assert sync_system_template_versions(dry_run=True) == len(templates)
    assert seed_evals(dry_run=True, force=True)[0] == 0
    assert log.fields("dry_run_summary")["would_create_template_versions"] == len(
        templates
    )
    # Under the version cache too.
    cache.set("system_evals_version", SYSTEM_EVALS_VERSION, timeout=None)
    assert seed_evals(dry_run=True) == (0, 0, 0)
    assert log.fields("system_evals_up_to_date")["template_versions_created"] == (
        len(templates)
    )
    assert not _system_versions().exists()

    # A changed seed under --dry-run: the template it would update is compared
    # as it would be stored, so exactly that one would get a new version.
    seed_evals(force=True)
    assert _system_versions().count() == len(templates)
    changed = copy.deepcopy(load_yaml_evals())
    for definition in changed:
        if definition["name"] == FUNCTION_EVAL:
            definition["pass_threshold"] = 0.75
    monkeypatch.setattr(seed_system_evals, "load_yaml_evals", lambda: changed)
    log.events.clear()

    created, updated, _ = seed_evals(dry_run=True, force=True)

    assert created == 0 and updated >= 1
    assert log.fields("dry_run_summary")["would_create_template_versions"] == 1
    assert _system_versions().count() == len(templates)
    assert _system(FUNCTION_EVAL).pass_threshold != 0.75


@pytest.mark.django_db
def test_a_run_binding_a_seeded_system_template_is_created_with_its_version(
    seeded, auth_client, organization, workspace, user
):
    """The SMK-02 failure itself, through the real run-create view."""
    template = _system(FUNCTION_EVAL)
    (v1,) = _versions(template)
    dataset = Dataset.no_workspace_objects.create(
        name="smk-02-dataset",
        organization=organization,
        workspace=workspace,
        user=user,
        column_order=[],
        column_config={},
    )
    version = DatasetVersion.no_workspace_objects.create(dataset=dataset)
    example = DatasetVersionItem.no_workspace_objects.create(
        dataset_version=version, input={"text": "{}"}, expected={}
    )
    metric = UserEvalMetric.no_workspace_objects.create(
        name="smk-02-binding",
        organization=organization,
        workspace=workspace,
        template=template,
        dataset=dataset,
        user=user,
    )
    response = auth_client.post(
        reverse("external-experiment-run-create"),
        {
            "run_id": str(uuid.uuid4()),
            "dataset_version_id": str(version.id),
            "expected_items": [
                {"example_id": str(example.example_id), "trial_number": 1}
            ],
            "evaluator_metric_ids": [str(metric.id)],
            "model_config": {},
            "metadata": {},
        },
        format="json",
    )
    assert response.status_code == status.HTTP_200_OK, response.json()
    (binding,) = response.json()["result"]["evaluator_bindings"]
    assert binding["metric_id"] == str(metric.id)
    assert binding["evaluator_version_id"] == str(v1.id)
    assert binding["version_number"] == 1
    assert binding["template_id"] == str(template.id)
