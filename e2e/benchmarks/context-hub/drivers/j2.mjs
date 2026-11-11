import { chromium, expect } from '@playwright/test';
import { execFileSync } from 'node:child_process';
import { mkdir, readFile, writeFile } from 'node:fs/promises';
import { resolve } from 'node:path';
import pg from 'pg';
import { MeasuredOperator } from '../../../lib/skills-r3/measured-operator.mjs';
import { digest, requireValue } from '../../../lib/skills-r3/canonical.mjs';
import { firstTurns, runOperations, sha256 } from '../../../lib/skills-r3/j2-oracle.mjs';

/** J2 on the public UI/API of a live standalone stack with the hosted native container
 * runtime. Evidence class: synthetic fixture (synthetic incident span, deterministic model
 * fixture, synthetic business ledger). Source/authentication provisioning are declared
 * configured prerequisites made by SKILLS-E2E-022's prepare mode; every operator step below
 * is measured. Fault-injection controls (API) are acceptance-matrix checks, not operator work.
 * J2_DRIVER_INPUT is a private VM-only JSON file (see j2-prepare in the repo flow). */
const TITLE = { refund: 'Original refund failure', chargeback: 'Working chargeback case', shipping: 'Working shipping case' };
const ARMS = ['candidate', 'reference', 'production'];

