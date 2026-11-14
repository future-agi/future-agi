import { randomUUID } from 'node:crypto';
import type { Request } from '@playwright/test';
import { test, expect } from '../../lib/fixtures';
import { sendTrace } from '../../lib/otlp';
import { POLL } from '../../lib/state-probe';
import { E2E } from '../../lib/env';
import { flowAnnotation } from '../../lib/flow-meta';
import { seedEnvironment } from '../../lib/environment-seed';

// Coverage this flow does not claim, and what covers it instead:
// - The builder's draft, its landing and the add to the suite need the simulation runner
//   and a sandbox, which this stack does not start. Backend tests cover them
//   (simulate/tests/test_evidence_drafts.py, test_evidence_dispatch.py), and a branch
//   backend with a scripted sandbox exercised them on dev data.
// - The Observe Calls entry needs voice call seeding this harness lacks. The frontend's
//   LLMTracingView restricted-org test covers its action and dialog.

// One browser action's first-paint budget (see README "UI_READY").
const UI_READY = 60_000;
// Bulk action and copy, pinned from
// frontend/src/sections/simulate/environments/evidence/evidence.constants.js.
const ACTION = 'Create environment test';
const NOT_EXACT_REPLAY = /the test is not an exact replay/;
// The import routes, pinned from simulate/views/harness_environment.py
// (`evidence-imports/` and `evidence-imports/{import_id}/`).
const imports = (env: string) => `/simulate/api/harness-environments/${env}/evidence-imports/`;

interface Bundle {
  id: string;
  cases: { id: string; source_id: string; status: string;
    transcript: { status: string; turns: { role: string; text: string }[] };
    starting_state: { status: string } }[];
  redaction: { method: string; reason: string; total: number;
    findings: { case_id: string; part: string; entity: string }[] };
}

