import { randomUUID } from 'node:crypto';
import { test, expect } from '../../lib/scope-actors';
import type { ScopeActor } from '../../lib/scope-actors';
import { sendTrace } from '../../lib/otlp';
import { POLL } from '../../lib/state-probe';
import { E2E } from '../../lib/env';
import { flowAnnotation } from '../../lib/flow-meta';
import { seedEnvironment } from '../../lib/environment-seed';

// Coverage this flow does not claim, and what covers it instead:
// - What the review shows and the bundle holds, and that Observe offers no Replay to an
//   environment-only org: SIM-E2E-003, on the same routes.
// - The legacy Replay API itself: it stays open to environment-only orgs by design
//   (accounts/services/simulate_access.py); only the UI hides it.

// One browser action's first-paint budget (see README "UI_READY").
const UI_READY = 60_000;
// Bulk action, pinned from frontend/src/sections/simulate/environments/evidence/evidence.constants.js.
const ACTION = 'Create environment test';
// The import routes, pinned from simulate/views/harness_environment.py
// (`evidence-imports/` and `evidence-imports/{import_id}/`).
const imports = (env: string) => `/simulate/api/harness-environments/${env}/evidence-imports/`;
// Each refusal's `detail`, pinned from simulate/views/harness_environment.py:
// HarnessEnvironmentViewSet._not_found, and the NotFound the import and its read raise for
// what lies outside the environment's scope (accounts.authentication.custom_exception_handler
// puts a raised one in the error envelope, whose `detail` is the same text).
const NO_ENVIRONMENT = 'Environment not found';
const NO_PROJECT = "Project not found in this environment's workspace";
const NO_IMPORT = 'Evidence import not found';
// The workspace write check every write passes first: on simulation routes a Viewer gets
// VIEWER_READ_ONLY from tfc/utils/error_codes.py (accounts/authentication.py).
const NO_WRITE = "Viewers can't make changes in this workspace. Ask a workspace admin for the Member role.";

type Side = 'a' | 'b';
// Every refusal carries its reason as `detail`, in the plain body and in the error envelope.
interface Refusal { detail: string }

