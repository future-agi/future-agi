"""Authoring-only regressions; expected frozen-head outcomes are in STEP-RECORD.

Use real INSERTs and unfiltered QuerySet reads for persistence assertions. The
post-save repair can mask a broken INSERT predicate, so test BaseModel without
both repair receivers, pre_save directly, and the complete registered path.
No authentication, provider transport, encryption or template-admission claim
is made by the boundary stubs in the call-site tests.
"""

import inspect
import uuid
from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from django.core.exceptions import FieldDoesNotExist
from django.db import IntegrityError, connection, models, transaction
from django.db.models.signals import post_save, pre_save
from django.test.utils import isolate_apps

from accounts.models import Organization, User
from accounts.models.workspace import Workspace
from accounts.services.hard_erase_sql import sql_authority
from agentcc.models.org_config import AgentccOrgConfig
from agentcc.models.project import AgentccProject
from agentcc.models.provider_credential import AgentccProviderCredential
from model_hub.models.eval_groups import EvalGroup, History
from model_hub.models.optimize_dataset import OptimizeDataset
from model_hub.tests.test_feedback_rag_consumer_scope import _django_db_helper  # noqa: F401
from tfc.middleware.workspace_context import (
    clear_workspace_context,
    set_workspace_context,
)
from tfc.utils import base_model as base_model_module
from tfc.utils.base_model import BaseModel
from tfc.utils.signals import auto_assign_workspace, ensure_workspace_after_save

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def clean_context():
    clear_workspace_context()
    try:
        yield
    finally:
        clear_workspace_context()


@pytest.fixture
def scope():
    # Keep setup outside ambient scope; fresh names avoid singleton collisions.
    suffix = uuid.uuid4().hex
    org = Organization.objects.create(name=f"class-fix-{suffix}")
    other_org = Organization.objects.create(name=f"class-fix-other-{suffix}")
    # Workspace.created_by is a required FK; create the owners first.
    user = User.objects.create(
        email=f"class-fix-{suffix}@example.invalid", organization=org
    )
    other_user = User.objects.create(
        email=f"class-fix-other-{suffix}@example.invalid", organization=other_org
    )
    w1 = Workspace.objects.create(
        organization=org, name=f"W1-{suffix}", created_by=user
    )
    w2 = Workspace.objects.create(
        organization=org, name=f"W2-{suffix}", created_by=user
    )
    foreign = Workspace.objects.create(
        organization=other_org, name=f"foreign-{suffix}", created_by=other_user
    )
    clear_workspace_context()
    return SimpleNamespace(
        org=org, other_org=other_org, w1=w1, w2=w2, foreign=foreign, user=user
    )


def rows(model):
    # Do not let a workspace-aware manager conceal a misassigned/NULL row.
    return models.QuerySet(model=model)


def select(scope, workspace=None):
    set_workspace_context(
        workspace=scope.w1 if workspace is None else workspace,
        organization=scope.org,
        user=scope.user,
        require_workspace=False,
    )


@contextmanager
def without_receivers(*pairs):
    disconnected = []
    try:
        for signal, receiver in pairs:
            removed = signal.disconnect(receiver)
            if removed:
                disconnected.append((signal, receiver))
            assert removed, f"Expected registered receiver: {receiver.__name__}"
        yield
    finally:
        for signal, receiver in disconnected:
            signal.connect(receiver)


@contextmanager
def observe_insert(model):
    seen = []

    def capture(sender, instance, **kwargs):
        if instance._state.adding:
            seen.append((instance.workspace_id, instance.organization_id))

    pre_save.connect(capture, sender=model, weak=False)
    try:
        yield seen
    finally:
        pre_save.disconnect(capture, sender=model)


def project(scope, **kwargs):
    return AgentccProject(
        name=f"project-{uuid.uuid4().hex}", organization=scope.org, **kwargs
    )


def assert_null_row(model, pk):
    assert rows(model).get(pk=pk).workspace_id is None


