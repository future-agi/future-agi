import { request } from '@playwright/test';
import { createHash } from 'node:crypto';
import { test, expect } from '../../lib/mock-model-fixtures';
import { sendTrace } from '../../lib/otlp';
import { E2E } from '../../lib/env';
import { POLL } from '../../lib/state-probe';
import { inspectManagedMock } from '../../lib/managed-mock';
import { flowAnnotation } from '../../lib/flow-meta';

// Routes and labels: EvalTaskView/SamplingPreviewMixin, TaskCreatePage,
// SamplingPolicyEditor, TaskDetailPage, and the existing EVAL-E2E-005 picker flow.
const TASK = '/tracer/eval-task/';
const PREVIEW = '/tracer/eval-task/sampling_preview/';
const UPDATE = '/tracer/eval-task/update_eval_task/';
const CONFIG = '/tracer/custom-eval-config/';
const INPUT = 'e2e.cohort.input';
const UI_READY = 60_000;
interface Created { result: { id: string } }
interface Sampling {
  cohort_id: string; policy_version: string; sampling_unit_id: string;
  inclusion_probability: number; window: { start: string; end: string | null };
}
interface Entry {
  observation_span_id: string; status: string; output_bool: boolean | null;
  output_metadata: { _task_selection: { sampling: Sampling } };
}
interface Preview {
  result: { policy_version: string; strata: {
    eligible_units: number; selected_units: number; selected_rows: number;
  }[] };
}

test.beforeAll(() => { inspectManagedMock({ evalBackground: true }); });
test.use({ evalBackground: true, viewport: { width: 1600, height: 1100 } });

