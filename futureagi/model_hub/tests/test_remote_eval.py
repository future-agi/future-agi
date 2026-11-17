"""Remote scorers through the browser's routes; no real outbound HTTP."""
import importlib.util
import json
import uuid
from unittest.mock import Mock, patch

import pytest
from cryptography.fernet import Fernet

from accounts.models.organization import Organization
from accounts.models.workspace import Workspace
from model_hub.models.dataset_versions import DatasetVersion
from model_hub.models.develop_dataset import Dataset
from model_hub.models.evals_metric import EvalTemplate
from model_hub.models.experiments import ExperimentRun, ExperimentRunItem
from tfc.utils.ssrf_post import PostResult

pytestmark = pytest.mark.django_db
CREATE = "/model-hub/eval-templates/create-remote/"
TEST = "/model-hub/eval-templates/remote/test/"
SERVICE = "model_hub.services.remote_eval"


def detail(template_id, suffix=""):
    return f"/model-hub/eval-templates/{template_id}/remote/{suffix}"


def job_url(job_id):
    return f"/model-hub/eval-templates/remote/jobs/{job_id}/"


def definition(**changes):
    return {"url": "https://scorer.example.com/score", "auth": {"kind": "none"},
            "mapping": {"question": "prompt"}, "output": {"type": "score"},
            "timeout_ms": 5000, "retries": 0, "calls_at_a_time": 1,
            "send": "row", **changes}


@pytest.fixture(autouse=True)
def encryption(settings):
    settings.INTEGRATION_ENCRYPTION_KEY = Fernet.generate_key().decode()


@pytest.fixture
def transport():
    # The RED tree has no service yet: let route assertions fail, rather than
    # turning every endpoint test into a fixture setup/import error.
    if importlib.util.find_spec(SERVICE) is None:
        yield Mock()
        return
    with patch(f"{SERVICE}.resolve_all", return_value=["93.184.216.34"]), patch(
        f"{SERVICE}.safe_post",
        return_value=PostResult(status=200, body=b'{"score":0.8,"reason":"matched"}'),
    ) as post:
        yield post


def create(client, remote=None, name="remote_check"):
    response = client.post(CREATE, {"name": name, "remote": remote or definition()}, format="json")
    assert response.status_code == 200, response.content
    return response.json()["result"]["id"]


@pytest.fixture
def run(organization, workspace, user):
    dataset = Dataset.no_workspace_objects.create(
        name="Remote rows", organization=organization, workspace=workspace, user=user,
    )
    version = DatasetVersion.no_workspace_objects.create(dataset=dataset)
    run = ExperimentRun.no_workspace_objects.create(dataset_version=version, status="completed")
    for text in ("first", "second"):
        ExperimentRunItem.no_workspace_objects.create(
            run=run, example_id=uuid.uuid4(), status="completed",
            input={"prompt": text}, output="answer",
        )
    return run


def test_create_get_patch_encrypt_and_never_read_credentials(auth_client, transport):
    token = "fixture-only-token"
    template_id = create(auth_client, definition(auth={"kind": "bearer", "token": token}))
    from model_hub.models.remote_eval import RemoteEvalConfig
    config = RemoteEvalConfig.objects.get(template_id=template_id)
    assert token.encode() not in bytes(config.encrypted_credentials)
    assert token not in json.dumps(EvalTemplate.no_workspace_objects.get(id=template_id).config)
    response = auth_client.get(detail(template_id))
    assert response.status_code == 200
    result = response.json()["result"]
    assert result["remote"]["auth"]["token_last4"] == token[-4:]
    assert token not in response.content.decode()
    response = auth_client.patch(detail(template_id), {
        "description": "changed", "remote": definition(auth={"kind": "bearer"}),
    }, format="json")
    assert response.status_code == 200
    assert response.json()["result"]["description"] == "changed"
    auth_client.post(TEST, {"template_id": template_id, "inputs": {"question": "hi"}, "output": "yes"}, format="json")
    assert ("Authorization", f"Bearer {token}") in transport.call_args.kwargs["headers"]


@pytest.mark.parametrize("url", ["http://scorer.example.com/", "https://localhost/", "https://127.0.0.1/", "https://[::1]/", "https://10.1.2.3/", "https://user:pass@scorer.example.com/", "https://scorer.example.com/#fragment"])
def test_create_rejects_unsafe_urls(auth_client, transport, url):
    response = auth_client.post(CREATE, {"name": "unsafe", "remote": definition(url=url)}, format="json")
    assert response.status_code == 400
    transport.assert_not_called()