def test_uuid_basemodel_assigns_on_insert_without_signal_repair(scope):
    """Head FAIL: UUID is truthy before save; both signals isolated out."""
    row = project(scope)
    assert row.pk and row._state.adding
    select(scope)
    with without_receivers(
        (pre_save, auto_assign_workspace),
        (post_save, ensure_workspace_after_save),
    ):
        row.save(force_insert=True)
    stored = rows(AgentccProject).get(pk=row.pk)
    assert stored.workspace_id == scope.w1.pk
    assert stored.organization_id == scope.org.pk


def test_uuid_signal_assigns_before_basemodel_bypass_insert(scope):
    """Head FAIL at direct signal assertion; no named input overrides save."""
    row = project(scope)
    select(scope)
    auto_assign_workspace(sender=AgentccProject, instance=row)
    assert row.workspace_id == scope.w1.pk
    # Bypass BaseModel.save, while still using real Django persistence.
    models.Model.save(row, force_insert=True)
    assert rows(AgentccProject).get(pk=row.pk).workspace_id == scope.w1.pk


def test_uuid_registered_path_assigns_before_first_insert(scope):
    """Head FAIL at snapshot, although post_save repairs the final DB value."""
    row = project(scope)
    select(scope)
    with observe_insert(AgentccProject) as seen:
        row.save(force_insert=True)
    assert rows(AgentccProject).get(pk=row.pk).workspace_id == scope.w1.pk
    assert seen == [(scope.w1.pk, scope.org.pk)]


@pytest.mark.parametrize("path", ["base", "signal"])
def test_uuid_organization_assignment_uses_adding(scope, path):
    """Head FAIL on the UUID predicate, not an unset required-FK descriptor."""
    row = EvalGroup(name=f"org-context-{uuid.uuid4().hex}", organization=None)
    assert row.pk and row._state.adding
    assert EvalGroup._meta.get_field("organization").null
    assert row.organization is None and row.organization_id is None
    set_workspace_context(organization=scope.org, workspace=None)
    with without_receivers(
        (pre_save, auto_assign_workspace),
        (post_save, ensure_workspace_after_save),
    ):
        if path == "signal":
            auto_assign_workspace(sender=EvalGroup, instance=row)
            models.Model.save(row, force_insert=True)
        else:
            row.save(force_insert=True)
    stored = rows(EvalGroup).get(pk=row.pk)
    assert stored.organization_id == scope.org.pk
    assert stored.workspace_id is None


@pytest.mark.parametrize("path", ["base", "signal"])
@pytest.mark.parametrize("case", ["explicit", "no_context", "org_mismatch", "opt_out"])
def test_uuid_assignment_controls(scope, path, case):
    """Head PASS; explicit scope, no context, mismatch and opt-out are controls."""
    row = project(scope)
    expected = None
    if case != "no_context":
        select(scope, scope.foreign if case == "org_mismatch" else scope.w1)
    if case == "explicit":
        row.workspace = scope.w2
        expected = scope.w2.pk
    if case == "opt_out":
        row.disable_auto_workspace_assignment = True
    if path == "base":
        row.save(force_insert=True)
    else:
        auto_assign_workspace(sender=AgentccProject, instance=row)
        assert row.workspace_id == expected
        models.Model.save(row, force_insert=True)
    stored = rows(AgentccProject).get(pk=row.pk)
    assert stored.workspace_id == expected
    assert stored.organization_id == scope.org.pk


@pytest.mark.parametrize("path", ["base", "signal"])
@pytest.mark.parametrize("case", ["same_org_string", "foreign_workspace_string"])
def test_uuid_string_organization_id_uses_canonical_identity(scope, path, case):
    """BR-R2-1: head/r2 FAIL for same_org_string; foreign workspace is a control."""
    row = AgentccProject(
        name=f"project-{uuid.uuid4().hex}", organization_id=str(scope.org.pk)
    )
    assert row._state.adding and row.organization_id == str(scope.org.pk)
    assert row.organization_id != scope.org.pk
    select(scope, scope.foreign if case == "foreign_workspace_string" else scope.w1)
    expected = scope.w1.pk if case == "same_org_string" else None
    with without_receivers(
        (pre_save, auto_assign_workspace),
        (post_save, ensure_workspace_after_save),
    ):
        if path == "base":
            row.save(force_insert=True)
        else:
            auto_assign_workspace(sender=AgentccProject, instance=row)
            models.Model.save(row, force_insert=True)
    stored = rows(AgentccProject).get(pk=row.pk)
    assert stored.workspace_id == expected
    assert stored.organization_id == scope.org.pk


