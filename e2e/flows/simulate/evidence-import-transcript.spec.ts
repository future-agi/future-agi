import type { Request } from '@playwright/test';
import { test, expect } from '../../lib/fixtures';
import { E2E } from '../../lib/env';
import { flowAnnotation } from '../../lib/flow-meta';
import { seedEnvironment } from '../../lib/environment-seed';

// Coverage this flow does not claim, and what covers it instead:
// - Uploaded files, and the JSON and CSV formats: the frontend's ImportTranscriptDialog test
//   and the backend's simulate/tests/test_transcript_parse.py and test_evidence_transcripts.py.
// - The cuts to the import's size, and a cut that ends at whitespace so no value is left in two
//   pieces: test_evidence_transcripts.py.
// - The builder's draft and the add to the suite: as in SIM-E2E-003, this stack starts no
//   sandbox; test_evidence_dispatch.py drafts a transcript import.
//
// Deliberate failures, one run per check; each run must fail on that check and no earlier one:
// 1. paste the fixed transcript the first time: the import is answered 201, not 400.
// 2. after the import lands, add the same transcript once more: two POSTs are sent.
// 3. tick "These transcripts come from production": the row is declared production.
// 4. look for the minted ask in the stored text instead of the email: the ask is stored,
//    which proves the probe reads the stored columns.
// 5. paste the fixed transcript without the email: check 4 still holds (no email, the ask
//    stored), but no <EMAIL_ADDRESS> token is shown and the count is 0.
// 6. leave the name blank: the card shows "Pasted transcript", not the given name.

// One browser action's first-paint budget (see README "UI_READY").
const UI_READY = 60_000;
// Copy, pinned from frontend/src/sections/simulate/environments/evidence/evidence.constants.js.
const DIALOG_BODY = /Paste a transcript or upload files/;
const CREATE = 'Create environment test';
const NOT_PRODUCTION = /Not marked as production, so these become tests, not regression tests/;
const NOT_EXACT_REPLAY = /the test is not an exact replay/;
// The import route, pinned from simulate/views/harness_environment.py (`evidence-imports/`).
const imports = (env: string) => `/simulate/api/harness-environments/${env}/evidence-imports/`;

interface Bundle {
  id: string;
  source_kind: string;
  production: boolean;
  cases: { id: string; source_id: string; name: string; status: string;
    transcript: { status: string; turns: { role: string; text: string }[] } }[];
  redaction: { method: string; total: number; findings: unknown[] };
}