def test_private_dns_answer_is_rejected(auth_client, transport):
    with patch(f"{SERVICE}.resolve_all", return_value=["93.184.216.34", "127.0.0.1"]):
        response = auth_client.post(CREATE, {"name": "unsafe", "remote": definition()}, format="json")
    assert response.status_code == 400
    transport.assert_not_called()


def test_test_calls_once_with_mapping_timeout_and_parsed_score(auth_client, transport):
    response = auth_client.post(TEST, {"remote": definition(), "inputs": {"question": "hello"}, "output": "answer"}, format="json")
    assert response.status_code == 200, response.content
    result = response.json()["result"]
    assert result["verdict"] == {"value": 0.8, "passed": True, "reason": "matched"}
    transport.assert_called_once()
    sent = transport.call_args.kwargs
    assert json.loads(sent["body"])["inputs"] == {"question": "hello"}
    assert sent["addresses"] == ["93.184.216.34"]
    assert sent["scheme"] == "https"
    assert sent["watchdog"] is not None
    assert len(result["tries"]) == 1


@pytest.mark.parametrize("answer", [b'not-json', b'{"score":2}', b'{"score":true}', b'{"reason":"absent"}'])
def test_bad_remote_answers_are_not_scores(auth_client, transport, answer):
    transport.return_value = PostResult(status=200, body=answer)
    response = auth_client.post(TEST, {"remote": definition(), "inputs": {}, "output": ""}, format="json")
    assert response.status_code == 200
    assert "error" in response.json()["result"]["verdict"]


def test_custom_score_and_reason_fields(auth_client, transport):
    transport.return_value = PostResult(status=200, body=b'{"result":{"value":0.7,"why":"ok"}}')
    response = auth_client.post(TEST, {"remote": definition(output={"type": "score", "score_field": "result.value", "reason_field": "result.why"}), "inputs": {}}, format="json")
    assert response.status_code == 200
    assert response.json()["result"]["verdict"]["value"] == 0.7
    assert response.json()["result"]["verdict"]["reason"] == "ok"


def test_signed_create_returns_secret_only_once(auth_client, transport):
    response = auth_client.post(CREATE, {"name": "signed", "remote": definition(auth={"kind": "hmac"})}, format="json")
    assert response.status_code == 200
    result = response.json()["result"]
    secret = result["signing_secret"]
    assert secret not in auth_client.get(detail(result["id"])).content.decode()
    tested = auth_client.post(TEST, {"template_id": result["id"], "inputs": {}, "output": "hello"}, format="json")
    assert tested.status_code == 200
    assert any(key == "X-FutureAGI-Signature" for key, _ in transport.call_args.kwargs["headers"])
    assert secret not in tested.content.decode()


def test_score_run_enqueues_and_returns_durable_results(auth_client, transport, run, django_capture_on_commit_callbacks):
    template_id = create(auth_client)
    with patch("model_hub.tasks.remote_eval.score_remote_run.delay") as delay, django_capture_on_commit_callbacks(execute=True):
        response = auth_client.post(detail(template_id, "score-run/"), {"run_id": str(run.id)}, format="json")
    assert response.status_code == 202, response.content
    job_id = response.json()["result"]["job_id"]
    delay.assert_called_once_with(job_id)
    assert auth_client.get(job_url(job_id)).json()["result"]["status"] == "queued"
    from model_hub.tasks.remote_eval import score_remote_run
    score_remote_run(job_id)
    view = auth_client.get(job_url(job_id)).json()["result"]
    assert view["status"] == "done"
    assert view["sent"] == view["total"] == 2
    assert view["result"]["scored"] == 2
    assert transport.call_count == 2
    assert {json.loads(call.kwargs["body"])["inputs"]["question"] for call in transport.call_args_list} == {"first", "second"}
    assert all(not item.score_refs for item in run.items.all())
    child = ExperimentRun.no_workspace_objects.get(id=view["result"]["child_run_id"])
    assert child.items.count() == 2
    assert all(item.score_refs[-1]["value"] == 0.8 for item in child.items.all())
    from model_hub.services.persisted_run_comparison import _bindings
    from model_hub.models.evals_metric import UserEvalMetric
    bindings = _bindings(child)
    assert len(bindings) == 1
    assert UserEvalMetric.no_workspace_objects.filter(id=next(iter(bindings)), template_id=template_id).exists()
    score_remote_run(job_id)
    assert transport.call_count == 2


