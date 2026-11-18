import type { Request } from '@playwright/test';
import { test, expect } from '../../lib/fixtures';
import { E2E } from '../../lib/env';
import { flowAnnotation } from '../../lib/flow-meta';
import { seedEnvironment } from '../../lib/environment-seed';

// Narrowed on purpose: this stack starts no sandbox, so no import can be drafted or added here.
// The drafting, drafted and partly added states, Resume's scroll to the step each reached, and
// the partial discard that keeps added scenarios are covered instead by the backend's
// simulate/tests/test_evidence_import_list.py and test_evidence_drafts.py (a real draft and add,
// then a discard) and by DraftImports.test.jsx and EvidenceReviewDrawer.test.jsx.
//
// Deliberate failures, one run per check; each run must fail on that check and no earlier one:
// 1. seed B before A: the strip and the list read A first.
// 2. resume B instead of A: the review opens on B.
// 3. press Cancel in the confirm: no DELETE is sent.
// 4. after the discard, create a third import through the API: the list reads [C, A].

// One browser action's first-paint budget (see README "UI_READY").
const UI_READY = 60_000;
// Copy, pinned from frontend/src/sections/simulate/environments/evidence/evidence.constants.js.
// A transcript import's row and its discard confirm name its first transcript.
const rowText = (name: string) => new RegExp(`^1 transcript: ${name} · waiting to be drafted · imported `);
const discardTitle = (name: string) => `Discard “1 transcript: ${name}”?`;
// Routes, pinned from simulate/views/harness_environment.py: the list is the create route's GET,
// the discard the one import's DELETE.
const imports = (env: string) => `/simulate/api/harness-environments/${env}/evidence-imports/`;

interface Listed { id: string; source_kind: string; status: string; case_count: number }
interface Created { id: string }

