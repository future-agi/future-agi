import { request } from '@playwright/test';
import { test, expect } from '../../lib/local-only-browser';
import { E2E } from '../../lib/env';
import { flowAnnotation } from '../../lib/flow-meta';

// TestPlayground.jsx: handleRunTest sends this endpoint and config.mapping.text.
const PLAYGROUND = '/model-hub/eval-playground/';
// seed_system_evals + system_evals/function/{contains_valid_link,no_invalid_links}.yaml.
const NAMES = ['contains_valid_link', 'no_invalid_links'];
// README: each navigation/submission gets the browser first-paint budget.
const UI_READY = 60_000;
type Template = { id: string; name: string; eval_id: number; owner: string;
  eval_type: string; visible_ui: boolean; required_keys: string[] };
type CanaryRequest = { method: string; host: string; path: string };

test('EVAL-E2E-016: shipped link evaluators accept approved links and refuse other destinations', {
  tag: ['@flow'],
  annotation: flowAnnotation({
    id: 'EVAL-E2E-016', area: 'evals',
    userGoal: 'Run the shipped link evaluators on approved reachable links and ordinary prose while refusing unapproved, private and redirected destinations without contacting them',
    steps: ['resolve the two shipped system template IDs without rewriting their code',
      'open each system evaluator in the browser',
      'submit approved links, link-free prose and refused destinations in the Custom playground',
      'read exact verdicts and explanations', 'inspect the exact request set on the owned canary'],
    backendChecks: [
      'both shipped templates retain their system identity and text input contract',
      'browser-submitted playground responses match the exact verdict and explanation matrix',
      'the owned canary records only approved HEAD requests and never receives refused destinations',
    ],
  }),
}, async ({ page, actor, probe }, testInfo) => {
  test.setTimeout(1_200_000); // 2 navigations * 60 + 16 submissions * 60 + 120 setup.
  page.setDefaultTimeout(UI_READY);
  const prefix = `e2e-links-${testInfo.workerIndex}-${Date.now().toString(36)}`;
  const req = await request.newContext();
  try {
    // EvalTemplate model: owner=system, stable eval_id, required_keys in JSON config.
    const templates = await probe.pg<Template>(`SELECT id, name, eval_id, owner,
      eval_type, visible_ui, config->'required_keys' AS required_keys
      FROM model_hub_evaltemplate WHERE name = ANY($1) AND deleted = false ORDER BY name`, [NAMES]);
    await testInfo.attach('seeded-ids', { contentType: 'application/json', body: JSON.stringify({
      prefix, organizationId: actor.organizationId, workspaceId: actor.workspaceId, templates,
    }) });
    expect(templates.map(({ id, ...row }) => row)).toEqual([
      { name: 'contains_valid_link', eval_id: 39, owner: 'system', eval_type: 'code', visible_ui: true, required_keys: ['text'] },
      { name: 'no_invalid_links', eval_id: 42, owner: 'system', eval_type: 'code', visible_ui: true, required_keys: ['text'] },
    ]);
    // Keep the complete pre-existing journal as the baseline. Filtering only on
    // minted URL paths would hide a mistaken email-domain HEAD to plain '/'.
    const before = await req.get(`${E2E.linkCanaryUrl}/requests`);
    expect(before.status()).toBe(200);
    const expectedRequests: CanaryRequest[] = await before.json();
    await testInfo.attach('canary-before', { contentType: 'application/json',
      body: JSON.stringify(expectedRequests) });
    for (const template of templates) {
      const contains = template.name === 'contains_valid_link';
      const casePrefix = `${prefix}-${template.name}`;
      // Independent literal expectations pinned from the two shipped evaluate functions.
      // Put the reported email regression first so a pristine-dev run fails
      // for that behavior, not merely for the revised URL-free explanation.
      const cases = [
        { name: 'email', text: `Contact ${casePrefix}.support@allowed.example`, pass: !contains,
          reason: contains ? 'No link found in output' : 'No links found in output, so none are invalid' },
        { name: 'approved', text: `http://allowed.example/ok/${casePrefix}-approved`, pass: true,
          reason: contains ? 'Link found and valid (status 200)' : 'All 1 link(s) are valid', path: `/ok/${casePrefix}-approved` },
        { name: 'punctuation', text: `(http://allowed.example/ok/${casePrefix}-punctuation).`, pass: true,
          reason: contains ? 'Link found and valid (status 200)' : 'All 1 link(s) are valid', path: `/ok/${casePrefix}-punctuation` },
        { name: 'decimal', text: `The value increased by 3.14 percent (${casePrefix})`, pass: !contains,
          reason: contains ? 'No link found in output' : 'No links found in output, so none are invalid' },
        { name: 'filename', text: `Read report.pdf and v1.2.3, e.g. ${casePrefix}`, pass: !contains,
          reason: contains ? 'No link found in output' : 'No links found in output, so none are invalid' },
        { name: 'unapproved', text: `http://denied.example/denied/${casePrefix}`, pass: false,
          reason: 'Link destination is not permitted' },
        { name: 'private', text: `http://private.example/private/${casePrefix}`, pass: false,
          reason: 'Link destination is not permitted' },
        { name: 'redirect', text: `http://allowed.example/redirect/${casePrefix}`, pass: false,
          reason: contains ? 'Link returned status 302' : 'Found invalid links (status 302)', path: `/redirect/${casePrefix}` },
      ];
      await test.step(`open ${template.name}`, async () => {
        // EvalDetailPage route and TestPlayground source tabs, not a copied eval.
        await page.goto(`/dashboard/evaluations/${template.id}`, { waitUntil: 'domcontentloaded' });
        await page.getByRole('tab', { name: 'Custom', exact: true }).click();
        await expect(page.getByRole('button', { name: 'Test Evaluation', exact: true }).last())
          .toBeVisible({ timeout: UI_READY });
      }, { timeout: UI_READY });
      for (const item of cases) {
        await test.step(`${template.name}: ${item.name}`, async () => {
          // CodeEditor.jsx mounts Monaco; JSON is the only editable Monaco in a system eval.
          const editor = page.locator('.monaco-editor textarea').last();
          await editor.focus();
          // Paste is Monaco's whole-document edit; insertText invokes its auto-pairs.
          await page.context().grantPermissions(['clipboard-read', 'clipboard-write']);
          await page.evaluate(text => navigator.clipboard.writeText(text), JSON.stringify({ text: item.text }));
          await page.keyboard.press('ControlOrMeta+A');
          await page.keyboard.press('ControlOrMeta+V');
          await expect(page.getByText('Invalid JSON', { exact: true })).toBeHidden({ timeout: UI_READY });
          const pending = page.waitForResponse(response =>
            new URL(response.url()).pathname === PLAYGROUND && response.request().method() === 'POST',
          { timeout: UI_READY });
          await page.getByRole('button', { name: 'Test Evaluation', exact: true }).last().click();
          const response = await pending;
          const body = await response.json();
          await testInfo.attach(`${template.name}-${item.name}`, { contentType: 'application/json',
            body: JSON.stringify({ sent: response.request().postDataJSON(), status: response.status(), body }) });
          expect(response.request().postDataJSON().config.mapping).toEqual({ text: item.text });
          expect(response.status()).toBe(200);
          expect(body.status).toBe(true);
          expect(body.result.reason).toBe(item.reason);
          expect(body.result.output).toBe(item.pass ? 'Passed' : 'Failed');
          await expect(page.getByText(item.reason, { exact: true })).toHaveText(item.reason, { timeout: UI_READY });
          // EvalResultDisplay's Pass/Fail chip, not the editor's output-type selector.
          await expect(page.locator('.MuiChip-label').filter({ hasText: /^(Pass|Fail)$/ }))
            .toHaveText([item.pass ? 'Pass' : 'Fail'], { timeout: UI_READY });
          if (item.path) expectedRequests.push({ method: 'HEAD', host: 'allowed.example', path: item.path });
          const observed = await req.get(`${E2E.linkCanaryUrl}/requests`);
          expect(observed.status()).toBe(200);
          const requests = await observed.json();
          await testInfo.attach(`${template.name}-${item.name}-canary`, { contentType: 'application/json', body: JSON.stringify(requests) });
          expect(requests).toEqual(expectedRequests);
        }, { timeout: UI_READY });
      }
    }
  } finally {
    await req.dispose();
  }
});
