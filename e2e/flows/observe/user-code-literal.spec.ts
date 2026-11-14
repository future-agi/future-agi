import { request } from '@playwright/test';
import { test, expect } from '../../lib/fixtures';
import { sendTrace } from '../../lib/otlp';
import { POLL } from '../../lib/state-probe';
import { E2E } from '../../lib/env';
import { flowAnnotation } from '../../lib/flow-meta';

// tracer/urls.py and frontend/sections/projects/UsersView/UsersEmptyScreen.jsx.
const EXAMPLE = '/tracer/users/get_code_example/';
// UsersEmptyScreen renders InstructionCodeCopy's SyntaxHighlighter as pre > code.
const CODE = 'pre code';
// Same first-paint budget as the other Observe flows.
const UI_READY = 60_000;

test('OBS-E2E-062: Users setup preserves project names as Python string literals', {
  tag: ['@flow'],
  annotation: flowAnnotation({
    id: 'OBS-E2E-062', area: 'observe',
    userGoal: 'A developer opens an empty project\'s Users setup and obtains a Python example that preserves the project name as one string literal',
    steps: ['ingest a trace without end-user attributes for an ordinary project',
      'open project Users and read its setup example',
      'repeat for names containing a quote, a backslash and a non-BMP Unicode character',
      'compare displayed code with the public API result and inspect the project_name argument'],
    backendChecks: ['the collector-created PostgreSQL project retains the exact minted name and actor organization',
      'the example API encodes the exact project name into one quoted Python literal',
      'the visible Users setup code equals the exact example API result'],
  }),
}, async ({ page, actor, probe }, testInfo) => {
  // Three sequential cases: 3 * (SPAN_VISIBLE + 2 * UI_READY), plus provisioning.
  test.setTimeout(540_000);
  const req = await request.newContext();
  const suffix = `${testInfo.workerIndex}-${Date.now().toString(36)}`;
  const prefix = `e2e-obs62-${suffix}`;
  // The collector refuses control characters in project names; futureagi/tracer/tests/test_user_code_example_literal.py covers their encoding.
  const names = [`${prefix}-plain`, `${prefix}-quote"back\\slash`, `${prefix}-astral\u{1f680}`];
  try {
    for (const [index, projectName] of names.entries()) {
      await test.step(['ordinary project golden path', 'quoted project regression', 'non-BMP literal regression'][index], async () => {
        const seeded = await sendTrace(req, {
          collectorUrl: E2E.collectorUrl, apiKey: actor.apiKey, secretKey: actor.secretKey,
          projectName, rootName: `e2e.obs62-${suffix}-${index}`,
        });
        await testInfo.attach(`seed-${index}`, {
          body: JSON.stringify(seeded), contentType: 'application/json',
        });
        await expect.poll(async () => {
          const rows = await probe.ch<{ n: string }>(
            'SELECT count() AS n FROM spans FINAL WHERE trace_id = {t:String}', { t: seeded.traceId });
          return Number(rows[0].n);
        }, POLL.SPAN_VISIBLE).toBe(seeded.spanIds.length);
        const projects = await probe.pg<{ id: string; name: string; organization_id: string }>(
          'SELECT id, name, organization_id FROM tracer_project WHERE name=$1 AND organization_id=$2',
          [projectName, actor.organizationId]);
        expect(projects).toHaveLength(1);
        expect(projects[0]).toMatchObject({ name: projectName, organization_id: actor.organizationId });
        const projectId = projects[0].id;
        await testInfo.attach(`project-${index}`, {
          body: JSON.stringify(projects[0]), contentType: 'application/json',
        });
        const example = await actor.api.get<{ result: string }>(EXAMPLE, { project_id: projectId });
        // The non-BMP case expects one Python scalar escape, not JSON's two
        // surrogate escapes. This independent expected spelling is not derived
        // from the server's encoder. Returned source is never executed.
        const literal = index === 2 ? `"${prefix}-astral\\U0001f680"` : JSON.stringify(projectName);
        const lines = example.result.split('\n').filter(line => line.trimStart().startsWith('project_name='));
        expect(lines).toEqual([`    project_name=${literal},`]);
        // Pinned from catalog-user-parity.spec.ts and the Observe users route.
        await page.goto(`/dashboard/observe/${projectId}/users`, { waitUntil: 'domcontentloaded' });
        await expect(page.getByText('Setup Telemetry', { exact: true })).toBeVisible({ timeout: UI_READY });
        const code = page.locator(CODE);
        await expect(code).toHaveCount(1, { timeout: UI_READY });
        // textContent retains line breaks and backslashes; whitespace-normalized
        // substring assertions would miss exactly the defect this flow covers.
        await expect.poll(() => code.textContent(), { timeout: UI_READY }).toBe(example.result);
        await testInfo.attach(`setup-${index}`, { body: await page.screenshot(), contentType: 'image/png' });
      });
    }
  } finally {
    await req.dispose();
  }
});
