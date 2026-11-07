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
 * Copies one seed script into the selected stack's Django runtime, runs it
 * there with `env`, and parses the JSON object it prints on its last line.
 */
function runSeedScript(prefix: string, script: string, env: Record<string, string>): unknown {
  const container = resolveBackendContainer();
  const suffix = env.SEED_SUFFIX;
  const dir = mkdtempSync(join(tmpdir(), `${prefix}-`));
  const scriptPath = join(dir, `seed-${suffix}.py`);
  writeFileSync(scriptPath, script);
  try {
    execFileSync('docker', ['cp', scriptPath, `${container}:/tmp/seed-${suffix}.py`]);
    const stdout = execFileSync('docker', ['exec',
      ...Object.entries(env).flatMap(([key, value]) => ['-e', `${key}=${value}`]),
      container, 'sh', '-c',
      `cd /app/backend && PYTHONPATH=/app/backend python /tmp/seed-${suffix}.py`,
    ], { encoding: 'utf-8' });
    const lastLine = stdout.trim().split('\n').pop() ?? '';
    return JSON.parse(lastLine);
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
}

function seedSuffix(): string {
  return `${Date.now().toString(36)}-${randomBytes(3).toString('hex')}`;
}

export function seedCallExecutions(opts: {
  organizationId: string;
  workspaceId: string;
  count?: number;
}): CallExecutionSeed {
  return runSeedScript('e2e-pag5', SEED_SCRIPT, {
    SEED_ORG_ID: opts.organizationId,
    SEED_WORKSPACE_ID: opts.workspaceId,
    SEED_SUFFIX: seedSuffix(),
    SEED_CALL_COUNT: String(opts.count ?? 12),
  }) as CallExecutionSeed;
}

/**
 * Seeds one completed Environments run with two voice calls for the
 * call-detail drawer's acoustic metrics (SIM-E2E-003):
 *
 * - `analyzedCallId` stores `audioMetrics` verbatim in
 *   `CallExecution.audio_metrics`, the column the analysis worker commits to;
 * - `notAnalyzedCallId` leaves that column NULL, as on every call recorded
 *   before the feature (the API reports it as never analyzed).
 *
 * Nothing is analyzed here: no recording exists, no provider is called and no
 * Temporal workflow starts. The analysis worker needs audio extras and
 * operator-supplied model weights this stack does not carry, so the flow
 * proves the stored result's path (API projection, drawer, raw export), not
 * the measurement. Every row is labelled as an e2e seed. The row chain
 * (HostedHarnessJob environment and run, RunTest, TestExecution,
 * HostedHarnessScenario, CallExecution) is the one the Environments run page
 * reads (`/dashboard/simulate/environments/:envId/runs/:testId/:executionId`,
 * frontend/src/routes/sections/dashboard.jsx). Same scoping and container
 * selection as `seedCallExecutions`.
 */
export interface AcousticMetricsSeed {
  environmentId: string;
  runTestId: string;
  testExecutionId: string;
  analyzedCallId: string;
  notAnalyzedCallId: string;
  suffix: string;
}

const ACOUSTIC_SEED_SCRIPT = `
import hashlib
import json
import os
import uuid
from datetime import timedelta

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "tfc.settings.settings")
import django
django.setup()

from django.db import transaction
from django.utils import timezone

from accounts.models import Organization
from accounts.models.workspace import Workspace
from simulate.models import AgentDefinition, HostedHarnessJob, RunTest, Scenarios
from simulate.models.hosted_harness import HostedHarnessScenario
from simulate.models.test_execution import CallExecution, CallTranscript, TestExecution

LABEL = "e2e SIM-E2E-003 seed: synthetic, no recording, no provider call"


def main():
    organization = Organization.objects.get(id=os.environ["SEED_ORG_ID"])
    workspace = Workspace.objects.get(id=os.environ["SEED_WORKSPACE_ID"])
    suffix = os.environ["SEED_SUFFIX"]
    audio_metrics = json.loads(os.environ["SEED_AUDIO_METRICS"])
    now = timezone.now()
    with transaction.atomic():
        agent = AgentDefinition.objects.create(
            agent_name=f"e2e-sim3-agent-{suffix}",
            agent_type=AgentDefinition.AgentTypeChoices.VOICE,
            inbound=True,
            description=LABEL,
            organization=organization,
            workspace=workspace,
            languages=["en"],
        )
        run_test = RunTest.objects.create(
            name=f"e2e-sim3-run-{suffix}",
            description=LABEL,
            agent_definition=agent,
            organization=organization,
            workspace=workspace,
        )
        execution = TestExecution.objects.create(
            run_test=run_test,
            agent_definition=agent,
            status=TestExecution.ExecutionStatus.COMPLETED,
            started_at=now - timedelta(seconds=90),
            completed_at=now,
            total_scenarios=2,
            total_calls=2,
            completed_calls=2,
            picked_up_by_executor=True,
            execution_metadata={"e2e_seed": "SIM-E2E-003"},
        )
        job = dict(
            organization=organization,
            workspace=workspace,
            request_digest="sha256:" + hashlib.sha256(suffix.encode()).hexdigest(),
            schema_version="v1",
            payload={"agent": {"mode": "voice"}, "metadata": {"e2e_seed": "SIM-E2E-003"}},
            state="completed",
            current_stage="completed",
            seed=3,
            scenario_count=2,
            completed_count=2,
            artifact_level="full",
            max_artifact_bytes=1024 * 1024,
            deadline_at=now,
            terminal_at=now,
            run_test=run_test,
        )
        environment = HostedHarnessJob.objects.create(
            run_id=uuid.uuid4(), name=f"e2e-sim3-environment-{suffix}",
            idempotency_key=f"e2e-sim3-environment-{suffix}", **job,
        )
        HostedHarnessJob.objects.create(
            run_id=uuid.uuid4(), name=f"e2e-sim3-run-job-{suffix}",
            idempotency_key=f"e2e-sim3-run-{suffix}", environment=environment, test_execution=execution, **job,
        )
        calls = {}
        for number, (key, stored) in enumerate(
            (("analyzed", audio_metrics), ("not_analyzed", None)), start=1
        ):
            scenario = Scenarios.objects.create(
                name=f"e2e-sim3-{key}-{suffix}",
                description=LABEL,
                source=LABEL,
                scenario_type=Scenarios.ScenarioTypes.SCRIPT,
                organization=organization,
                workspace=workspace,
                agent_definition=agent,
            )
            run_test.scenarios.add(scenario)
            call = CallExecution.objects.create(
                test_execution=execution,
                scenario=scenario,
                simulation_call_type=CallExecution.SimulationCallType.VOICE,
                status=CallExecution.CallStatus.COMPLETED,
                started_at=now - timedelta(seconds=30),
                completed_at=now,
                ended_at=now,
                duration_seconds=20,
                call_metadata={"e2e_seed": "SIM-E2E-003"},
                call_summary=LABEL,
                ended_reason="e2e_seed_completed",
                recording_available=False,
                transcript_available=True,
                message_count=1,
                audio_metrics=stored,
            )
            HostedHarnessScenario.objects.create(
                job=environment,
                scenario_key=f"e2e-sim3-{key}",
                scenario=scenario,
                call_execution=call,
                name=scenario.name,
                instruction=LABEL,
                number=number,
            )
            CallTranscript.objects.create(
                call_execution=call,
                speaker_role="user",
                content=LABEL,
                start_time_ms=0,
                end_time_ms=20000,
            )
            calls[key] = call
    print(json.dumps({
        "environmentId": str(environment.id),
        "runTestId": str(run_test.id),
        "testExecutionId": str(execution.id),
        "analyzedCallId": str(calls["analyzed"].id),
        "notAnalyzedCallId": str(calls["not_analyzed"].id),
        "suffix": suffix,
    }))


main()
`;

export function seedAcousticMetricsCalls(opts: {
  organizationId: string;
  workspaceId: string;
  audioMetrics: Record<string, unknown>;
}): AcousticMetricsSeed {
  return runSeedScript('e2e-sim3', ACOUSTIC_SEED_SCRIPT, {
    SEED_ORG_ID: opts.organizationId,
    SEED_WORKSPACE_ID: opts.workspaceId,
    SEED_SUFFIX: seedSuffix(),
    SEED_AUDIO_METRICS: JSON.stringify(opts.audioMetrics),
  }) as AcousticMetricsSeed;
}