test('EVAL-E2E-020: sample whole contacts, retain late spans, and explicitly rerun an edited quota', {
  tag: ['@flow'],
  annotation: flowAnnotation({
    id: 'EVAL-E2E-020', area: 'evals',
    userGoal: 'Preview and run a contact quota without splitting contacts, including late spans, then edit the quota with clear new-run provenance',
    steps: ['seed two sessions with two traces and two spans per trace',
      'choose the project and configure an echo judge through the task editor',
      'preview a closed session quota of one and create a continuous span task',
      'verify the independently hash-ranked contact is the exact evaluated set',
      'ingest a late trace in each contact and verify only the sampled contact expands',
      'pause, edit the quota to two, preview and explicitly Delete & rerun',
      'verify one new cohort with probability one and the exact expanded span set'],
    backendChecks: [
      'all eight initial spans have two canonical session identities in ClickHouse',
      'UI preview returns exactly two eligible contacts, one selected contact and four selected spans',
      'created task evaluates exactly the independently hash-ranked contact with probability one half',
      'late ingestion extends only the frozen sampled contact and does not rerank its membership',
      'edited quota is persisted and visible results have one new cohort with probability one, while old provenance remains archived',
    ],
  }),
}, async ({ page, actor, probe, mockModel }, testInfo) => {
  // 2×SPAN_VISIBLE(30) + 3×EVAL_RESULT(270) + 4×UI_READY(240) + navigation(60).
  test.setTimeout(600_000);
  page.setDefaultTimeout(UI_READY);
  const req = await request.newContext();
  const suffix = `${testInfo.workerIndex}-${Date.now().toString(36)}`;
  const projectName = `e2e-cohort20-${suffix}`;
  const taskName = `e2e-cohort-task-${suffix}`;
  const evalName = `e2e-cohort-judge-${suffix}`;
  const sessionLabels = [`e2e-contact-a-${suffix}`, `e2e-contact-b-${suffix}`];
  const eventMs = Date.now() - 120_000;
  const allIds: string[] = [];
  const contacts: { label: string; spanIds: string[]; canonical: string }[] = [];
  try {
    for (const label of sessionLabels) {
      const spanIds: string[] = [];
      for (let turn = 0; turn < 2; turn++) {
        const attributes = { 'session.id': label, 'fi.span.kind': 'llm', [INPUT]: 'cohort-input' };
        const seeded = await sendTrace(req, { collectorUrl: E2E.collectorUrl, apiKey: actor.apiKey,
          secretKey: actor.secretKey, projectName, resourceAttributes: { project_type: 'observe' },
          startTimeUnixNano: BigInt(eventMs + turn * 1000) * 1_000_000n,
          rootAttributes: attributes, childAttributes: attributes });
        spanIds.push(...seeded.spanIds);
      }
      contacts.push({ label, spanIds, canonical: '' });
      allIds.push(...spanIds);
    }
    const idsLiteral = allIds.map(id => `'${id}'`).join(','); // harness-generated hex IDs, not request SQL
    let facts: { id: string; project_id: string; session_id: string }[] = [];
    await expect.poll(async () => {
      facts = await probe.ch<{ id: string; project_id: string; session_id: string }>(
        `SELECT id,toString(project_id) AS project_id,toString(trace_session_id) AS session_id
         FROM spans FINAL WHERE id IN (${idsLiteral})`);
      return facts.map(row => row.id).sort();
    }, POLL.SPAN_VISIBLE).toEqual([...allIds].sort());
    const projectId = facts[0].project_id;
    for (const contact of contacts) {
      const canonical = new Set(facts.filter(row => contact.spanIds.includes(row.id)).map(row => row.session_id));
      expect(canonical.size).toBe(1);
      contact.canonical = [...canonical][0];
    }
    expect(new Set(contacts.map(contact => contact.canonical)).size).toBe(2);
    await testInfo.attach('cohort-fixture', { contentType: 'application/json', body: JSON.stringify({
      projectId, projectName, taskName, contacts, eventMs,
    }) });

    await mockModel.assertReady();
    // Serializer-pinned echo-judge template body, same API lane as EVAL-E2E-005.
    await actor.api.post<Created>('/model-hub/eval-templates/create-v2/', {
      name: evalName, eval_type: 'llm', model: mockModel.model,
      instructions: 'Reply with exactly this JSON: {"result": "Pass", "explanation": "cohort proof saw {{output}}"}',
      output_type: 'pass_fail', pass_threshold: 0.5,
    });
    const range = new URLSearchParams({ startDate: new Date(eventMs - 60_000).toISOString(),
      endDate: new Date(Date.now() + 60_000).toISOString() });
    await page.goto(`/dashboard/tasks/create?${range}`);
    await page.getByPlaceholder('Enter task name').fill(taskName);
    await page.getByPlaceholder('Select Option', { exact: true }).click();
    await page.getByRole('menuitem', { name: projectName, exact: true }).click();
    await page.getByText('New incoming data', { exact: true }).click();
    await page.getByRole('checkbox', { name: 'Contact-aware sampling policy' }).check();
    await page.getByRole('combobox', { name: /^Selection(?: |$)/ }).click();
    await page.getByRole('option', { name: 'Target units', exact: true }).click();
    await page.getByRole('spinbutton', { name: 'Target units', exact: true }).fill('1');
    await testInfo.attach('ui-policy-editor', { contentType: 'image/png', body: await page.screenshot({ fullPage: true }) });

    const previewResponse = page.waitForResponse(r => r.url().endsWith(PREVIEW) && r.request().method() === 'POST', { timeout: UI_READY });
    await page.getByRole('button', { name: 'Preview sampling policy', exact: true }).click();
    const response = await previewResponse;
    expect(response.ok()).toBe(true);
    const preview = await response.json() as Preview;
    expect(preview.result.strata).toHaveLength(1);
    expect(preview.result.strata[0]).toMatchObject({ eligible_units: 2, selected_units: 1, selected_rows: 4 });
    await expect(page.getByRole('table').filter({ has: page.getByRole('columnheader', { name: 'Eligible units' }) })).toBeVisible({ timeout: UI_READY });
    await testInfo.attach('ui-preview-counts', { contentType: 'image/png', body: await page.screenshot({ fullPage: true }) });
    const ranked = contacts.map(contact => ({ contact,
      hash: createHash('sha256').update(`${preview.result.policy_version}:session:${contact.canonical}`).digest('hex'),
    })).sort((a, b) => a.hash.localeCompare(b.hash));
    const chosen = ranked[0].contact;

    await mockModel.assertReady();
    await page.getByRole('button', { name: 'Add Evaluation', exact: true }).click();
    await page.getByPlaceholder('Search evaluations...').fill(evalName);
    await page.getByRole('row').filter({ hasText: evalName }).getByRole('button', { name: 'Add', exact: true }).click();
    await page.getByPlaceholder('Search or type a path (e.g. attributes.input.value)').fill(INPUT);
    await page.getByRole('option', { name: INPUT, exact: true }).click();
    const configured = page.waitForResponse(r => r.url().endsWith(CONFIG) && r.request().method() === 'POST', { timeout: UI_READY });
    await page.getByRole('button', { name: 'Add Evaluation', exact: true }).click();
    const configResponse = await configured;
    expect(configResponse.ok()).toBe(true);
    const config = await configResponse.json() as Created;
    await mockModel.assertReady();
    const created = page.waitForResponse(r => r.url().endsWith(TASK) && r.request().method() === 'POST', { timeout: UI_READY });
    await page.getByRole('button', { name: 'Create Task', exact: true }).click();
    const createResponse = await created;
    expect(createResponse.ok()).toBe(true);
    expect(createResponse.request().postDataJSON()).toMatchObject({ project: projectId, evals: [config.result.id],
      row_type: 'spans', run_type: 'continuous', sampling_policy: { sampling_unit: 'session', mode: 'closed', default: { target_n: 1 } } });
    const taskId = (await createResponse.json() as Created).result.id;
    await expect(page).toHaveURL(new RegExp(`/dashboard/tasks/${taskId}`), { timeout: UI_READY });
    await testInfo.attach('ui-created-task', { contentType: 'image/png', body: await page.screenshot({ fullPage: true }) });
    const readEntries = () => probe.pg<Entry>(
      'SELECT observation_span_id,status,output_bool,output_metadata FROM tracer_eval_logger WHERE eval_task_id=$1 AND deleted=false ORDER BY observation_span_id', [taskId]);
    let entries: Entry[] = [];
    await expect.poll(async () => {
      entries = await readEntries();
      return entries.filter(entry => entry.status === 'completed').map(entry => entry.observation_span_id).sort();
    }, POLL.EVAL_RESULT).toEqual([...chosen.spanIds].sort());
    expect(entries.map(entry => entry.observation_span_id).sort()).toEqual([...chosen.spanIds].sort());
    expect(entries.every(entry => entry.output_bool === true)).toBe(true);
    const originalCohort = entries[0].output_metadata._task_selection.sampling.cohort_id;
    for (const entry of entries) expect(entry.output_metadata._task_selection.sampling).toMatchObject({
      cohort_id: originalCohort, policy_version: preview.result.policy_version,
      sampling_unit_id: `session:${chosen.canonical}`, inclusion_probability: .5,
    });

    for (const contact of contacts) {
      const attributes = { 'session.id': contact.label, 'fi.span.kind': 'llm', [INPUT]: 'late-cohort-input' };
      const late = await sendTrace(req, { collectorUrl: E2E.collectorUrl, apiKey: actor.apiKey,
        secretKey: actor.secretKey, projectName, resourceAttributes: { project_type: 'observe' },
        startTimeUnixNano: BigInt(eventMs + 5000) * 1_000_000n, rootAttributes: attributes, childAttributes: attributes });
      contact.spanIds.push(...late.spanIds); allIds.push(...late.spanIds);
    }
    await expect.poll(async () => {
      entries = await readEntries();
      return entries.filter(entry => entry.status === 'completed').map(entry => entry.observation_span_id).sort();
    }, POLL.EVAL_RESULT).toEqual([...chosen.spanIds].sort());
    expect(entries.map(entry => entry.observation_span_id).sort()).toEqual([...chosen.spanIds].sort());
    expect(entries).toHaveLength(6);
    await testInfo.attach('ui-late-arrival-result', { contentType: 'image/png', body: await page.screenshot({ fullPage: true }) });
    const membership = await probe.pg<{ unit_id: string; inclusion_probability: number }>(
      'SELECT unit_id,inclusion_probability FROM tracer_eval_sampling_member WHERE cohort_id=$1 AND selected=true', [originalCohort]);
    expect(membership).toEqual([{ unit_id: `session:${chosen.canonical}`, inclusion_probability: .5 }]);

    await page.getByRole('button', { name: 'Pause', exact: true }).click();
    await expect.poll(async () => (await actor.api.get<{ status: string }>(`${TASK}${taskId}/`)).status, POLL.ASYNC_JOB).toBe('paused');
    await page.reload();
    await expect(page.getByRole('button', { name: 'Resume', exact: true })).toBeVisible({ timeout: UI_READY });
    await page.getByRole('spinbutton', { name: 'Target units', exact: true }).fill('2');
    const editedPreview = page.waitForResponse(r => r.url().endsWith(PREVIEW), { timeout: UI_READY });
    await page.getByRole('button', { name: 'Preview sampling policy', exact: true }).click();
    const editedResponse = await editedPreview;
    expect(editedResponse.ok()).toBe(true);
    expect((await editedResponse.json() as Preview).result.strata[0]).toMatchObject({ eligible_units: 2, selected_units: 2, selected_rows: 12 });
    await testInfo.attach('ui-edited-preview', { contentType: 'image/png', body: await page.screenshot({ fullPage: true }) });
    await page.getByRole('button', { name: 'Save', exact: true }).click();
    await expect(page.getByText('Delete Past Data & Start Fresh', { exact: true })).toBeVisible({ timeout: UI_READY });
    await mockModel.assertReady();
    const edited = page.waitForResponse(r => r.url().endsWith(UPDATE) && r.request().method() === 'PATCH', { timeout: UI_READY });
    await page.getByRole('button', { name: 'Run task', exact: true }).click();
    const editedTask = await edited;
    expect(editedTask.ok()).toBe(true);
    expect(editedTask.request().postDataJSON()).toMatchObject({ edit_type: 'fresh_run', sampling_policy: { default: { target_n: 2 } } });
    await expect.poll(async () => {
      entries = await readEntries();
      return entries.filter(entry => entry.status === 'completed').map(entry => entry.observation_span_id).sort();
    }, POLL.EVAL_RESULT).toEqual([...allIds].sort());
    expect(entries.map(entry => entry.observation_span_id).sort()).toEqual([...allIds].sort());
    const newCohorts = new Set(entries.map(entry => entry.output_metadata._task_selection.sampling.cohort_id));
    expect(newCohorts.size).toBe(1); expect(newCohorts.has(originalCohort)).toBe(false);
    expect(entries.every(entry => entry.output_metadata._task_selection.sampling.inclusion_probability === 1)).toBe(true);
    await testInfo.attach('ui-rerun-result', { contentType: 'image/png', body: await page.screenshot({ fullPage: true }) });
    const [stored] = await probe.pg<{ sampling_policy: { default: { target_n: number } } }>('SELECT sampling_policy FROM tracer_eval_task WHERE id=$1', [taskId]);
    expect(stored.sampling_policy.default.target_n).toBe(2);
    const archived = await probe.pg<{ sampling: Sampling }>(
      "SELECT output_metadata->'_task_selection'->'sampling' AS sampling FROM tracer_eval_logger WHERE eval_task_id=$1 AND deleted=true", [taskId]);
    expect(archived).toHaveLength(6);
    expect(archived.every(row => row.sampling.cohort_id === originalCohort && row.sampling.inclusion_probability === .5)).toBe(true);
    await page.getByRole('button', { name: 'Pause', exact: true }).click();
  } finally { await req.dispose(); }
});