def test_optimizedataset_opt_out_preserves_null(scope):
    """Head PASS: the serializer's existing flag already blocks post_save."""
    select(scope)
    row = OptimizeDataset(
        name="intentional-null",
        organization=scope.org,
        workspace=None,
        optimize_type=OptimizeDataset.OptimizeType.TEMPLATE,
        environment=OptimizeDataset.EnvTypes.TRAINING,
        version="1.0",
    )
    row.disable_auto_workspace_assignment = True
    with observe_insert(OptimizeDataset) as seen:
        row.save(force_insert=True)
    assert_null_row(OptimizeDataset, row.pk)
    assert seen == [(None, scope.org.pk)]


@pytest.fixture
def integer_model(db):
    # The PostgreSQL lane supports transactional DDL. Stay in django_db's
    # rollback-only wrapper rather than TransactionTestCase's flush lifecycle.
    assert connection.features.can_rollback_ddl
    assert connection.in_atomic_block
    if not str(connection.settings_dict["NAME"]).split("/")[-1].startswith("test_"):
        raise AssertionError(
            "integer probe fixtures require an isolated test_ database"
        )
    with isolate_apps():

        class IntegerProbe(BaseModel):
            id = models.AutoField(primary_key=True)
            organization = models.ForeignKey(
                Organization,
                null=True,
                on_delete=models.CASCADE,
                related_name="+",
            )
            workspace = models.ForeignKey(
                Workspace,
                null=True,
                on_delete=models.CASCADE,
                related_name="+",
            )

            class Meta:
                app_label = "workspace_class_fix_probe"
                db_table = "workspace_class_fix_r2_integer_probe"

        # Roll back the table and all probe writes together, including on a
        # failed assertion. No DROP with pending deferred FK events, no raw
        # cleanup of protected tables, and no connection close/reset is needed.
        with transaction.atomic():
            try:
                # FK DDL names protected tables. Use the hard-erase tests'
                # connection-bound fixture authority for schema setup only,
                # including schema_editor's deferred SQL on exit. Release it
                # before yielding so every test-body write keeps the guard.
                with sql_authority(connection.alias, "erase"):
                    with connection.schema_editor(atomic=False) as editor:
                        editor.create_model(IntegerProbe)
                yield IntegerProbe
            finally:
                transaction.set_rollback(True)


@pytest.mark.parametrize("path", ["base", "signal", "registered"])
@pytest.mark.parametrize(
    "case", ["assignment", "opt_out", "explicit", "no_context", "org_mismatch"]
)
def test_integer_pk_assignment_and_opt_out(scope, integer_model, path, case):
    """Real AutoField INSERTs; head FAIL only for base/registered opt_out."""
    row = integer_model(organization=scope.org)
    assert row.pk is None and row._state.adding
    expected = scope.w1.pk if case == "assignment" else None
    if case != "no_context":
        select(scope, scope.foreign if case == "org_mismatch" else scope.w1)
    if case == "opt_out":
        row.disable_auto_workspace_assignment = True
    if case == "explicit":
        row.workspace = scope.w2
        expected = scope.w2.pk
    if path == "registered":
        with observe_insert(integer_model) as seen:
            row.save(force_insert=True)
        assert seen == [(expected, scope.org.pk)]
    else:
        with without_receivers(
            (pre_save, auto_assign_workspace),
            (post_save, ensure_workspace_after_save),
        ):
            if path == "base":
                row.save(force_insert=True)
            else:
                auto_assign_workspace(sender=integer_model, instance=row)
                models.Model.save(row, force_insert=True)
    assert isinstance(row.pk, int) and row.pk > 0
    assert not row._state.adding
    stored = rows(integer_model).get(pk=row.pk)
    assert stored.workspace_id == expected
    assert stored.organization_id == scope.org.pk


