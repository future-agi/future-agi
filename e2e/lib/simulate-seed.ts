import { randomBytes } from 'node:crypto';
import { execFileSync } from 'node:child_process';
import { mkdtempSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { E2E } from './env';

/**
 * Seeds `CallExecution` rows for the agent-definition-scoped call-log screen
 * (`simulate/agent-definitions/{id}/versions/{id}/call-executions/`), the one
 * consumer of `CursorGridPagination` backed by a plain DRF
 * `PageNumberPagination` view instead of the cursor list contract (see
 * `getListPagerState`'s `hasCursorContract` comment in
 * `frontend/src/sections/projects/LLMTracing/listPagerState.js`).
 *
 * There is no REST path that creates a *completed* `CallExecution` with
 * non-empty `eval_outputs` (the view's own filter —
 * `AgentVersionCallExecutionView.get`, `futureagi/simulate/views/agent_version.py`)
 * short of actually running a voice/chat simulation through Temporal + a
 * provider (LiveKit/Vapi/Retell) or the chat-sim mock path — infrastructure
 * this harness does not stand up. Instead this creates the row chain
 * (`AgentDefinition` -> `AgentVersion` -> `Scenarios` -> `RunTest` ->
 * `TestExecution` -> N `CallExecution`) directly through the real Django ORM,
 * executed inside the running backend container. Field requirements are
 * pinned off `futureagi/simulate/tests/test_call_execution_action_scope.py`,
 * an existing backend test that builds the same chain by hand for the same
 * reason (no create-call-execution API to call instead).
 *
 * Scoped per-call: every row belongs to the caller's own `organizationId` /
 * `workspaceId`, so parallel workers each seeding their own actor never share
 * state. Requires the Docker CLI and the selected stack's running Django
 * service (`backend` on Distributed, `app` on Standalone). Set
 * `SIMULATE_SEED_CONTAINER` to explicitly override the container name.
 */
export interface CallExecutionSeed {
  agentDefinitionId: string;
  agentVersionId: string;
  callExecutionCount: number;
}

const SEED_SCRIPT = `
import json
import os
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "tfc.settings.settings")
import django
django.setup()

from accounts.models import Organization
from accounts.models.workspace import Workspace
from model_hub.models.choices import StatusType
from simulate.models import AgentDefinition, AgentVersion, RunTest, Scenarios
from simulate.models.test_execution import CallExecution, TestExecution


def main():
    organization = Organization.objects.get(id=os.environ["SEED_ORG_ID"])
    workspace = Workspace.objects.get(id=os.environ["SEED_WORKSPACE_ID"])
    count = int(os.environ.get("SEED_CALL_COUNT", "12"))
    suffix = os.environ["SEED_SUFFIX"]

    agent_definition = AgentDefinition.objects.create(
        agent_name=f"e2e-pag5-agent-{suffix}",
        agent_type=AgentDefinition.AgentTypeChoices.VOICE,
        inbound=True,
        description="e2e PAG-05 seed: non-cursor call-log pager",
        organization=organization,
        workspace=workspace,
        languages=["en"],
    )
    agent_version = AgentVersion.objects.create(
        agent_definition=agent_definition,
        organization=organization,
        workspace=workspace,
        description=f"e2e PAG-05 seed version {suffix}",
        commit_message="e2e PAG-05 seed",
        status=AgentVersion.StatusChoices.ACTIVE,
    )
    scenario = Scenarios.objects.create(
        name=f"e2e-pag5-scenario-{suffix}",
        description="e2e PAG-05 seed scenario",
        source="test",
        scenario_type=Scenarios.ScenarioTypes.DATASET,
        organization=organization,
        workspace=workspace,
        agent_definition=agent_definition,
        status=StatusType.COMPLETED.value,
    )
    run_test = RunTest.objects.create(
        name=f"e2e-pag5-run-{suffix}",
        description="e2e PAG-05 seed run",
        agent_definition=agent_definition,
        organization=organization,
        workspace=workspace,
    )
    run_test.scenarios.add(scenario)
    test_execution = TestExecution.objects.create(
        run_test=run_test,
        status=TestExecution.ExecutionStatus.COMPLETED,
        total_scenarios=1,
        total_calls=count,
        agent_definition=agent_definition,
    )
    for _ in range(count):
        CallExecution.objects.create(
            test_execution=test_execution,
            scenario=scenario,
            agent_version=agent_version,
            status=CallExecution.CallStatus.COMPLETED,
            simulation_call_type=CallExecution.SimulationCallType.VOICE,
            eval_outputs={"e2e_seed_check": {"output_bool": True}},
        )
    print(json.dumps({
        "agentDefinitionId": str(agent_definition.id),
        "agentVersionId": str(agent_version.id),
        "callExecutionCount": count,
    }))


main()
`;

type DockerRow = [name: string, ports: string, project: string, service: string];

const CONTAINER_FORMAT = '{{.Names}}\t{{.Ports}}\t{{.Label "com.docker.compose.project"}}\t{{.Label "com.docker.compose.service"}}';

function runningContainers(): string {
  return execFileSync('docker', ['ps', '--format', CONTAINER_FORMAT], { encoding: 'utf-8' });
}

/**
 * Names the backend container to seed through.
 *
 * Distributed, Standalone and attached stacks can coexist, so a guessed
 * container name can seed the wrong database or name a service that does not
 * exist. Resolve the Postgres publishing E2E_PG_URL's port, then require one
 * running backend/app service with that exact compose-project label. Docker
 * names (including custom names and replica suffixes) are not scope evidence.
 */
export function resolveBackendContainer(
  pgUrl = E2E.pgUrl,
  override = process.env.SIMULATE_SEED_CONTAINER,
  listContainers: () => string = runningContainers,
): string {
  if (override) return override;

  const pgPort = new URL(pgUrl).port || '5432';
  const rows = listContainers().trim().split('\n').filter(Boolean)
    .map((line) => line.split('\t') as DockerRow);
  const postgres = rows.filter(([, ports, , service]) =>
    service === 'postgres' && (ports ?? '').includes(`:${pgPort}->`));
  if (postgres.length !== 1 || !postgres[0][2]) {
    throw new Error(
      `simulate seed: expected exactly one running compose Postgres on port ${pgPort} `
      + '(from E2E_PG_URL) with a project label. Bring the stack up, or set '
      + 'SIMULATE_SEED_CONTAINER to the backend container to seed through.',
    );
  }

  const project = postgres[0][2];
  const runtimes = rows.filter(([name, , candidateProject, service]) =>
    name && candidateProject === project && (service === 'backend' || service === 'app'));
  if (runtimes.length !== 1) {
    throw new Error(
      `simulate seed: Postgres on port ${pgPort} belongs to compose project "${project}", but `
      + `expected exactly one running backend/app service, found ${runtimes.length}. `
      + 'Set SIMULATE_SEED_CONTAINER to override.',
    );
  }
  return runtimes[0][0];
}

/**
 * Runs one seed script inside the backend container with `env` and returns the
 * JSON object it prints on its last line. `SEED_SUFFIX` is always set, unique
 * per call.
 */
function runSeedScript<T>(script: string, env: Record<string, string>): T {
  const container = resolveBackendContainer();
  const suffix = `${Date.now().toString(36)}-${randomBytes(3).toString('hex')}`;
  const dir = mkdtempSync(join(tmpdir(), 'e2e-seed-'));
  const scriptPath = join(dir, `seed-${suffix}.py`);
  writeFileSync(scriptPath, script);
  try {
    execFileSync('docker', ['cp', scriptPath, `${container}:/tmp/seed-${suffix}.py`]);
    const vars = Object.entries({ ...env, SEED_SUFFIX: suffix }).flatMap(([name, value]) => ['-e', `${name}=${value}`]);
    const stdout = execFileSync('docker', ['exec', ...vars,
      container, 'sh', '-c',
      `cd /app/backend && PYTHONPATH=/app/backend python /tmp/seed-${suffix}.py`,
    ], { encoding: 'utf-8' });
    const lastLine = stdout.trim().split('\n').pop() ?? '';
    return JSON.parse(lastLine) as T;
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
}

export function seedCallExecutions(opts: {
  organizationId: string;
  workspaceId: string;
  count?: number;
}): CallExecutionSeed {
  return runSeedScript<CallExecutionSeed>(SEED_SCRIPT, {
    SEED_ORG_ID: opts.organizationId,
    SEED_WORKSPACE_ID: opts.workspaceId,
    SEED_CALL_COUNT: String(opts.count ?? 12),
  });
}

/**
 * A versioned hosted environment whose draft holds one chat-cited rule, for the
 * review, Validate and Save routes (TH-8928).
 *
 * No hosted build runs in this harness (no sandbox provider), so the
 * environment is built the way the backend's own versioning tests build one
 * (`simulate/tests/test_harness_versioning_backfill.py` `_legacy`, then
 * `backfill_environment`): a completed hosted job with one scenario, its
 * authoring archive in object storage, and an agent definition whose version
 * the backfill pins with a side row, so the pairing can be validated. The rule
 * is written into the environment draft through `record_draft` with the id of
 * a person's chat message, exactly as a builder-chat turn writes it.
 *
 * Requires the E2E settings' `HARNESS_VERSIONING_ENABLED` and
 * `HARNESS_BUNDLE_CERTIFY_ENABLED` (e2e/stack/backend/settings_e2e.py).
 */
export interface ReviewEnvironmentSeed {
  environmentId: string;
  messageId: string;
  rule: string;
  amendment: string;
}

const REVIEW_ENVIRONMENT_SCRIPT = `
import json
import os
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "tfc.settings.settings")
import django
django.setup()

from django.contrib.auth import get_user_model

from accounts.models import Organization
from accounts.models.workspace import Workspace
from simulate.models import (
    AgentDefinition,
    AgentVersion,
    HostedHarnessConversation,
    HostedHarnessConversationMessage,
    HostedHarnessJob,
    RunTest,
)
from simulate.services.harness_versioning import archives, drafts
from simulate.services.harness_versioning.backfill import backfill_environment
from simulate.services.hosted_harness import create_hosted_job, provision_scenarios, register_attempt
from simulate.services.hosted_harness_conversation import enqueue_message
from simulate.services.hosted_harness_gateway import authoring_content_digest

NAME = "book_ride_airport"
TOOLS = ("request_approval", "approve_discount", "reject_discount")
CONTRACT = {
    "agent": "sales",
    "tools": [{"name": name, "description": name.replace("_", " ") + " on a deal"} for name in TOOLS],
    "tool_entrypoints": [{"tool": name, "service": "tools-api", "endpoint": "crm/" + name} for name in TOOLS],
}
SECRET = {"manager": "platform-vault", "purpose": "target_provider", "version": "1"}


def payload():
    return {
        "schema_version": "futureagi.harness-job.v1",
        "source": {"kind": "github", "repository": "acme/agent", "ref": "main", "visibility": "public"},
        "agent": {
            "connector": "auto",
            "config": {},
            "secret_refs": {
                name: {"key": "harness-" + name.lower(), **SECRET}
                for name in ("LIVEKIT_URL", "LIVEKIT_API_KEY", "LIVEKIT_API_SECRET")
            },
        },
        "scenario_count": 1,
        "seed": 42,
        "runtime": {
            "isolation": "dedicated_vm", "cpu_units": 4, "memory_mb": 8192, "parallelism": 1,
            "concurrency_weight": 1, "max_duration_seconds": 600, "network_policy": "live",
        },
        "security": {
            "untrusted_source": True, "read_only_source": True, "allow_privileged": False,
            "allow_host_runtime_control": False, "allowed_egress_domains": ["api.example.com"],
        },
        "retry": {
            "max_infrastructure_attempts": 2, "initial_backoff_seconds": 1, "max_backoff_seconds": 15,
            "retryable_domains": ["infrastructure"],
        },
        "artifacts": {"level": "full", "retention_days": 30, "allow_bundle_download": False, "max_artifact_bytes": 1024},
        "metadata": {},
    }


def authoring(contract):
    document = {"name": NAME, "scenario_key": NAME.replace("_", "-"), "instruction": "call about " + NAME, "tests": "t"}
    files = {
        "contract.json": json.dumps(contract),
        "sub_goals.json": json.dumps({"sub_goals": [{"name": "refunded", "what": "a refund was issued"}]}),
        "schema.sql": "create table orders(id int);",
        "scenarios.json": json.dumps([document]),
        "scenarios/" + NAME + "/scenario.json": json.dumps(document),
        "scenarios/" + NAME + "/ready.py": "def ready(world):\n    return True\n",
        "environment-bundle/manifest.json": json.dumps({"files": []}),
        "runtime-validation.json": json.dumps({"status": "passed"}),
    }
    return archives.pack_archive({path: text.encode() for path, text in files.items()})


def main():
    organization = Organization.objects.get(id=os.environ["SEED_ORG_ID"])
    workspace = Workspace.objects.get(id=os.environ["SEED_WORKSPACE_ID"])
    user = get_user_model().objects.get(email=os.environ["SEED_USER_EMAIL"])
    suffix = os.environ["SEED_SUFFIX"]

    environment, _ = create_hosted_job(
        organization, payload(), idempotency_key="e2e-review-" + suffix, workspace=workspace
    )
    attempt = register_attempt(environment.id, endpoint_base_url="https://harness.example.test").attempt
    provision_scenarios(attempt, {
        "operation": "provision", "name": "Suite", "modality": "voice",
        "personas": [{"scenario_key": NAME.replace("_", "-"), "name": NAME, "role": "customer",
                      "situation": "s", "outcome": "o", "persona": {"name": NAME}}],
    })
    body = authoring(CONTRACT)
    key = "harness/environments/e2e-review-" + suffix + ".tar.gz"
    archives.put_archive(key, body)
    environment.refresh_from_db()
    environment.payload = {**environment.payload, "metadata": {
        **(environment.payload.get("metadata") or {}),
        "authoring_object_key": key,
        "authoring_revision": authoring_content_digest(body),
    }}
    environment.state = HostedHarnessJob.State.COMPLETED
    environment.current_stage = "completed"
    environment.save(update_fields=["payload", "state", "current_stage", "updated_at"])
    definition = AgentDefinition.objects.create(
        agent_name="e2e-review-agent-" + suffix, agent_type=AgentDefinition.AgentTypeChoices.VOICE,
        contact_number="+12345678901", inbound=True, description="e2e review seed", provider="vapi",
        organization=organization, workspace=workspace, languages=["en"],
    )
    AgentVersion.no_workspace_objects.create(
        agent_definition=definition, organization=organization, workspace=workspace,
        description="prompt v1", commit_message="prompt v1", status="active",
    )
    RunTest.objects.filter(id=environment.run_test_id).update(agent_definition=definition, agent_version=None)
    backfill_environment(environment.id)
    environment.refresh_from_db()

    # A builder-chat turn answering the person's message adds the rule and logs it, as the harness does.
    conversation, message, _ = enqueue_message(
        environment, content="Discounts above 25% are never approved", client_request_id="e2e-review-" + suffix
    )
    HostedHarnessConversationMessage.no_workspace_objects.filter(id=message.id).update(state="completed")
    HostedHarnessConversation.no_workspace_objects.filter(id=conversation.id).update(state="cold")
    rule = "Never approve a discount above 25% (" + suffix + ")"
    amendment = "rule added \u2014 " + rule + ": the policy caps discounts"
    sides = drafts.edit_sides(environment)
    tree = {**sides["suite"], **sides["environment"]}
    tree["contract.json"] = json.dumps({**CONTRACT, "hard_constraints": [rule], "amendments": [amendment]}).encode()
    drafts.record_draft(
        environment, archives.pack_archive(tree), origin="chat", user=user, pieces={"environment"},
        message_id=str(message.id),
    )
    print(json.dumps({
        "environmentId": str(environment.id),
        "messageId": str(message.id),
        "rule": rule,
        "amendment": amendment,
    }))


main()
`;

export function seedReviewEnvironment(opts: {
  organizationId: string;
  workspaceId: string;
  userEmail: string;
}): ReviewEnvironmentSeed {
  return runSeedScript<ReviewEnvironmentSeed>(REVIEW_ENVIRONMENT_SCRIPT, {
    SEED_ORG_ID: opts.organizationId,
    SEED_WORKSPACE_ID: opts.workspaceId,
    SEED_USER_EMAIL: opts.userEmail,
  });
}

/**
 * The validation job's success for the environment's live candidate set: a
 * ready bundle row for its pairing with a certified sealed archive, as the
 * pair validation job leaves it when its certify passes. This harness has no
 * sandbox to run that job, so the flow stands in for it here.
 */
const CERTIFY_CANDIDATES_SCRIPT = `
import json
import os
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "tfc.settings.settings")
import django
django.setup()

from simulate.models import HarnessAssembledBundle, HostedHarnessJob
from simulate.services.harness_versioning import archives
from simulate.services.harness_versioning.assembly import CERTIFICATE, SEALED_MANIFEST
from simulate.services.harness_versioning.candidate_sets import live_set


def main():
    environment = HostedHarnessJob.no_workspace_objects.get(id=os.environ["SEED_ENVIRONMENT_ID"])
    entry = live_set(environment)
    key = "bundles/e2e-" + os.environ["SEED_SUFFIX"] + ".tar.gz"
    archives.put_archive(key, archives.pack_archive({
        SEALED_MANIFEST: json.dumps({"files": []}).encode(),
        CERTIFICATE: json.dumps({"status": "passed"}).encode(),
    }))
    bundle, _ = HarnessAssembledBundle._base_manager.update_or_create(
        job_id=environment.id,
        assembly_key=entry["assembly_key"],
        defaults={
            "organization_id": environment.organization_id,
            "agent_version_id": entry["agent_version_id"],
            "environment_version_id": entry["environment_version_id"],
            "suite_version_id": entry["suite_version_id"],
            "state": HarnessAssembledBundle.State.READY,
            "object_key": key,
            "error": None,
        },
    )
    print(json.dumps({"bundleId": str(bundle.id)}))


main()
`;

export function certifyCandidates(opts: { environmentId: string }): { bundleId: string } {
  return runSeedScript<{ bundleId: string }>(CERTIFY_CANDIDATES_SCRIPT, {
    SEED_ENVIRONMENT_ID: opts.environmentId,
  });
}
