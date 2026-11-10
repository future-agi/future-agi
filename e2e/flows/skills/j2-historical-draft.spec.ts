import { randomUUID } from 'node:crypto';
import { test, expect } from '../../lib/scope-actors';
import { flowAnnotation } from '../../lib/flow-meta';
import { E2E } from '../../lib/env';
import { authInitScript } from '../../lib/auth';

type File = { path: string; content: string };
type Draft = { revision: number; base_version: number | null; files: File[]; digest: string; recovery_digest: string; note: string; context_spec: unknown; resolved_lock: unknown };
type Checkpoint = { id: string; owner_id: string; digest: string; payload: { files: File[]; base_version: number; note: string; context_spec: unknown; resolved_lock: unknown } };
const BASE = '/model-hub/skills/contexts/';
const UI = '/dashboard/skills/contexts/';
const READY = 60_000;

// Independent API + PG observations, not toasts or mocked browser responses.
// Each backendCheck has a one-sided mutation below: the real passing witness is
// changed only on the observed side, then the same assertion must reject it.
function assertCheckpoint(rows: Checkpoint[], original: Draft, owner: string) {
  expect(rows).toHaveLength(1);
  expect(rows[0]).toMatchObject({ owner_id: owner, digest: original.recovery_digest, payload: {
    files: original.files, base_version: original.base_version, note: original.note,
    context_spec: original.context_spec, resolved_lock: original.resolved_lock,
  } });
}
function assertOrigin(draft: Draft, files: File[]) {
  expect(draft.base_version).toBe(4);
  expect(draft.files).toEqual(files);
}
function assertHead(detail: { context: { latest_version: number; labels: unknown[] } }) {
  expect(detail.context.latest_version).toBe(6);
  expect(detail.context.labels).toEqual([]);
}

