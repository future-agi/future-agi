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
 * state. Requires the Docker CLI and a reachable `futureagi-backend-1`
 * container (the attach-mode dev stack) — set `SIMULATE_SEED_CONTAINER` to
 * override the container name.
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

type DockerRow = [name: string, ports: string];

/**
 * Names the backend container to seed through.
 *
 * The suite runs either against the managed stack (compose project
 * `futureagi-e2e`, what CI and `bin/e2e up` boot) or, in attach mode, against
 * the dev stack (`futureagi`) — and both can be up at once on a developer
 * machine, so a hardcoded name silently seeds the wrong database or, in CI,
 * finds no container at all. A backend container shares its compose project
 * with the Postgres it reads, so the stack the suite is pointed at is the one
 * whose Postgres publishes `E2E_PG_URL`'s port: the same setting every other
 * storage-lane assertion is steered by.
 */
function resolveBackendContainer(): string {
  const override = process.env.SIMULATE_SEED_CONTAINER;
  if (override) return override;

  const pgPort = new URL(E2E.pgUrl).port || '5432';
  const rows = execFileSync('docker', ['ps', '--format', '{{.Names}}\t{{.Ports}}'], { encoding: 'utf-8' })
    .trim().split('\n').filter(Boolean)
    .map((line) => line.split('\t') as DockerRow);

  const postgres = rows.find(([name, ports]) => /-postgres-\d+$/.test(name) && (ports ?? '').includes(`:${pgPort}->`));
  if (!postgres) {
    throw new Error(
      `simulate seed: no running Postgres container publishes port ${pgPort} (from E2E_PG_URL), `
      + 'so the stack to seed cannot be identified. Bring the stack up, or set '
      + 'SIMULATE_SEED_CONTAINER to the backend container to seed through.',
    );
  }

  const project = postgres[0].replace(/-postgres-\d+$/, '');
  const backend = `${project}-backend-1`;
  if (!rows.some(([name]) => name === backend)) {
    throw new Error(
      `simulate seed: Postgres on port ${pgPort} belongs to compose project "${project}", but `
      + `${backend} is not running. Set SIMULATE_SEED_CONTAINER to override.`,
    );
  }
  return backend;
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
