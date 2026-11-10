import { createHash, randomUUID } from 'node:crypto';
import { test, expect } from '../../lib/fixtures';
import { ApiClient } from '../../lib/api-client';
import { E2E } from '../../lib/env';
import { flowAnnotation } from '../../lib/flow-meta';

// urls_skills_context_authoring.py / urls_skills_hub_v12.py and routes/paths.js.
const CONTEXTS = '/model-hub/skills/contexts/';
const UI = '/dashboard/skills/contexts/';
const UI_READY = 60_000;
type Envelope<T> = { result: T };
type Version = { id: string; number: number; digest: string };
type Binding = { id: string; name: string };
type Observations = { results: { binding: Binding; states: string[]; loaded: { version_id: string } | null }[] };

// This is the declared-cohort regression in J6, NOT qualified application recovery.
// Synthetic report receipts establish the reporting contract, not model delivery.
test('SKILLS-E2E-060: distinguish reported delivery from incomplete and unknown recovery cohorts', {
  tag: ['@flow'],
  annotation: flowAnnotation({
    id: 'SKILLS-E2E-060', area: 'skills',
    userGoal: 'Inspect declared recovery consumers without mistaking one successful reporter for a completely recovered cohort',
    steps: [
      'open a published synthetic context and inspect its Consumers tab',
      'distinguish a fully observed pin, an incomplete binding, a later failure and an unobserved binding',
      'open the incomplete binding receipt drawer and inspect its historical delivery without a recovered claim',
    ],
    backendChecks: [
      'the scoped persisted receipt identities and stages yield exactly one matching pin and three unknown bindings in the public consumer summary',
    ],
  }),
}, async ({ page, actor, request, probe }, testInfo) => {
  test.setTimeout(300_000); // fixture setup 60s + four UI_READY inspection stages.
  await page.setViewportSize({ width: 1440, height: 900 });
  const name = `e2e-j6-${testInfo.workerIndex}-${Date.now().toString(36)}`;
  const text = `Synthetic recovery policy ${name}. Do not replay completed actions.\n`;
  const sha256 = createHash('sha256').update(text).digest('hex');
  const human = () => new ApiClient(request, E2E.apiUrl, {
    Authorization: `Bearer ${actor.tokens.access}`,
    'X-Organization-Id': actor.organizationId, 'X-Workspace-Id': actor.workspaceId,
    'Idempotency-Key': randomUUID(),
  });
  // Exact wire bodies: skills_context_authoring.Create and skills_hub_v12.ContextPublish.
  const created = (await human().post<Envelope<{ context_id: string; revision: number }>>(CONTEXTS, {
    name, description: 'Synthetic recovery coverage, not application qualification.', tags: [],
    files: [{ path: 'AGENTS.md', content: text }],
  })).result;
  const root = `${CONTEXTS}${created.context_id}/`;
  const head = (await human().post<Envelope<{
    draft_revision: number; candidate_digest: string; expected_head_version: number | null; review_fingerprint: string;
  }>>(`${root}draft/head-review/`, { revision: created.revision })).result;
  const acknowledged = (await human().post<Envelope<{ revision: number; review_fingerprint: string }>>(`${root}draft/acknowledge-head/`, {
    revision: head.draft_revision, expected_digest: head.candidate_digest,
    expected_head_version: head.expected_head_version, review_fingerprint: head.review_fingerprint,
  })).result;
  const version = (await human().post<Envelope<{ version: Version }>>(`${root}draft/publish/`, {
    revision: acknowledged.revision, expected_digest: head.candidate_digest,
    expected_head_version: head.expected_head_version, review_fingerprint: acknowledged.review_fingerprint,
    base_version: null, note: '', acknowledge_warnings: true,
  })).result.version;
  const bindings: Binding[] = [];
  for (const suffix of ['complete-pin', 'incomplete', 'failed', 'unobserved']) {
    // skills_hub_v12.BindingInput. Each binding is owned by this test's actor.
    bindings.push((await human().post<Envelope<Binding>>(`${root}bindings/`, {
      name: `${name}-${suffix}`, kind: 'runtime', selector: { version: version.number },
      update_mode: 'new_thread', profile: 'synthetic-reporter', profile_version: '1',
      repository: null, expected_report_interval_seconds: 3600,
    })).result);
  }
  const credential = (await human().post<Envelope<{ id: string; secret: string }>>(`${CONTEXTS}credentials/`, {
    purpose: 'report', grants: [], binding_ids: bindings.map(binding => binding.id),
    expires_at: new Date(Date.now() + 3_600_000).toISOString(),
  })).result;
  const reporter = new ApiClient(request, E2E.apiUrl, { Authorization: `Context ${credential.secret}` });
  const seeded: { binding_id: string; instance_id: string; stage: string; sequence: number }[] = [];
  const report = async (binding: Binding, stage: string, instance = randomUUID(), sequence = 0) => {
    // skills_hub_v12.ReceiptInput / ReceiptDetails, bounded synthetic metadata only.
    const delivered = stage === 'instructions_delivered';
    await reporter.post(`${CONTEXTS}bindings/${binding.id}/receipts/`, {
      event_id: randomUUID(), session_id: null, instance_id: instance, sequence, stage,
      version_id: version.id, digest: version.digest, materialization_digest: null,
      observed_at: new Date().toISOString(),
      details: { path: delivered ? 'AGENTS.md' : null, sha256: delivered ? sha256 : null,
        ranges: delivered ? [[0, text.length]] : [], runtime_version: 'synthetic-1',
        adapter_version: 'synthetic-1', diagnostic_codes: [], trace_ref: null },
    });
    seeded.push({ binding_id: binding.id, instance_id: instance, stage, sequence });
    return instance;
  };
  try {
    await report(bindings[0], 'instructions_delivered');
    await report(bindings[1], 'instructions_delivered');
    await report(bindings[1], 'installed');
    const failed = await report(bindings[2], 'instructions_delivered');
    await report(bindings[2], 'failed', failed, 1);
    await testInfo.attach('j6-declared-cohort', { body: JSON.stringify({ contextId: created.context_id,
      workspaceId: actor.workspaceId, version, bindings, seeded }), contentType: 'application/json' });

    // Independent PG read pins the exact stored cohort, not just the reporting API's toast.
    const persisted = await probe.pg<{ binding_id: string; instance_id: string; stage: string; sequence: string; version_id: string }>(
      'SELECT binding_id, instance_id, stage, sequence, version_id FROM model_hub_hubskillcontextreceipt WHERE organization_id = $1 AND workspace_id = $2 AND binding_id = ANY($3::uuid[])',
      [actor.organizationId, actor.workspaceId, bindings.map(binding => binding.id)],
    );
    expect(persisted.map(row => ({ ...row, sequence: Number(row.sequence) })).sort((a, b) => `${a.instance_id}:${a.sequence}`.localeCompare(`${b.instance_id}:${b.sequence}`)))
      .toEqual(seeded.map(row => ({ ...row, version_id: version.id })).sort((a, b) => `${a.instance_id}:${a.sequence}`.localeCompare(`${b.instance_id}:${b.sequence}`)));
    const observations = (await actor.api.get<Envelope<Observations>>(`${root}consumers/`)).result;
    const states = Object.fromEntries(observations.results.map(row => [row.binding.id, row.states]));
    expect(states).toEqual({
      [bindings[0].id]: ['matches_target', 'pinned_as_intended'],
      [bindings[1].id]: ['unknown'], [bindings[2].id]: ['unknown'], [bindings[3].id]: ['unknown'],
    });
    await testInfo.attach('j6-independent-observations', { body: JSON.stringify({ persisted, observations }), contentType: 'application/json' });

    await test.step('Open the declared consumer table', async () => {
      await page.goto(`${UI}${created.context_id}`);
      await page.getByRole('tab', { name: 'Consumers', exact: true }).click({ timeout: UI_READY });
      await expect(page.getByRole('table', { name: 'Context delivery observations' })).toBeVisible({ timeout: UI_READY });
    }, { timeout: UI_READY });
    await test.step('Inspect each recovery state', async () => {
      const table = page.getByRole('table', { name: 'Context delivery observations' });
      await expect(table.getByRole('row')).toHaveCount(5);
      const complete = table.getByRole('row').filter({ hasText: bindings[0].name });
      await expect(complete.getByText('Matches target', { exact: true })).toBeVisible();
      await expect(complete.getByText('Pinned as intended', { exact: true })).toBeVisible();
      for (const binding of bindings.slice(1)) {
        const row = table.getByRole('row').filter({ hasText: binding.name });
        await expect(row.getByText('Runtime loading not observed', { exact: true })).toBeVisible();
        await expect(row.getByText('Matches target', { exact: true })).toHaveCount(0);
      }
      await page.screenshot({ path: testInfo.outputPath('j6-consumers.png') });
      await table.getByRole('row').filter({ hasText: bindings[3].name }).scrollIntoViewIfNeeded();
      await page.screenshot({ path: testInfo.outputPath('j6-unobserved.png') });
    }, { timeout: UI_READY });
    await test.step('Inspect retained delivery without claiming business recovery', async () => {
      await page.getByRole('button', { name: bindings[1].name, exact: true }).click();
      await expect(page.getByText('Delivery receipts', { exact: true })).toBeVisible();
      await expect(page.getByText('observe_runtime', { exact: true })).toBeVisible();
      await expect(page.getByText(/Delivery is supplied content, not proven compliance/)).toBeVisible();
      await page.screenshot({ path: testInfo.outputPath('j6-receipts.png') });
    }, { timeout: UI_READY });
  } finally {
    // Public credential revocation; retained synthetic rows are isolated by actor/name.
    await human().delete(`${CONTEXTS}credentials/${credential.id}/`);
  }
});