@pytest.mark.parametrize("path", ["base", "signal"])
@pytest.mark.parametrize("case", ["same_org_string", "foreign_workspace_string"])
def test_integer_pk_string_organization_id_keeps_assignment(
    scope, integer_model, path, case
):
    """BR-R2-1: base PASS at head (related-object guard), r2 FAIL; signal FAIL at head and r2."""
    row = integer_model(organization_id=str(scope.org.pk))
    assert row.pk is None and row._state.adding
    assert row.organization_id != scope.org.pk
    select(scope, scope.foreign if case == "foreign_workspace_string" else scope.w1)
    expected = scope.w1.pk if case == "same_org_string" else None
    with without_receivers(
        (pre_save, auto_assign_workspace),
        (post_save, ensure_workspace_after_save),
    ):
        if path == "base":
            row.save(force_insert=True)
        else:
            auto_assign_workspace(sender=integer_model, instance=row)
            models.Model.save(row, force_insert=True)
    assert isinstance(row.pk, int) and row.pk > 0
    stored = rows(integer_model).get(pk=row.pk)
    assert stored.workspace_id == expected
    assert stored.organization_id == scope.org.pk


def _key_namespace_case(
    *,
    instance_target_pk,
    workspace_org_pk,
    workspace_org_code,
    row_value,
    related_model=Organization,
):
    # Field metadata stand-ins: the real Organization has no unique non-pk
    # field, so a to_field relation cannot be declared against it today.
    target = SimpleNamespace(
        primary_key=instance_target_pk,
        attname="id" if instance_target_pk else "code",
        to_python=lambda value: None if value is None else str(value),
    )
    instance_field = SimpleNamespace(target_field=target, related_model=related_model)
    workspace_field = SimpleNamespace(
        target_field=SimpleNamespace(primary_key=True), related_model=Organization
    )
    instance = SimpleNamespace(
        organization_id=row_value,
        _meta=SimpleNamespace(get_field=lambda name: instance_field),
    )
    workspace = SimpleNamespace(
        organization_id=workspace_org_pk,
        organization=SimpleNamespace(id=workspace_org_pk, code=workspace_org_code),
        _meta=SimpleNamespace(get_field=lambda name: workspace_field),
    )
    return base_model_module._same_organization(workspace, instance)


def test_organization_has_no_unique_non_pk_target_today():
    """Control: a to_field relation to Organization is not declarable on this head."""
    assert [
        field.name
        for field in Organization._meta.concrete_fields
        if field.unique and not field.primary_key
    ] == []


@pytest.mark.parametrize(
    "row_value, expected",
    [("alpha", True), ("42", False)],
    ids=["same-org-alternate-key", "foreign-org-key-collides-with-workspace-pk"],
)
def test_non_pk_target_compares_in_the_instance_key_namespace(row_value, expected):
    """Slice r1 P1: never compare a workspace pk with an alternate key value."""
    assert (
        _key_namespace_case(
            instance_target_pk=False,
            workspace_org_pk=42,
            workspace_org_code="alpha",
            row_value=row_value,
        )
        is expected
    )


def test_relation_to_a_different_model_never_matches():
    """Fail closed when the instance organization FK targets another model."""
    assert (
        _key_namespace_case(
            instance_target_pk=True,
            workspace_org_pk=42,
            workspace_org_code="alpha",
            row_value="42",
            related_model=Workspace,
        )
        is False
    )


@pytest.mark.parametrize("failure", [AttributeError, TypeError, ValueError])
def test_metadata_lookup_errors_fail_closed(failure):
    """Slice r2 P2: a metadata lookup error refuses assignment, never escapes save()."""

    def broken_lookup(name):
        raise failure("metadata unavailable")

    instance = SimpleNamespace(
        organization_id="42", _meta=SimpleNamespace(get_field=broken_lookup)
    )
    workspace = SimpleNamespace(organization_id=42)
    assert base_model_module._same_organization(workspace, instance) is False


def test_missing_workspace_organization_fails_closed_on_fallback():
    """Instance without an organization field: an unusable workspace never matches."""

    def no_field(name):
        raise FieldDoesNotExist(name)

    instance = SimpleNamespace(
        organization_id="42", _meta=SimpleNamespace(get_field=no_field)
    )
    assert base_model_module._same_organization(SimpleNamespace(), instance) is False


