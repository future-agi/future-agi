import { createHash, randomUUID } from 'node:crypto';
import { mkdir, writeFile } from 'node:fs/promises';
import { test, expect } from '../../lib/skills-native-fixtures';
import { E2E } from '../../lib/env';
import { flowAnnotation } from '../../lib/flow-meta';
import { sendTrace } from '../../lib/otlp';
import { POLL } from '../../lib/state-probe';
import { SkillsFixtures } from '../../lib/skills-fixtures';
import { runOperations, firstTurns } from '../../lib/skills-r3/j2-oracle.mjs';
import { ACTION, REQUESTS, ROOT, STATE, modelRules, neighborCase, publishRoot, seedApplication, expectationFixture } from '../../lib/skills-r3/j2-fixture';

const API = '/model-hub/skills/';
const RUNTIME_CONTAINER = process.env.J2_RUNTIME_CONTAINER ?? 'futureagi-e2e-standalone-native-runtime-1';
// The credential the runtime container is started with (compose overlay). Scenario-scoped, synthetic.

// A pure oracle with a one-sided mutation below: it must reject a changed observation.
export function assertMatchedOutcome(cells: any[], expected: { caseId: string; variant: string; selection: string; delivery: string; tools: string }[]) {
  for (const want of expected) {
    const got = cells.filter(cell => cell.case_id === want.caseId && cell.variant === want.variant);
    expect(got.length).toBeGreaterThan(0);
    for (const cell of got) expect({ selection: cell.selection, delivery: cell.delivery, tools: cell.tools }).toEqual({ selection: want.selection, delivery: want.delivery, tools: want.tools });
  }
}

export function assertDelivery(turns: { system: string; catalogMembers: number }[]) {
  expect(turns).toHaveLength(9);
  expect(turns.filter(turn => turn.system.includes('POLICY-REFUND: capped-25'))).toHaveLength(3); // candidate cells
  expect(turns.filter(turn => turn.system.includes('POLICY-REFUND: full-amount'))).toHaveLength(6); // reference and production
  expect(turns.every(turn => turn.catalogMembers === 15)).toBe(true);
}
export function assertBusiness(business: { status: string; cell: { case_id: string; variant: string } }[], historical: string) {
  expect(business.filter(row => row.status === 'applied')).toHaveLength(7);
  expect(business.filter(row => row.status === 'declined').map(row => `${row.cell.case_id}/${row.cell.variant}`).sort())
    .toEqual([`${historical}/production`, `${historical}/reference`].sort());
}