def test_score_run_records_timeout_and_subset(auth_client, transport, run, django_capture_on_commit_callbacks):
    template_id = create(auth_client)
    row_id = str(run.items.first().example_id)
    transport.return_value = PostResult(error="timeout")
    with patch("model_hub.tasks.remote_eval.score_remote_run.delay"), django_capture_on_commit_callbacks(execute=True):
        response = auth_client.post(detail(template_id, "score-run/"), {"run_id": str(run.id), "row_ids": [row_id]}, format="json")
    assert response.status_code == 202
    from model_hub.tasks.remote_eval import score_remote_run
    job_id = response.json()["result"]["job_id"]
    score_remote_run(job_id)
    view = auth_client.get(job_url(job_id)).json()["result"]
    assert view["status"] == "done"
    assert view["failed"] == 1
    assert view["result"]["scored"] == 0
    transport.assert_called_once()


def test_options_counts_and_invalid_run_rows(auth_client, transport, run):
    template_id = create(auth_client)
    options = auth_client.get(detail(template_id), {"for": "score-run"}).json()["result"]["score_run_options"]
    assert options[0]["runs"][0]["run_id"] == str(run.id)
    count = auth_client.get(detail(template_id), {"for": "score-run-calls", "run_id": str(run.id)}).json()["result"]["score_run_calls"]
    assert count == {"calls": 2, "cells": 2}
    response = auth_client.post(detail(template_id, "score-run/"), {"run_id": str(run.id), "row_ids": [str(uuid.uuid4())]}, format="json")
    assert response.status_code == 400
    run.status = "running"
    run.save(update_fields=["status"])
    response = auth_client.post(detail(template_id, "score-run/"), {"run_id": str(run.id)}, format="json")
    assert response.status_code == 400


def test_other_organization_cannot_read_patch_test_score_or_poll(auth_client, transport, run, user, django_capture_on_commit_callbacks):
    template_id = create(auth_client)
    with patch("model_hub.tasks.remote_eval.score_remote_run.delay"), django_capture_on_commit_callbacks(execute=True):
        queued = auth_client.post(detail(template_id, "score-run/"), {"run_id": str(run.id)}, format="json")
    job_id = queued.json()["result"]["job_id"]
    other = Organization.objects.create(name="Other remote tenant")
    other_workspace = Workspace.objects.create(name="Other remote space", organization=other, created_by=user)
    EvalTemplate.no_workspace_objects.filter(id=template_id).update(organization=other, workspace=other_workspace)
    assert auth_client.get(detail(template_id)).status_code == 404
    assert auth_client.patch(detail(template_id), {"description": "no"}, format="json").status_code == 404
    assert auth_client.post(TEST, {"template_id": template_id, "inputs": {}}, format="json").status_code == 404
    assert auth_client.post(detail(template_id, "score-run/"), {"run_id": str(run.id)}, format="json").status_code == 404
    assert auth_client.get(job_url(job_id)).status_code == 404
    own_id = create(auth_client, name="own_remote")
    Dataset.no_workspace_objects.filter(id=run.dataset_version.dataset_id).update(organization=other, workspace=other_workspace)
    assert auth_client.post(detail(own_id, "score-run/"), {"run_id": str(run.id)}, format="json").status_code == 404
    transport.assert_not_called()


def test_same_org_other_workspace_is_not_visible(auth_client, transport, workspace, user, organization):
    template_id = create(auth_client)
    other_workspace = Workspace.objects.create(name="Sibling", organization=organization, created_by=user)
    EvalTemplate.no_workspace_objects.filter(id=template_id).update(workspace=other_workspace)
    assert auth_client.get(detail(template_id)).status_code == 404
    assert auth_client.post(TEST, {"template_id": template_id}, format="json").status_code == 404


def test_remote_template_remains_readable_in_list_and_detail(auth_client, transport):
    template_id = create(auth_client)
    listed = auth_client.post("/model-hub/eval-templates/list/", {"filters": {"eval_type": ["remote"]}}, format="json")
    assert listed.status_code == 200, listed.content
    assert template_id in listed.content.decode()
    response = auth_client.get(f"/model-hub/eval-templates/{template_id}/detail/")
    assert response.status_code == 200, response.content
    assert response.json()["result"]["eval_type"] == "remote"


def test_test_cannot_send_saved_secret_to_a_different_url(auth_client, transport):
    template_id = create(auth_client, definition(auth={"kind": "bearer", "token": "fixture-secret-token"}))
    response = auth_client.post(TEST, {"template_id": template_id, "remote": definition(url="https://other.example.com/score", auth={"kind": "bearer"})}, format="json")
    assert response.status_code == 400
    transport.assert_not_called()


def test_redirect_is_not_followed_or_scored(auth_client, transport):
    transport.return_value = PostResult(status=302, headers={"Location": "https://127.0.0.1/"})
    response = auth_client.post(TEST, {"remote": definition(), "inputs": {}}, format="json")
    assert response.status_code == 200
    assert "error" in response.json()["result"]["verdict"]
    transport.assert_called_once()