export async function createDriver({ jobId, runId, registration }) {
  requireValue(process.platform === 'linux' && jobId === 'J2', 'J2 requires the approved cloud Linux host');
  requireValue(process.env.J2_DRIVER_INPUT, 'A private cloud-local J2 input is required');
  const input = JSON.parse(await readFile(process.env.J2_DRIVER_INPUT, 'utf8'));
  requireValue(registration.evidence_scope === 'synthetic', 'This driver qualifies synthetic fixture mechanics only');
  for (const key of ['appUrl', 'apiUrl']) {
    const url = new URL(input[key]);
    requireValue(url.hostname === 'localhost' && !url.username && !url.password, 'Only the owned localhost stack is supported');
  }
  requireValue(/^[a-f0-9-]{36}$/.test(input.contextId), 'An exact context fixture is required');
  const output = resolve(process.env.J2_DRIVER_OUTPUT || 'proof/j2-driver', runId);
  await mkdir(resolve(output, 'shots'), { recursive: true });
  const headers = { Authorization: `Bearer ${input.actor.access}`, 'X-Organization-Id': input.actor.organizationId, 'X-Workspace-Id': input.actor.workspaceId };
  const skills = `${input.apiUrl}/model-hub/skills/`;
  const root = `contexts/${input.contextId}/`;
  const api = async (method, path, data, { signal } = {}) => {
    const response = await fetch(`${skills}${path}`, { method, signal, headers: { ...headers, 'Content-Type': 'application/json', 'Idempotency-Key': crypto.randomUUID() }, body: data === undefined ? undefined : JSON.stringify(data) });
    const body = await response.json().catch(() => ({}));
    requireValue(response.ok, `Independent API observation failed: ${method} ${path} ${response.status}`);
    return body.result;
  };
  const pool = new pg.Pool({ connectionString: input.pgUrl, max: 2 });
  const record = async (name, value) => {
    await writeFile(resolve(output, `${name}.json`), `${JSON.stringify(value, null, 2)}\n`, { mode: 0o600 });
    return digest(value);
  };
  const waitRun = async (evidenceId, signal) => {
    const deadline = Date.now() + 300_000;
    for (;;) {
      requireValue(!signal?.aborted && Date.now() < deadline, 'Native run did not finish');
      const detail = await api('GET', `${root}native/runs/${evidenceId}/`, undefined, { signal });
      if (['completed', 'failed', 'canceled'].includes(detail.status)) return detail;
      await new Promise((r) => setTimeout(r, 2000));
    }
  };
  const fixtureLedger = async () => (await (await fetch(`${input.fixtureOrigin}/scenarios/${input.providerNamespace}`)).json()).ledger;
  const attemptOf = async (jobIdValue) => (await pool.query('SELECT id FROM simulate_hosted_harness_attempt WHERE job_id=$1', [jobIdValue])).rows[0].id;
  const state = { ledger: null, runs: {}, controls: {}, repaired: null, source: null, capture: null, identityEdits: 0 };
  let browser; let context; let page;
  const open = async () => {
    if (page) return;
    browser = await chromium.launch({ headless: true });
    context = await browser.newContext({ baseURL: input.appUrl, viewport: { width: 1440, height: 900 } });
    await context.addInitScript((actor) => {
      localStorage.setItem('accessToken', actor.access);
      localStorage.setItem('refreshToken', actor.refresh);
      localStorage.setItem('rememberMe', 'true');
      sessionStorage.setItem('organizationId', actor.organizationId);
      sessionStorage.setItem('workspaceId', actor.workspaceId);
      sessionStorage.setItem('workspaceOrgId', actor.organizationId);
    }, input.actor);
    page = await context.newPage();
    page.setDefaultTimeout(60_000);
  };
  const shot = (name) => page.screenshot({ path: resolve(output, 'shots', `${name}.png`), animations: 'disabled' });

  return {
    mode: 'product', provenance: input.provenance,
    async perform(stage, { ledger, signal }) {
      requireValue(!signal.aborted, 'Journey cancelled');
      state.ledger = ledger;
      await open();
      const cancel = () => { void context.close().catch(() => {}); };
      signal.addEventListener('abort', cancel, { once: true });
      const op = new MeasuredOperator({ ledger, actor: 'incident-responder', step: stage.id, phase: stage.phase, signal });
      const go = (target, url) => op.transition(target, () => ledger.measure({ dimension: 'A', primitive: 'click', actor: 'incident-responder', step: stage.id, phase: stage.phase, target }, () => page.goto(url)));
      const edit = (target, effect) => ledger.measure({ dimension: 'A', primitive: 'field_edit', actor: 'incident-responder', step: stage.id, phase: stage.phase, target }, effect);
      const choose = async (label, option, target) => {
        await op.click(page.getByLabel(label, { exact: true }), `open-${target}`);
        await op.click(page.getByRole('option', { name: option, exact: typeof option === 'string' }), `choose-${target}`);
      };
      const receipt = (source, hash, decision = null) => ledger.stage({ step: stage.id, phase: stage.phase, source, artifactHash: hash, actionSequences: [], ...(decision ? { decision } : {}) });
      /** Plan, accept the paid ceiling and run through the public Tests UI. Returns the evidence id. */
      const runNative = async ({ candidate, reference, production, titles, historical, tag }) => {
        if (candidate) await choose('Candidate version', `v${candidate}`, `${tag}-candidate`);
        if (reference) await choose('Recorded reference version', `v${reference}`, `${tag}-reference`);
        if (production) await choose('Production version', `v${production}`, `${tag}-production`);
        const group = page.getByRole('group', { name: 'Examples to run' });
        for (const title of titles) await op.click(group.getByRole('checkbox', { name: title, exact: true }), `${tag}-select-${title.split(' ')[1]}`);
        if (historical) await op.click(group.getByRole('checkbox', { name: 'Recorded failure to reproduce' }).last(), `${tag}-mark-historical`);
        const preview = page.waitForResponse((res) => res.url().includes('/native/preview/') && res.request().method() === 'POST');
        await op.click(page.getByRole('button', { name: 'Preview application run', exact: true }), `${tag}-preview`);
        requireValue((await preview).ok(), 'Plan was refused');
        await op.click(page.getByRole('checkbox', { name: /I accept up to/ }), `${tag}-accept-ceiling`);
        const created = page.waitForResponse((res) => res.url().endsWith('/native/runs/') && res.request().method() === 'POST');
        await op.click(page.getByRole('button', { name: 'Run on the application', exact: true }), `${tag}-run`);
        const response = await created;
        requireValue(response.ok(), 'Native run was refused');
        return (await response.json()).result.evidence_id;
      };
      try {
        if (stage.id === 'qualify-application') {
          await go('inspect-context-tests', `/dashboard/skills/contexts/${input.contextId}?version=${input.versions.recorded}`);
          await op.click(page.getByRole('tab', { name: 'Tests', exact: true }), 'open-context-tests');
          // The application's production binding already exists; a deployment without one registers it here.
          if (await page.getByRole('button', { name: 'Add application target', exact: true }).count()) await op.click(page.getByRole('button', { name: 'Add application target', exact: true }), 'add-application-target');
          await op.click(page.getByRole('button', { name: 'Review runtime configuration', exact: true }), 'review-runtime-configuration');
          await expect(page.getByText('Pin the application configuration')).toBeVisible();
          await shot('01-pin-configuration');
          await op.click(page.getByRole('button', { name: 'Pin this configuration', exact: true }), 'pin-application-configuration');
          await expect(page.getByText('Configuration pinned')).toBeVisible();
          const targets = await api('GET', `${root}native/targets/`);
          const measured = JSON.parse(execFileSync('docker', ['exec', input.runtimeContainer, 'python', '/opt/ctxhub/measure.py', 'config'], { encoding: 'utf8' }));
          requireValue(targets.targets[0].qualified && digest(targets.targets[0].configuration) === digest(measured), 'Pinned configuration differs from the runtime measurement');
          receipt('api', await record('target-qualified', { evidenceClass: 'synthetic fixture', target: targets.targets[0], measuredDigest: digest(measured) }));
          return;
        }
        if (stage.id === 'external-failure') {
          // The retained incident is a synthetic OTLP project in this product's tracing, so opening it
          // is not a cross-system handoff. A real retained-Langfuse source would add a counted handoff.
          await go('synthetic-retained-incident', '/dashboard/observe');
          await op.click(page.getByText(input.projectName, { exact: true }), 'open-incident-project');
          await op.transition('physical-source-span', () => op.click(page.locator('.clean-data-table:visible .ag-row [col-id="trace_name"]').filter({ hasText: 'Synthetic refund failure' }), 'open-failed-trace'));
          const trace = await api('GET', `../../tracer/trace/${input.traceId}/?project_id=${input.projectId}`, undefined, { signal }).catch(() => null);
          receipt('api', await record('synthetic-source-observation', { evidenceClass: 'synthetic fixture', traceId: input.traceId, visible: Boolean(trace), retainedSystemConformance: false }));
          return;
        }
        if (stage.id === 'capture-and-redact') {
          const before = await api('GET', `${root}tests/`);
          const sourceResponse = page.waitForResponse((res) => res.url().endsWith(`${root}captures/source/`) && res.request().method() === 'POST');
          await op.click(page.getByRole('button', { name: 'Create context regression case', exact: true }), 'capture-selected-span');
          await op.click(page.getByLabel('Regression context', { exact: true }), 'choose-regression-context');
          await op.click(page.getByRole('option', { name: input.contextName, exact: true }), 'select-context');
          await op.click(page.getByRole('button', { name: 'Review selected span', exact: true }), 'load-source-review');
          const response = await sourceResponse;
          requireValue(response.ok(), 'Selected source was not authorized');
          state.source = (await response.json()).result;
          requireValue(state.source.trace_ref.project_id === input.projectId && state.source.trace_ref.trace_id === input.traceId && state.source.trace_ref.span_id === input.spanId, 'Captured source identity differs');
          const dialog = page.getByRole('dialog');
          const boxes = dialog.getByRole('checkbox');
          await expect(boxes).toHaveCount(state.source.segments.length);
          for (let index = 0; index < state.source.segments.length; index++) await op.click(boxes.nth(index), `select-segment-${index}`);
          await op.transition('redaction-review', () => op.click(dialog.getByRole('button', { name: 'Preview selected text', exact: true }), 'review-selected-text'));
          await edit('case-title', () => dialog.getByRole('textbox', { name: 'Example title', exact: true }).fill(TITLE.refund));
          await edit('expected-answer', () => dialog.getByRole('textbox', { name: 'Expected answer must contain', exact: true }).fill('receipt'));
          const decision = { packet_sha256: digest({ source: state.source, expected: 'receipt' }), preconditions_sha256: digest({ suiteRevision: before.revision, contextId: input.contextId }) };
          await op.review('review-captured-case', state.source.segments.map((_, i) => `segment-${i}`), [], async () => {
            await dialog.getByRole('checkbox', { name: /confirmed the expected behavior/ }).check();
            await dialog.getByRole('checkbox', { name: /workspace/ }).check();
          }, decision);
          const saved = page.waitForResponse((res) => res.url().includes('/captures/') && res.url().endsWith('/accept/') && res.request().method() === 'POST');
          await op.click(dialog.getByRole('button', { name: 'Save reviewed example', exact: true }), 'save-reviewed-case');
          requireValue((await saved).ok(), 'Capture acceptance failed');
          // The reviewer adds which skill and which business action the case requires.
          await go('context-tests', `/dashboard/skills/contexts/${input.contextId}?version=${input.versions.recorded}`);
          await op.click(page.getByRole('tab', { name: 'Tests', exact: true }), 'open-context-tests');
          await op.click(page.getByRole('row', { name: new RegExp(TITLE.refund) }).getByRole('button', { name: 'Review example' }), 'review-captured-example');
          const editor = page.getByRole('dialog');
          await op.click(editor.getByRole('button', { name: 'Add text fixture' }), 'add-expectation-fixture');
          const index = await editor.getByRole('textbox', { name: /Fixture \d+ name/ }).count();
          await edit('expectation-name', () => editor.getByRole('textbox', { name: `Fixture ${index} name` }).fill('fi.application.expectations'));
          await edit('expectation-text', () => editor.getByRole('textbox', { name: `Fixture ${index} text` }).fill(JSON.stringify({ schema: 1, skill_id: input.skills.refund, action: 'issue_refund' })));
          await op.click(editor.getByRole('checkbox', { name: /I reviewed the request/ }), 'confirm-expectation-review');
          const suiteSaved = page.waitForResponse((res) => res.url().endsWith(`${root}tests/`) && res.request().method() === 'PUT');
          await op.click(editor.getByRole('button', { name: 'Save example' }), 'save-expectation');
          requireValue((await suiteSaved).ok(), 'Expectation save failed');
          const suite = await api('GET', `${root}tests/`);
          const captured = suite.cases.find((item) => item.title === TITLE.refund);
          requireValue(captured?.reviewed && captured.source_group_id === `trace:${state.source.source_fingerprint}` && captured.fixtures.some((f) => f.name === 'fi.application.expectations')
            && captured.fixtures.some((f) => f.text.includes('support-order')), 'Independent captured-case observation differs');
          const rows = await pool.query('SELECT trace_ref, source_hashes, accepted_receipt, recorded_version_id FROM model_hub_hubskillcontextcapture WHERE context_id=$1 AND workspace_id=$2', [input.contextId, input.actor.workspaceId]);
          requireValue(rows.rows.length === 1, 'Exactly one retained capture expected');
          state.capture = { caseId: captured.id, row: rows.rows[0] };
          await shot('02-captured-case');
          const hash = await record('capture-observation', { evidenceClass: 'synthetic fixture', suite, capture: rows.rows[0], decision });
          receipt('api', hash, decision);
          return;
        }
        if (stage.id === 'reproduce-historical') {
          await go('context-tests-head', `/dashboard/skills/contexts/${input.contextId}?version=${input.versions.head}`);
          await op.click(page.getByRole('tab', { name: 'Tests', exact: true }), 'open-context-tests');
          // Head v6 stands as the candidate; recorded v4 is the reference that must show the failure.
          const evidenceId = await runNative({ reference: input.versions.recorded, titles: [TITLE.refund], historical: true, tag: 'reproduce' });
          const run = await waitRun(evidenceId, signal);
          requireValue(run.status === 'completed' && run.result.reproduction === 'reproduced', `Historical failure was not reproduced (${run.result?.reproduction ?? run.failure})`);
          state.runs.reproduction = run;
          await op.transition('reproduction-result', () => expect(page.getByText(/Historical failure: reproduced/)).toBeVisible({ timeout: 120_000 }));
          await shot('03-reproduced');
          receipt('api', await record('reproduction-run', { evidenceClass: 'synthetic fixture', run }));
          return;
        }
        if (stage.id === 'preserve-and-start-hotfix') {
          await go('historical-context', `/dashboard/skills/contexts/${input.contextId}?version=${input.versions.recorded}`);
          await op.click(page.getByRole('button', { name: 'Edit viewed v4', exact: true }), 'edit-viewed-v4');
          await op.click(page.getByRole('button', { name: 'Review draft preservation', exact: true }), 'review-draft-preservation');
          await op.click(page.getByRole('checkbox', { name: 'Save a recovery checkpoint and suspend my current draft.', exact: true }), 'confirm-preservation');
          await op.click(page.getByRole('button', { name: 'Preserve and suspend draft', exact: true }), 'preserve-and-suspend');
          await op.click(page.getByRole('button', { name: 'Start preserved-origin draft', exact: true }), 'start-v4-draft');
          await expect(page.getByRole('button', { name: 'Review against current latest', exact: true })).toBeVisible();
          const checkpoints = await pool.query('SELECT digest, payload FROM model_hub_hubskilldraftcheckpoint WHERE skill_id=$1 AND workspace_id=$2', [input.contextId, input.actor.workspaceId]);
          const draft = await api('GET', `${root}draft/`);
          requireValue(checkpoints.rows.length === 1 && draft.base_version === input.versions.recorded && draft.files.find((f) => f.path === 'AGENTS.md').content === input.recordedRoot, 'Preserved origin differs');
          state.preserved = { checkpoints: checkpoints.rows.length, base: draft.base_version };
          receipt('pg', await record('preserved-origin', { evidenceClass: 'synthetic fixture', checkpoint: checkpoints.rows[0].digest, base: draft.base_version }));
          return;
        }
        if (stage.id === 'repair-and-flush') {
          await op.click(page.getByText('POLICY-REFUND: full-amount'), 'focus-refund-clause');
          await edit('replace-refund-clause', async () => {
            await page.keyboard.press('Home'); await page.keyboard.press('Shift+End');
            await page.keyboard.type('POLICY-REFUND: capped-25');
          });
          await expect(page.getByText('POLICY-REFUND: capped-25')).toBeVisible();
          await expect(page.getByText('Saved', { exact: true })).toBeVisible();
          await op.transition('review-candidate', () => op.click(page.getByRole('button', { name: 'Review changes' }), 'review-changes'));
          await edit('version-note', () => page.getByLabel('Version note').fill('Cap refunds at the order total'));
          await op.click(page.getByRole('checkbox', { name: /I reviewed the saved candidate/ }), 'review-candidate-against-head');
          await op.click(page.getByRole('checkbox', { name: /I acknowledge the static warnings/ }), 'acknowledge-warnings');
          await op.click(page.getByRole('button', { name: 'Acknowledge reviewed head', exact: true }), 'acknowledge-head');
          // The acknowledged head is a new exact candidate review; it is confirmed again for publication.
          await expect(page.getByRole('button', { name: 'Publish version', exact: true })).toBeVisible();
          await op.click(page.getByRole('checkbox', { name: /I reviewed the saved candidate/ }), 'review-acknowledged-candidate');
          await op.click(page.getByRole('checkbox', { name: /I acknowledge the static warnings/ }), 'acknowledge-warnings-for-publication');
          const published = page.waitForResponse((res) => res.url().endsWith(`${root}draft/publish/`) && res.request().method() === 'POST');
          published.catch(() => {});
          await op.click(page.getByRole('button', { name: 'Publish version', exact: true }), 'publish-repair');
          const result = (await (await published).json()).result;
          const number = result.version.number;
          const inspection = await api('GET', `${root}versions/${number}/inspection/`);
          const bytes = await api('GET', `${root}versions/${number}/inspection/files/?path=AGENTS.md`).catch(() => null);
          const row = (await pool.query('SELECT origin_number, parent_number FROM model_hub_hubskillversion WHERE skill_id=$1 AND number=$2', [input.contextId, number])).rows[0];
          requireValue(row.origin_number === input.versions.recorded && number > input.versions.head, 'Repair did not start from recorded v4 and publish after head');
          state.repaired = { number, origin: row.origin_number, parent: row.parent_number, id: result.version.id, digest: result.version.digest, inspection: Boolean(inspection), bytes };
          await shot('04-published-repair');
          receipt('pg', await record('repair-published', { evidenceClass: 'synthetic fixture', ...state.repaired }));
          return;
        }
        if (stage.id === 'matched-native-comparison') {
          await go('repaired-context-tests', `/dashboard/skills/contexts/${input.contextId}?version=${state.repaired.number}`);
          await op.click(page.getByRole('tab', { name: 'Tests', exact: true }), 'open-context-tests');
          const evidenceId = await runNative({ reference: input.versions.recorded, production: input.versions.head, titles: Object.values(TITLE), historical: true, tag: 'matched' });
          const run = await waitRun(evidenceId, signal);
          requireValue(run.status === 'completed', `Matched run did not complete (${run.failure})`);
          state.runs.matched = run;
          state.matchedId = evidenceId;
          await op.transition('matched-result', () => expect(page.getByText(/Release-ready on the application|Hold this release/).first()).toBeVisible({ timeout: 120_000 }));
          await shot('05-matched-result');
          // Acceptance-matrix controls through the public API (not operator work).
          const targets = await api('GET', `${root}native/targets/`);
          const target = targets.targets[0];
          const suite = await api('GET', `${root}tests/`);
          const byTitle = Object.fromEntries(suite.cases.map((item) => [item.title, item.id]));
          const control = async (candidate, caseIds) => {
            const plan = { binding_id: target.binding_id, binding_revision: target.revision, candidate_version: candidate, baselines: { reference: input.versions.recorded }, case_ids: caseIds, repetitions: 1, historical_case_ids: [] };
            const planned = await api('POST', `${root}native/preview/`, plan);
            const created = await api('POST', `${root}native/runs/`, { ...plan, execution_fingerprint: planned.execution_fingerprint, acknowledge_spend: true });
            return waitRun(created.evidence_id, signal);
          };
          const publish = async (content, note) => {
            let draft = await api('POST', `${root}draft/start/`, { revision: 0, from_version: input.versions.recorded });
            draft = await api('PUT', `${root}draft/`, { revision: draft.revision, base_version: draft.base_version, files: [{ path: 'AGENTS.md', content }], description: 'Synthetic support desk', note });
            const review = await api('POST', `${root}draft/head-review/`, { revision: draft.revision });
            const ack = await api('POST', `${root}draft/acknowledge-head/`, { revision: draft.revision, expected_digest: draft.digest, expected_head_version: review.expected_head_version, review_fingerprint: review.review_fingerprint });
            return (await api('POST', `${root}draft/publish/`, { revision: ack.revision, expected_digest: draft.digest, base_version: draft.base_version, expected_head_version: review.expected_head_version, review_fingerprint: ack.review_fingerprint, note, acknowledge_warnings: true })).version.number;
          };
          ledger.fault('text-pass-native-tool-fail', 'wording-only-repair');
          const wording = await publish(input.controlRoots.wordingOnly, 'Control: wording-only repair');
          state.controls.textOnly = await control(wording, [byTitle[TITLE.refund]]);
          ledger.fault('routing-near-miss', 'dispute-as-refund');
          const overbroad = await publish(input.controlRoots.overbroad, 'Control: overbroad routing repair');
          state.controls.nearMiss = await control(overbroad, [byTitle[TITLE.chargeback]]);
          ledger.fault('missing-historical-state', 'case-without-business-state');
          const bare = { id: crypto.randomUUID(), title: 'Shipping without state', source_group_id: 'synthetic:bare', prompt: 'Where is my package? Please track the shipment.', fixtures: [{ id: crypto.randomUUID(), name: 'fi.application.expectations', text: JSON.stringify({ schema: 1, skill_id: input.skills.shipping, action: 'track_shipment' }) }], expected_trigger: null, reviewed: true,
            checks: [{ id: crypto.randomUUID(), kind: 'contains', critical: true, config: { text: 'package', case_sensitive: false } }] };
          const latest = await api('GET', `${root}tests/`);
          const keep = (item) => ({ id: item.id, title: item.title, source_group_id: item.source_group_id, prompt: item.prompt, fixtures: item.fixtures, expected_trigger: item.expected_trigger, checks: item.checks, reviewed: item.reviewed });
          await api('PUT', `${root}tests/`, { revision: latest.revision, cases: [...latest.cases, bare].map(keep) });
          state.controls.noState = await api('POST', `${root}native/preview/`, { binding_id: target.binding_id, binding_revision: target.revision, candidate_version: state.repaired.number, baselines: { reference: input.versions.recorded }, case_ids: [bare.id], repetitions: 1, historical_case_ids: [] });
          receipt('api', await record('matched-run', { evidenceClass: 'synthetic fixture', run, controls: state.controls }));
          return;
        }
        if (stage.id === 'inspect-and-annotate') {
          const run = state.runs.matched;
          const historical = run.cases.find((item) => item.historical);
          const cells = run.cells.filter((cell) => cell.case_id === historical.id && cell.variant === 'reference');
          const decision = { packet_sha256: digest({ run: run.id, cell: cells[0].attempt_id, outcomes: [cells[0].tools, cells[0].answer] }), preconditions_sha256: digest({ annotationRevision: run.annotation_revision, fingerprint: run.execution_fingerprint }) };
          await op.click(page.getByRole('row', { name: new RegExp(`${TITLE.refund}.*Recorded reference`) }).first(), 'open-original-failure-cell');
          await expect(page.getByText('Observed by the application')).toBeVisible();
          await shot('06-cell-observation');
          await op.click(page.getByRole('combobox', { name: 'Your assessment' }), 'open-assessment');
          await op.click(page.getByRole('option', { name: /Disagree/ }), 'choose-disagree');
          await edit('annotation-why', () => page.getByLabel('Why').fill('The answer claims success but the business ledger shows the refund was declined.'));
          const appended = page.waitForResponse((res) => res.url().includes('/annotations/') && res.request().method() === 'POST');
          await op.review('record-human-disagreement', [`cell-${cells[0].attempt_id}`], [], async () => { await page.getByRole('button', { name: 'Record review' }).click(); }, decision);
          requireValue((await appended).ok(), 'Annotation was not accepted');
          const list = await api('GET', `evidence/simulation_native/${run.id}/annotations/`);
          requireValue(list.results.length === 1 && list.results[0].verdict === 'disagree' && list.results[0].attempt_id === cells[0].attempt_id, 'Independent annotation observation differs');
          const after = await api('GET', `${root}native/runs/${run.id}/`);
          requireValue(canonicalCell(after.cells, cells[0]) === canonicalCell(run.cells, cells[0]), 'Automatic grade changed after annotation');
          await shot('07-annotation');
          receipt('api', await record('annotation', { evidenceClass: 'synthetic fixture', annotations: list.results, decision }), decision);
          return;
        }
        if (stage.id === 'retained-regression-pipeline') {
          // The incumbent CI is pointed at the exact candidate: one exact-version binding and a context-only credential.
          const label = `ci-${runId}`.toLowerCase();
          const access = await ledger.measure({ dimension: 'A', primitive: 'command', actor: 'incident-responder', step: stage.id, phase: stage.phase, target: 'issue-exact-candidate-access' }, async () => {
            const binding = await api('POST', `${root}bindings/`, { name: `retained-ci ${label}`.slice(0, 120), kind: 'runtime', selector: { version: state.repaired.number }, update_mode: 'manual', profile: 'retained-ci', profile_version: '1', repository: null, expected_report_interval_seconds: 3600 });
            const credential = await api('POST', 'contexts/credentials/', { purpose: 'consume', grants: [{ kind: 'agent_context', resource_id: input.contextId, labels: [], version_ids: [state.repaired.id] }], binding_ids: [], expires_at: new Date(Date.now() + 3_600_000).toISOString() });
            return { bindingId: binding.id, secret: credential.secret };
          });
          // Handing the exact candidate identity and its credential to the separate CI system is a counted handoff.
          const ci = await op.handoff('retained-ci', () => op.command('run-retained-ci', 'docker', ['exec', '-e', `CTXHUB_CONTEXT_CREDENTIAL=${access.secret}`, '-e', 'CTXHUB_REGISTRY_URL=http://app:8000',
            input.runtimeContainer, 'python', '/opt/ctxhub/retained_ci.py', '--binding-id', access.bindingId, '--context-id', input.contextId, '--label', label], { timeoutMs: 280_000 }));
          const summary = JSON.parse(ci.stdout.trim().split('\n').at(-1));
          const read = await op.command('read-retained-ci-result', 'docker', ['exec', input.runtimeContainer, 'cat', `/fi_output/ci/${label}.json`]);
          state.ci = { label, summary, report: JSON.parse(read.stdout) };
          requireValue(state.ci.report.passed === true, 'The retained pipeline did not pass');
          receipt('external_pipeline', await record('retained-ci-report', { evidenceClass: 'synthetic fixture', report: state.ci.report }));
          return;
        }
        if (stage.id === 'release-ready-packet') {
          await go('matched-run-result', `/dashboard/skills/contexts/${input.contextId}?version=${state.repaired.number}`);
          await op.click(page.getByRole('tab', { name: 'Tests', exact: true }), 'open-context-tests');
          // Later verifications (the acceptance controls) are newer; the packet is for the matched candidate run.
          await choose('Verification', new RegExp(`^${state.runs.matched.execution_fingerprint.replace('sha256:', '').slice(0, 12)}`), 'matched-verification');
          await expect(page.getByText('Release packet')).toBeVisible({ timeout: 120_000 });
          // The pipeline's identity is carried from the CI result into the packet by the operator: a counted reconciliation.
          await op.reconcile('pipeline-identity-into-packet', async () => {
            await page.getByLabel('Retained pipeline run URL').fill(state.ci.summary.url);
            await page.getByLabel('Pipeline configuration identity').fill(state.ci.report.configuration_identity);
            await page.getByLabel('Pipeline decision').fill(state.ci.report.passed ? 'passed' : 'failed');
          });
          await op.click(page.getByRole('button', { name: 'Share evidence and prepare packet', exact: true }), 'prepare-release-packet');
          await expect(page.getByText(/Packet ready/)).toBeVisible();
          await shot('08-release-packet');
          const evidence = await api('GET', `evidence/simulation_native/${state.matchedId}/`);
          const preview = await api('POST', 'release-requests/preview/', { resource_id: input.contextId, kind: 'agent_context', candidate_version_id: state.repaired.id, label: 'production',
            target: (({ binding_id, revision, configuration_digest }) => ({ binding_id, environment_id: null, revision, configuration_digest }))((await api('GET', `${root}native/targets/`)).targets[0]),
            evidence: [{ source: 'simulation_native', source_id: state.matchedId, annotation_revision: evidence.annotation_revision }],
            external_pipeline: { url: state.ci.summary.url, configuration_identity: state.ci.report.configuration_identity, decision: 'passed', trust: 'operator_recorded_unimported', recorded_by: input.userId } });
          requireValue(evidence.visibility === 'workspace' && preview.packet.candidate_version_id === state.repaired.id && preview.packet.evidence.length === 1, 'Independent packet observation differs');
          state.packet = preview;
          receipt('api', await record('release-packet', { evidenceClass: 'synthetic fixture', fingerprint: preview.fingerprint, packet: preview.packet }));
          return;
        }
        if (stage.id === 'retained-trace-conformance') {
          // The separately measured J4 job releases the candidate to production. The harness stands in for it
          // outside this ledger: the production binding moves to the exact version, with a context-only credential for it.
          const bindings = await api('GET', `${root}bindings/`);
          const current = (bindings.results ?? bindings).find((row) => row.id === input.production.bindingId);
          const moved = await api('PATCH', `contexts/bindings/${input.production.bindingId}/`, { revision: current.revision, binding: { name: 'support-app production', kind: 'runtime', selector: { version: state.repaired.number }, update_mode: 'manual', profile: 'support-app', profile_version: '1', repository: null, expected_report_interval_seconds: 3600 } });
          const productionCredential = await api('POST', 'contexts/credentials/', { purpose: 'consume', grants: [{ kind: 'agent_context', resource_id: input.contextId, labels: [], version_ids: [state.repaired.id] }], binding_ids: [], expires_at: new Date(Date.now() + 3_600_000).toISOString() });
          requireValue(moved.selector.version === state.repaired.number, 'Production binding did not move to the repaired version');
          const produced = JSON.parse(execFileSync('docker', ['exec', '-e', `CTXHUB_CONTEXT_CREDENTIAL=${productionCredential.secret}`, '-e', 'CTXHUB_REGISTRY_URL=http://app:8000', '-e', `TRACE_URL=${input.collectorInternalUrl}`,
            '-e', `TRACE_API_KEY=${input.apiKey}`, '-e', `TRACE_SECRET_KEY=${input.secretKey}`, '-e', `TRACE_PROJECT=${input.projectName}`, input.runtimeContainer, 'python', '/opt/ctxhub/production_request.py',
            '--binding-id', input.production.bindingId, '--context-id', input.contextId], { encoding: 'utf8' }).trim().split('\n').at(-1));
          state.production = produced;
          await go('retained-tracing', '/dashboard/observe');
          await op.click(page.getByText(input.projectName, { exact: true }), 'open-incident-project');
          await op.transition('production-trace', () => op.click(page.locator('.clean-data-table:visible .ag-row [col-id="trace_name"]').filter({ hasText: 'Production refund request' }).first(), 'open-production-trace'));
          await shot('09-production-trace');
          const trace = await api('GET', `../../tracer/trace/${produced.trace_id}/?project_id=${input.projectId}`);
          state.trace = JSON.stringify(trace);
          receipt('external_trace', await record('production-trace', { evidenceClass: 'synthetic fixture', trace: produced, observed: state.trace.length }));
          return;
        }
        throw new Error(`Unknown stage: ${stage.id}`);
      } catch (error) {
        // Operator-visible diagnostics for a failed stage; never request bodies or credentials.
        await page.screenshot({ path: resolve(output, 'shots', `failed-${stage.id}.png`), animations: 'disabled' }).catch(() => {});
        if (process.env.J2_DEBUG) console.error('J2_DEBUG', stage.id, String(error.message).slice(0, 700));
        throw error;
      } finally { signal.removeEventListener('abort', cancel); }
    },
    async observe(check, { signal }) {
      requireValue(!signal.aborted, 'Journey cancelled');
      const run = state.runs.matched;
      const make = async (source, value, extra = {}) => ({ source, value, artifact_sha256: await record(`observe-${check.id}`, { evidenceClass: 'synthetic fixture', value, ...extra }) });
      const runtime = input.runtimeContainer;
      if (check.id === 'physical-source-and-redaction') {
        const row = state.capture.row;
        return make('pg', { projectId: row.trace_ref.project_id, traceId: row.trace_ref.trace_id, spanId: row.trace_ref.span_id, manualIdentityReentry: state.identityEdits > 0 });
      }
      if (check.id === 'historical-reproduced') {
        const rep = state.runs.reproduction;
        const attempt = await attemptOf(rep.job_id);
        const operations = runOperations(runtime, attempt, rep.cells);
        const reference = operations.find((row) => row.cell.variant === 'reference');
        const turns = firstTurns(await fixtureLedger());
        const recordedBytes = turns.some((turn) => turn.system.includes(input.recordedRoot.split('\n')[2]));
        const health = rep.runtime;
        const measured = JSON.parse(execFileSync('docker', ['exec', runtime, 'python', '/opt/ctxhub/measure.py', 'config'], { encoding: 'utf8' }));
        return make('guest', { reproduced: reference?.status === 'declined' && rep.result.reproduction === 'reproduced', exactHistoricalContext: recordedBytes && sha256(input.recordedRoot) === input.recordedRootSha256,
          qualifiedEnvironment: health.read_only_source === true && health.read_only_context === true && health.separate_writable_state === true && health.controlled_paid_egress === true && health.image_digest === measured.image_digest && health.code_digest === measured.code_digest });
      }
      if (check.id === 'origin-and-reviewed-head') {
        const row = (await pool.query('SELECT origin_number, parent_number FROM model_hub_hubskillversion WHERE skill_id=$1 AND number=$2', [input.contextId, state.repaired.number])).rows[0];
        return make('pg', { origin: row.origin_number, reviewedHead: row.parent_number, personalWorkPreserved: state.preserved.checkpoints === 1 });
      }
      if (check.id === 'case-arm-repeat-matrix') {
        const titles = Object.fromEntries(run.cases.map((item) => [item.id, item.title]));
        const rows = run.cells.map((cell) => ({ case_id: titles[cell.case_id], arm: cell.variant, repeat: cell.repeat_index, complete: cell.status === 'completed' && cell.attempt_id !== null }));
        return make('guest', rows);
      }
      if (check.id === 'natural-child-selection') {
        const ledger = await fixtureLedger();
        const turns = firstTurns(ledger);
        const candidateTurns = ledger.filter((row) => row.kind === 'model' && row.turn === 1 && row.messages[0].content.includes('POLICY-REFUND: capped-25') && !row.messages[0].content.includes('POLICY-ROUTING'));
        const selected = (row) => JSON.parse(row.messages.find((m) => m.role === 'assistant').tool_calls[0].function.arguments).skill_id;
        const byRequest = (text) => candidateTurns.filter((row) => row.request === text).map(selected);
        return make('model_boundary', { catalogMembers: Math.min(...turns.map((t) => t.catalogMembers)), refundCorrect: byRequest(input.requests.refund).length > 0 && byRequest(input.requests.refund).every((id) => id === input.skills.refund),
          chargebackNearMissHeld: byRequest(input.requests.chargeback).length > 0 && byRequest(input.requests.chargeback).every((id) => id === input.skills.chargeback) });
      }
      if (check.id === 'tool-failure-hold') {
        const { textOnly, nearMiss } = state.controls;
        const evidence = await api('GET', `evidence/simulation_native/${textOnly.id}/`);
        const row = (await pool.query('SELECT count(*)::int AS n FROM model_hub_hubskillnativeevidence WHERE id=$1', [textOnly.id])).rows[0];
        return make('api', { textPassToolFailHeld: textOnly.decision.state === 'hold' && /text pass is not a repair/.test(textOnly.decision.reasons.join(' ')) && nearMiss.decision.state === 'hold',
          nativeEvidenceRequired: evidence.source === 'simulation_native' && row.n === 1 && state.controls.noState.reproduction === 'not_reproduced' }, { decisions: { textOnly: textOnly.decision, nearMiss: nearMiss.decision } });
      }
      if (check.id === 'neighboring-behavior') {
        const attempt = await attemptOf(run.job_id);
        const operations = runOperations(runtime, attempt, run.cells).filter((row) => row.cell.variant === 'candidate');
        const titles = Object.fromEntries(run.cases.map((item) => [item.id, item.title]));
        const ok = (title, action) => operations.some((row) => titles[row.cell.case_id] === title && row.action === action && row.status === 'applied');
        return make('guest', { chargebackPreserved: ok(TITLE.chargeback, 'open_chargeback'), shippingPreserved: ok(TITLE.shipping, 'track_shipment') });
      }
      if (check.id === 'external-identity') {
        const mentions = (id) => state.trace.includes(id);
        const old = state.repaired && input.v4Id;
        return make('external_trace', { changedHubIdentity: mentions(state.repaired.id) && mentions(state.repaired.digest) && state.production.version_id === state.repaired.id,
          oldExternalVersionReused: mentions(input.v4Id) }, { traceId: state.production.trace_id, old });
      }
      if (check.id === 'retained-pipeline-result') {
        const read = execFileSync('docker', ['exec', runtime, 'cat', `/fi_output/ci/${state.ci.label}.json`], { encoding: 'utf8' });
        const report = JSON.parse(read);
        return make('external_pipeline', { exactApplicationPipelineInspected: report.version_ids.length === 1 && report.version_ids[0] === state.repaired.id && report.cases.length === 3 && report.cases.every((row) => row.context_digest === state.repaired.digest),
          passed: report.passed === true }, { report });
      }
      throw new Error(`Whole-job independent evidence is not implemented: ${check.id}`);
    },
    async teardown() {
      await context?.close();
      await browser?.close();
      await fetch(`${input.fixtureOrigin}/scenarios/${input.providerNamespace}`, { method: 'DELETE' }).catch(() => {});
      await pool.end().catch(() => {});
      return { qualified: !browser || !browser.isConnected() };
    },
  };
}

function canonicalCell(cells, wanted) {
  const cell = cells.find((item) => item.attempt_id === wanted.attempt_id);
  return JSON.stringify({ selection: cell.selection, delivery: cell.delivery, tools: cell.tools, answer: cell.answer });
}