test('SKILLS-E2E-022: verify a context repair on the real application and hold false repairs', {
  tag: ['@flow'],
  annotation: flowAnnotation({ id: 'SKILLS-E2E-022', area: 'skills',
    userGoal: 'An incident responder proves a context repair on the actual application with matched native runs, and the product holds a text-only success and a routing near-miss',
    steps: [
      'pin the runtime-measured application configuration as the context target',
      'review the captured failure with its required skill and business action, then plan a matched run with the carried v4 reference',
      'run the application with candidate, reference and production contexts and read selection, delivery, tool and answer outcomes per cell',
      'append a human disagreement beside the automatic grade',
    ],
    backendChecks: [
      'the native runtime proved read-only source and context mounts and closed paid egress in its authenticated health',
      'the model fixture ledger shows each arm received different root bytes while every arm saw the same fifteen-member catalog',
      'the independent business ledger shows one applied action per cell and the original failure declined',
      'an application whose answer text passes while its business tool fails is held',
      'an overbroad routing repair that sends a dispute to the refund skill is held',
      'a case without its business state fixture is Not reproduced and nothing launches',
    ],
  }),
}, async ({ page, actor, probe, request, nativeNamespace }, testInfo) => {
  test.setTimeout(900_000);
  const evidence = process.env.J2_EVIDENCE_DIR;
  const shot = async (name: string) => {
    const image = await page.screenshot({ animations: 'disabled' });
    await testInfo.attach(name, { body: image, contentType: 'image/png' });
    if (evidence) { await mkdir(`${evidence}/shots`, { recursive: true }); await writeFile(`${evidence}/shots/${name}.png`, image); }
  };
  await page.setViewportSize({ width: 1440, height: 900 });
  await page.addInitScript(orgId => sessionStorage.setItem('workspaceOrgId', orgId), actor.organizationId);
  page.setDefaultTimeout(60_000);
  const headers = { Authorization: `Bearer ${actor.tokens.access}`, 'X-Organization-Id': actor.organizationId, 'X-Workspace-Id': actor.workspaceId };
  const req = async (method: string, path: string, data?: unknown, extra: Record<string, string> = {}) => {
    const response = await request.fetch(`${E2E.apiUrl}${API}${path}`, { method, headers: { ...headers, 'Idempotency-Key': randomUUID(), ...extra }, data });
    const body = await response.json().catch(() => ({}));
    expect(response.ok(), JSON.stringify({ method, path, status: response.status(), body })).toBeTruthy();
    return body.result;
  };
  const prefix = `e2e-j2n-${Date.now().toString(36)}`;
  let seededRef: any;
  const prepare = async (contextId: string) => {
    // Configured prerequisites for a registered-driver run: nothing the responder does is done here.
    const { skills } = seededRef;
    fixtures.keep = true;
    // A crashed earlier run may have left its runtime-owned scenario registered.
    await fixtures.dispose().catch(() => undefined);
    await fixtures.register(modelRules(skills));
    const started = await req('POST', `contexts/${contextId}/draft/start/`, { revision: 0, from_version: 6 });
    await req('PUT', `contexts/${contextId}/draft/`, { revision: started.revision, base_version: 6, files: [{ path: 'AGENTS.md', content: '# Personal draft\nKeep my unfinished unrelated work.\n' }], description: 'Private work to preserve', note: 'Unrelated personal note' });
    const projectName = `${prefix}-incident`;
    const start = BigInt(Math.floor(Date.now() / 1000)) * 1_000_000_000n + 123456000n;
    const v4 = seededRef.releases[3];
    const trace = await sendTrace(request, { collectorUrl: E2E.collectorUrl, apiKey: actor.apiKey, secretKey: actor.secretKey, projectName, rootName: 'Synthetic refund failure', startTimeUnixNano: start,
      rootAttributes: { 'input.value': JSON.stringify({ messages: [{ role: 'system', content: 'Hidden system' }, { role: 'user', content: REQUESTS.refund }, { role: 'tool', content: JSON.stringify({ 'support-order': STATE.refund }) }, { role: 'assistant', content: 'False refund success' }] }),
        'output.value': 'False refund success', 'fi.context.id': contextId, 'fi.context.version_id': v4.id, 'fi.context.digest': v4.digest } });
    await expect.poll(async () => (await probe.ch<{ n: string }>('SELECT count() AS n FROM spans FINAL WHERE trace_id={t:String}', { t: trace.traceId }))[0].n, POLL.SPAN_VISIBLE).toBe('2');
    const projects = await probe.pg<{ id: string }>('SELECT id FROM tracer_project WHERE name=$1 AND organization_id=$2', [projectName, actor.organizationId]);
    if (process.env.J2_COHORT === 'configured') {
      const created = await req('POST', `contexts/${contextId}/native/targets/`, { version: 4 });
      const configuration = (await req('GET', `contexts/${contextId}/native/runtime-configuration/`)).configuration;
      await req('PUT', `contexts/${contextId}/native/targets/${created.binding_id}/`, { revision: created.revision, configuration });
    }
    // The application team's production wiring: its runtime binding, currently on the recorded head.
    const production = await req('POST', `contexts/${contextId}/bindings/`, { name: 'support-app production', kind: 'runtime', selector: { version: 6 }, update_mode: 'manual', profile: 'support-app', profile_version: '1', repository: null, expected_report_interval_seconds: 3600 });
    await writeFile(process.env.J2_PREPARE_OUT!, JSON.stringify({
      userId: (await actor.api.get<{ id: string }>('/accounts/user-info/')).id, v4Id: v4.id, production: { bindingId: production.id, revision: production.revision }, apiKey: actor.apiKey, secretKey: actor.secretKey, collectorInternalUrl: 'http://app:4318',
      appUrl: E2E.appUrl, apiUrl: E2E.apiUrl, pgUrl: E2E.pgUrl, chUrl: E2E.chUrl, chPassword: E2E.chPassword, fixtureOrigin: `http://localhost:${process.env.E2E_SKILLS_FIXTURE_PORT ?? '28082'}`,
      providerNamespace: nativeNamespace, runtimeContainer: RUNTIME_CONTAINER, contextId, contextName: `${prefix}-support`,
      versions: { recorded: 4, head: 6 }, skills, requests: REQUESTS, recordedRoot: ROOT.recorded, recordedRootSha256: createHash('sha256').update(ROOT.recorded).digest('hex'),
      controlRoots: { wordingOnly: ROOT.wordingOnly, overbroad: ROOT.overbroad },
      projectName, projectId: projects[0].id, traceId: trace.traceId, spanId: trace.spanIds[0],
      actor: { access: actor.tokens.access, refresh: actor.tokens.refresh, organizationId: actor.organizationId, workspaceId: actor.workspaceId },
    }), { mode: 0o600 });
  };
  const fixtures = new SkillsFixtures(nativeNamespace);
  try {
    // Prerequisite: a funded workspace policy, a fifteen-member application context with recorded v4 failing and v6 head.
    const spend = await req('GET', 'spend-policy/');
    await req('PUT', 'spend-policy/', { revision: spend.revision, enabled: true, period_limit_usd: '20.00' });
    const seeded = await seedApplication(req, prefix);
    seededRef = seeded;
    const { contextId, skills } = seeded;
    const suite = await req('PUT', `contexts/${contextId}/tests/`, { revision: 0, cases: [neighborCase('chargeback', skills), neighborCase('shipping', skills)] });
    const prepareOut = process.env.J2_PREPARE_OUT;
    if (prepareOut) return prepare(contextId);
    const v7 = await publishRoot(req, contextId, 4, ROOT.repaired, 'Repair refund limit from recorded v4');
    const v8 = await publishRoot(req, contextId, 4, ROOT.wordingOnly, 'Control: wording-only repair');
    const v9 = await publishRoot(req, contextId, 4, ROOT.overbroad, 'Control: overbroad routing repair');
    expect([v7.version.number, v8.version.number, v9.version.number]).toEqual([7, 8, 9]);
    await fixtures.dispose().catch(() => undefined);
    await fixtures.register(modelRules(skills));

    // The retained failure: a physical span whose request and business state tool result the responder captures.
    const projectName = `${prefix}-incident`;
    const start = BigInt(Math.floor(Date.now() / 1000)) * 1_000_000_000n + 123456000n;
    const stateText = JSON.stringify({ 'support-order': STATE.refund });
    const v4 = { version: seeded.releases[3] };
    const trace = await sendTrace(request, { collectorUrl: E2E.collectorUrl, apiKey: actor.apiKey, secretKey: actor.secretKey, projectName, rootName: 'Synthetic refund failure', startTimeUnixNano: start,
      rootAttributes: { 'input.value': JSON.stringify({ messages: [{ role: 'system', content: 'Hidden system' }, { role: 'user', content: REQUESTS.refund }, { role: 'tool', content: stateText }, { role: 'assistant', content: 'False refund success' }] }),
        'output.value': 'False refund success', 'fi.context.id': contextId, 'fi.context.version_id': v4.version.id, 'fi.context.digest': v4.version.digest } });
    await expect.poll(async () => (await probe.ch<{ n: string }>('SELECT count() AS n FROM spans FINAL WHERE trace_id={t:String}', { t: trace.traceId }))[0].n, POLL.SPAN_VISIBLE).toBe('2');

    // Capture through the UI (physical identity carried, nothing retyped). The span names the context it
    // was delivered from, so the review opens in it directly with every supported segment preselected.
    await page.goto('/dashboard/observe');
    await page.getByText(projectName, { exact: true }).click();
    await page.locator('.clean-data-table:visible .ag-row [col-id="trace_name"]').filter({ hasText: 'Synthetic refund failure' }).click();
    await page.getByRole('button', { name: 'Save as test case', exact: true }).click();
    const dialog = page.getByRole('dialog');
    await expect(dialog.getByRole('checkbox')).toHaveCount(2);
    for (const box of await dialog.getByRole('checkbox').all()) await expect(box).toBeChecked();
    await dialog.getByRole('button', { name: 'Preview selected text', exact: true }).click();
    await dialog.getByRole('textbox', { name: 'Case name', exact: true }).fill('Original refund failure');
    await dialog.getByRole('textbox', { name: 'Expected answer must contain', exact: true }).fill('receipt');
    // The reviewer chooses the required skill and business action in the same review; no identity or JSON is typed.
    await dialog.getByLabel('Required skill', { exact: true }).click();
    await page.getByRole('option', { name: /refund-policy$/ }).click();
    await dialog.getByLabel('Required business action', { exact: true }).click();
    await page.getByRole('option', { name: 'Issue a refund', exact: true }).click();
    await dialog.getByText('Required application behavior').scrollIntoViewIfNeeded();
    await shot('J2-required-behavior');
    await dialog.getByRole('checkbox', { name: /confirmed the expected behavior/ }).check();
    await dialog.getByRole('checkbox', { name: /workspace/ }).check();
    const accepted = page.waitForResponse(res => res.url().includes('/captures/') && res.url().endsWith('/accept/') && res.request().method() === 'POST');
    await dialog.getByRole('button', { name: 'Save test case', exact: true }).click();
    expect((await accepted).ok()).toBe(true);

    // The saved example hands off to its reproduction: the recorded version is carried, not chosen.
    await dialog.getByRole('button', { name: 'Set up application test', exact: true }).click();
    await expect(page).toHaveURL(/tab=tests/);
    await expect(page).toHaveURL(/reproduce=/);
    await expect(page).toHaveURL(/reference=4/);
    await page.getByRole('button', { name: 'Add application target' }).click();
    await page.getByRole('button', { name: 'Show app configuration' }).click();
    await expect(page.getByText('Pin the application configuration')).toBeVisible();
    await shot('J2-pin-configuration');
    await page.getByRole('button', { name: 'Pin this configuration' }).click();
    await expect(page.getByText('Configuration pinned')).toBeVisible();

    // Plan: the recorded v4 reference and the captured failure (historical) are preselected; the responder
    // widens it to candidate v7, production v6 and the neighboring cases.
    await expect(page.getByLabel('Was failing on', { exact: true })).toContainText('v4');
    const group = page.getByRole('group', { name: 'Examples to run' });
    await expect(group.getByRole('checkbox', { name: /^Was failing on v/ }).last()).toBeChecked();
    await page.getByLabel('Test version').click(); await page.getByRole('option', { name: 'v7' }).click();
    await page.getByRole('combobox', { name: /^Compare with/ }).click(); await page.getByRole('option', { name: 'v6' }).click();
    for (const title of ['Working chargeback case', 'Working shipping case']) await group.getByRole('checkbox', { name: title }).check();
    await expect(page.getByText(/Up to 9 runs/)).toBeVisible();
    await shot('J2-native-preview');
    await page.getByRole('checkbox', { name: /I accept up to/ }).check();
    await page.getByRole('button', { name: 'Run application test', exact: true }).click();
    await expect(page.getByText(/Repair verified:|Repair not verified:|Hold this release|The run stopped/).first()).toBeVisible({ timeout: 240_000 });
    await expect(page.getByText(/Repair verified: v7 passed all 3 cases/)).toBeVisible();
    await shot('J2-native-result');

    // Independent evidence.
    const runs = await req('GET', `contexts/${contextId}/native/runs/`);
    expect(runs.results).toHaveLength(1);
    const run = await req('GET', `contexts/${contextId}/native/runs/${runs.results[0].id}/`);
    expect(run.decision.state).toBe('release_ready');
    expect(run.runtime).toMatchObject({ read_only_source: true, read_only_context: true, separate_writable_state: true, controlled_paid_egress: true });
    const historical = run.cases.find((item: any) => item.historical).id;
    const ids = Object.fromEntries(run.cases.map((item: any) => [item.title, item.id]));
    assertMatchedOutcome(run.cells, [
      { caseId: historical, variant: 'candidate', selection: 'pass', delivery: 'pass', tools: 'pass' },
      { caseId: historical, variant: 'reference', selection: 'pass', delivery: 'pass', tools: 'fail' },
      { caseId: ids['Working chargeback case'], variant: 'candidate', selection: 'pass', delivery: 'pass', tools: 'pass' },
      { caseId: ids['Working shipping case'], variant: 'candidate', selection: 'pass', delivery: 'pass', tools: 'pass' },
    ]);
    expect(() => assertMatchedOutcome(run.cells, [{ caseId: historical, variant: 'reference', selection: 'pass', delivery: 'pass', tools: 'pass' }])).toThrow();
    const ledger = await fixtures.ledger();
    const turns = firstTurns(ledger);
    assertDelivery(turns);
    const attempts = await probe.pg<{ id: string }>('SELECT id FROM simulate_hosted_harness_attempt WHERE job_id=$1', [run.job_id]);
    expect(attempts).toHaveLength(1);
    const business = runOperations(RUNTIME_CONTAINER, attempts[0].id, run.cells);
    assertBusiness(business, historical);
    // One-sided controls: each independent oracle rejects a single changed observation.
    expect(() => assertDelivery(turns.map(turn => ({ ...turn, system: turn.system.replace('capped-25', 'full-amount') })))).toThrow();
    expect(() => assertDelivery(turns.map(turn => ({ ...turn, catalogMembers: 14 })))).toThrow();
    expect(() => assertBusiness(business.map(row => ({ ...row, status: 'applied' })), historical)).toThrow();
    const credentials = await probe.pg<{ n: string }>('SELECT count(*) AS n FROM model_hub_hubskillcontextcredential WHERE workspace_id=$1 AND revoked_at IS NULL AND purpose=$2', [actor.workspaceId, 'consume']);
    expect(credentials[0].n).toBe('0');

    // F8: the incident is retained, but completed native cells created no customer skill links.
    const [incident] = await probe.ch<{ project: string }>('SELECT toString(project_id) AS project FROM spans FINAL WHERE trace_id={t:String} LIMIT 1', { t: trace.traceId });
    expect(incident.project).toBeTruthy();
    const [nativeLinks] = await probe.ch<{ n: string }>('SELECT count() AS n FROM skill_trace_links FINAL WHERE project_id={p:UUID} AND start_time >= fromUnixTimestamp64Nano({start:Int64})', { p: incident.project, start: String(start) });
    expect(nativeLinks.n).toBe('0');

    // Human disagreement is appended beside the automatic grade.
    await page.getByRole('row', { name: /Original refund failure.*Was failing on v/ }).first().click();
    await page.getByRole('radio', { name: /Disagree/ }).check();
    await page.getByLabel('Why').fill('The answer claims success but the business ledger shows the refund was declined.');
    await page.getByRole('button', { name: 'Record assessment' }).click();
    await expect(page.getByText('Disagrees', { exact: true }).first()).toBeVisible();
    await expect(page.getByText('The answer claims success but the business ledger shows the refund was declined.', { exact: true })).toBeVisible();
    await shot('J2-native-annotation');

    // Controls (public API, as the acceptance matrix): text-pass/tool-fail and routing near-miss are held;
    // a case with no business state is Not reproduced and launches nothing.
    const targets = await req('GET', `contexts/${contextId}/native/targets/`);
    const target = targets.targets[0];
    const allCases = (await req('GET', `contexts/${contextId}/tests/`)).cases;
    const runControl = async (candidate: number, caseIds: string[]) => {
      const plan = { binding_id: target.binding_id, binding_revision: target.revision, candidate_version: candidate, baselines: { reference: 4 }, case_ids: caseIds, repetitions: 1, historical_case_ids: [] as string[] };
      const preview = await req('POST', `contexts/${contextId}/native/preview/`, plan);
      const created = await req('POST', `contexts/${contextId}/native/runs/`, { ...plan, execution_fingerprint: preview.execution_fingerprint, acknowledge_spend: true });
      let detail: any;
      await expect.poll(async () => { detail = await req('GET', `contexts/${contextId}/native/runs/${created.evidence_id}/`); return detail.status; }, { timeout: 300_000 }).toBe('completed');
      return detail;
    };
    const refundCase = allCases.find((item: any) => item.title === 'Original refund failure').id;
    const chargebackCase = allCases.find((item: any) => item.title === 'Working chargeback case').id;
    const textOnly = await runControl(8, [refundCase]);
    expect(textOnly.decision.state).toBe('hold');
    expect(textOnly.decision.reasons.join(' ')).toMatch(/text pass is not a repair/);
    const nearMiss = await runControl(9, [chargebackCase]);
    expect(nearMiss.decision.state).toBe('hold');
    expect(nearMiss.decision.reasons.join(' ')).toMatch(/skill selection failed/);
    const bare = { ...allCases.find((item: any) => item.title === 'Working shipping case'), id: randomUUID(), title: 'Shipping without state', fixtures: [expectationFixture(skills.shipping, ACTION.shipping)] };
    const latest = await req('GET', `contexts/${contextId}/tests/`);
    const input = (item: any) => ({ id: item.id, title: item.title, source_group_id: item.source_group_id, prompt: item.prompt, fixtures: item.fixtures, expected_trigger: item.expected_trigger, checks: item.checks, reviewed: item.reviewed });
    await req('PUT', `contexts/${contextId}/tests/`, { revision: latest.revision, cases: [...latest.cases, bare].map(input) });
    const noState = await req('POST', `contexts/${contextId}/native/preview/`, { binding_id: target.binding_id, binding_revision: target.revision, candidate_version: 7, baselines: { reference: 4 }, case_ids: [bare.id], repetitions: 1, historical_case_ids: [] });
    expect(noState.reproduction).toBe('not_reproduced');
    expect(noState.missing_inputs[0].input).toContain('business state fixture');
    await testInfo.attach('J2-native-independent', { body: JSON.stringify({ evidenceClass: 'synthetic fixture', run, textOnly: textOnly.decision, nearMiss: nearMiss.decision, noState, business, suite: suite.revision }), contentType: 'application/json' });
    if (process.env.J2_DRIVER_FIXTURE_OUTPUT) await writeFile(process.env.J2_DRIVER_FIXTURE_OUTPUT, JSON.stringify({ contextId }), { mode: 0o600 });
  } finally { if (!process.env.J2_PREPARE_OUT) await fixtures.dispose().catch(() => undefined); }
});