def test_custom_header_is_encrypted_and_redacted(auth_client, transport):
    template_id = create(auth_client, definition(auth={"kind": "header", "header_name": "X-Eval-Key", "token": "fixture-header-secret"}))
    transport.return_value = PostResult(status=200, body=b'{"score":0.8,"reason":"fixture-header-secret"}')
    response = auth_client.post(TEST, {"template_id": template_id, "inputs": {}}, format="json")
    assert ("X-Eval-Key", "fixture-header-secret") in transport.call_args.kwargs["headers"]
    assert "fixture-header-secret" not in response.content.decode()
    assert response.json()["result"]["request"]["headers"]["X-Eval-Key"] == "[redacted]"


def test_rotate_signed_secret_is_one_time_and_keeps_grace_signature(auth_client, transport):
    template_id = create(auth_client, definition(auth={"kind": "hmac"}))
    rotated = auth_client.patch(detail(template_id), {"rotate_secret": True}, format="json")
    assert rotated.status_code == 200
    secret = rotated.json()["result"]["signing_secret"]
    assert rotated.json()["result"]["signing"]["previous_valid_until"]
    assert secret not in auth_client.get(detail(template_id)).content.decode()
    tested = auth_client.post(TEST, {"template_id": template_id, "inputs": {}}, format="json")
    assert tested.status_code == 200
    signature = dict(transport.call_args.kwargs["headers"])["X-FutureAGI-Signature"]
    assert signature.count("sha256=") == 2


def test_oauth_credentials_are_never_returned(auth_client, transport):
    remote = definition(auth={"kind": "oauth", "oauth": {"token_url": "https://auth.example.com/token", "client_id": "fixture-client", "client_secret": "fixture-oauth-secret"}})
    template_id = create(auth_client, remote)
    stored = auth_client.get(detail(template_id))
    assert "fixture-oauth-secret" not in stored.content.decode()
    assert stored.json()["result"]["remote"]["auth"]["client_secret_set"] is True
    transport.side_effect = [PostResult(status=200, body=b'{"access_token":"fixture-access-token"}'), PostResult(status=200, body=b'{"score":1}')]
    response = auth_client.post(TEST, {"template_id": template_id, "inputs": {}}, format="json")
    assert response.status_code == 200
    assert response.json()["result"]["verdict"]["value"] == 1
    assert ("Authorization", "Bearer fixture-access-token") in transport.call_args.kwargs["headers"]
    assert "fixture-access-token" not in response.content.decode()


@pytest.mark.parametrize("output,answer,expected", [
    ({"type": "pass_fail"}, b'{"passed":true}', 1),
    ({"type": "choice", "choices": [{"label": "good", "score": 1}, {"label": "bad", "score": 0}]}, b'{"label":"good"}', 1),
])
def test_output_variants_create_and_test(auth_client, transport, output, answer, expected):
    template_id = create(auth_client, definition(output=output))
    transport.return_value = PostResult(status=200, body=answer)
    tested = auth_client.post(TEST, {"template_id": template_id, "inputs": {}}, format="json")
    assert tested.status_code == 200
    assert tested.json()["result"]["verdict"]["value"] == expected


def test_dataset_test_scopes_rows_and_applies_mapping(auth_client, transport, organization, workspace, user):
    from model_hub.models.choices import DataTypeChoices, SourceChoices
    from model_hub.models.develop_dataset import Cell, Column, Row
    dataset = Dataset.no_workspace_objects.create(name="Sample rows", organization=organization, workspace=workspace, user=user)
    column = Column.no_workspace_objects.create(name="prompt", dataset=dataset, data_type=DataTypeChoices.TEXT.value, source=SourceChoices.OTHERS.value)
    row = Row.no_workspace_objects.create(dataset=dataset, order=0)
    Cell.no_workspace_objects.create(dataset=dataset, column=column, row=row, value="sample question")
    template_id = create(auth_client)
    payload = {"template_id": template_id, "dataset_id": str(dataset.id), "row_id": str(row.id), "output": "answer"}
    response = auth_client.post(TEST, payload, format="json")
    assert response.status_code == 200
    assert json.loads(transport.call_args.kwargs["body"])["inputs"] == {"question": "sample question"}
    other = Organization.objects.create(name="Foreign sample tenant")
    Dataset.no_workspace_objects.filter(id=dataset.id).update(organization=other)
    assert auth_client.post(TEST, payload, format="json").status_code == 404
    assert transport.call_count == 1


