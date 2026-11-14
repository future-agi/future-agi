"""The annotation-label type resolver, called for real against PostgreSQL.

``resolve_annotation_label_output_type`` is the one function the dashboard
compiler asks for an annotation label's authoritative value type. Every other
test of that compiler stubs it out, because a query-compilation test must not
become a PostgreSQL client — so without this module the resolver's own
contract (which label it finds, which fence it applies, what it answers when
it finds nothing) has no witness at all, and the compiler tests keep passing
against any implementation.

What the compiler does with a ``None`` is pinned next door, in
``test_dashboard_filter_value_types.py``: it rejects the whole request rather
than compiling a predicate against a guessed type.
"""

from __future__ import annotations

import uuid

import pytest

from accounts.models.organization import Organization
from model_hub.models.choices import AnnotationTypeChoices
from model_hub.models.develop_annotations import AnnotationsLabels
from tracer.services.clickhouse.query_builders.filters import (
    resolve_annotation_label_output_type,
)


def _label(organization, label_type: str, *, deleted: bool = False):
    return AnnotationsLabels.all_objects.create(
        name=f"label-{uuid.uuid4().hex[:8]}",
        type=label_type,
        organization=organization,
        deleted=deleted,
    )


@pytest.mark.django_db
@pytest.mark.parametrize(
    "label_type", [choice.value for choice in AnnotationTypeChoices]
)
def test_a_live_label_resolves_to_its_own_configured_type(organization, label_type):
    # The label's own ``type`` is the answer, for every type the product
    # offers — not a default, and not something inferred from the operator.
    label = _label(organization, label_type)

    resolved = resolve_annotation_label_output_type(str(label.id), str(organization.id))

    assert resolved == label_type


@pytest.mark.django_db
def test_the_organization_is_a_fence_and_not_a_hint(organization):
    # The dashboard supplies the request's own organization (dashboard.py's
    # annotation_metric branch passes ``self.organization_id``). A label id
    # from another tenant must resolve to nothing, so the filter is rejected
    # rather than compiled against a type read across the fence.
    other_organization = Organization.objects.create(name="Other Organization")
    label = _label(organization, AnnotationTypeChoices.CATEGORICAL.value)

    assert (
        resolve_annotation_label_output_type(str(label.id), str(other_organization.id))
        is None
    )
    assert (
        resolve_annotation_label_output_type(str(label.id), str(organization.id))
        == AnnotationTypeChoices.CATEGORICAL.value
    )


@pytest.mark.django_db
def test_the_fence_is_applied_only_when_the_caller_supplies_one(organization):
    # Documented, because it is a fence the caller can decline: with no
    # organization the resolver answers from the label alone. The dashboard
    # never calls it that way; a future caller that does gets no tenancy
    # check from this function.
    label = _label(organization, AnnotationTypeChoices.TEXT.value)

    assert (
        resolve_annotation_label_output_type(str(label.id))
        == AnnotationTypeChoices.TEXT.value
    )


@pytest.mark.django_db
def test_a_soft_deleted_label_resolves_to_nothing(organization):
    # A label a workspace has removed is not a live label, so a filter naming
    # it is rejected rather than compiled. The exclusion comes from the
    # ``no_workspace_objects`` manager, which already filters ``deleted``;
    # the explicit ``deleted=False`` in the query restates it at the call
    # site. This test pins the behaviour, not which of the two supplies it.
    label = _label(organization, AnnotationTypeChoices.NUMERIC.value, deleted=True)

    assert (
        resolve_annotation_label_output_type(str(label.id), str(organization.id))
        is None
    )


@pytest.mark.django_db
def test_an_unknown_label_id_resolves_to_nothing(organization):
    assert (
        resolve_annotation_label_output_type(str(uuid.uuid4()), str(organization.id))
        is None
    )


@pytest.mark.django_db
@pytest.mark.parametrize("label_id", ["not-a-uuid", "", "1234", "  "])
def test_a_malformed_label_id_is_answered_and_not_raised(organization, label_id):
    # Django raises ValidationError when a non-UUID reaches a UUIDField
    # lookup. A dashboard filter carrying a junk identity must come back as
    # "no such label" — which the compiler turns into a 400 naming the leaf —
    # rather than as an unhandled exception reaching the generic handler as
    # a 500.
    assert resolve_annotation_label_output_type(label_id, str(organization.id)) is None


@pytest.mark.django_db
def test_a_malformed_organization_id_is_answered_and_not_raised(organization):
    label = _label(organization, AnnotationTypeChoices.STAR.value)

    assert resolve_annotation_label_output_type(str(label.id), "not-a-uuid") is None
