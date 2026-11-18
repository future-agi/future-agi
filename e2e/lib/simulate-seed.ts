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
  /** With `trial`: the finished run and its one trial. */
  runTestId?: string;
  testExecutionId?: string;
  callExecutionId?: string;
}

/**
 * Seeds an Environment as a finished build leaves it, then versions it with the
 * real backfill: agent, environment and suite versions, suite members and, when
 * `certified`, the pairing's certified bundle (every check passed, both tools
 * certified). Uncertified, the pairing has no bundle and its first check is a
 * validation. `productionOnly` names a tool the contract says reaches production.
 * `failedCheck` names an action an uncertified pairing's last validation failed on,
 * leaving its bundle failed by that check.
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
  failedCheck?: 'lookup_order' | 'issue_refund';
  /** The refunds include a world actor with no channel (Dana Brooks). */
  actors?: boolean;
  /** One finished trial; the string variants pick its World changes (see harness_environment_seed.py). */
  trial?: boolean | 'fault' | 'actor' | 'duplicate' | 'refused';
  /** The contract's tools with their fields: inputs, units and what each returns. */
  toolDetail?: boolean;
  // Two separately wired workers and what ALK sealed for them: a tool proxy holding their limits, no door in front of
  // the tool service, or, as an old seal, nothing.
  workers?: 'sealed' | 'not_fronted' | 'old_seal';
  // Each refund case gives the refund worker a budget, so the case relies on that worker.
  refundWorkerBudget?: boolean;
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
        failed_check: opts.failedCheck ?? null,
        actors: opts.actors ?? false,
        trial: opts.trial ?? false,
        tool_detail: opts.toolDetail ?? false,
        workers: opts.workers ?? null,
        refund_worker_budget: opts.refundWorkerBudget ?? false,
      }),
    },
  ) as HarnessEnvironmentSeed;
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
  // A second call the user stopped mid-call: a worker cut short as cancelled, its run's sandbox removed.
  cancelledCallExecutionId: string;
  // A third call whose refund worker, served as its own process, the harness stopped when its deadline passed.
  stoppedWorkerCallExecutionId: string;
}