def test_test_still_calls_once_when_retries_are_configured(auth_client, transport):
    transport.return_value = PostResult(status=503)
    response = auth_client.post(TEST, {"remote": definition(retries=3, retry_on=["5xx"]), "inputs": {}}, format="json")
    assert response.status_code == 200
    transport.assert_called_once()


@pytest.mark.parametrize("action", ["restore", "set-default"])
def test_remote_version_restore_keeps_flat_config_and_version_pin(auth_client, transport, run, action):
    from model_hub.models.evals_metric import EvalTemplateVersion
    from model_hub.models.remote_eval import RemoteEvalJob

    template_id = create(auth_client, definition(auth={"kind": "bearer", "token": "version-fixture-token"}))
    original = EvalTemplateVersion.objects.get(eval_template_id=template_id)
    assert "remote" in original.config_snapshot
    changed = definition(auth={"kind": "bearer"}, mapping={"changed": "prompt"})
    assert auth_client.patch(detail(template_id), {"remote": changed}, format="json").status_code == 200
    path = f"/model-hub/eval-templates/{template_id}/versions/{original.id}/{action}/"
    response = getattr(auth_client, "post" if action == "restore" else "put")(path, {}, format="json")
    assert response.status_code == 200, response.content
    active_id = response.json()["result"]["id"]
    template = EvalTemplate.no_workspace_objects.get(id=template_id)
    assert template.config["remote_version_id"] == active_id
    assert template.config["remote"]["mapping"] == {"question": "prompt"}
    assert auth_client.get(detail(template_id)).status_code == 200
    tested = auth_client.post(TEST, {"template_id": template_id}, format="json")
    assert tested.status_code == 200
    assert tested.json()["result"]["verdict"]["value"] == 0.8
    assert ("Authorization", "Bearer version-fixture-token") in transport.call_args.kwargs["headers"]
    with patch("model_hub.tasks.remote_eval.score_remote_run.delay"):
        scored = auth_client.post(detail(template_id, "score-run/"), {"run_id": str(run.id)}, format="json")
    assert scored.status_code == 202, scored.content
    job = RemoteEvalJob.objects.get(id=scored.json()["result"]["job_id"])
    assert job.definition["version_id"] == active_id


@pytest.mark.parametrize("action", ["restore", "set-default"])
@pytest.mark.parametrize("changed", [
    {"url": "https://other.example.com/score"},
    {"auth": {"kind": "header", "header_name": "X-New-Key", "token": "new-fixture-token"}},
])
def test_remote_version_refuses_changed_credential_binding(auth_client, transport, action, changed):
    from model_hub.models.evals_metric import EvalTemplateVersion

    template_id = create(auth_client, definition(auth={"kind": "header", "header_name": "X-Old-Key", "token": "old-fixture-token"}))
    original = EvalTemplateVersion.objects.get(eval_template_id=template_id)
    remote = definition(auth={"kind": "header", "header_name": "X-Old-Key", "token": "new-fixture-token"}, **{k: v for k, v in changed.items() if k != "auth"})
    remote.update(changed)
    assert auth_client.patch(detail(template_id), {"remote": remote}, format="json").status_code == 200
    before = EvalTemplate.no_workspace_objects.get(id=template_id).config
    count = EvalTemplateVersion.objects.filter(eval_template_id=template_id).count()
    path = f"/model-hub/eval-templates/{template_id}/versions/{original.id}/{action}/"
    response = getattr(auth_client, "post" if action == "restore" else "put")(path, {}, format="json")
    assert response.status_code == 400, response.content
    assert EvalTemplate.no_workspace_objects.get(id=template_id).config == before
    assert EvalTemplateVersion.objects.filter(eval_template_id=template_id).count() == count
    transport.assert_not_called()


@pytest.mark.parametrize("auth", [
    {"kind": "none", "token": "leftover-fixture-token"},
    {"kind": "hmac", "token": "leftover-fixture-token"},
    {"kind": "none", "oauth": {"client_secret": "leftover-fixture-secret"}},
    {"kind": "bearer", "token": "active-fixture-token", "oauth": {"client_secret": "leftover-fixture-secret"}},
    {"kind": "oauth", "token": "leftover-fixture-token", "oauth": {"token_url": "https://auth.example.com/token", "client_id": "fixture", "client_secret": "active-fixture-secret"}},
])
def test_inactive_credentials_rejected_before_create_edit_or_test(auth_client, transport, auth):
    from model_hub.models.evals_metric import EvalTemplateVersion

    template_id = create(auth_client, definition(auth={"kind": "bearer", "token": "saved-fixture-token"}))
    before = EvalTemplate.no_workspace_objects.get(id=template_id).config
    payload = {"remote": definition(auth=auth)}
    response = auth_client.post(CREATE, {"name": "inactive_credentials", **payload}, format="json")
    assert response.status_code == 400, response.content
    response = auth_client.patch(detail(template_id), payload, format="json")
    assert response.status_code == 400, response.content
    response = auth_client.post(TEST, {"template_id": template_id, **payload}, format="json")
    assert response.status_code == 400, response.content
    assert EvalTemplate.no_workspace_objects.get(id=template_id).config == before
    assert EvalTemplateVersion.objects.filter(eval_template_id=template_id).count() == 1
    transport.assert_not_called()