def update_only_save(row, path, operation):
    fields = ["workspace", "organization"]
    # This Django version can force an INSERT for an adding-state pk with a
    # default, even with update_fields. Keep the reconstructed state but make
    # UPDATE intent explicit in that case. AutoField partial updates still
    # exercise update_fields alone; empty update_fields remains a true no-op.
    default_pk_update = row._state.adding and row._meta.pk.has_default()
    args, kwargs = (), {}
    if operation == "force_update":
        kwargs = {"force_update": True}
    elif operation == "update_fields":
        kwargs = {"update_fields": fields}
        if default_pk_update:
            kwargs["force_update"] = True
    elif operation == "both":
        kwargs = {"force_update": True, "update_fields": fields}
    elif operation == "empty_update_fields":
        kwargs = {"update_fields": []}
    elif operation == "force_update_positional":
        args = (False, True)
    elif operation == "update_fields_positional":
        args = (False, default_pk_update, None, fields)
    else:
        raise AssertionError(operation)
    if path == "base":
        row.save(*args, **kwargs)
    else:
        # The real registered pre_save path, without BaseModel.save. The
        # inherited save_base still receives Django's force_update argument.
        models.Model.save(row, *args, **kwargs)


@pytest.mark.parametrize("path", ["base", "signal"])
@pytest.mark.parametrize(
    "operation",
    [
        "force_update",
        "update_fields",
        "both",
        "empty_update_fields",
        "force_update_positional",
        "update_fields_positional",
    ],
)
def test_reconstructed_uuid_update_does_not_assign(scope, path, operation):
    """Head PASS; r1b FAIL except signal+empty_update_fields (a Django no-op)."""
    original = EvalGroup(name=f"reconstructed-{uuid.uuid4().hex}")
    original.save(force_insert=True)
    row = EvalGroup(pk=original.pk, name=original.name, created_at=original.created_at)
    assert row._state.adding
    assert row.organization_id is None and row.workspace_id is None
    select(scope)
    # Full force_update has an old post-save repair; isolate that unchanged
    # behavior here, then test it separately with all receivers registered.
    with without_receivers((post_save, ensure_workspace_after_save)):
        update_only_save(row, path, operation)
    assert row.organization_id is None and row.workspace_id is None
    stored = rows(EvalGroup).get(pk=original.pk)
    assert stored.organization_id is None and stored.workspace_id is None
    assert rows(EvalGroup).filter(pk=original.pk).count() == 1
    assert "_workspace_autoassign_force_update" not in row.__dict__


@pytest.mark.parametrize("path", ["base", "signal"])
@pytest.mark.parametrize("operation", ["update_fields", "both", "empty_update_fields"])
def test_reconstructed_org_config_registered_update_stays_null(scope, path, operation):
    """Head PASS; partial/no-op updates do not run the old post-save repair."""
    original = AgentccOrgConfig(organization=scope.org, version=1)
    original.save(force_insert=True)
    row = AgentccOrgConfig(
        pk=original.pk,
        organization=scope.org,
        version=1,
        created_at=original.created_at,
    )
    assert row._state.adding
    select(scope)
    update_only_save(row, path, operation)
    assert row.workspace_id is None
    assert_null_row(AgentccOrgConfig, original.pk)
    assert rows(AgentccOrgConfig).filter(organization=scope.org).count() == 1
    assert "_workspace_autoassign_force_update" not in row.__dict__


@pytest.mark.parametrize("path", ["base", "signal"])
def test_reconstructed_force_update_preserves_registered_post_save_repair(scope, path):
    """Head PASS; NULL at UPDATE pre_save, W1 only after the old repair."""
    original = AgentccOrgConfig(organization=scope.org, version=1)
    original.save(force_insert=True)
    row = AgentccOrgConfig(
        pk=original.pk,
        organization=scope.org,
        version=1,
        created_at=original.created_at,
    )
    select(scope)
    with observe_insert(AgentccOrgConfig) as seen:
        # observe_insert sees adding-state instances, including this
        # reconstructed UPDATE; the snapshot is not proof of an SQL INSERT.
        update_only_save(row, path, "force_update")
    assert seen == [(None, scope.org.pk)]
    assert rows(AgentccOrgConfig).get(pk=row.pk).workspace_id == scope.w1.pk
    assert rows(AgentccOrgConfig).filter(organization=scope.org).count() == 1
    assert "_workspace_autoassign_force_update" not in row.__dict__