test(
  'SIM-E2E-006: a person finds two imports they never drafted under Draft imports, resumes one and discards the other',
  {
    tag: ['@flow'],
    annotation: flowAnnotation({
      id: 'SIM-E2E-006',
      area: 'simulate',
      userGoal:
        "A person finds two imports they never drafted under Draft imports on an environment's Scenarios tab, resumes one, and discards the other",
      steps: [
        'seed a built, versioned environment (lib/environment-seed.ts) and two transcript imports, A then B, through the create route',
        "open the environment's Scenarios tab and read the Draft imports strip",
        "resume A: its review opens",
        'close the review',
        'discard B and confirm',
      ],
      backendChecks: [
        'the strip rows are exactly B then A, newest first, each "1 transcript · waiting to be drafted", and equal the list route\'s answer',
        'Resume sends no mutating /simulate/ request, puts import=<A> in the URL, and the review shows A\'s name',
        'from the confirm on, the only mutating /simulate/ request is DELETE on B, answered 204; B is deleted with its redaction and its case\'s stored transcript cleared, and A is untouched',
        'afterwards the list route answers exactly [A] and the strip shows only A',
      ],
    }),
  },
  async ({ page, actor, probe }, testInfo) => {
    // Seeding + six UI_READY browser steps (tab, strip, resume, close, confirm, after).
    test.setTimeout(420_000);
    const suffix = `${testInfo.workerIndex}-${Date.now().toString(36)}`;
    const names = { a: `e2e-sim6-${suffix}-a`, b: `e2e-sim6-${suffix}-b`, c: `e2e-sim6-${suffix}-c` };

    const environment = seedEnvironment({
      organizationId: actor.organizationId, workspaceId: actor.workspaceId, name: `e2e-sim6-env-${suffix}`,
    });
    const env = environment.environmentId;
    // Body pinned from EvidenceTranscriptInputSerializer, the wire SIM-E2E-004 captures.
    const seed = async (name: string) => (await actor.api.post<Created>(imports(env), {
      source_kind: 'transcript', production: false,
      transcripts: [{ name, format: 'text', text: `User: ${name}: where is my refund?\nAgent: It is on its way.` }],
    })).id;
    const a = await seed(names.a);
    const b = await seed(names.b);
    await testInfo.attach('seeded', { contentType: 'application/json', body: JSON.stringify({ environment, a, b, names }) });

    const listed = async () => (await actor.api.get<Listed[]>(imports(env))).map((one) => one.id);
    const strip = page.getByTestId('draft-imports');
    const rows = strip.locator('[data-testid^="draft-import-"]');
    const row = (id: string) => strip.getByTestId(`draft-import-${id}`);

    await test.step('read the Draft imports strip', async () => {
      await page.goto(`/dashboard/simulate/environments/${env}?tab=scenarios`);
      await expect(rows).toHaveCount(2, { timeout: UI_READY });
      // Check 1.
      expect(await rows.evaluateAll((els) => els.map((el) => el.getAttribute('data-testid'))))
        .toEqual([`draft-import-${b}`, `draft-import-${a}`]);
      for (const [id, name] of [[b, names.b], [a, names.a]]) await expect(row(id).getByText(rowText(name))).toBeVisible();
      const answer = await actor.api.get<Listed[]>(imports(env));
      expect(answer.map(({ id, source_kind, status, case_count }) => ({ id, source_kind, status, case_count })))
        .toEqual([b, a].map((id) => ({ id, source_kind: 'transcript', status: 'waiting', case_count: 1 })));
    });

    const sent: Request[] = [];
    page.on('request', (request) => sent.push(request));
    const mutating = () => sent.filter((r) => r.url().startsWith(E2E.apiUrl) && r.url().includes('/simulate/')
      && r.method() !== 'GET').map((r) => `${r.method()} ${new URL(r.url()).pathname}`);

    await test.step('resume A', async () => {
      await row(a).getByRole('button', { name: 'Resume' }).click();
      // Check 2.
      await expect(page).toHaveURL(new RegExp(`/simulate/environments/${env}\\?tab=scenarios&import=${a}$`), { timeout: UI_READY });
      const review = page.getByTestId('evidence-review');
      await expect(review.locator('[data-testid^="evidence-case-"]')).toHaveText([new RegExp(names.a)], { timeout: UI_READY });
      expect(mutating()).toEqual([]);
    });

    await test.step('close the review', async () => {
      // SideDrawer's own close button, in the drawer that holds the review.
      await page.locator('.MuiDrawer-paper').filter({ has: page.getByTestId('evidence-review') })
        .getByRole('button', { name: 'Close' }).click();
      await expect(page).toHaveURL(new RegExp(`/simulate/environments/${env}\\?tab=scenarios$`), { timeout: UI_READY });
      await expect(rows).toHaveCount(2, { timeout: UI_READY });
    });

    sent.length = 0;
    await test.step('discard B', async () => {
      await row(b).getByRole('button', { name: 'Discard' }).click();
      const dialog = page.getByRole('dialog').filter({ hasText: discardTitle(names.b) });
      await expect(dialog).toBeVisible({ timeout: UI_READY });
      const answered = page.waitForResponse((r) => r.url().endsWith(`${imports(env)}${b}/`)
        && r.request().method() === 'DELETE', { timeout: UI_READY });
      await dialog.getByRole('button', { name: 'Discard' }).click();
      // Check 3.
      expect((await answered).status()).toBe(204);
      expect(mutating()).toEqual([`DELETE ${imports(env)}${b}/`]);
    });
    const stored = await probe.pg<{ id: string; deleted: boolean; redaction: boolean; transcript: boolean }>(
      `SELECT i.id::text, i.deleted, i.redaction IS NOT NULL AS redaction, c.transcript IS NOT NULL AS transcript
         FROM simulate_evidence_import i JOIN simulate_evidence_import_case c ON c.evidence_import_id = i.id
        WHERE i.id IN ($1, $2) ORDER BY i.created_at`, [a, b]);
    expect(stored).toEqual([
      { id: a, deleted: false, redaction: true, transcript: true },
      { id: b, deleted: true, redaction: false, transcript: false },
    ]);

    await test.step('read the strip after the discard', async () => {
      // Check 4.
      await expect(rows).toHaveCount(1, { timeout: UI_READY });
      await expect(row(a)).toBeVisible();
      expect(await listed()).toEqual([a]);
    });
  },
);