@pytest.mark.parametrize("token", ['fixture-"quoted"-token', "fixture-\\backslash-token"])
def test_escaped_credentials_redacted_from_response_verdict_and_job(auth_client, transport, run, token):
    from model_hub.tasks.remote_eval import score_remote_run

    template_id = create(auth_client, definition(auth={"kind": "header", "header_name": "X-Eval-Key", "token": token}))
    transport.return_value = PostResult(status=200, body=json.dumps({
        "score": 0.8, "reason": f"reflected {token}", "nested": [{"value": token}], token: token,
    }).encode())
    tested = auth_client.post(TEST, {"template_id": template_id}, format="json")
    assert tested.status_code == 200
    result = tested.json()["result"]
    assert result["verdict"]["reason"] == "reflected [redacted]"
    assert result["response"]["body"]["nested"] == [{"value": "[redacted]"}]
    assert token not in result["response"]["body"]
    with patch("model_hub.tasks.remote_eval.score_remote_run.delay"):
        response = auth_client.post(detail(template_id, "score-run/"), {"run_id": str(run.id)}, format="json")
    assert response.status_code == 202
    job_id = response.json()["result"]["job_id"]
    score_remote_run(job_id)
    result = auth_client.get(job_url(job_id)).json()["result"]
    assert result["status"] == "done"
    assert all(row["verdict"]["reason"] == "reflected [redacted]" for row in result["result"]["rows"])
    child = ExperimentRun.no_workspace_objects.get(id=result["result"]["child_run_id"])
    assert all(item.score_refs[-1]["reason"] == "reflected [redacted]" for item in child.items.all())


@pytest.mark.parametrize("reflected", [
    pytest.param(1234567890123456, id="integer-exact"),
    pytest.param(912345678901234569, id="integer-containing"),
    pytest.param(1234567890123456.0, id="float-containing"),
    pytest.param(1234567890123456.5, id="float-fractional"),
])
def test_numeric_credentials_redacted_from_response_and_persisted_results(auth_client, transport, run, reflected):
    from model_hub.models.remote_eval import RemoteEvalJob
    from model_hub.tasks.remote_eval import score_remote_run

    token = "1234567890123456"  # Synthetic digit-only header credential.
    template_id = create(auth_client, definition(auth={"kind": "header", "header_name": "X-Eval-Key", "token": token}))
    transport.return_value = PostResult(status=200, body=json.dumps({
        "score": 0.8, "reason": f"reflected {token}",
        "debug": {"token": reflected, "nested": [reflected, [{"token": reflected}]]},
        "safe": [42, 0.25, True, False, None],
    }).encode())
    tested = auth_client.post(TEST, {"template_id": template_id}, format="json")
    assert tested.status_code == 200, tested.content
    assert ("X-Eval-Key", token) in transport.call_args.kwargs["headers"]
    result = tested.json()["result"]
    expected_verdict = {"value": 0.8, "passed": True, "reason": "reflected [redacted]"}
    assert result["verdict"] == expected_verdict
    assert result["response"]["body"]["debug"] == {
        "token": "[redacted]", "nested": ["[redacted]", [{"token": "[redacted]"}]],
    }
    assert result["response"]["body"]["score"] == 0.8
    assert result["response"]["body"]["safe"] == [42, 0.25, True, False, None]
    assert token not in tested.content.decode()

    with patch("model_hub.tasks.remote_eval.score_remote_run.delay"):
        queued = auth_client.post(detail(template_id, "score-run/"), {"run_id": str(run.id)}, format="json")
    assert queued.status_code == 202, queued.content
    job_id = queued.json()["result"]["job_id"]
    score_remote_run(job_id)
    polled = auth_client.get(job_url(job_id))
    assert polled.status_code == 200, polled.content
    assert polled.json()["result"]["status"] == "done"
    assert token not in polled.content.decode()
    job = RemoteEvalJob.objects.get(id=job_id)
    assert job.result["scored"] == 2
    assert len(job.result["rows"]) == 2
    assert all(row["verdict"] == expected_verdict for row in job.result["rows"])
    assert token not in json.dumps(job.result)
    child = ExperimentRun.no_workspace_objects.get(id=job.result["child_run_id"])
    assert child.items.count() == 2
    for item in child.items.all():
        assert {key: item.score_refs[-1][key] for key in expected_verdict} == expected_verdict
        assert token not in json.dumps(item.score_refs)