@pytest.mark.parametrize("path", ["base", "signal"])
@pytest.mark.parametrize(
    "operation",
    [
        "force_update",
        "update_fields",
        "both",
        "empty_update_fields",
        "update_fields_positional",
    ],
)
def test_reconstructed_integer_update_does_not_assign(
    scope, integer_model, path, operation
):
    """Existing positive integer pk must not become an INSERT defaulting cue."""
    original = integer_model()
    original.save(force_insert=True)
    assert isinstance(original.pk, int) and original.pk > 0
    row = integer_model(pk=original.pk, created_at=original.created_at)
    assert row._state.adding
    # No default pk: update_fields_positional passes force_update=False, so the
    # positional update_fields argument is the only update-only cue.
    assert not row._meta.pk.has_default()
    select(scope)
    with without_receivers((post_save, ensure_workspace_after_save)):
        update_only_save(row, path, operation)
    assert row.organization_id is None and row.workspace_id is None
    stored = rows(integer_model).get(pk=original.pk)
    assert stored.organization_id is None and stored.workspace_id is None
    assert rows(integer_model).count() == 1
    assert "_workspace_autoassign_force_update" not in row.__dict__


@pytest.mark.parametrize("path", ["base", "signal"])
def test_failed_force_update_does_not_poison_later_insert(scope, path):
    """Head PASS; save-intent state must unwind when a receiver raises."""
    original = project(scope)
    original.save(force_insert=True)
    row = project(scope, id=original.pk)
    select(scope)
    failure = RuntimeError("class-fix receiver failure")

    def reject(sender, instance, **kwargs):
        raise failure

    pre_save.connect(reject, sender=AgentccProject, weak=False)
    try:
        with pytest.raises(RuntimeError) as raised:
            update_only_save(row, path, "force_update")
        assert raised.value is failure
    finally:
        pre_save.disconnect(reject, sender=AgentccProject)
    assert row.workspace_id is None
    assert "_workspace_autoassign_force_update" not in row.__dict__
    assert_null_row(AgentccProject, original.pk)
    row.pk = uuid.uuid4()
    assert row._state.adding
    row.save(force_insert=True)
    assert rows(AgentccProject).get(pk=row.pk).workspace_id == scope.w1.pk


@pytest.mark.parametrize("path", ["base", "signal"])
def test_existing_uuid_null_row_is_not_insert_assigned(scope, path):
    """Head PASS; post_save's pre-existing update repair is explicitly isolated."""
    row = project(scope)
    row.save(force_insert=True)
    row = rows(AgentccProject).get(pk=row.pk)
    assert not row._state.adding
    select(scope)
    with without_receivers((post_save, ensure_workspace_after_save)):
        if path == "base":
            row.save()
        else:
            auto_assign_workspace(sender=AgentccProject, instance=row)
            models.Model.save(row)
    assert_null_row(AgentccProject, row.pk)


@pytest.fixture(params=["registered", "without_post_save"])
def null_control_path(request):
    # Head: these six agentcc sites PASS without post_save but FAIL with the
    # frozen registered fallback. Do not silently claim PASS-at-head globally.
    if request.param == "registered":
        yield
    else:
        with without_receivers((post_save, ensure_workspace_after_save)):
            yield


@pytest.mark.parametrize("copy_active", [False, True])
def test_org_config_writer_both_constructors_stay_null(
    scope, monkeypatch, null_control_path, copy_active
):
    from agentcc.services import org_config_writer as writer

    monkeypatch.setattr(writer, "initial_managed_models", lambda org: {})
    active = None
    if copy_active:
        active = AgentccOrgConfig(
            organization=scope.org, version=1, guardrails={"enabled": False}
        )
        active.save(force_insert=True)
    select(scope)
    config = writer._copy_of(active, scope.org)
    config.version = 2 if copy_active else 1
    config.is_active = not copy_active
    config.save(force_insert=True)
    assert_null_row(AgentccOrgConfig, config.pk)
    if copy_active:
        assert config.guardrails == active.guardrails
        assert rows(AgentccOrgConfig).get(pk=active.pk).workspace_id is None


