import { execFileSync } from 'node:child_process';
import { randomBytes } from 'node:crypto';
import { test, expect } from '../../lib/scope-actors';
import { flowAnnotation } from '../../lib/flow-meta';
import { resolveBackendContainer } from '../../lib/simulate-seed';

// One navigation's first paint, not a stage (as environments-only-routing.spec.ts).
const UI_READY = 60_000;
const ENVIRONMENTS = '/dashboard/simulate/environments';
// frontend/src/api/simulate-environments/scenarios.js (scenariosPath), served by
// futureagi/simulate/services/harness_provider.py list_scenarios.
const LIST = (environmentId: string) => `/simulate/api/harness-jobs/${environmentId}/scenarios/`;
// frontend/src/sections/simulate/environments/workspace/scenarios/useScenarioPage.js (PAGE_SIZE).
const PAGE_LIMIT = '25';
// The params the list always carries (scenariosHooks.js useHarnessScenarios); any other key is a filter.
const BASE_PARAMS = new Set(['page', 'limit', 'search', 'group_by', 'ordering']);

// Copy pinned off frontend/src/components/filter-panel/FilterPanel.jsx (STRING_OPERATORS, the value
// placeholder and Clear all), scenarios/scenarios.constants.js (SCENARIOS_COPY.filterLabel) and the
// field labels in futureagi/simulate/services/harness_scenarios.py (FIELDS).
const COPY = {
  filter: 'Filter',
  scenario: 'Scenario',
  situation: 'Situation',
  contains: 'Contains',
  startsWith: 'Starts with',
  notContains: 'Does not contain',
  valuePlaceholder: 'Enter value',
  clearAll: 'Clear all',
};

interface SeededScenario {
  key: string;
  name: string;
  instruction: string;
}

interface Seeded {
  environmentId: string;
  suffix: string;
  // In the order of futureagi/simulate/tests/test_harness_scenario_text_matchers.py DOCUMENTS:
  // refund_late_order, late_refund_policy, Refund_Upper, cancel_ride (each with the suffix).
  scenarios: SeededScenario[];
}

interface ScenarioPage {
  count: number;
  results: { name: string; scenario_key: string }[];
}

// A finished environment is not needed to list scenarios: the list reads HostedHarnessScenario rows.
// So, as lib/simulate-seed.ts does for call executions, the rows are created through the real Django
// ORM inside the running backend: an environment HostedHarnessJob (as SIM-E2E-003 makes one), then
// harness_scenarios.index_scenarios(environment, documents, prune=True), the call the backend test
// futureagi/simulate/tests/test_harness_scenario_text_matchers.py makes. It writes database rows only
// (no object storage), and HARNESS_VERSIONING_ENABLED stays off, so the list is the plain suite
// (harness_versioning/drafts.py drafts_apply is False). The four documents are the backend test's
// DOCUMENTS, each name and key ending in the run's suffix so a rerun never collides.
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
from simulate.models import AgentDefinition, HostedHarnessJob, RunTest
from simulate.services import harness_scenarios