@pytest.mark.parametrize("count,timeout,retries,concurrency", [(1000, 5000, 0, 1), (20, 30000, 3, 1), (121, 30000, 3, 8)])
def test_over_budget_run_rejected_before_queue(auth_client, transport, run, count, timeout, retries, concurrency, django_capture_on_commit_callbacks):
    from model_hub.models.remote_eval import RemoteEvalJob

    ExperimentRunItem.no_workspace_objects.bulk_create([
        ExperimentRunItem(run=run, example_id=uuid.uuid4(), status="completed", input={"prompt": "row"}, output="answer")
        for _ in range(count - 2)
    ])
    template_id = create(auth_client, definition(timeout_ms=timeout, retries=retries, retry_on=["5xx"], calls_at_a_time=concurrency))
    with patch("model_hub.tasks.remote_eval.score_remote_run.delay") as delay, django_capture_on_commit_callbacks(execute=True):
        response = auth_client.post(detail(template_id, "score-run/"), {"run_id": str(run.id)}, format="json")
    assert response.status_code == 400, response.content
    assert "budget" in response.content.decode().lower()
    assert not RemoteEvalJob.objects.filter(template_id=template_id).exists()
    delay.assert_not_called()
    transport.assert_not_called()


@pytest.mark.parametrize("count_only", [False, True])
def test_selected_run_queries_filter_and_bound_rows(auth_client, transport, run, count_only):
    from django.db import connection
    from django.test.utils import CaptureQueriesContext
    from model_hub.services.remote_eval_runs import selected_run

    template_id = create(auth_client)
    row_id = str(run.items.first().example_id)
    with CaptureQueriesContext(connection) as queries:
        if count_only:
            response = auth_client.get(detail(template_id), {"for": "score-run-calls", "run_id": str(run.id), "row_ids": row_id})
            assert response.status_code == 200
            assert response.json()["result"]["score_run_calls"] == {"calls": 1, "cells": 1}
        else:
            _, items = selected_run(run.dataset_version.dataset.organization_id, run.dataset_version.dataset.workspace_id, run.id, [row_id])
            assert [str(item.example_id) for item in items] == [row_id]
    table = ExperimentRunItem._meta.db_table
    selects = [q["sql"] for q in queries if f'FROM "{table}"' in q["sql"] and q["sql"].lstrip().upper().startswith("SELECT")]
    assert selects
    assert all('"example_id" IN' in sql for sql in selects)
    assert all("LIMIT 1001" in sql for sql in selects)
    if count_only:
        assert all('"input"' not in sql and '"output"' not in sql and '"metadata"' not in sql for sql in selects)


def test_unselected_run_query_is_bounded(run):
    from django.db import connection
    from django.test.utils import CaptureQueriesContext
    from model_hub.services.remote_eval_runs import selected_run

    with CaptureQueriesContext(connection) as queries:
        _, items = selected_run(run.dataset_version.dataset.organization_id, run.dataset_version.dataset.workspace_id, run.id)
    assert len(items) == 2
    table = ExperimentRunItem._meta.db_table
    selects = [q["sql"] for q in queries if f'FROM "{table}"' in q["sql"]]
    assert selects and all("LIMIT 1001" in sql for sql in selects)


@pytest.mark.parametrize("kind", ["bearer", "header", "oauth"])
@pytest.mark.parametrize("secret", ["8", "123456789012345"])
def test_short_credentials_refused_at_create_and_edit(auth_client, transport, kind, secret):
    from model_hub.models.evals_metric import EvalTemplateVersion

    auth = {"kind": kind}
    if kind == "oauth":
        auth["oauth"] = {"token_url": "https://auth.example.com/token",
                         "client_id": "fixture-client", "client_secret": secret}
    else:
        auth.update(token=secret, header_name="X-Eval-Key")
    template_id = create(auth_client)
    before = EvalTemplate.no_workspace_objects.get(id=template_id).config
    payload = {"remote": definition(auth=auth)}
    created = auth_client.post(CREATE, {"name": "short_credentials", **payload}, format="json")
    edited = auth_client.patch(detail(template_id), payload, format="json")
    for response in (created, edited):
        assert response.status_code == 400, response.content
        assert "16 characters" in response.content.decode()
        assert "123456789012345" not in response.content.decode()
    assert not EvalTemplate.no_workspace_objects.filter(name="short_credentials").exists()
    assert EvalTemplate.no_workspace_objects.get(id=template_id).config == before
    assert EvalTemplateVersion.objects.filter(eval_template_id=template_id).count() == 1
    transport.assert_not_called()