def response_methods():
    return SimpleNamespace(
        success_response=lambda data: data,
        create_response=lambda data: data,
        bad_request=lambda *args, **kwargs: pytest.fail("Unexpected view rejection"),
    )


def test_provider_view_create_stays_null(scope, monkeypatch, null_control_path):
    """Needs both provider-view patchspec hunks folded by the owning lane."""
    from agentcc.views import provider_credential as view_module

    view = view_module.AgentccProviderCredentialViewSet()
    # synthetic test value
    data = {"provider_name": "openai", "credentials": {"api_key": "test-only"}}
    incoming = SimpleNamespace(is_valid=lambda: True, validated_data=data)
    monkeypatch.setattr(view, "get_serializer", lambda **kwargs: incoming)
    monkeypatch.setattr(view, "_gm", response_methods())
    monkeypatch.setattr(view, "_push_config_to_gateway", lambda org: True)
    monkeypatch.setattr(
        view_module, "ensure_provider_base_url_allowed", lambda *a, **k: None
    )
    # synthetic test value
    monkeypatch.setattr(
        view_module.CredentialManager, "encrypt", lambda value: b"test-only"
    )
    monkeypatch.setattr(
        view_module,
        "AgentccProviderCredentialSerializer",
        lambda row: SimpleNamespace(data={"id": str(row.pk)}),
    )
    select(scope)
    result = view.create(SimpleNamespace(data={}, organization=scope.org))
    assert result["gateway_synced"] is True
    row = rows(AgentccProviderCredential).get(
        organization=scope.org, provider_name="openai"
    )
    assert row.workspace_id is None


def test_provider_view_initial_config_stays_null(scope, monkeypatch, null_control_path):
    from agentcc.views import provider_credential as view_module

    monkeypatch.setattr(view_module, "initial_managed_models", lambda org: {})
    push = Mock(return_value=True)
    monkeypatch.setattr(view_module, "push_org_config", push)
    select(scope)
    view = view_module.AgentccProviderCredentialViewSet()
    assert view._push_config_to_gateway(scope.org) is True
    row = rows(AgentccOrgConfig).get(organization=scope.org, is_active=True)
    assert row.workspace_id is None
    assert push.call_count == 1
    assert push.call_args.args[1].pk == row.pk


def test_gateway_new_provider_stays_null(scope, monkeypatch, null_control_path):
    from agentcc.views import gateway as view_module
    from integrations.services.credentials import CredentialManager

    view = view_module.AgentccGatewayViewSet()
    monkeypatch.setattr(view, "_gm", response_methods())
    monkeypatch.setattr(view, "_push_current_config", lambda org: True)
    monkeypatch.setattr(
        view_module, "ensure_provider_configuration", lambda *a, **k: None
    )
    monkeypatch.setattr(
        view_module, "ensure_provider_base_url_allowed", lambda *a, **k: None
    )
    monkeypatch.setattr(view_module, "unsupported_credential_keys", lambda *a, **k: [])
    # synthetic test value
    monkeypatch.setattr(CredentialManager, "encrypt", lambda value: b"test-only")
    # Mirror middleware-resolved scope without bypassing the gateway guard.
    request = SimpleNamespace(
        organization=scope.org,
        workspace=scope.w1,
        validated_data={
            "name": "openai",
            # synthetic test value
            "config": {"api_key": "test-only"},
        },
    )
    select(scope)
    result = inspect.unwrap(type(view).update_provider)(view, request)
    assert result["gateway_synced"] is True
    row = rows(AgentccProviderCredential).get(
        organization=scope.org, provider_name="openai"
    )
    assert row.workspace_id is None


def test_gateway_initial_config_stays_null(scope, monkeypatch, null_control_path):
    from agentcc.views import gateway as view_module

    monkeypatch.setattr(view_module, "initial_managed_models", lambda org: {})
    select(scope)
    view = view_module.AgentccGatewayViewSet()
    config = view._get_or_create_org_config(scope.org)
    assert_null_row(AgentccOrgConfig, config.pk)
    assert config.version == 1
    again = view._get_or_create_org_config(scope.org)
    assert again.pk == config.pk
    assert rows(AgentccOrgConfig).filter(organization=scope.org).count() == 1