def main():
    organization = Organization.objects.get(id=os.environ["SEED_ORG_ID"])
    workspace = Workspace.objects.get(id=os.environ["SEED_WORKSPACE_ID"])
    suffix = os.environ["SEED_SUFFIX"]

    agent_definition = AgentDefinition.objects.create(
        agent_name=f"e2e-sim4-agent-{suffix}",
        agent_type=AgentDefinition.AgentTypeChoices.VOICE,
        inbound=True,
        description="e2e SIM-E2E-004 seed",
        organization=organization,
        workspace=workspace,
        languages=["en"],
    )
    run_test = RunTest.objects.create(
        name=f"e2e-sim4-run-test-{suffix}",
        description="e2e SIM-E2E-004 seed",
        agent_definition=agent_definition,
        organization=organization,
        workspace=workspace,
    )

    environment = HostedHarnessJob.no_workspace_objects.create(
        organization=organization,
        workspace=workspace,
        run_id=uuid.uuid4(),
        name=f"e2e-sim4-environment-{suffix}",
        idempotency_key=f"e2e-sim4-{suffix}",
        request_digest="sha256:" + "0" * 64,
        schema_version="v1",
        payload={"metadata": {}, "source": {}, "agent": {}},
        state=HostedHarnessJob.State.COMPLETED,
        current_stage="completed",
        seed=1,
        scenario_count=4,
        artifact_level="summary",
        max_artifact_bytes=1048576,
        deadline_at=timezone.now() + timedelta(hours=1),
        run_test=run_test,
    )

    documents = [
        {
            "name": f"refund_late_order_{suffix}",
            "scenario_key": f"refund-late-order-{suffix}",
            "instruction": "Customer asks for a refund on a late order",
        },
        {
            "name": f"late_refund_policy_{suffix}",
            "scenario_key": f"late-refund-policy-{suffix}",
            "instruction": "Customer asks about the refund policy",
        },
        {
            "name": f"Refund_Upper_{suffix}",
            "scenario_key": f"refund-upper-{suffix}",
            "instruction": "REFUND in capitals",
        },
        {
            # No situation written: the stored text is empty.
            "name": f"cancel_ride_{suffix}",
            "scenario_key": f"cancel-ride-{suffix}",
        },
    ]
    held = harness_scenarios.index_scenarios(environment, documents, prune=True)
    assert held == len(documents), held

    print(json.dumps({
        "environmentId": str(environment.id),
        "suffix": suffix,
        "scenarios": [
            {
                "key": doc["scenario_key"],
                "name": doc["name"],
                "instruction": doc.get("instruction", ""),
            }
            for doc in documents
        ],
    }))


