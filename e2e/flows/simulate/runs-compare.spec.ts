import { execFileSync } from 'node:child_process';
import { randomBytes } from 'node:crypto';
import { test, expect } from '../../lib/scope-actors';
import { flowAnnotation } from '../../lib/flow-meta';
import { resolveBackendContainer } from '../../lib/simulate-seed';

// One navigation's first paint, not a stage (as environments-only-routing.spec.ts).
const UI_READY = 60_000;
const ENVIRONMENTS = '/dashboard/simulate/environments';
// futureagi/simulate/views/harness_environment.py, HarnessEnvironmentViewSet.compare.
const COMPARE = (environmentId: string) => `/simulate/api/harness-environments/${environmentId}/compare/`;

// Copy pinned off frontend/src/sections/simulate/environments/workspace/workspace.constants.js (COMPARE_COPY).
const COPY = {
  pick: 'Select 2 finished runs to compare',
  compare: 'Compare',
  heading: 'Compare runs',
  onlyFinished: 'Only finished runs can be compared',
  // COMPARE_COPY.versions for a run with no versions, and COMPARE_COPY.editedScenarioNote.
  noVersions: 'no versions recorded',
  editedScenarioNote: 'An edited scenario is compared as the same scenario.',
};

interface Seeded {
  environmentId: string;
  scenarioKeys: string[];
  // Run N is the Nth created: Run 1 and Run 2 finished, Run 3 still running.
  runs: { label: string; id: string; status: string }[];
}

interface Comparison {
  execution_id: string;
  against_execution_id: string | null;
  pairs: { scenario_key: string; band: string }[];
  summary: Record<string, number>;
  versions: unknown;
}

// There is no REST path that makes a finished harness run (it takes a sandbox, a provider and a
// Temporal workflow this stack does not stand up), so, as lib/simulate-seed.ts does for call
// executions, the rows are created through the real Django ORM inside the running backend. The
// chain: AgentDefinition -> AgentVersion -> Scenarios -> RunTest -> HostedHarnessJob (the
// environment) and three TestExecutions of that RunTest, two COMPLETED with graded calls and one
// RUNNING. Call fields follow futureagi/simulate/tests/test_harness_run_compare.py (_trials).
// Everything belongs to the caller's own organization and workspace.
const SEED_SCRIPT = `
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
    AgentVersion,
    HostedHarnessJob,
    RunTest,
    Scenarios,
)
from simulate.models.test_execution import CallExecution, TestExecution


def main():
    organization = Organization.objects.get(id=os.environ["SEED_ORG_ID"])
    workspace = Workspace.objects.get(id=os.environ["SEED_WORKSPACE_ID"])
    suffix = os.environ["SEED_SUFFIX"]
    keys = [f"e2e-cmp-a-{suffix}", f"e2e-cmp-b-{suffix}"]

    agent_definition = AgentDefinition.objects.create(
        agent_name=f"e2e-sim3-agent-{suffix}",
        agent_type=AgentDefinition.AgentTypeChoices.VOICE,
        inbound=True,
        description="e2e SIM-E2E-003 seed",
        organization=organization,
        workspace=workspace,
        languages=["en"],
    )
    agent_version = AgentVersion.objects.create(
        agent_definition=agent_definition,
        organization=organization,
        workspace=workspace,
        description=f"e2e SIM-E2E-003 seed version {suffix}",
        commit_message="e2e SIM-E2E-003 seed",
        status=AgentVersion.StatusChoices.ACTIVE,
    )
    scenario = Scenarios.objects.create(
        name=f"e2e-sim3-scenario-{suffix}",
        description="e2e SIM-E2E-003 seed scenario",
        source="test",
        scenario_type=Scenarios.ScenarioTypes.DATASET,
        organization=organization,
        workspace=workspace,
        agent_definition=agent_definition,
        status=StatusType.COMPLETED.value,
    )
    run_test = RunTest.objects.create(
        name=f"e2e-sim3-run-test-{suffix}",
        description="e2e SIM-E2E-003 seed",
        agent_definition=agent_definition,
        organization=organization,
        workspace=workspace,
    )
    run_test.scenarios.add(scenario)

    environment = HostedHarnessJob.no_workspace_objects.create(
        organization=organization,
        workspace=workspace,
        run_id=uuid.uuid4(),
        name=f"e2e-sim3-environment-{suffix}",
        idempotency_key=f"e2e-sim3-{suffix}",
        request_digest="sha256:" + "0" * 64,
        schema_version="v1",
        payload={"metadata": {}, "source": {}, "agent": {}},
        state=HostedHarnessJob.State.COMPLETED,
        current_stage="completed",
        seed=1,
        scenario_count=2,
        artifact_level="summary",
        max_artifact_bytes=1048576,
        deadline_at=timezone.now() + timedelta(hours=1),
        run_test=run_test,
    )

    def trial(key, held):
        status = "passed" if held else "failed"
        return {
            "harness_scenario_key": key,
            "harness_outcome_status": status,
            "hosted_harness_receipt": {
                "scenario_key": key,
                "status": status,
                "sub_goals": [{"name": "refunded", "held": held}],
            },
        }

    # Oldest first: Run 1 fails scenario A, Run 2 passes it, Run 3 has not finished.
    plan = [
        (TestExecution.ExecutionStatus.COMPLETED, [False, True], 30),
        (TestExecution.ExecutionStatus.COMPLETED, [True, True], 20),
        (TestExecution.ExecutionStatus.RUNNING, [], 10),
    ]
    runs = []
    for index, (status, held, minutes_ago) in enumerate(plan, start=1):
        execution = TestExecution.objects.create(
            run_test=run_test,
            status=status,
            total_scenarios=2,
            total_calls=len(held),
            agent_definition=agent_definition,
        )
        TestExecution.objects.filter(id=execution.id).update(
            created_at=timezone.now() - timedelta(minutes=minutes_ago)
        )
        for key, ok in zip(keys, held):
            CallExecution.objects.create(
                test_execution=execution,
                scenario=scenario,
                agent_version=agent_version,
                status=CallExecution.CallStatus.COMPLETED,
                simulation_call_type=CallExecution.SimulationCallType.VOICE,
                call_metadata=trial(key, ok),
                eval_outputs={
                    "policy": {
                        "name": "Policy",
                        "output": "Passed" if ok else "Failed",
                        "source": "harness",
                    }
                },
            )
        runs.append(
            {"label": f"Run {index}", "id": str(execution.id), "status": status}
        )

    print(json.dumps({
        "environmentId": str(environment.id),
        "scenarioKeys": keys,
        "runs": runs,
    }))


main()
`;