test(
  'SIM-E2E-004: a person pastes a transcript into an environment and sees its personal data replaced and marked as their transcript',
  {
    tag: ['@flow'],
    annotation: flowAnnotation({
      id: 'SIM-E2E-004',
      area: 'simulate',
      userGoal:
        'A person pastes a transcript, imports it into an environment, and sees its personal data replaced and marked as their transcript',
      steps: [
        'seed a built, versioned environment in the actor workspace (lib/environment-seed.ts)',
        "open the environment's Scenarios tab, choose Import evidence, then Add a transcript",
        'paste a transcript whose second line has an unknown speaker, import it and read the refusal',
        'fix the line, name the transcript and import it',
        "land on the Scenarios tab with the import's review open",
      ],
      backendChecks: [
        'the first import is answered 400 transcript_unreadable naming transcript 1 and line 2, the dialog shows that message, and no simulate_evidence_import row is made',
        'from the fixed import on, the only mutating /simulate/ request is one POST to the environment evidence-imports route, answered 201',
        'simulate_evidence_import has one row for the actor org and the seeded environment: source_kind transcript, no project, not declared production, with one pending case transcript-1',
        "neither the import's stored redaction nor its case's stored transcript holds the minted email, and the stored transcript holds the minted ask",
        'the bundle read again holds the minted ask, shows the email as <EMAIL_ADDRESS>, counts at least one replaced value, lists no finding and is not production',
        'the review shows the case under its given name, marked "Your transcript", says "1 transcript you added" and that it is not marked production, shows tool calls missing, and that the test is not an exact replay',
      ],
    }),
  },
  async ({ page, actor, probe }, testInfo) => {
    // Seeding + five UI_READY browser steps (tab, dialog, refusal, import, review).
    test.setTimeout(240_000);
    const suffix = `${testInfo.workerIndex}-${Date.now().toString(36)}`;
    const ask = `e2e-ask-${suffix}`;
    const email = `e2e-${suffix}@example.com`;
    const name = `e2e-sim4-${suffix}`;
    const unreadable = `User: ${ask}: where is my refund\nManager: let me check`;
    const fixed = `User: ${ask}: my refund never arrived, write to ${email}\nAgent: I have issued a second refund.`;
    const refusal = "Transcript 1 (Pasted transcript): Line 2: Unknown speaker 'Manager': use User or Agent.";

    const environment = seedEnvironment({
      organizationId: actor.organizationId, workspaceId: actor.workspaceId, name: `e2e-sim4-env-${suffix}`,
    });
    const env = environment.environmentId;
    await testInfo.attach('seeded', { contentType: 'application/json', body: JSON.stringify({ environment, ask }) });

    const dialog = page.getByRole('dialog').filter({ hasText: DIALOG_BODY });
    await test.step('open Add a transcript', async () => {
      await page.goto(`/dashboard/simulate/environments/${env}?tab=scenarios`);
      await page.getByRole('button', { name: 'Import evidence' }).click({ timeout: UI_READY });
      await page.getByRole('dialog').getByRole('button', { name: 'Add a transcript' }).click();
      await expect(dialog).toBeVisible({ timeout: UI_READY });
    });

    await test.step('import a transcript with an unknown speaker', async () => {
      await dialog.getByLabel('Transcript', { exact: true }).fill(unreadable);
      const answered = page.waitForResponse((r) => r.url().endsWith(imports(env))
        && r.request().method() === 'POST', { timeout: UI_READY });
      await dialog.getByRole('button', { name: CREATE }).click();
      const response = await answered;
      // Check 1.
      expect(response.status()).toBe(400);
      expect(await response.json()).toMatchObject({ error: 'transcript_unreadable', message: refusal });
      await expect(dialog).toContainText(refusal, { timeout: UI_READY });
      expect(await probe.pg('SELECT id FROM simulate_evidence_import WHERE environment_id = $1', [env])).toEqual([]);
    });

    // Check 2 records from here: every request the fixed import makes.
    const sent: Request[] = [];
    page.on('request', (request) => sent.push(request));

    let bundle!: Bundle;
    await test.step('fix the line, name it and import', async () => {
      await dialog.getByLabel('Transcript', { exact: true }).fill(fixed);
      await dialog.getByLabel('Name', { exact: true }).fill(name);
      const created = page.waitForResponse((r) => r.url().endsWith(imports(env))
        && r.request().method() === 'POST', { timeout: UI_READY });
      await dialog.getByRole('button', { name: CREATE }).click();
      const response = await created;
      expect(response.status()).toBe(201);
      bundle = await response.json() as Bundle;
      await expect(page).toHaveURL(new RegExp(`/simulate/environments/${env}\\?tab=scenarios&import=${bundle.id}$`), { timeout: UI_READY });
    });
    await testInfo.attach('import', { contentType: 'application/json', body: JSON.stringify({ importId: bundle.id }) });

    // Check 2.
    const mutating = sent.filter((r) => r.url().startsWith(E2E.apiUrl) && r.url().includes('/simulate/')
      && r.method() !== 'GET').map((r) => `${r.method()} ${new URL(r.url()).pathname}`);
    expect(mutating).toEqual([`POST ${imports(env)}`]);

    // Check 3.
    const rows = await probe.pg<{ organization_id: string; project_id: string | null; source_kind: string; declared_production: boolean }>(
      `SELECT organization_id::text, project_id::text, source_kind, declared_production
         FROM simulate_evidence_import WHERE environment_id = $1`, [env]);
    expect(rows).toEqual([{ organization_id: actor.organizationId, project_id: null,
      source_kind: 'transcript', declared_production: false }]);
    const cases = await probe.pg<{ source_id: string; status: string }>(
      'SELECT source_id, status FROM simulate_evidence_import_case WHERE evidence_import_id = $1', [bundle.id]);
    expect(cases).toEqual([{ source_id: 'transcript-1', status: 'pending' }]);

    // Check 4: only redacted text is stored.
    const stored = await probe.pg<{ redaction: string; transcript: string }>(
      `SELECT i.redaction::text AS redaction, c.transcript::text AS transcript
         FROM simulate_evidence_import i JOIN simulate_evidence_import_case c ON c.evidence_import_id = i.id
        WHERE i.id = $1`, [bundle.id]);
    expect(stored).toHaveLength(1);
    expect(`${stored[0].redaction}\n${stored[0].transcript}`).not.toContain(email);
    expect(stored[0].transcript).toContain(ask);

    // Check 5: read again through the route the review reads.
    const again = await actor.api.get<Bundle>(`${imports(env)}${bundle.id}/`);
    const text = again.cases[0].transcript.turns.map((t) => t.text).join('\n');
    expect(text).toContain(ask);
    expect(text).toContain('<EMAIL_ADDRESS>');
    expect(text).not.toContain(email);
    expect(again.redaction.total).toBeGreaterThanOrEqual(1);
    expect(again.redaction.findings).toEqual([]);
    expect(again.production).toBe(false);

    await test.step('read the review', async () => {
      // Check 6.
      const review = page.getByTestId('evidence-review');
      const card = review.locator('[data-testid^="evidence-case-"]');
      await expect(card).toHaveCount(1, { timeout: UI_READY });
      await expect(card).toContainText(name);
      await expect(card).toContainText('Your transcript');
      await expect(card).not.toContainText('from production');
      // EvidenceCaseCard.jsx: one "Missing:" line, parts in PART_LABELS order.
      await expect(card).toContainText('Missing: Tool calls');
      await expect(review).toContainText('1 transcript you added');
      await expect(review.getByTestId('evidence-transcript')).toContainText(NOT_PRODUCTION);
      await expect(review).toContainText(NOT_EXACT_REPLAY);
    });
  },
);
