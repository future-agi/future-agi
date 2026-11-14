"""An environment's unfinished evidence imports over HTTP: the list a person
resumes or discards from, read from what each import stores."""

from __future__ import annotations

import pytest
from rest_framework import status

from accounts.tests.test_rbac_regressions import WORKSPACE_WRITE_DENIED, _viewer_client
from model_hub.models.develop_dataset import Dataset
from simulate.models import EvidenceImport, EvidenceImportCase
from simulate.selectors import evidence_import as evidence_selectors
from simulate.services.hosted_harness import create_hosted_job
from tracer.models.project import Project

from .test_evidence_datasets import dataset as dataset
from .test_evidence_import_api import ENVIRONMENTS
from .test_evidence_import_api import client as client
from .test_evidence_import_api import environment as environment
from .test_evidence_import_api import project as project
from .test_evidence_transcripts import _post
from .test_hosted_harness_channels import _payload

pytestmark = pytest.mark.django_db


def _import(environment, statuses, *, project=None, dataset=None, drafting=None):
    kind = "session" if project else "dataset" if dataset else "transcript"
    made = EvidenceImport.objects.create(
        organization_id=environment.organization_id,
        workspace_id=environment.workspace_id,
        environment=environment,
        project=project,
        dataset=dataset,
        source_kind=kind,
        drafting_job=drafting,
    )
    EvidenceImportCase.objects.bulk_create(
        [
            EvidenceImportCase(
                evidence_import=made, position=n, source_id=f"s{n}", status=one
            )
            for n, one in enumerate(statuses)
        ]
    )
    return made


def _list(client, environment):
    response = client.get(f"{ENVIRONMENTS}/{environment.id}/evidence-imports/")
    assert response.status_code == status.HTTP_200_OK, response.data
    return response.json()


def _discard(client, environment, made):
    return client.delete(f"{ENVIRONMENTS}/{environment.id}/evidence-imports/{made.id}/")


def _statuses(made):
    return list(
        EvidenceImportCase.objects.filter(evidence_import=made)
        .order_by("position")
        .values_list("status", flat=True)
    )


class TestList:
    def test_unfinished_imports_are_listed_newest_first_with_their_status(
        self, client, environment
    ):
        waiting = _import(environment, ["pending", "unavailable"])
        drafted = _import(environment, ["drafted", "pending"])
        partly = _import(environment, ["added", "drafted"])
        drafting = _import(environment, ["pending"], drafting=environment)
        # A blocked case stays reachable: retried by importing again, or discarded.
        partly_blocked = _import(environment, ["added", "blocked"])
        blocked = _import(environment, ["blocked", "unavailable"])
        # Finished: nothing is left to draft, add or retry.
        _import(environment, ["unavailable"])
        _import(environment, ["added", "discarded"])

        listed = _list(client, environment)

        assert [(one["id"], one["status"]) for one in listed] == [
            (str(blocked.id), "blocked"),
            (str(partly_blocked.id), "blocked"),
            (str(drafting.id), "drafting"),
            (str(partly.id), "partly_added"),
            (str(drafted.id), "drafted"),
            (str(waiting.id), "waiting"),
        ]
        assert [one["can_discard"] for one in listed] == [
            True,
            True,
            False,
            True,
            True,
            True,
        ]
        assert [(one["case_count"], one["added_count"]) for one in listed] == [
            (2, 0),
            (2, 1),
            (1, 0),
            (2, 1),
            (2, 0),
            (2, 0),
        ]

    def test_each_names_its_source_and_says_when_it_is_gone(
        self, client, environment, project, dataset
    ):
        observe = _import(environment, ["pending"], project=project)
        rows = _import(environment, ["pending"], dataset=dataset)
        transcript = _import(environment, ["pending", "pending"])
        unnamed = _import(environment, ["pending"])
        # A transcript import is named by its first transcript, as the person
        # named it.
        for position, name in enumerate(["Refund call", "Second call"]):
            EvidenceImportCase.objects.filter(
                evidence_import=transcript, position=position
            ).update(transcript={"name": name, "turns": []})

        def sources():
            return {
                one["id"]: (
                    one["source_kind"],
                    one["source_name"],
                    one["source_available"],
                )
                for one in _list(client, environment)
            }

        assert sources() == {
            str(observe.id): ("session", "Support agent", True),
            str(rows.id): ("dataset", "Support evals", True),
            str(transcript.id): ("transcript", "Refund call", True),
            str(unnamed.id): ("transcript", None, True),
        }

        Project.objects.filter(id=project.id).update(deleted=True)
        Dataset.objects.filter(id=dataset.id).update(deleted=True)

        assert sources() == {
            str(observe.id): ("session", "Support agent", False),
            str(rows.id): ("dataset", "Support evals", False),
            str(transcript.id): ("transcript", "Refund call", True),
            str(unnamed.id): ("transcript", None, True),
        }

    def test_another_environments_imports_are_not_listed(
        self, client, environment, user, workspace
    ):
        other, _ = create_hosted_job(
            user.organization, _payload(), idempotency_key="other", workspace=workspace
        )
        _import(other, ["pending"])

        assert _list(client, environment) == []