test('SKILLS-E2E-020: preserve unrelated personal work before editing historical v4', {
  tag: ['@flow'],
  annotation: flowAnnotation({ id: 'SKILLS-E2E-020', area: 'skills',
    userGoal: 'An incident responder starts a repair from recorded v4 while latest is unrelated v6, without losing another personal draft or changing publication',
    steps: [
      'inspect the exact historical context v4 while latest v6 and a different personal draft exist',
      'choose Edit viewed v4 and explicitly review preserving the other draft',
      'confirm a private recovery checkpoint and suspend the old draft',
      'separately start the exact selected historical draft, without copying latest bytes',
      'inspect saved origin and preservation independently; native application verification remains a separate job prerequisite',
    ],
    backendChecks: [
      'one owner-private PostgreSQL checkpoint retains the old files, note, content origin and accepted links under the recovery digest',
      'the public draft read contains exact historical v4 owned bytes and base version, not unrelated v6 bytes',
      'the public context read keeps latest at v6 and does not move any release label',
    ],
  }),
}, async ({ browser, request, scopeActors, scopeProbe }, testInfo) => {
  test.setTimeout(600_000); // bounded: six seed publications and six UI_READY stages.
  const owner = scopeActors.ownerA;
  const name = `e2e-j2-${Date.now().toString(36)}`;
  const read = async <T>(path: string) => (await owner.api.get<{ result: T }>(path)).result;
  const post = async <T>(path: string, data: unknown) => {
    const response = await request.post(`${E2E.apiUrl}${path}`, {
      headers: { ...scopeActors.headers(owner), 'Idempotency-Key': randomUUID() }, data,
    });
    expect(response.ok(), `seed ${path}: ${await response.text()}`).toBe(true);
    return (await response.json()).result as T;
  };
  const files = (version: number): File[] => [{ path: 'AGENTS.md', content: `# Synthetic policy v${version}\n${version === 4 ? 'Recorded refund policy: require the refund tool result.' : 'Unrelated policy revision; do not silently import into a historical repair.'}\n` }];
  const created = await post<{ context_id: string }>(BASE, { name, description: 'Synthetic historical repair prerequisite', tags: [], files: files(1) });
  const root = `${BASE}${created.context_id}/`;
  // Prerequisites only. All preservation and historical switching below use UI.
  for (let number = 1; number <= 6; number++) {
    let draft = number === 1 ? await read<Draft>(`${root}draft/`) : await post<Draft>(`${root}draft/start/`, { revision: 0, from_version: number - 1 });
    draft = (await owner.api.put<{ result: Draft }>(`${root}draft/`, {
      revision: draft.revision, base_version: draft.base_version, files: files(number),
      description: 'Synthetic historical repair prerequisite', note: `Seed v${number}`,
    })).result;
    const review = await post<{ expected_head_version: number | null; review_fingerprint: string }>(`${root}draft/head-review/`, { revision: draft.revision });
    const ack = await post<Draft & { review_fingerprint: string }>(`${root}draft/acknowledge-head/`, {
      revision: draft.revision, expected_digest: draft.digest,
      expected_head_version: review.expected_head_version, review_fingerprint: review.review_fingerprint,
    });
    await post(`${root}draft/publish/`, {
      revision: ack.revision, expected_digest: draft.digest, base_version: draft.base_version,
      expected_head_version: review.expected_head_version, review_fingerprint: ack.review_fingerprint,
      note: `Seed v${number}`, acknowledge_warnings: true,
    });
  }
  const started = await post<Draft>(`${root}draft/start/`, { revision: 0, from_version: 6 });
  const original = (await owner.api.put<{ result: Draft }>(`${root}draft/`, {
    revision: started.revision, base_version: 6, files: [{ path: 'AGENTS.md', content: '# Personal draft\nKeep my unfinished unrelated work.\n' }],
    description: 'Private work to preserve', note: 'Unrelated personal note',
  })).result;
  // Custom contexts do not inherit the project's video option. Record this
  // owned context explicitly and initialize only the established public login.
  const context = await browser.newContext({ baseURL: E2E.appUrl,
    viewport: { width: 1440, height: 900 },
    recordVideo: { dir: testInfo.outputPath('video'), size: { width: 1440, height: 900 } },
  });
  await context.addInitScript(authInitScript, { access: owner.tokens.access, refresh: owner.tokens.refresh,
    organizationId: owner.organizationId, workspaceId: owner.workspaceId });
  await context.addInitScript(orgId => sessionStorage.setItem('workspaceOrgId', orgId), owner.organizationId);
  const page = await context.newPage();
  page.setDefaultTimeout(READY);
  try {
    await page.goto(`${UI}${created.context_id}?version=4`);
    await expect(page.getByRole('button', { name: 'Edit viewed v4', exact: true })).toBeEnabled();
    await page.screenshot({ path: testInfo.outputPath('J2-historical-origin.png') });
    await page.getByRole('button', { name: 'Edit viewed v4', exact: true }).click();
    await page.getByRole('button', { name: 'Review draft preservation', exact: true }).click();
    await expect(page.getByRole('button', { name: 'Preserve and suspend draft', exact: true })).toBeDisabled();
    await page.getByRole('checkbox', { name: 'Save a recovery checkpoint and suspend my current draft.', exact: true }).check();
    await page.screenshot({ path: testInfo.outputPath('J2-preservation-review.png') });
    await page.getByRole('button', { name: 'Preserve and suspend draft', exact: true }).click();
    await expect(page.getByRole('button', { name: 'Start preserved-origin draft', exact: true })).toBeVisible();
    const rows = await scopeProbe.pg<Checkpoint>('SELECT id, owner_id, digest, payload FROM model_hub_hubskilldraftcheckpoint WHERE skill_id = $1 AND workspace_id = $2', [created.context_id, owner.workspaceId]);
    assertCheckpoint(rows, original, owner.userId);
    expect(() => assertCheckpoint([{ ...rows[0], payload: { ...rows[0].payload, files: [] } }], original, owner.userId)).toThrow();
    await page.getByRole('button', { name: 'Start preserved-origin draft', exact: true }).click();
    await expect(async () => assertOrigin(await read<Draft>(`${root}draft/`), files(4))).toPass({ timeout: READY });
    const repaired = await read<Draft>(`${root}draft/`);
    assertOrigin(repaired, files(4));
    expect(() => assertOrigin({ ...repaired, base_version: 6, files: files(6) }, files(4))).toThrow();
    const detail = await read<{ context: { latest_version: number; labels: unknown[] } }>(root);
    assertHead(detail);
    expect(() => assertHead({ context: { ...detail.context, latest_version: 7 } })).toThrow();
    await expect(page.getByRole('button', { name: 'Review against current latest', exact: true })).toBeVisible();
    await page.screenshot({ path: testInfo.outputPath('J2-v4-editor.png') });
    await page.evaluate(() => { const settings = JSON.parse(localStorage.getItem('settings') || '{}'); settings.themeMode = 'dark'; localStorage.setItem('settings', JSON.stringify(settings)); });
    await page.reload();
    await expect(page.getByRole('button', { name: 'Review against current latest', exact: true })).toBeVisible();
    await page.screenshot({ path: testInfo.outputPath('J2-v4-editor-dark.png') });
    await testInfo.attach('J2-independent-state', { body: JSON.stringify({ contextId: created.context_id, original, checkpoint: rows[0], historicalDraft: repaired, detail, fallibility: ['lost-personal-files-rejected', 'wrong-origin-rejected', 'unexpected-publication-rejected'], scope: 'synthetic step 4 only; not native reproduction or complete J2' }), contentType: 'application/json' });
  } finally { await context.close(); }
});