def test_gateway_config_recovers_from_competing_insert(scope, monkeypatch):
    """Head PASS; seed a winner and force the initial read to miss it."""
    from agentcc.views import gateway as view_module

    existing = AgentccOrgConfig(organization=scope.org, version=1, is_active=True)
    existing.save(force_insert=True)
    original_first = models.QuerySet.first
    misses = []

    def stale_first(queryset):
        if queryset.model is AgentccOrgConfig and not misses:
            misses.append(True)
            return None
        return original_first(queryset)

    monkeypatch.setattr(models.QuerySet, "first", stale_first)
    monkeypatch.setattr(view_module, "initial_managed_models", lambda org: {})
    select(scope)
    config = view_module.AgentccGatewayViewSet()._get_or_create_org_config(scope.org)
    assert misses == [True]
    assert config.pk == existing.pk
    assert_null_row(AgentccOrgConfig, existing.pk)
    assert rows(AgentccOrgConfig).filter(organization=scope.org).count() == 1


def test_gateway_config_does_not_swallow_unrelated_integrity_error(scope, monkeypatch):
    """Head PASS; no matching winner means the original error must propagate."""
    from agentcc.views import gateway as view_module

    failure = IntegrityError("class-fix injected insertion failure")

    def reject_save(instance, *args, **kwargs):
        raise failure

    monkeypatch.setattr(AgentccOrgConfig, "save", reject_save)
    monkeypatch.setattr(view_module, "initial_managed_models", lambda org: {})
    select(scope)
    with pytest.raises(IntegrityError) as raised:
        view_module.AgentccGatewayViewSet()._get_or_create_org_config(scope.org)
    assert raised.value is failure
    assert not rows(AgentccOrgConfig).filter(organization=scope.org).exists()


@pytest.mark.parametrize(
    "case", ["ambient_none_argument", "explicit_other", "no_context"]
)
def test_eval_group_histories_follow_saved_group(scope, monkeypatch, case):
    """Head FAIL for ambient_none_argument; two other cases are PASS controls.

    The admission/serialization/M2M boundaries are stubbed because their source
    is not an input to this packet. Parent creation and both History inserts
    are real; two distinct references prevent an empty-history false pass.
    """
    from model_hub.services import eval_group as service

    templates = [
        SimpleNamespace(id=uuid.uuid4(), name=f"template-{i}", eval_tags=[])
        for i in range(2)
    ]
    accessible = Mock(return_value=templates)
    monkeypatch.setattr(service, "get_accessible_eval_templates_by_ids", accessible)
    monkeypatch.setattr(
        service,
        "EvalGroupSerializer",
        lambda row: SimpleNamespace(data={"id": str(row.pk)}),
    )
    link = Mock()
    monkeypatch.setattr(EvalGroup.eval_templates.related_manager_cls, "set", link)
    monkeypatch.setattr(
        service, "fetch_required_keys_for_eval_template", lambda values: []
    )
    passed_workspace = scope.w2 if case == "explicit_other" else None
    expected = (
        None
        if case == "no_context"
        else (scope.w2.pk if case == "explicit_other" else scope.w1.pk)
    )
    if case != "no_context":
        select(scope)
    result = service.create_eval_group(
        "class-fix-group",
        "",
        [str(item.id) for item in templates],
        scope.user,
        passed_workspace,
    )
    group = rows(EvalGroup).get(pk=result["id"])
    assert group.workspace_id == expected
    histories = list(rows(History).filter(source_id=group.pk, source_type="EVAL_GROUP"))
    assert len(histories) == 2
    assert {row.reference_id for row in histories} == {item.id for item in templates}
    assert all(row.workspace_id == group.workspace_id for row in histories)
    assert all(row.organization_id == group.organization_id for row in histories)
    link.assert_called_once_with(templates)
    accessible.assert_called_once_with(
        [str(item.id) for item in templates], scope.org, passed_workspace
    )