const PARTICIPANTS_SEED_SCRIPT = `
import base64
import json
import os
import urllib.parse
import urllib.request
import uuid
from datetime import timedelta
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "tfc.settings.settings")
import django
django.setup()
from django.utils import timezone

from accounts.models import Organization
from accounts.models.workspace import Workspace
from model_hub.models.choices import StatusType
from simulate.models import AgentDefinition, HostedHarnessAttempt, HostedHarnessJob, RunTest, Scenarios
from simulate.models.test_execution import CallExecution, TestExecution
from simulate.services.agent_setup import store_agent
from tracer.services.simulation_project import ensure_simulation_project


def at(seconds):
    return f"2026-11-05T10:00:{seconds:02d}"


def traced_source_agent(suffix):
    return {
        "schema_version": "1.0", "type": "chat", "kind": "source", "name": f"e2e_participants_{suffix}",
        "provider_ref": None,
        "behaviour": [{"id": "main", "shape": "external", "instructions": "Returns coordinator.",
                       "tools": ["get_order"], "transitions": []}],
        "tools": [{"name": "get_order", "description": "Look up an order.", "kind": "builtin",
                   "parameters": {"type": "object", "properties": {"order_id": {}}, "required": ["order_id"]},
                   "endpoint": None}],
        "model": None, "voice": None, "telephony": [], "supports": [], "direction_answer": None, "gaps": [],
        "instrumentation": "traceai", "extension": {}, "setup_hash": "sh1:" + "e" * 64,
    }


def send_span(project_id, trace_id, job_id, call_id):
    row = {
        "project_id": str(project_id), "observation_type": "agent",
        "start_time": timezone.now().strftime("%Y-%m-%d %H:%M:%S.%f"),
        "trace_id": trace_id, "id": uuid.uuid4().hex[:16], "parent_span_id": "", "name": "Returns coordinator",
        "latency_ms": 12, "status": "OK",
        "attrs_string": {"fi.simulator.call_execution_id": str(call_id)},
        "resource_attrs": {"fi.simulation.job_id": str(job_id)},
    }
    url = f"http://{os.environ['CH_HOST']}:{os.environ.get('CH_HTTP_PORT', '8123')}/?" + urllib.parse.urlencode(
        {"database": os.environ.get("CH_DATABASE", "default"), "query": "INSERT INTO spans FORMAT JSONEachRow"})
    request = urllib.request.Request(url, data=json.dumps(row).encode(), method="POST")
    user = os.environ.get("CH_USER") or os.environ.get("CH_USERNAME") or "default"
    token = base64.b64encode(f"{user}:{os.environ.get('CH_PASSWORD', '')}".encode()).decode()
    request.add_header("Authorization", f"Basic {token}")
    urllib.request.urlopen(request, timeout=30).read()


def main():
    organization = Organization.objects.get(id=os.environ["SEED_ORG_ID"])
    workspace = Workspace.objects.get(id=os.environ["SEED_WORKSPACE_ID"])
    suffix = os.environ["SEED_SUFFIX"]
    # A real trace id: the Traces tab lists the call's spans, and a step opens only a trace it lists.
    trace_id = uuid.uuid4().hex
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
    # A connected agent that sends traces: the run reads its spans from the agent's own simulation project.
    version = agent.create_version(description="", commit_message="first", status="active")
    store_agent(agent_version_id=version.id, agent=traced_source_agent(suffix), sdk_version="0.2.3")
    project = ensure_simulation_project(agent)
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
    # The contract snapshot declares the two workers, as the Contract tab's Participants card reads them.
    contract = {"workers": [
        {"id": "order_lookup", "name": "Order-lookup worker", "tools": ["get_order", "list_refunds", "update_order_status"],
         "allowed_tools": ["get_order", "list_refunds"], "binding": "separate", "address_env": "ORDER_LOOKUP_API_URL"},
        {"id": "refund", "name": "Refund worker", "tools": ["list_refunds", "issue_refund"], "allowed_tools": [],
         "binding": "shared", "process": "refund-worker"},
    ]}
    HostedHarnessJob.no_workspace_objects.filter(id=environment.id).update(
        state=HostedHarnessJob.State.COMPLETED, current_stage="completed",
        stage_outputs=[{"kind": "contract", "data": contract}],
    )
    run = f"run-{suffix}"
    record = {
        "collected_at": at(30),
        "started_at": at(0),
        "spans_received": True,
        "collection": "complete",
        # The project its spans were read from, as record_call_workers keeps it: Participants opens a step's trace there.
        "project_id": str(project.id),
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
        # Two workers named the same order: the World changes tab lists the record and its committed writes.
        "shared_records": [
            {"key": "order_id=1042", "argument": "order_id", "value": "1042", "committed_writes": 1,
             "truncated": False,
             "actions": [
                 {"who": "worker:order_lookup", "tool": "list_refunds", "outcome": "committed", "write": None},
                 {"who": "worker:refund", "tool": "issue_refund", "outcome": "committed", "write": True},
             ]},
        ],
    }
    call = CallExecution.objects.create(
        test_execution=execution,
        scenario=scenario,
        status=CallExecution.CallStatus.COMPLETED,
        simulation_call_type=CallExecution.SimulationCallType.TEXT,
        completed_at=timezone.now(),
        call_metadata={"harness_workers": record, "harness_trial_state": trial_state},
    )
    # The span the trace is: in the agent's project, marked with this run and this call, as the harness marks them.
    send_span(project.id, trace_id, environment.id, call.id)
    # The run's attempt with its sandbox removed, and a call the user stopped while the refund hand-off was out.
    HostedHarnessJob.no_workspace_objects.filter(id=environment.id).update(current_attempt_number=1)
    HostedHarnessAttempt.no_workspace_objects.create(
        job=environment, attempt_number=1, token_hash="t" * 64, fence_hash="f" * 64,
        expires_at=timezone.now() + timedelta(hours=1), cleanup_verified_at=timezone.now(),
    )
    stopped = {
        **record,
        "workers": [
            {**record["workers"][0], "state": "completed"},
            {**record["workers"][1], "state": "cancelled"},
        ],
        "events": record["events"][:4],
    }
    cancelled = CallExecution.objects.create(
        test_execution=execution,
        scenario=scenario,
        status=CallExecution.CallStatus.CANCELLED,
        simulation_call_type=CallExecution.SimulationCallType.TEXT,
        completed_at=timezone.now(),
        # The guest sealed it as the user's stop, as a Stop during the call does.
        call_metadata={
            "harness_workers": stopped,
            "hosted_harness_receipt": {"status": "errored", "failure": {"code": "user_canceled"}},
        },
    )
    # The refund worker runs as its own process: past its deadline the harness ended it (sealed as stopped_workers).
    past_deadline = {
        **record,
        "workers": [
            record["workers"][0],
            {**record["workers"][1], "state": "deadline_passed", "stopped_at": 1763200000.0},
        ],
        "events": record["events"][:4],
    }
    stopped_worker = CallExecution.objects.create(
        test_execution=execution,
        scenario=scenario,
        status=CallExecution.CallStatus.COMPLETED,
        simulation_call_type=CallExecution.SimulationCallType.TEXT,
        completed_at=timezone.now(),
        call_metadata={"harness_workers": past_deadline},
    )
    print(json.dumps({
        "environmentId": str(environment.id),
        "runTestId": str(run_test.id),
        "testExecutionId": str(execution.id),
        "callExecutionId": str(call.id),
        "traceId": trace_id,
        "cancelledCallExecutionId": str(cancelled.id),
        "stoppedWorkerCallExecutionId": str(stopped_worker.id),
    }))


main()
`;