test(
  'SIM-E2E-003: an environment-only org turns Observe sessions into an environment test import through the reviewed bridge',
  {
    tag: ['@flow'],
    annotation: flowAnnotation({
      id: 'SIM-E2E-003',
      area: 'simulate',
      userGoal:
        'An environment-only organisation turns production sessions from Observe into a draft regression import for one of its environments, through the reviewed bridge and never legacy Replay, and sees what the evidence holds and lacks',
      steps: [
        'seed two sessions in a minted Observe project; one user turn carries a minted ask and a minted email',
        'seed a built, versioned environment in the actor workspace (lib/environment-seed.ts)',
        "open the project's Sessions and select the two seeded rows",
        'open Actions and read the menu',
        'choose Create environment test, pick the seeded environment and import',
        "land on the environment's Scenarios tab with the import's review open",
      ],
      backendChecks: [
        'both seeded sessions are stored for the minted project in ClickHouse trace_sessions (FINAL)',
        'the Actions menu is exactly "Add to annotation queue" and "Create environment test": no Replay for an environment-only org',
        'from the Actions click on, the only mutating /simulate/ request is one POST to the environment evidence-imports route, answered 201, and no request names replay',
        'simulate_evidence_import has one row for the actor org, the seeded environment and the minted project, with two pending cases whose source ids are the seeded sessions',
        "the import's bundle re-reads Observe: each case's transcript holds its minted ask, and redaction reports the minted email as an EMAIL_ADDRESS finding",
        'the review shows both cases waiting, the transcript present, the starting state missing, and that the test is not an exact replay',
      ],
    }),
  },
  async ({ page, actor, probe }, testInfo) => {
    // SPAN_VISIBLE 15 s + four UI_READY browser steps (sessions grid, menu, import, review) + seeding.
    test.setTimeout(300_000);
    const suffix = `${testInfo.workerIndex}-${Date.now().toString(36)}`;
    const projectName = `e2e-sim3-${suffix}`;
    const sessions = [`e2e-sim3-a-${suffix}`, `e2e-sim3-b-${suffix}`];
    const asks = [`e2e-ask-a-${suffix}`, `e2e-ask-b-${suffix}`];
    const email = `e2e-${suffix}@example.com`;

    // Seed: one trace per session; the root's input/output is one exchange
    // (tracer/selectors/observe_evidence.py::_session_turns).
    const cfg = { collectorUrl: E2E.collectorUrl, apiKey: actor.apiKey, secretKey: actor.secretKey, projectName };
    for (const [index, session] of sessions.entries()) {
      await sendTrace(page.request, {
        ...cfg,
        traceId: randomUUID(),
        resourceAttributes: { project_type: 'observe' },
        rootName: `e2e-sim3-turn-${index}`,
        childName: 'llm_call',
        rootAttributes: {
          'session.id': session,
          'input.value': index === 0 ? `${asks[0]}: my refund never arrived, write to ${email}` : `${asks[1]}: where is my order`,
          'output.value': `e2e-reply-${index}-${suffix}`,
        },
      });
    }
    const environment = seedEnvironment({
      organizationId: actor.organizationId, workspaceId: actor.workspaceId, name: `e2e-sim3-env-${suffix}`,
    });
    await testInfo.attach('seeded', { contentType: 'application/json', body: JSON.stringify({ projectName, sessions, environment }) });

    const projects = await probe.pg<{ id: string }>(
      'SELECT id FROM tracer_project WHERE name=$1 AND organization_id=$2 AND workspace_id=$3',
      [projectName, actor.organizationId, actor.workspaceId]);
    expect(projects).toHaveLength(1);
    const projectId = projects[0].id;

    // Check 1: both sessions stored.
    let stored: { trace_session_id: string; external_session_id: string }[] = [];
    await expect.poll(async () => {
      stored = await probe.ch<{ trace_session_id: string; external_session_id: string }>(
        'SELECT toString(trace_session_id) AS trace_session_id, external_session_id FROM trace_sessions FINAL WHERE project_id={p:UUID} AND is_deleted=0',
        { p: projectId });
      return stored.map((s) => s.external_session_id).sort();
    }, POLL.SPAN_VISIBLE).toEqual([...sessions].sort());
    const sessionUuids = stored.map((s) => s.trace_session_id).sort();

    await test.step('select the seeded sessions', async () => {
      await page.goto(`/dashboard/observe/${projectId}/sessions`);
      // A row shows the session's UUID, not its external id; its first message is the minted ask.
      for (const ask of asks) {
        const row = page.locator('.ag-center-cols-container .ag-row').filter({ hasText: ask });
        await expect(row).toHaveCount(1, { timeout: UI_READY });
        const index = await row.getAttribute('row-index');
        // Clicked, not check(): AG Grid redraws the cell as it selects the row.
        await page.locator(`.ag-row[row-index="${index}"] .ag-checkbox-input`).first().click({ force: true });
        await expect(page.locator(`.ag-row.ag-row-selected[row-index="${index}"]`).first()).toBeVisible({ timeout: UI_READY });
      }
    });

    // Check 3 records from here: every request the bridge makes.
    const sent: Request[] = [];
    page.on('request', (request) => sent.push(request));

    await test.step('read the Actions menu', async () => {
      await page.getByRole('button', { name: 'Actions', exact: true }).click();
      // Check 2. Exact on purpose: a bulk action another PR adds fails this, so its author
      // decides whether an environment-only org should see it.
      await expect(page.getByRole('menuitem')).toHaveText(['Add to annotation queue', ACTION], { timeout: UI_READY });
    });

    let bundle!: Bundle;
    await test.step('import into the seeded environment', async () => {
      await page.getByRole('menuitem', { name: ACTION }).click();
      const dialog = page.getByRole('dialog');
      await dialog.getByRole('combobox').click();
      await page.getByRole('option', { name: environment.name }).click();
      const created = page.waitForResponse((r) => r.url().endsWith(imports(environment.environmentId))
        && r.request().method() === 'POST', { timeout: UI_READY });
      await dialog.getByRole('button', { name: ACTION }).click();
      const response = await created;
      expect(response.status()).toBe(201);
      bundle = await response.json() as Bundle;
      await expect(page).toHaveURL(new RegExp(`/simulate/environments/${environment.environmentId}\\?tab=scenarios&import=${bundle.id}$`), { timeout: UI_READY });
    });
    await testInfo.attach('import', { contentType: 'application/json', body: JSON.stringify({ importId: bundle.id }) });

    // Check 3.
    const mutating = sent.filter((r) => r.url().startsWith(E2E.apiUrl) && r.url().includes('/simulate/')
      && r.method() !== 'GET').map((r) => `${r.method()} ${new URL(r.url()).pathname}`);
    expect(mutating).toEqual([`POST ${imports(environment.environmentId)}`]);
    expect(sent.filter((r) => /replay/i.test(r.url())).map((r) => r.url())).toEqual([]);

    // Check 4.
    const rows = await probe.pg<{ organization_id: string; environment_id: string; project_id: string; source_kind: string }>(
      `SELECT organization_id::text, environment_id::text, project_id::text, source_kind
         FROM simulate_evidence_import WHERE id = $1`, [bundle.id]);
    expect(rows).toEqual([{ organization_id: actor.organizationId, environment_id: environment.environmentId,
      project_id: projectId, source_kind: 'session' }]);
    const cases = await probe.pg<{ source_id: string; status: string }>(
      'SELECT source_id, status FROM simulate_evidence_import_case WHERE evidence_import_id = $1', [bundle.id]);
    // Sorted here, not in SQL: a database collation can order these strings differently.
    expect(cases.map((c) => `${c.source_id}:${c.status}`).sort())
      .toEqual(sessionUuids.map((id) => `${id}:pending`));

    // Check 5: read again through the route the review reads.
    const again = await actor.api.get<Bundle>(`${imports(environment.environmentId)}${bundle.id}/`);
    const transcripts = again.cases.map((c) => c.transcript.turns.map((t) => t.text).join('\n'));
    expect(asks.map((ask) => transcripts.some((text) => text.includes(ask)))).toEqual([true, true]);
    const emailCase = again.cases.find((c) => c.transcript.turns.some((t) => t.text.includes(email)));
    expect(emailCase).toBeDefined();
    expect(again.redaction.findings.filter((f) => f.case_id === emailCase!.id && f.part === 'transcript')
      .map((f) => f.entity)).toContain('EMAIL_ADDRESS');

    await test.step('read the review', async () => {
      // Check 6.
      const review = page.getByTestId('evidence-review');
      const cards = review.locator('[data-testid^="evidence-case-"]');
      await expect(cards).toHaveCount(2, { timeout: UI_READY });
      for (const card of await cards.all()) {
        await expect(card).toContainText('Waiting');
        // EvidenceCaseCard.jsx: one "Has:" and one "Missing:" line, parts in PART_LABELS order.
        await expect(card).toContainText('Has: Transcript');
        await expect(card).toContainText(/Missing: .*Starting state/);
      }
      await expect(review).toContainText(NOT_EXACT_REPLAY);
    });
  },
);