def test_the_list_is_read_as_typed_records(environment):
    made = _import(environment, ["pending", "added"])

    [one] = evidence_selectors.open_evidence_imports(environment)

    assert isinstance(one, evidence_selectors.OpenEvidenceImport)
    assert (one.id, one.source_kind, one.case_count, one.added_count) == (
        made.id,
        "transcript",
        2,
        1,
    )
    assert (one.status, one.can_discard, one.source_available) == (
        "partly_added",
        True,
        True,
    )


class TestDiscard:
    def test_an_import_never_added_is_deleted_with_its_text(self, client, environment):
        made = EvidenceImport.no_workspace_objects.get(
            id=_post(client, environment).json()["id"]
        )

        response = _discard(client, environment, made)

        assert response.status_code == status.HTTP_204_NO_CONTENT
        stored = EvidenceImport.all_objects.get(id=made.id)
        assert (stored.deleted, stored.redaction) == (True, None)
        assert [
            c.transcript
            for c in EvidenceImportCase._base_manager.filter(evidence_import=made)
        ] == [None]
        assert _list(client, environment) == []
        assert (
            client.get(
                f"{ENVIRONMENTS}/{environment.id}/evidence-imports/{made.id}/"
            ).status_code
            == status.HTTP_404_NOT_FOUND
        )

    def test_of_a_partly_added_import_only_the_cases_not_added_go(
        self, client, environment
    ):
        made = _import(environment, ["added", "pending", "drafted", "blocked"])

        response = _discard(client, environment, made)

        assert response.status_code == status.HTTP_204_NO_CONTENT
        assert EvidenceImport.no_workspace_objects.filter(id=made.id).exists()
        assert _statuses(made) == ["added", "discarded", "discarded", "discarded"]
        assert _list(client, environment) == []

    def test_it_is_refused_while_the_builder_drafts(self, client, environment):
        made = _import(environment, ["pending"], drafting=environment)

        response = _discard(client, environment, made)

        assert response.status_code == status.HTTP_409_CONFLICT
        assert response.json()["error"] == "evidence_drafting_busy"
        assert _statuses(made) == ["pending"]
        assert EvidenceImport.no_workspace_objects.filter(id=made.id).exists()

    def test_another_environments_import_is_not_found(
        self, client, environment, user, workspace
    ):
        other, _ = create_hosted_job(
            user.organization, _payload(), idempotency_key="other", workspace=workspace
        )
        made = _import(other, ["pending"])

        response = _discard(client, environment, made)

        assert response.status_code == status.HTTP_404_NOT_FOUND
        assert _statuses(made) == ["pending"]

    def test_a_viewer_cannot_discard(self, organization, workspace, environment):
        made = _import(environment, ["pending"])
        viewer = _viewer_client(organization, workspace)

        response = _discard(viewer, environment, made)

        assert response.status_code == status.HTTP_403_FORBIDDEN
        assert WORKSPACE_WRITE_DENIED in str(response.data)
        assert EvidenceImport.no_workspace_objects.filter(id=made.id).exists()