@pytest.mark.parametrize("kind,credentials", [
    ("bearer", {"token": "8"}),
    ("header", {"token": "8"}),
    ("oauth", {"client_secret": "8"}),
    ("hmac", {"signing_secret": "8"}),
    ("hmac", {"signing_secret": "fixture-signing-secret", "previous_secret": "8",
              "previous_valid_until": 9999999999}),
])
def test_edit_revalidates_retained_short_secrets(transport, kind, credentials):
    from rest_framework.exceptions import ValidationError
    from model_hub.services.remote_eval import prepare_definition

    auth = {"kind": kind}
    if kind == "oauth":
        auth["oauth"] = {"token_url": "https://auth.example.com/token", "client_id": "fixture-client"}
    if "previous_secret" in credentials:
        auth["previous_valid_until"] = "2286-11-20T17:46:39Z"
    remote = definition(auth=auth)
    with pytest.raises(ValidationError, match="16 characters"):
        prepare_definition(remote, remote, credentials, new=True)
    transport.assert_not_called()


def test_short_numeric_secret_does_not_redact_unrelated_numbers(transport):
    from model_hub.services.remote_eval import call_scorer, redact_values

    # Legacy credentials and OAuth access tokens can still be short at call time.
    answer = {"score": 0.8, "count": 18, "exact": 8, "nested": [80, 0.18]}
    expected = {**answer, "exact": "[redacted]"}
    assert redact_values(answer, ["8"]) == expected
    transport.return_value = PostResult(status=200, body=json.dumps(answer).encode())
    result = call_scorer(definition(auth={"kind": "header"}), {"token": "8"}, {}, "")
    assert result["verdict"] == {"value": 0.8, "passed": True, "reason": ""}
    assert result["response"]["body"] == expected


@pytest.mark.parametrize("variant", ["score-exact", "score-containing", "choice-label"])
def test_credential_echo_in_score_is_error_in_test_and_run(auth_client, transport, run, variant):
    from model_hub.models.remote_eval import RemoteEvalJob
    from model_hub.tasks.remote_eval import score_remote_run

    token = "0.1234567890123456" if variant == "score-exact" else "1234567890123456"
    label = f"echo-{token}"
    output = {"type": "choice", "choices": [{"label": label, "score": 1}, {"label": "safe", "score": 0}]} if variant == "choice-label" else {"type": "score"}
    answer = {"label": label} if variant == "choice-label" else {"score": 0.1234567890123456}
    template_id = create(auth_client, definition(auth={"kind": "header", "token": token}, output=output))
    transport.return_value = PostResult(status=200, body=json.dumps(answer).encode())
    expected = {"error": "remote scorer echoed a credential in its score"}
    tested = auth_client.post(TEST, {"template_id": template_id}, format="json")
    assert tested.status_code == 200, tested.content
    result = tested.json()["result"]
    assert result["verdict"] == expected
    assert result["tries"][0]["error"] == expected["error"]
    assert token not in tested.content.decode()
    with patch("model_hub.tasks.remote_eval.score_remote_run.delay"):
        queued = auth_client.post(detail(template_id, "score-run/"), {"run_id": str(run.id)}, format="json")
    assert queued.status_code == 202, queued.content
    job_id = queued.json()["result"]["job_id"]
    score_remote_run(job_id)
    job = RemoteEvalJob.objects.get(id=job_id)
    assert job.status == "done"
    assert job.failed == job.result["failed"] == 2
    assert job.result["scored"] == job.result["moved_cells"] == 0
    assert len(job.result["rows"]) == 2
    assert all(row["verdict"] == expected for row in job.result["rows"])
    assert token not in json.dumps(job.result)
    child = ExperimentRun.no_workspace_objects.get(id=job.result["child_run_id"])
    assert child.items.count() == 2
    for item in child.items.all():
        ref = item.score_refs[-1]
        assert ref["status"] == "error"
        assert ref["error"] == expected["error"]
        assert "value" not in ref and "passed" not in ref and "label" not in ref
        assert token not in json.dumps(item.score_refs)


def test_authentication_required(api_client):
    assert api_client.post(CREATE, {}, format="json").status_code in (401, 403)
    assert api_client.post(TEST, {}, format="json").status_code in (401, 403)
    assert api_client.get(detail(uuid.uuid4())).status_code in (401, 403)
    assert api_client.get(job_url(uuid.uuid4())).status_code in (401, 403)
