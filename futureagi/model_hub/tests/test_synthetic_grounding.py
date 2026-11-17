"""Grounded generation validates passage provenance and atomic persistence."""

import json
import uuid

import pytest

from model_hub.models.choices import StatusType
from model_hub.models.develop_dataset import (
    Cell,
    Dataset,
    Files,
    KnowledgeBaseFile,
    Row,
)
from model_hub.tests.test_generate_rows import CREATE, path


@pytest.fixture
def grounded(auth_client):
    result = auth_client.post(
        CREATE,
        {
            "dataset_name": "Grounded examples",
            "number_of_rows": 1,
            "columns": [
                {"name": name, "type": "text", "role": "metadata"}
                for name in ("question", "context", "citation")
            ],
        },
        format="json",
    )
    assert result.status_code == 200, result.content
    dataset = Dataset.objects.get(id=result.json()["result"]["dataset_id"])
    kb = KnowledgeBaseFile.objects.create(
        name="Support passages",
        organization=dataset.organization,
        workspace=dataset.workspace,
        status=StatusType.COMPLETED.value,
    )
    file = Files.objects.create(
        name="support.txt",
        uploaded_url="http://localhost/support.txt",
        status=StatusType.COMPLETED.value,
    )
    kb.files.add(file)
    config = {
        "generation_mode": "append",
        "model": "gpt-4o-mini",
        "num_rows": 1,
        "columns": [
            {"name": name, "data_type": "text", "description": name, "property": []}
            for name in ("question", "context", "citation")
        ],
        "dataset": {
            "name": dataset.name,
            "description": "Support questions",
            "objective": "",
            "patterns": "",
        },
        "kb_id": str(kb.id),
    }
    return dataset, kb, file, config


@pytest.fixture
def grounding_boundaries(grounded, monkeypatch):
    from agentic_eval.core.database.ch_vector import ClickHouseVectorDB
    from agentic_eval.core_evals.run_prompt.litellm_response import RunPrompt

    dataset, kb, file, _ = grounded
    records = {
        "file_id": str(file.id),
        "organization_id": str(dataset.organization_id),
        "chunk_id": "chunk-1",
        "chunk_text": "Refunds take exactly seven days.",
    }
    reads, calls = [], []

    def read(self, sql, params, **kwargs):
        reads.append((sql, params))
        return [(list(records), list(records.values()))]

    def complete(self, streaming=False):
        calls.append(json.loads(self.messages[-1]["content"]))
        passage = calls[-1]["passages"][0]
        return json.dumps(
            {
                "rows": [
                    {
                        "question": "How long do refunds take?",
                        "context": passage["text"],
                        "citation": passage["citation"],
                    }
                ]
            }
        ), {}

    monkeypatch.setattr(ClickHouseVectorDB, "execute_read", read)
    monkeypatch.setattr(RunPrompt, "litellm_response", complete)
    return records, reads, calls


@pytest.mark.django_db
def test_indexed_passages_and_citations_are_scoped_and_saved(
    auth_client, grounded, grounding_boundaries
):
    dataset, kb, file, config = grounded
    _, reads, calls = grounding_boundaries
    response = auth_client.post(
        path(dataset, "add_synthetic_data"), config, format="json"
    )
    assert response.status_code == 200, response.content
    citation = f"kb:{kb.id}/file:{file.id}/chunk:chunk-1"
    assert calls[0]["passages"] == [
        {"text": "Refunds take exactly seven days.", "citation": citation}
    ]
    assert all(
        params
        == {"kb": str(kb.id), "org": str(kb.organization_id), "files": (str(file.id),)}
        for _, params in reads
    )
    assert all("FROM syn FINAL" in sql and "deleted = 0" in sql for sql, _ in reads)
    created = Row.objects.filter(dataset=dataset, order__gt=0).values_list(
        "id", flat=True
    )
    assert dict(
        Cell.objects.filter(row_id__in=created).values_list("column__name", "value")
    ) == {
        "question": "How long do refunds take?",
        "context": "Refunds take exactly seven days.",
        "citation": citation,
    }
    dataset.refresh_from_db()
    assert dataset.dataset_config["row_generation"] == config


@pytest.mark.django_db
@pytest.mark.parametrize(
    "scope", ["missing", "foreign-org", "other-workspace", "deleted"]
)
def test_unavailable_knowledge_base_never_reaches_storage_or_model(
    auth_client, grounded, grounding_boundaries, scope
):
    from accounts.models.organization import Organization
    from accounts.models.workspace import Workspace

    dataset, kb, _, config = grounded
    if scope == "missing":
        config["kb_id"] = str(uuid.uuid4())
    elif scope == "foreign-org":
        KnowledgeBaseFile.no_workspace_objects.filter(id=kb.id).update(
            organization=Organization.objects.create(name="Other organization")
        )
    elif scope == "other-workspace":
        other = Workspace.objects.create(
            name="Other workspace",
            organization=dataset.organization,
            is_default=False,
            created_by=dataset.workspace.created_by,
        )
        KnowledgeBaseFile.no_workspace_objects.filter(id=kb.id).update(workspace=other)
    else:
        KnowledgeBaseFile.no_workspace_objects.filter(id=kb.id).update(deleted=True)
    response = auth_client.post(
        path(dataset, "add_synthetic_data"), config, format="json"
    )
    assert response.status_code == 404, response.content
    assert grounding_boundaries[1:] == ([], [])
    assert Row.objects.filter(dataset=dataset).count() == 1


@pytest.mark.django_db
@pytest.mark.parametrize("fault", ["organization_id", "file_id", "chunk_text"])
def test_mismatched_index_metadata_fails_closed(
    auth_client, grounded, grounding_boundaries, fault
):
    dataset, _, _, config = grounded
    records, _, calls = grounding_boundaries
    records[fault] = "" if fault == "chunk_text" else str(uuid.uuid4())
    response = auth_client.post(
        path(dataset, "add_synthetic_data"), config, format="json"
    )
    assert response.status_code == 400, response.content
    assert calls == []
    assert Row.objects.filter(dataset=dataset).count() == 1


@pytest.mark.django_db
@pytest.mark.parametrize("fault", ["context", "citation"])
def test_invented_citations_or_context_never_persist(
    auth_client, grounded, grounding_boundaries, monkeypatch, fault
):
    from agentic_eval.core_evals.run_prompt.litellm_response import RunPrompt

    dataset, _, _, config = grounded

    def complete(self, streaming=False):
        passage = json.loads(self.messages[-1]["content"])["passages"][0]
        row = {
            "question": "Refund time?",
            "context": passage["text"],
            "citation": passage["citation"],
        }
        row[fault] += "invented"
        return json.dumps({"rows": [row]}), {}

    monkeypatch.setattr(RunPrompt, "litellm_response", complete)
    response = auth_client.post(
        path(dataset, "add_synthetic_data"), config, format="json"
    )
    assert response.status_code == 502, response.content
    assert Row.objects.filter(dataset=dataset).count() == 1
    dataset.refresh_from_db()
    assert not (dataset.dataset_config or {}).get("row_generation")
