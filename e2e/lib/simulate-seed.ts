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

export function seedCallExecutions(opts: {
  organizationId: string;
  workspaceId: string;
  count?: number;
}): CallExecutionSeed {
  const container = resolveBackendContainer();
  const suffix = `${Date.now().toString(36)}-${randomBytes(3).toString('hex')}`;
  const dir = mkdtempSync(join(tmpdir(), 'e2e-pag5-'));
  const scriptPath = join(dir, `seed-${suffix}.py`);
  writeFileSync(scriptPath, SEED_SCRIPT);
  try {
    execFileSync('docker', ['cp', scriptPath, `${container}:/tmp/seed-${suffix}.py`]);
    const stdout = execFileSync('docker', ['exec',
      '-e', `SEED_ORG_ID=${opts.organizationId}`,
      '-e', `SEED_WORKSPACE_ID=${opts.workspaceId}`,
      '-e', `SEED_SUFFIX=${suffix}`,
      '-e', `SEED_CALL_COUNT=${opts.count ?? 12}`,
      container, 'sh', '-c',
      `cd /app/backend && PYTHONPATH=/app/backend python /tmp/seed-${suffix}.py`,
    ], { encoding: 'utf-8' });
    const lastLine = stdout.trim().split('\n').pop() ?? '';
    return JSON.parse(lastLine) as CallExecutionSeed;
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
}


/**
 * Seeds one completed chat call whose Participants record is already stored, for the Participants table flow
 * (`simulate/v3/call-executions/{id}/workers/`). The record is the shape `record_call_workers` writes
 * (`futureagi/simulate/services/harness_workers.py`): two caller turns, two declared workers, a hand-off with a tool
 * call read from a trace, a hand-off the record could not place (`relationship: unknown`), and the trial's world
 * changes with a worker's refused attempt. Built through the ORM for the same reason as `seedCallExecutions`: the
 * harness that writes these records runs in a sandbox this stack does not start.
 */
export interface ParticipantsSeed {
  environmentId: string;
  runTestId: string;
  testExecutionId: string;
  callExecutionId: string;
  traceId: string;
}

const PARTICIPANTS_SEED_SCRIPT = `
import json
import os
import uuid
from datetime import timedelta
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "tfc.settings.settings")
import django
django.setup()
from django.utils import timezone

from accounts.models import Organization
from accounts.models.workspace import Workspace
from model_hub.models.choices import StatusType
from simulate.models import AgentDefinition, HostedHarnessJob, RunTest, Scenarios
from simulate.models.test_execution import CallExecution, TestExecution


def at(seconds):
    return f"2026-11-05T10:00:{seconds:02d}"


def main():
    organization = Organization.objects.get(id=os.environ["SEED_ORG_ID"])
    workspace = Workspace.objects.get(id=os.environ["SEED_WORKSPACE_ID"])
    suffix = os.environ["SEED_SUFFIX"]
    trace_id = f"e2e-trace-{suffix}"
    agent = AgentDefinition.objects.create(
        agent_name=f"e2e-participants-agent-{suffix}",
        agent_type=AgentDefinition.AgentTypeChoices.TEXT,
        inbound=True,
        description="e2e SIM participants seed",
        organization=organization,
        workspace=workspace,
        languages=["en"],
    )
    scenario = Scenarios.objects.create(
        name=f"e2e-participants-scenario-{suffix}",
        description="e2e SIM participants seed scenario",
        source="test",
        scenario_type=Scenarios.ScenarioTypes.DATASET,
        organization=organization,
        workspace=workspace,
        agent_definition=agent,
        status=StatusType.COMPLETED.value,
    )
    run_test = RunTest.objects.create(
        name=f"e2e-participants-run-{suffix}",
        agent_definition=agent,
        organization=organization,
        workspace=workspace,
    )
    run_test.scenarios.add(scenario)
    execution = TestExecution.objects.create(
        run_test=run_test,
        agent_definition=agent,
        status=TestExecution.ExecutionStatus.COMPLETED,
        total_scenarios=1,
        total_calls=1,
    )
    environment = HostedHarnessJob.no_workspace_objects.create(
        organization=organization,
        workspace=workspace,
        run_test=run_test,
        test_execution=execution,
        name=f"e2e-participants-env-{suffix}",
        run_id=uuid.uuid4(),
        idempotency_key=uuid.uuid4().hex,
        request_digest=uuid.uuid4().hex,
        schema_version="1.6",
        seed=1,
        artifact_level="standard",
        max_artifact_bytes=1024,
        deadline_at=timezone.now() + timedelta(hours=1),
        scenario_count=1,
        payload={"source": {"kind": "archive"}, "metadata": {"name": f"e2e-participants-env-{suffix}"}},
    )
    # A built environment: its run detail shows calls rather than the build overview.
    HostedHarnessJob.no_workspace_objects.filter(id=environment.id).update(
        state=HostedHarnessJob.State.COMPLETED, current_stage="completed"
    )
    run = f"run-{suffix}"
    record = {
        "collected_at": at(30),
        "started_at": at(0),
        "spans_received": True,
        "collection": "complete",
        "coordinator": {"name": "Returns coordinator"},
        "workers": [
            {"worker_id": f"{run}/order_lookup", "id": "order_lookup", "name": "Order-lookup worker",
             "observed": True, "undeclared": False, "instance_ids": ["d1"]},
            {"worker_id": f"{run}/refund", "id": "refund", "name": "Refund worker",
             "observed": True, "undeclared": False, "instance_ids": ["d2"]},
        ],
        "messages": [
            {"role": "user", "text": f"look up order {suffix}", "at": at(1)},
            {"role": "agent", "text": "order found", "at": at(5)},
            {"role": "user", "text": "now refund it", "at": at(8)},
            {"role": "agent", "text": "refund handled", "at": at(14)},
        ],
        "events": [
            {"seq": 1, "type": "delegated", "worker_id": f"{run}/order_lookup", "by": None, "delegation_id": "d1",
             "task": f"inspect order {suffix}", "at": at(2), "attribution": "reported", "trace_id": trace_id},
            {"seq": 2, "type": "tool_call", "worker_id": f"{run}/order_lookup", "delegation_id": "d1",
             "tool": "get_order", "status": "ok", "input": {"order_id": suffix}, "output": {"status": "delivered"},
             "span_id": "s2", "trace_id": trace_id, "at": at(3), "attribution": "reported"},
            {"seq": 3, "type": "returned", "worker_id": f"{run}/order_lookup", "delegation_id": "d1",
             "result": "eligible", "status": "ok", "at": at(4), "attribution": "reported"},
            {"seq": 4, "type": "delegated", "worker_id": f"{run}/refund", "by": f"{run}/order_lookup",
             "delegation_id": "d2", "relationship": "unknown", "task": "refund the order", "at": at(9),
             "attribution": "reported"},
            {"seq": 5, "type": "returned", "worker_id": f"{run}/refund", "delegation_id": "d2",
             "result": "refunded", "status": "ok", "at": at(12), "attribution": "reported"},
        ],
    }
    trial_state = {
        "state_changes": [
            {"who": "worker:order_lookup", "name": "order_lookup", "attempted": 2, "committed": 1, "refused": 1,
             "faulted": 0, "duplicate": 0, "committed_actions": ["get_order"],
             "refused_actions": [{"action": "update_order_status", "code": "not_permitted",
                                  "reason": "orders.update_status is not permitted for order_lookup"}]},
        ],
        "decisions": [],
    }
    call = CallExecution.objects.create(
        test_execution=execution,
        scenario=scenario,
        status=CallExecution.CallStatus.COMPLETED,
        simulation_call_type=CallExecution.SimulationCallType.TEXT,
        completed_at=timezone.now(),
        call_metadata={"harness_workers": record, "harness_trial_state": trial_state},
    )
    print(json.dumps({
        "environmentId": str(environment.id),
        "runTestId": str(run_test.id),
        "testExecutionId": str(execution.id),
        "callExecutionId": str(call.id),
        "traceId": trace_id,
    }))


main()
`;

export function seedParticipantsCall(opts: { organizationId: string; workspaceId: string }): ParticipantsSeed {
  const container = resolveBackendContainer();
  const suffix = `${Date.now().toString(36)}${randomBytes(2).toString('hex')}`;
  const dir = mkdtempSync(join(tmpdir(), 'e2e-participants-'));
  const scriptPath = join(dir, `seed-${suffix}.py`);
  writeFileSync(scriptPath, PARTICIPANTS_SEED_SCRIPT);
  try {
    execFileSync('docker', ['cp', scriptPath, `${container}:/tmp/seed-${suffix}.py`]);
    const stdout = execFileSync('docker', ['exec',
      '-e', `SEED_ORG_ID=${opts.organizationId}`,
      '-e', `SEED_WORKSPACE_ID=${opts.workspaceId}`,
      '-e', `SEED_SUFFIX=${suffix}`,
      container, 'sh', '-c',
      `cd /app/backend && PYTHONPATH=/app/backend python /tmp/seed-${suffix}.py`,
    ], { encoding: 'utf-8' });
    const lastLine = stdout.trim().split('\n').pop() ?? '';
    return JSON.parse(lastLine) as ParticipantsSeed;
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
}
