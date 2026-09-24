"""Optional cross-repository contract smoke, entirely mocked inference/storage."""

import json
import os
import subprocess
from pathlib import Path
from unittest.mock import Mock, patch

import pytest
from django.core.serializers.json import DjangoJSONEncoder
from django.test import override_settings

from tracer.models.trace_error_analysis import ErrorClusterTraces
from tracer.models.trace_grouping import TraceGroupingFeature, TraceGroupingWork
from tracer.services.grouping.accounting import reserve_call, settle_call
from tracer.services.grouping.control import (
    checkpoint_attempt,
    claim_feature_jobs,
    claim_grouping_work,
)
from tracer.services.grouping.feature_completion import complete_feature_job
from tracer.services.grouping.outbox import list_grouping_outbox
from tracer.services.grouping.publish import publish_grouping
from tracer.services.grouping_features import enqueue_grouping_features
from tracer.tests.test_grouping_snapshot import _saved_report

pytestmark = pytest.mark.django_db


@override_settings(
    ERROR_FEED_GROUPING_ENABLED=True,
    ERROR_FEED_GROUPING_ALL_PROJECTS=True,
    ERROR_FEED_GROUPING_WORK_BUDGET_USD="10",
    ERROR_FEED_GROUPING_PROJECT_BUDGET_USD="10",
    ERROR_FEED_GROUPING_TENANT_BUDGET_USD="10",
    ERROR_FEED_GROUPING_DEBOUNCE_SECONDS=0,
)
def test_python_claim_node_features_python_completion(observe_project):
    worker_root = os.environ.get("OMEGA_GROUPING_WORKER_ROOT")
    if not worker_root:
        pytest.skip("set OMEGA_GROUPING_WORKER_ROOT to the worker checkout")
    coordinator = Path(worker_root) / "workers/error-feed-node/grouping/coordinator.mjs"
    assert coordinator.is_file()
    report = _saved_report(observe_project)
    enqueue_grouping_features(report=report)
    claim = claim_feature_jobs(worker_id="contract-test", limit=1)["claims"][0]
    script = """
import {pathToFileURL} from 'node:url';
const {processFeatureClaim}=await import(pathToFileURL(process.argv[1]));
let input='';for await(const chunk of process.stdin) input+=chunk;
let completion;
await processFeatureClaim(JSON.parse(input),{
  model:{name:'all-MiniLM-L6-v2',dimension:384,servingRelease:'contract-test'},
  embedBatch:async texts=>({vectors:texts.map(()=>Array.from({length:384},(_,i)=>i===0?1:0))}),
  control:async(path,body)=>{completion=body;return {state:'ready'};},
});
process.stdout.write(JSON.stringify(completion));
"""
    response = subprocess.run(
        ["node", "--input-type=module", "-e", script, str(coordinator)],
        input=json.dumps(claim, cls=DjangoJSONEncoder),
        text=True,
        capture_output=True,
        check=True,
        timeout=20,
    )
    completion = json.loads(response.stdout)
    store = Mock()
    result = complete_feature_job(
        feature_job_id=claim["feature_job_id"], store=store, **completion
    )
    assert result["state"] == "ready"
    store.write_and_verify.assert_called_once()
    assert TraceGroupingFeature.no_workspace_objects.count() == 2
    assert TraceGroupingWork.no_workspace_objects.filter(report=report).exists()
    assert len(list_grouping_outbox(limit=20)["events"]) >= 2
    duplicate = complete_feature_job(
        feature_job_id=claim["feature_job_id"], store=store, **completion
    )
    assert duplicate["idempotent"] is True
    store.write_and_verify.assert_called_once()

    # Reuse the actual Node-built vectors, but never connect to ClickHouse.
    vectors = [
        {**row, "bucket_keys": row["index_buckets"]} for row in completion["features"]
    ]
    with patch(
        "tracer.services.grouping.context.GroupingFeatureStore"
    ) as feature_store:
        feature_store.return_value.candidate_occurrences.return_value = []
        feature_store.return_value.read_vectors.return_value = vectors
        grouping_claim = claim_grouping_work(worker_id="contract-test", limit=1)[
            "claims"
        ][0]
    runner = (
        Path(worker_root) / "workers/error-feed-node/grouping/cross-service.fixture.mjs"
    )

    def run_engine(receipt_ids=None):
        wire = {"claim": grouping_claim}
        if receipt_ids is not None:
            wire["receipt_ids_override"] = receipt_ids
        process = subprocess.run(
            ["node", str(runner)],
            input=json.dumps(wire, cls=DjangoJSONEncoder),
            text=True,
            capture_output=True,
            timeout=20,
        )
        assert process.returncode == 0, process.stdout + process.stderr
        return json.loads(process.stdout)

    first = run_engine()
    receipt_ids = []
    for raw in first["raw_results"]:
        reserved = reserve_call(
            attempt_id=grouping_claim["attempt_id"],
            lease_token=grouping_claim["lease_token"],
            request_key=raw["request_digest"],
            request_digest=raw["request_digest"],
            max_cost_usd="0.1",
            repair_intent=raw["repair_intent"],
        )
        receipt_ids.append(reserved["receipt_id"])
        settle_call(
            attempt_id=grouping_claim["attempt_id"],
            lease_token=grouping_claim["lease_token"],
            request_key=raw["request_digest"],
            request_digest=raw["request_digest"],
            status="settled",
            result=raw["result"],
            cost_usd="0.001",
            model_used="google/gemini-3.8-flash",
            input_tokens=10,
            output_tokens=10,
        )
    final = run_engine(receipt_ids)
    checkpoint_attempt(
        attempt_id=grouping_claim["attempt_id"],
        lease_token=grouping_claim["lease_token"],
        expected_revision=0,
        checkpoint=final["final_checkpoint"],
    )
    published = publish_grouping(
        attempt_id=grouping_claim["attempt_id"], **final["publish_body"]
    )
    assert (
        publish_grouping(
            attempt_id=grouping_claim["attempt_id"], **final["publish_body"]
        )
        == published
    )
    finding = report.findings.get()
    assert finding.cluster_id is not None
    assert (
        ErrorClusterTraces.no_workspace_objects.filter(
            finding=finding, cluster_id=finding.cluster_id
        ).count()
        == 1
    )
    report.refresh_from_db()
    assert report.grouping_status == "completed"
