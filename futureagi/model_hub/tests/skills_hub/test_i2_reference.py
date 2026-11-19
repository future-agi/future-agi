"""Historical context references survive detailed-payload retention."""

from datetime import timedelta
from uuid import uuid4

import pytest
from django.utils import timezone

from model_hub.selectors.skills_hub_v11 import run_summary
from model_hub.serializers.skills_hub_v12 import ContextRunSummary
from model_hub.services.skills_hub.v11 import jobs, runner
from model_hub.services.skills_hub.v12 import authoring, testing
from model_hub.tests.skills_hub import test_i2_integration as fixtures

pytestmark = pytest.mark.django_db(transaction=True)
execution = fixtures.execution
principal = fixtures.principal
context_factory = fixtures.context_factory
context = fixtures.context
publish_context = fixtures.publish_context


def test_historical_reference_is_retained_not_relabelled_as_production(
    execution, principal, context, publish_context, monkeypatch
):
    historical = publish_context(context).version
    draft = authoring.start_context_draft(
        principal=principal,
        context_id=context.pk,
        revision=0,
        from_version=historical.number,
    )
    authoring.save_context_draft(
        principal=principal,
        context_id=context.pk,
        revision=draft.revision,
        base_version=historical.number,
        files=[{"path": "AGENTS.md", "content": "Reviewed new synthetic policy."}],
        description=draft.context_spec["description"],
        note="",
    )
    candidate = publish_context(context).version
    config = execution[2]
    config["candidate"] = {
        "kind": "version",
        "version": candidate.number,
        "digest": candidate.digest,
    }
    config["harness"] = testing.HARNESS_VERSION
    config["baselines"] = [{"kind": "version", "version": historical.number}]
    config["cases"]["cases"][0]["expected_trigger"] = None
    monkeypatch.setattr(
        runner, "SUPPORTED_HARNESSES", (testing.HARNESS_VERSION,), raising=False
    )
    monkeypatch.setattr(testing, "dispatch_job", lambda **kwargs: None)
    preview = testing.preview_context_test(
        principal=principal, context_id=context.pk, configuration=config
    )
    run = testing.create_context_test(
        principal=principal,
        context_id=context.pk,
        idempotency_key=uuid4(),
        request={
            "configuration": config,
            "expected_fingerprint": preview["fingerprint"],
            "acknowledge_provider_processing": True,
            "inventory_review": {
                "fingerprint": preview["fingerprint"],
                "reviewed_all_files": True,
                "no_known_answer_keys": True,
                "classifications": [],
                "false_positive_secret_findings": [],
            },
        },
    )
    assert run.reference is not None
    assert run.reference["version_id"] == str(historical.pk)
    assert run.production is None
    jobs.finish_job(scope=principal.scope, job_id=run.pk)
    run.refresh_from_db()
    run.payload_expires_at = timezone.now() - timedelta(seconds=1)
    run.save(update_fields=["payload_expires_at"])
    jobs.purge_expired_payloads()
    run.refresh_from_db()
    assert run.snapshot is None
    output = ContextRunSummary(run_summary(principal=principal, run=run)).data
    assert output["reference"]["version_id"] == str(historical.pk)
    assert output["reference"]["digest"] == historical.digest
    assert output["production"] is None and output["payload_expired"] is True