function seedFinishedRuns(organizationId: string, workspaceId: string): Seeded {
  const container = resolveBackendContainer();
  const suffix = `${Date.now().toString(36)}-${randomBytes(3).toString('hex')}`;
  const stdout = execFileSync('docker', ['exec', '-i',
    '-e', `SEED_ORG_ID=${organizationId}`,
    '-e', `SEED_WORKSPACE_ID=${workspaceId}`,
    '-e', `SEED_SUFFIX=${suffix}`,
    container, 'sh', '-c', 'cd /app/backend && PYTHONPATH=/app/backend python -',
  ], { encoding: 'utf-8', input: SEED_SCRIPT });
  return JSON.parse(stdout.trim().split('\n').pop() ?? '') as Seeded;
}

test('SIM-E2E-003: two finished runs of an environment are compared from the Runs tab', {
  tag: ['@flow'],
  annotation: flowAnnotation({
    id: 'SIM-E2E-003', area: 'simulate',
    userGoal: 'A developer ticks two finished runs on an environment\'s Runs tab, with versioning on or off, presses Compare, reads the newer run set against the older one and what the comparison cannot tell, and closes it; another organization cannot read it',
    steps: ['open the environment Runs tab', 'tick the two finished runs, leaving the unfinished one unticked',
            'press Compare', 'read the comparison', 'close it'],
    backendChecks: ['the compare request carries the two run ids and answers 200',
                    'the answer names the newer run as execution_id and the older as against_execution_id, with both seeded scenarios paired',
                    "another organization's GET of the same compare answers 404"],
    browserChecks: ['only the two finished runs have an enabled tick box; the running run is disabled',
                    'Compare is disabled with none or one run ticked and enabled at exactly two',
                    'the comparison drawer opens with the Compare runs heading and the newer run (Run 2) before the older (Run 1) in its columns, however the ticks were made',
                    'the drawer names each seeded run "no versions recorded" and says "An edited scenario is compared as the same scenario."',
                    'Close removes the drawer and leaves the two ticks in place'],
  }),
}, async ({ browser, scopeActors: scopes }, testInfo) => {
  // 4 browser waits × 60s, plus 60s headroom.
  test.setTimeout(300_000);
  const actor = scopes.ownerA;
  const seeded = seedFinishedRuns(actor.organizationId, actor.workspaceId);
  const [older, newer, running] = seeded.runs;
  expect([older.label, newer.label, running.label]).toEqual(['Run 1', 'Run 2', 'Run 3']);
  await testInfo.attach('seeded', {
    body: JSON.stringify({ organizationId: actor.organizationId, environmentId: seeded.environmentId,
      runs: seeded.runs.map(({ label, id }) => ({ label, id })) }),
    contentType: 'application/json',
  });

  const context = await scopes.openContext(browser, actor);
  const page = await context.newPage();
  const box = (run: { label: string }) => page.getByRole('checkbox', { name: `Select ${run.label}` });
  const compare = page.getByRole('button', { name: COPY.compare, exact: true });
  const drawer = page.getByRole('region', { name: COPY.heading });

  await test.step('ui: the Runs tab lists the runs with the compare controls', async () => {
    await page.goto(`${ENVIRONMENTS}/${seeded.environmentId}?tab=runs`);
    await expect(page).toHaveURL(new RegExp(`${seeded.environmentId}\\?tab=runs$`), { timeout: UI_READY });
    // Compare shows on every environment, with HARNESS_VERSIONING_ENABLED on or off (RunsSummary).
    await expect(compare, 'the Runs tab offers Compare').toBeVisible({ timeout: UI_READY });
    await expect(page.getByText(COPY.pick)).toBeVisible({ timeout: UI_READY });
  }, { timeout: UI_READY });

  await test.step('ui: only finished runs have an enabled tick box', async () => {
    await expect(box(older)).toBeEnabled({ timeout: UI_READY });
    await expect(box(newer)).toBeEnabled();
    await expect(box(running)).toBeDisabled();
    // The tooltip on a disabled box says why. A disabled MUI checkbox takes no pointer events, so
    // the pointer goes over its cell; the tooltip's wrapper span under it gets the hover.
    await box(running).locator('xpath=ancestor::td[1]').hover();
    await expect(page.getByText(COPY.onlyFinished)).toBeVisible();
    // Move off so the tooltip closes and covers no tick box the next step clicks.
    await page.mouse.move(0, 0);
    await expect(page.getByText(COPY.onlyFinished)).toBeHidden();
    await expect(compare).toBeDisabled();
  }, { timeout: UI_READY });

  await test.step('ui: Compare is enabled at exactly two ticked runs', async () => {
    // The older run first: the newer one is still the run compared.
    await box(older).check();
    await expect(page.getByText('1 of 2 selected')).toBeVisible();
    await expect(compare).toBeDisabled();
    await box(newer).check();
    await expect(page.getByText('2 of 2 selected. Clear one to pick another')).toBeVisible();
    await expect(compare).toBeEnabled();
    await expect(box(running)).toBeDisabled();
  }, { timeout: UI_READY });

  let answer: Comparison;
  await test.step('ui: Compare asks the compare endpoint for the two runs and the drawer shows them', async () => {
    const requested = page.waitForResponse(r =>
      new URL(r.url()).pathname === COMPARE(seeded.environmentId) && r.request().method() === 'GET',
      { timeout: UI_READY });
    await compare.click();
    const response = await requested;
    const query = new URL(response.url()).searchParams;
    expect(query.get('execution_id')).toBe(newer.id);
    expect(query.get('against_execution_id')).toBe(older.id);
    expect(response.status()).toBe(200);
    answer = await response.json() as Comparison;

    await expect(drawer).toBeVisible({ timeout: UI_READY });
    // The newer run is the one compared: its column comes first, then the older run's.
    const columns = drawer.getByRole('columnheader');
    await expect(columns.nth(1)).toHaveText(newer.label, { timeout: UI_READY });
    await expect(columns.nth(2)).toHaveText(older.label);
    // The versions line names both runs, the newer first. The seed pins no versions on either run,
    // with the flag on or off, so each reads the neutral line and the drawer says what pairing by
    // scenario key cannot show.
    await expect(drawer.getByText(
      `${newer.label}: ${COPY.noVersions} · ${older.label}: ${COPY.noVersions}`, { exact: true }))
      .toBeVisible();
    await expect(drawer.getByText(COPY.editedScenarioNote, { exact: true })).toBeVisible();
    await expect(drawer.locator('tbody tr')).toHaveCount(seeded.scenarioKeys.length);
  }, { timeout: UI_READY });

  await test.step('ui: Close removes the comparison', async () => {
    await page.getByRole('button', { name: 'Close', exact: true }).click();
    await expect(drawer).toBeHidden({ timeout: UI_READY });
    await expect(page).toHaveURL(new RegExp(`${seeded.environmentId}\\?tab=runs$`));
    await expect(box(older)).toBeChecked();
    await expect(box(newer)).toBeChecked();
  }, { timeout: UI_READY });

  await test.step('api: the answer names the same two runs and another organization gets 404', async () => {
    expect(answer.execution_id).toBe(newer.id);
    expect(answer.against_execution_id).toBe(older.id);
    expect(answer.pairs.map(p => p.scenario_key).sort()).toEqual([...seeded.scenarioKeys].sort());
    expect(answer.pairs.every(p => p.band === 'paired')).toBe(true);

    const params = { execution_id: newer.id, against_execution_id: older.id };
    const own = await scopes.send<Comparison>(actor, 'GET', COMPARE(seeded.environmentId), undefined, params);
    expect(own.status).toBe(200);
    expect(own.body.execution_id).toBe(newer.id);
    expect(own.body.against_execution_id).toBe(older.id);

    const foreign = await scopes.send<{ detail: string }>(
      scopes.ownerB, 'GET', COMPARE(seeded.environmentId), undefined, params);
    expect(foreign.status).toBe(404);
  });
});