export function seedParticipantsCall(opts: { organizationId: string; workspaceId: string }): ParticipantsSeed {
  return runInlineSeed<ParticipantsSeed>('e2e-participants-', PARTICIPANTS_SEED_SCRIPT, opts);
}

/** Runs one seed script in the backend container for the caller's org and workspace; returns its last JSON line. */
function runInlineSeed<T>(prefix: string, script: string, opts: { organizationId: string; workspaceId: string }): T {
  const container = resolveBackendContainer();
  const suffix = `${Date.now().toString(36)}${randomBytes(2).toString('hex')}`;
  const dir = mkdtempSync(join(tmpdir(), prefix));
  const scriptPath = join(dir, `seed-${suffix}.py`);
  writeFileSync(scriptPath, script);
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
    return JSON.parse(lastLine) as T;
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
}

/**
 * Seeds a hosted Run that is under way, for the Stop journey: one call in progress and one case the harness already
 * skipped (stored cancelled with `harness_outcome_status: skipped` and its skipped receipt, as
 * `_call_lifecycle_status` in `futureagi/simulate/services/hosted_harness_ingestion.py` writes it). The Run's attempt
 * has no sandbox, so the user's Stop runs the product's own cancel path to its end on this stack: the workflow signal
 * finds no workflow and `HostedHarnessGateway.cancel` closes the attempt, the job, the Run and its open call as
 * cancelled. Built through the ORM for the same reason as `seedParticipantsCall`: the sandbox that runs calls is not
 * part of this stack.
 */
export interface StopJourneySeed {
  environmentId: string;
  runJobId: string;
  runTestId: string;
  testExecutionId: string;
  ongoingCallExecutionId: string;
  skippedCallExecutionId: string;
  ongoingScenarioName: string;
  skippedScenarioName: string;
}

