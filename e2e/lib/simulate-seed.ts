import { randomBytes } from 'node:crypto';
import { execFileSync } from 'node:child_process';
import { mkdtempSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';
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

/** Copies a seed script into the backend container, runs it there with `env`, and
 *  parses the JSON line it prints last. */
function runSeedScript(container: string, scriptPath: string, name: string, env: Record<string, string>): unknown {
  execFileSync('docker', ['cp', scriptPath, `${container}:/tmp/${name}`]);
  const stdout = execFileSync('docker', ['exec',
    ...Object.entries(env).flatMap(([key, value]) => ['-e', `${key}=${value}`]),
    container, 'sh', '-c',
    `cd /app/backend && PYTHONPATH=/app/backend python /tmp/${name}`,
  ], { encoding: 'utf-8' });
  return JSON.parse(stdout.trim().split('\n').pop() ?? '');
}

function seedSuffix(): string {
  return `${Date.now().toString(36)}-${randomBytes(3).toString('hex')}`;
}

export function seedCallExecutions(opts: {
  organizationId: string;
  workspaceId: string;
  count?: number;
}): CallExecutionSeed {
  const container = resolveBackendContainer();
  const suffix = seedSuffix();
  const dir = mkdtempSync(join(tmpdir(), 'e2e-pag5-'));
  const scriptPath = join(dir, `seed-${suffix}.py`);
  writeFileSync(scriptPath, SEED_SCRIPT);
  try {
    return runSeedScript(container, scriptPath, `seed-${suffix}.py`, {
      SEED_ORG_ID: opts.organizationId,
      SEED_WORKSPACE_ID: opts.workspaceId,
      SEED_SUFFIX: suffix,
      SEED_CALL_COUNT: String(opts.count ?? 12),
    }) as CallExecutionSeed;
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
}

/** A built, versioned Environment and the ids of its default pairing. */
export interface HarnessEnvironmentSeed {
  environmentId: string;
  agentVersionId: string;
  environmentVersionId: string;
  suiteVersionId: string;
  /** Every scenario key, in number order: the refunds, then the lookups. */
  scenarioKeys: string[];
  refundKeys: string[];
  lookupKeys: string[];
}

/**
 * Seeds an Environment as a finished build leaves it, then versions it with the
 * real backfill: agent, environment and suite versions, suite members and, when
 * `certified`, the pairing's certified bundle (every check passed, both tools
 * certified). Uncertified, the pairing has no bundle and its first check is a
 * validation. `productionOnly` names a tool the contract says reaches production.
 * Refund scenarios use `lookup_order` and `issue_refund`; lookups use only
 * `lookup_order`. See e2e/lib/harness_environment_seed.py.
 */
export function seedHarnessEnvironment(opts: {
  organizationId: string;
  workspaceId: string;
  refunds?: number;
  lookups?: number;
  certified?: boolean;
  productionOnly?: 'lookup_order' | 'issue_refund';
}): HarnessEnvironmentSeed {
  const suffix = seedSuffix();
  return runSeedScript(
    resolveBackendContainer(),
    fileURLToPath(new URL('./harness_environment_seed.py', import.meta.url)),
    `harness-seed-${suffix}.py`,
    {
      SEED_ORG_ID: opts.organizationId,
      SEED_WORKSPACE_ID: opts.workspaceId,
      SEED_SUFFIX: suffix,
      SEED_OPTIONS: JSON.stringify({
        refunds: opts.refunds ?? 1,
        lookups: opts.lookups ?? 1,
        certified: opts.certified ?? true,
        production_only: opts.productionOnly ?? null,
      }),
    },
  ) as HarnessEnvironmentSeed;
}

/** The chat-cited rule `seedReviewDraft` writes into an Environment draft. */
export interface ReviewDraftSeed {
  messageId: string;
  rule: string;
  amendment: string;
}

function runReviewSeed<T>(operation: string, env: Record<string, string>): T {
  const suffix = seedSuffix();
  return runSeedScript(
    resolveBackendContainer(),
    fileURLToPath(new URL('./harness_review_seed.py', import.meta.url)),
    `review-seed-${suffix}.py`,
    { ...env, SEED_OPERATION: operation, SEED_SUFFIX: suffix },
  ) as T;
}

/**
 * Writes a rule into the Environment's draft the way a builder-chat turn does: the
 * person's message, then `record_draft` citing it, with the rule's amendment line. Run
 * it on an Environment from `seedHarnessEnvironment`. See e2e/lib/harness_review_seed.py.
 */
export function seedReviewDraft(opts: { environmentId: string; userEmail: string }): ReviewDraftSeed {
  return runReviewSeed<ReviewDraftSeed>('chat_rule', {
    SEED_ENVIRONMENT_ID: opts.environmentId,
    SEED_USER_EMAIL: opts.userEmail,
  });
}

/**
 * Stands in for the pair validation job's success on the Environment's live candidate
 * set (the stack has no sandbox to run it): a ready bundle with a certified sealed
 * archive for that pairing.
 */
export function certifyCandidates(opts: { environmentId: string }): { bundleId: string } {
  return runReviewSeed<{ bundleId: string }>('certify_candidates', { SEED_ENVIRONMENT_ID: opts.environmentId });
}