main()
`;

function seedScenarios(organizationId: string, workspaceId: string): Seeded {
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

test('SIM-E2E-004: a scenario\'s text filters match part of the text', {
  tag: ['@flow'],
  annotation: flowAnnotation({
    id: 'SIM-E2E-004', area: 'simulate',
    userGoal: 'A developer opens an environment\'s Scenarios tab, filters the scenarios on the Scenario name with Contains, Starts with and Does not contain and on the Situation with Contains by typing part of a text, sees exactly the matching scenarios each time, and clears the filter to see them all again',
    steps: ['open the environment Scenarios tab', 'filter Scenario Contains refund', 'change it to Starts with refund',
            'change it to Does not contain refund', 'filter Situation Contains REFUND', 'clear the filter'],
    backendChecks: ['each filter request carries its own key: name_contains, name_starts_with, name_not_contains, instruction_contains, with the typed text as the value, and answers 200',
                    'the same requests sent directly answer with the same scenarios the page shows',
                    'the unfiltered list holds all four seeded scenarios'],
    browserChecks: ['all four seeded scenarios show before any filter',
                    'Contains refund shows exactly refund_late_order and late_refund_policy (not Refund_Upper: the match is on case)',
                    'Starts with refund shows exactly refund_late_order',
                    'Does not contain refund shows exactly Refund_Upper and cancel_ride',
                    'Situation Contains REFUND shows exactly Refund_Upper',
                    'each filtered list reads "N of 4" and the Filter button shows one applied filter',
                    'Clear all shows the four scenarios again with no "N of 4" readout'],
  }),
}, async ({ browser, scopeActors: scopes }, testInfo) => {
  // 6 browser steps × 60s, plus 60s headroom.
  test.setTimeout(420_000);
  const actor = scopes.ownerA;
  const seeded = seedScenarios(actor.organizationId, actor.workspaceId);
  const [late, policy, upper, cancel] = seeded.scenarios;
  expect([late.name, policy.name, upper.name, cancel.name]).toEqual([
    `refund_late_order_${seeded.suffix}`, `late_refund_policy_${seeded.suffix}`,
    `Refund_Upper_${seeded.suffix}`, `cancel_ride_${seeded.suffix}`]);
  await testInfo.attach('seeded', {
    body: JSON.stringify({ organizationId: actor.organizationId, environmentId: seeded.environmentId,
      scenarios: seeded.scenarios.map(({ key, name }) => ({ key, name })) }),
    contentType: 'application/json',
  });

  const context = await scopes.openContext(browser, actor);
  const page = await context.newPage();

  // Each scenario is a table row named "Open <name>" (ScenarioTable.jsx, SCENARIOS_COPY.openRow).
  const scenarioRows = page.getByRole('row', { name: /^Open / });
  const rowOf = (name: string) => page.getByRole('row', { name: `Open ${name}`, exact: true });
  const filterButton = (applied: number) => page.getByRole('button',
    { name: applied ? `${COPY.filter} (${applied})` : COPY.filter, exact: true });
  const valueBox = page.getByPlaceholder(COPY.valuePlaceholder);

  const expectRows = async (shown: { name: string }[], suiteTotal: number) => {
    await expect(scenarioRows).toHaveCount(shown.length, { timeout: UI_READY });
    for (const one of shown) await expect(rowOf(one.name)).toBeVisible();
    // The "N of M" readout shows only while a filter is set (ScenariosStep.jsx readout).
    if (shown.length < suiteTotal) {
      await expect(page.getByText(`${shown.length} of ${suiteTotal}`, { exact: true })).toBeVisible();
    } else {
      await expect(page.getByText(/^\d+ of \d+$/)).toHaveCount(0);
    }
  };

  // The next list request that carries this filter key with exactly this value, and no other filter.
  const asksFor = (key: string, value: string) => page.waitForResponse(r => {
    const url = new URL(r.url());
    if (url.pathname !== LIST(seeded.environmentId) || r.request().method() !== 'GET') return false;
    if (url.searchParams.get('limit') !== PAGE_LIMIT) return false;
    const filters = [...new Set(url.searchParams.keys())].filter(k => !BASE_PARAMS.has(k));
    return filters.length === 1 && filters[0] === key && url.searchParams.getAll(key).join('\n') === value;
  }, { timeout: UI_READY });

  // The panel's two selects are the first two comboboxes while it is open: the rest of the page is
  // aria-hidden behind it (the field first, then the operator).
  const openFilter = async (applied: number) => {
    await filterButton(applied).click();
    await expect(valueBox).toBeVisible({ timeout: UI_READY });
  };
  const choose = async (select: 'field' | 'operator', label: string) => {
    await page.getByRole('combobox').nth(select === 'field' ? 0 : 1).click();
    await page.getByRole('option', { name: label, exact: true }).click();
  };
  // Escape closes the popover; rows behind it are readable again once it is gone.
  const closeFilter = async () => {
    await page.keyboard.press('Escape');
    await expect(valueBox).toBeHidden({ timeout: UI_READY });
  };

  await test.step('ui: the Scenarios tab lists all four scenarios', async () => {
    const listed = page.waitForResponse(r => {
      const url = new URL(r.url());
      return url.pathname === LIST(seeded.environmentId) && r.request().method() === 'GET'
        && url.searchParams.get('limit') === PAGE_LIMIT
        && [...url.searchParams.keys()].every(k => BASE_PARAMS.has(k));
    }, { timeout: UI_READY });
    await page.goto(`${ENVIRONMENTS}/${seeded.environmentId}?tab=scenarios`);
    await expect(page).toHaveURL(new RegExp(`${seeded.environmentId}\\?tab=scenarios$`), { timeout: UI_READY });
    const response = await listed;
    expect(response.status()).toBe(200);
    expect(((await response.json()) as ScenarioPage).count).toBe(seeded.scenarios.length);
    await expect(filterButton(0), 'the Scenarios tab offers Filter').toBeVisible({ timeout: UI_READY });
    await expectRows([late, policy, upper, cancel], 4);
  }, { timeout: UI_READY });

  await test.step('ui: Scenario Contains refund shows the two names with it', async () => {
    await openFilter(0);
    await expect(page.getByRole('combobox').nth(0)).toHaveText(COPY.scenario);
    await choose('operator', COPY.contains);
    const requested = asksFor('name_contains', 'refund');
    await valueBox.fill('refund');
    const response = await requested;
    expect(response.status()).toBe(200);
    await closeFilter();
    await expectRows([late, policy], 4);
    // Refund_Upper has a capital R: Contains matches on case.
    await expect(rowOf(upper.name)).toHaveCount(0);
    await expect(filterButton(1)).toBeVisible();
  }, { timeout: UI_READY });

  await test.step('ui: Scenario Starts with refund shows the one name that begins with it', async () => {
    await openFilter(1);
    // The panel reopens on what is applied: Scenario, Contains, refund.
    await expect(page.getByRole('combobox').nth(1)).toHaveText(COPY.contains);
    await expect(valueBox).toHaveValue('refund');
    const requested = asksFor('name_starts_with', 'refund');
    await choose('operator', COPY.startsWith);
    const response = await requested;
    expect(response.status()).toBe(200);
    await closeFilter();
    await expectRows([late], 4);
    await expect(filterButton(1)).toBeVisible();
  }, { timeout: UI_READY });

  await test.step('ui: Scenario Does not contain refund shows the rest', async () => {
    await openFilter(1);
    await expect(page.getByRole('combobox').nth(1)).toHaveText(COPY.startsWith);
    const requested = asksFor('name_not_contains', 'refund');
    await choose('operator', COPY.notContains);
    const response = await requested;
    expect(response.status()).toBe(200);
    await closeFilter();
    await expectRows([upper, cancel], 4);
    await expect(filterButton(1)).toBeVisible();
  }, { timeout: UI_READY });

  await test.step('ui: Situation Contains REFUND shows the scenario whose situation has it', async () => {
    await openFilter(1);
    // Picking another field starts it on Equals with no text; Contains and the text follow.
    await choose('field', COPY.situation);
    await expect(page.getByRole('combobox').nth(0)).toHaveText(COPY.situation);
    await choose('operator', COPY.contains);
    const requested = asksFor('instruction_contains', 'REFUND');
    await valueBox.fill('REFUND');
    const response = await requested;
    expect(response.status()).toBe(200);
    await closeFilter();
    // Only Refund_Upper has REFUND in its situation: its name has no lower-case refund, so the
    // Scenario filters above could not have shown it for this reason.
    await expectRows([upper], 4);
    await expect(filterButton(1)).toBeVisible();
  }, { timeout: UI_READY });

  await test.step('ui: Clear all shows every scenario again', async () => {
    await openFilter(1);
    const requested = page.waitForResponse(r => {
      const url = new URL(r.url());
      return url.pathname === LIST(seeded.environmentId) && r.request().method() === 'GET'
        && url.searchParams.get('limit') === PAGE_LIMIT
        && [...url.searchParams.keys()].every(k => BASE_PARAMS.has(k));
    }, { timeout: UI_READY });
    await page.getByRole('button', { name: COPY.clearAll, exact: true }).click();
    const response = await requested;
    expect(((await response.json()) as ScenarioPage).count).toBe(seeded.scenarios.length);
    // Clear all closes the panel.
    await expect(valueBox).toBeHidden({ timeout: UI_READY });
    await expectRows([late, policy, upper, cancel], 4);
    await expect(filterButton(0)).toBeVisible();
  }, { timeout: UI_READY });

  await test.step('api: the same requests answer with the same scenarios', async () => {
    const names = async (params: Record<string, string>) => {
      const answer = await scopes.send<ScenarioPage>(actor, 'GET', LIST(seeded.environmentId), undefined,
        { limit: PAGE_LIMIT, ...params });
      expect(answer.status).toBe(200);
      return answer.body.results.map(one => one.name).sort();
    };
    const sorted = (...list: { name: string }[]) => list.map(one => one.name).sort();

    expect(await names({})).toEqual(sorted(late, policy, upper, cancel));
    expect(await names({ name_contains: 'refund' })).toEqual(sorted(late, policy));
    expect(await names({ name_starts_with: 'refund' })).toEqual(sorted(late));
    expect(await names({ name_not_contains: 'refund' })).toEqual(sorted(upper, cancel));
    expect(await names({ instruction_contains: 'REFUND' })).toEqual(sorted(upper));
  });
});