const STOP_JOURNEY_SEED_SCRIPT = `
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
from simulate.models import (
    AgentDefinition,
    HostedHarnessAttempt,
    HostedHarnessJob,
    HostedHarnessReceipt,
    HostedHarnessScenario,
    RunTest,
    Scenarios,
)
from simulate.models.test_execution import CallExecution, TestExecution


def job(organization, workspace, run_test, name, **fields):
    return HostedHarnessJob.no_workspace_objects.create(
        organization=organization,
        workspace=workspace,
        run_test=run_test,
        name=name,
        run_id=uuid.uuid4(),
        idempotency_key=uuid.uuid4().hex,
        request_digest=uuid.uuid4().hex,
        schema_version="1.6",
        seed=1,
        artifact_level="standard",
        max_artifact_bytes=1024,
        deadline_at=timezone.now() + timedelta(hours=1),
        scenario_count=2,
        payload={"source": {"kind": "archive"}, "metadata": {"name": name}},
        **fields,
    )


def main():
    organization = Organization.objects.get(id=os.environ["SEED_ORG_ID"])
    workspace = Workspace.objects.get(id=os.environ["SEED_WORKSPACE_ID"])
    suffix = os.environ["SEED_SUFFIX"]
    agent = AgentDefinition.objects.create(
        agent_name=f"e2e-stop-agent-{suffix}",
        agent_type=AgentDefinition.AgentTypeChoices.TEXT,
        inbound=True,
        description="e2e SIM stop journey seed",
        organization=organization,
        workspace=workspace,
        languages=["en"],
    )
    scenarios = {}
    for kind in ("ongoing", "skipped"):
        scenarios[kind] = Scenarios.objects.create(
            name=f"e2e-stop-{kind}-{suffix}",
            description="e2e SIM stop journey scenario",
            source="test",
            scenario_type=Scenarios.ScenarioTypes.DATASET,
            organization=organization,
            workspace=workspace,
            agent_definition=agent,
            status=StatusType.COMPLETED.value,
        )
    run_test = RunTest.objects.create(
        name=f"e2e-stop-run-{suffix}", agent_definition=agent, organization=organization, workspace=workspace,
    )
    run_test.scenarios.add(*scenarios.values())
    # A built environment: its run detail shows calls rather than the build overview.
    environment = job(organization, workspace, run_test, f"e2e-stop-env-{suffix}")
    HostedHarnessJob.no_workspace_objects.filter(id=environment.id).update(
        state=HostedHarnessJob.State.COMPLETED, current_stage="completed",
    )
    execution = TestExecution.objects.create(
        run_test=run_test,
        agent_definition=agent,
        status=TestExecution.ExecutionStatus.RUNNING,
        total_scenarios=2,
        total_calls=2,
    )
    # The Run's own job, running on its first attempt, whose sandbox does not exist on this stack.
    run_job = job(organization, workspace, run_test, f"e2e-stop-env-{suffix}", environment=environment,
                  test_execution=execution)
    HostedHarnessJob.no_workspace_objects.filter(id=run_job.id).update(
        state=HostedHarnessJob.State.RUNNING, current_stage="running", current_attempt_number=1,
    )
    attempt = HostedHarnessAttempt.no_workspace_objects.create(
        job=run_job, attempt_number=1, token_hash=uuid.uuid4().hex, fence_hash=uuid.uuid4().hex,
        expires_at=timezone.now() + timedelta(hours=1), state=HostedHarnessAttempt.State.RUNNING,
    )
    ongoing = CallExecution.objects.create(
        test_execution=execution,
        scenario=scenarios["ongoing"],
        status=CallExecution.CallStatus.ONGOING,
        simulation_call_type=CallExecution.SimulationCallType.TEXT,
        started_at=timezone.now(),
    )
    skipped = CallExecution.objects.create(
        test_execution=execution,
        scenario=scenarios["skipped"],
        status=CallExecution.CallStatus.CANCELLED,
        simulation_call_type=CallExecution.SimulationCallType.TEXT,
        completed_at=timezone.now(),
        call_metadata={"harness_outcome_status": "skipped"},
    )
    # The harness's receipt for the case it skipped, as it posts one before the user stops the Run.
    registration = HostedHarnessScenario.no_workspace_objects.create(
        job=run_job, scenario_key=f"skipped-{suffix}", scenario=scenarios["skipped"], call_execution=skipped,
    )
    HostedHarnessReceipt.no_workspace_objects.create(
        job=run_job, attempt=attempt, scenario=registration, attempt_number=1, digest="sha256:" + "0" * 64,
        status="skipped", body={"scenario_key": registration.scenario_key, "status": "skipped"},
    )
    print(json.dumps({
        "environmentId": str(environment.id),
        "runJobId": str(run_job.id),
        "runTestId": str(run_test.id),
        "testExecutionId": str(execution.id),
        "ongoingCallExecutionId": str(ongoing.id),
        "skippedCallExecutionId": str(skipped.id),
        "ongoingScenarioName": scenarios["ongoing"].name,
        "skippedScenarioName": scenarios["skipped"].name,
    }))


main()
`;

export function seedStopJourney(opts: { organizationId: string; workspaceId: string }): StopJourneySeed {
  return runInlineSeed<StopJourneySeed>('e2e-stop-', STOP_JOURNEY_SEED_SCRIPT, opts);
}