test(
  "SIM-E2E-036: an environment-only org imports Observe sessions only into its own workspace's environments, and no other org or workspace reaches them",
  {
    tag: ['@flow'],
    annotation: flowAnnotation({
      id: 'SIM-E2E-036',
      area: 'simulate',
      userGoal:
        "A person in an environment-only organisation creates an environment test from Observe and reaches only their own workspace's environments and projects, while another organisation or workspace can neither import into them nor read the import",
      steps: [
        "seed one session in a minted Observe project for each of two organisations' owners, A and B",
        "seed three built, versioned environments: in A's workspace, in A's sibling workspace and in B's (lib/environment-seed.ts)",
        "as A, open the project's Sessions, select the seeded session, choose Actions then Create environment test, and open the environment picker",
        "pick A's environment and import; land on its review",
        'as A, and as A scoped to the sibling workspace, import naming another organisation\'s or workspace\'s environment or project',
        "as B, read A's import through A's environment and through B's own",
        "as a viewer of A's workspace, import into A's environment and draft A's import",
      ],
      backendChecks: [
        'both seeded sessions are stored for their own project in ClickHouse trace_sessions (FINAL)',
        "A's organisation is environment-only, and the environment picker lists exactly A's environment: not the sibling workspace's, not B's",
        "the import answers 201 and lands on the review; simulate_evidence_import has one row for A's organisation, A's environment and A's project, with one pending case for the seeded session",
        "the import route answers 404 with its reason, and makes no simulate_evidence_import row, for A naming B's environment, A naming B's project, A in the sibling workspace naming A's environment, and A in the sibling workspace naming the sibling's environment with A's project",
        "B reading A's import answers 404, through A's environment and through B's own",
        "a viewer of A's workspace is refused the import and the draft with 403 (Viewers can't make changes in this workspace): A's organisation keeps its one import, still undrafted, with its case pending",
      ],
    }),
  },
  async ({ browser, request, scopeActors, scopeProbe }, testInfo) => {
    // Seeding (four identities, two traces, three environments) + SPAN_VISIBLE 15 s + four
    // UI_READY browser steps (sessions grid, picker, import, review) + headroom.
    test.setTimeout(360_000);
    const suffix = `${testInfo.workerIndex}-${Date.now().toString(36)}`;
    const owners: Record<Side, ScopeActor> = { a: scopeActors.ownerA, b: scopeActors.ownerB };
    const sibling = scopeActors.withWorkspace(owners.a, scopeActors.emptyWorkspace.id);
    const names = {
      project: { a: `e2e-sim36-a-${suffix}`, b: `e2e-sim36-b-${suffix}` },
      session: { a: `e2e-sim36-sa-${suffix}`, b: `e2e-sim36-sb-${suffix}` },
      ask: { a: `e2e-ask36-a-${suffix}`, b: `e2e-ask36-b-${suffix}` },
      environment: { a: `e2e-sim36-env-a-${suffix}`, a2: `e2e-sim36-env-a2-${suffix}`, b: `e2e-sim36-env-b-${suffix}` },
    };

    // Seed: one trace per organisation, sent with its own owner's org keys (scope actors keep
    // the keys on the owners); the root's input/output is one exchange
    // (tracer/selectors/observe_evidence.py::_session_turns).
    for (const side of ['a', 'b'] as const) {
      const owner = scopeActors.owners.find((one) => one.email === owners[side].email)!;
      await sendTrace(request, {
        collectorUrl: E2E.collectorUrl, apiKey: owner.apiKey, secretKey: owner.secretKey,
        projectName: names.project[side],
        traceId: randomUUID(),
        resourceAttributes: { project_type: 'observe' },
        rootName: `e2e-sim36-turn-${side}`,
        childName: 'llm_call',
        rootAttributes: {
          'session.id': names.session[side],
          'input.value': `${names.ask[side]}: my refund never arrived`,
          'output.value': `e2e-reply36-${side}-${suffix}`,
        },
      });
    }
    const environments = {
      a: seedEnvironment({ organizationId: owners.a.organizationId, workspaceId: owners.a.workspaceId, name: names.environment.a }),
      a2: seedEnvironment({ organizationId: owners.a.organizationId, workspaceId: scopeActors.emptyWorkspace.id, name: names.environment.a2 }),
      b: seedEnvironment({ organizationId: owners.b.organizationId, workspaceId: owners.b.workspaceId, name: names.environment.b }),
    };
    const projects = {} as Record<Side, string>;
    for (const side of ['a', 'b'] as const) {
      const rows = await scopeProbe.pg<{ id: string }>(
        'SELECT id FROM tracer_project WHERE name=$1 AND organization_id=$2 AND workspace_id=$3',
        [names.project[side], owners[side].organizationId, owners[side].workspaceId]);
      expect(rows).toHaveLength(1);
      projects[side] = rows[0].id;
    }
    await testInfo.attach('seeded', { contentType: 'application/json', body: JSON.stringify({ names, environments, projects }) });

    // Check 1: each session stored for its own project.
    const sessions = {} as Record<Side, string>;
    await expect.poll(async () => {
      const found: string[][] = [];
      for (const side of ['a', 'b'] as const) {
        const rows = await scopeProbe.ch<{ trace_session_id: string; external_session_id: string }>(
          'SELECT toString(trace_session_id) AS trace_session_id, external_session_id FROM trace_sessions FINAL WHERE project_id={p:UUID} AND is_deleted=0',
          { p: projects[side] });
        if (rows.length === 1) sessions[side] = rows[0].trace_session_id;
        found.push(rows.map((row) => row.external_session_id));
      }
      return found;
    }, POLL.SPAN_VISIBLE).toEqual([[names.session.a], [names.session.b]]);

    // Check 2, first half: the org is a new customer's, so Simulate shows it only Environments.
    const info = await owners.a.api.get<{ restrict_simulate_to_environments: boolean }>('/accounts/user-info/');
    expect(info.restrict_simulate_to_environments).toBe(true);

    const context = await scopeActors.openContext(browser, owners.a);
    const page = await context.newPage();

    await test.step('select the seeded session', async () => {
      await page.goto(`/dashboard/observe/${projects.a}/sessions`);
      // A row shows the session's UUID, not its external id; its first message is the minted ask.
      const row = page.locator('.ag-center-cols-container .ag-row').filter({ hasText: names.ask.a });
      await expect(row).toHaveCount(1, { timeout: UI_READY });
      const index = await row.getAttribute('row-index');
      // Clicked, not check(): AG Grid redraws the cell as it selects the row.
      await page.locator(`.ag-row[row-index="${index}"] .ag-checkbox-input`).first().click({ force: true });
      await expect(page.locator(`.ag-row.ag-row-selected[row-index="${index}"]`).first()).toBeVisible({ timeout: UI_READY });
    });

    await test.step('open the environment picker', async () => {
      await page.getByRole('button', { name: 'Actions', exact: true }).click();
      await page.getByRole('menuitem', { name: ACTION }).click();
      await page.getByRole('dialog').getByRole('combobox').click();
      // Check 2, second half. Exact: the sibling workspace's and B's environments are absent.
      await expect(page.getByRole('option')).toHaveText([names.environment.a], { timeout: UI_READY });
    });

    let importId = '';
    await test.step("import into A's environment", async () => {
      await page.getByRole('option', { name: names.environment.a }).click();
      const created = page.waitForResponse((r) => r.url().endsWith(imports(environments.a.environmentId))
        && r.request().method() === 'POST', { timeout: UI_READY });
      await page.getByRole('dialog').getByRole('button', { name: ACTION }).click();
      const response = await created;
      // Check 3.
      expect(response.status()).toBe(201);
      importId = (await response.json() as { id: string }).id;
      await expect(page).toHaveURL(new RegExp(`/simulate/environments/${environments.a.environmentId}\\?tab=scenarios&import=${importId}$`), { timeout: UI_READY });
      await expect(page.getByTestId('evidence-review').locator('[data-testid^="evidence-case-"]')).toHaveCount(1, { timeout: UI_READY });
    });
    await testInfo.attach('import', { contentType: 'application/json', body: JSON.stringify({ importId }) });

    // Check 3: the one row the import made.
    const rows = await scopeProbe.pg<{ organization_id: string; environment_id: string; project_id: string; source_kind: string }>(
      `SELECT organization_id::text, environment_id::text, project_id::text, source_kind
         FROM simulate_evidence_import WHERE id = $1`, [importId]);
    expect(rows).toEqual([{ organization_id: owners.a.organizationId, environment_id: environments.a.environmentId,
      project_id: projects.a, source_kind: 'session' }]);
    const readCases = () => scopeProbe.pg<{ id: string; source_id: string; status: string }>(
      'SELECT id::text, source_id, status FROM simulate_evidence_import_case WHERE evidence_import_id = $1', [importId]);
    const cases = await readCases();
    expect(cases.map(({ source_id, status }) => ({ source_id, status }))).toEqual([{ source_id: sessions.a, status: 'pending' }]);

    // Check 4: what the dialog posts (EvidenceImportCreateSerializer), naming what lies outside
    // the caller's scope.
    const refusals: [string, ScopeActor, string, Side, string][] = [
      ["A naming B's environment", owners.a, environments.b.environmentId, 'a', NO_ENVIRONMENT],
      ["A naming B's project", owners.a, environments.a.environmentId, 'b', NO_PROJECT],
      ["A in the sibling workspace naming A's environment", sibling, environments.a.environmentId, 'a', NO_ENVIRONMENT],
      ["A in the sibling workspace naming the sibling's environment with A's project", sibling, environments.a2.environmentId, 'a', NO_PROJECT],
    ];
    const answered: { label: string; status: number; detail: string }[] = [];
    for (const [label, actor, environmentId, side] of refusals) {
      const answer = await scopeActors.send<Refusal>(actor, 'POST', imports(environmentId),
        { source_kind: 'session', project_id: projects[side], ids: [sessions[side]] });
      answered.push({ label, status: answer.status, detail: answer.body.detail });
      await testInfo.attach(`refusal: ${label}`, { contentType: 'application/json', body: JSON.stringify(answer) });
    }
    expect(answered).toEqual(refusals.map(([label, , , , detail]) => ({ label, status: 404, detail })));
    const made = await scopeProbe.pg<{ organization_id: string; n: number }>(
      `SELECT organization_id::text, count(*)::int AS n FROM simulate_evidence_import
        WHERE organization_id = ANY($1::uuid[]) GROUP BY 1`, [[owners.a.organizationId, owners.b.organizationId]]);
    expect(made).toEqual([{ organization_id: owners.a.organizationId, n: 1 }]);

    // Check 5: the route the review reads, as B.
    const reads = [
      { through: "A's environment", answer: await scopeActors.send<Refusal>(owners.b, 'GET', `${imports(environments.a.environmentId)}${importId}/`) },
      { through: "B's environment", answer: await scopeActors.send<Refusal>(owners.b, 'GET', `${imports(environments.b.environmentId)}${importId}/`) },
    ];
    expect(reads.map(({ through, answer }) => ({ through, status: answer.status, detail: answer.body.detail }))).toEqual([
      { through: "A's environment", status: 404, detail: NO_ENVIRONMENT },
      { through: "B's environment", status: 404, detail: NO_IMPORT },
    ]);

    // Check 6: a read-only role in A's own workspace. The import's body is the dialog's; the
    // draft's is EvidenceImportDraftSerializer's (case_ids), as the review's Draft sends it.
    const viewer = scopeActors.viewer;
    const writes = [
      { write: 'import', answer: await scopeActors.send<Refusal>(viewer, 'POST', imports(environments.a.environmentId),
        { source_kind: 'session', project_id: projects.a, ids: [sessions.a] }) },
      { write: 'draft', answer: await scopeActors.send<Refusal>(viewer, 'POST', `${imports(environments.a.environmentId)}${importId}/draft/`,
        { case_ids: cases.map((one) => one.id) }) },
    ];
    expect(writes.map(({ write, answer }) => ({ write, status: answer.status, detail: answer.body.detail }))).toEqual([
      { write: 'import', status: 403, detail: NO_WRITE },
      { write: 'draft', status: 403, detail: NO_WRITE },
    ]);
    const after = await scopeProbe.pg<{ id: string; drafting_job_id: string | null }>(
      'SELECT id::text, drafting_job_id::text FROM simulate_evidence_import WHERE organization_id = $1', [owners.a.organizationId]);
    expect(after).toEqual([{ id: importId, drafting_job_id: null }]);
    expect((await readCases()).map(({ status }) => status)).toEqual(['pending']);
  },
);
