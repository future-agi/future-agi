import { test, expect } from '../../lib/fixtures';
import { flowAnnotation } from '../../lib/flow-meta';

// frontend/src/utils/axios.js knowledge endpoints; CreateKnowledgeBaseDrawer.jsx
// requests type=create. No template response is mocked by this flow.
const PAGE = '/dashboard/knowledge';
const TEMPLATE = '/model-hub/knowledge-base/';
// accounts/urls.py and accounts/serializers/org_api_key.py.
const CREATE = '/accounts/key/generate_secret_key/';
const LIST = '/accounts/key/get_secret_keys/';
const BOOTSTRAP = '/accounts/keys/';
const UI_READY = 60_000;
type Pair = { key_id: string; api_key: string; secret_key: string; masked_api_key: string; masked_secret_key: string };

// Credentials remain in memory; report attachments contain identities only.
test.use({ trace: 'off', screenshot: 'off', video: 'off' });
// Playwright also captures an ARIA error context independently of screenshots.
// Close the credential-bearing page before its context is recorded on failure.
test.afterEach(async ({ page }) => { await page.close(); });

test('KB-E2E-001: a developer creates a personal key inline and forgets it from the SDK example', {
  tag: ['@flow'],
  annotation: flowAnnotation({
    id: 'KB-E2E-001', area: 'knowledge-base',
    userGoal: 'A developer explicitly creates a personal key in the Knowledge Base SDK setup and copies that example without fetching a shared credential.',
    steps: [
      'open Knowledge Base, choose Create Knowledge Base and Import from SDK',
      'copy the real template with placeholders and verify no credential was fetched or created',
      'explicitly submit one uniquely named personal key and save its show-once pair',
      'copy the active SDK example containing that exact pair',
      'Forget key, copy again, close and reopen SDK setup; placeholders return without another mint',
    ],
    backendChecks: [
      'Opening SDK setup and fetching the real template leaves the key row set unchanged without bootstrap or issuance calls.',
      'Explicit submission adds exactly the named personal row for the session person and organization with NULL workspace and a masked list identity.',
      'The copied SDK example contains the issued pair only until Forget; copied and reopened examples then contain placeholders and no raw pair remains in browser storage.',
    ],
  }),
}, async ({ page, context, browserName, actor, probe }, testInfo) => {
  test.setTimeout(420_000); // 6 * UI_READY + 60 s API/storage headroom.
  await context.route('**/*', route => new URL(route.request().url()).hostname === 'localhost' ? route.continue() : route.abort('blockedbyclient'));
  // Firefox uses the native clipboard with the test-only asyncClipboard preference
  // in the cloud runner. Neither implementation substitutes a fake clipboard.
  if (browserName === 'chromium') await context.grantPermissions(['clipboard-read', 'clipboard-write']);
  const name = `e2e-kb1-${testInfo.workerIndex}-${Date.now().toString(36)}`;
  const info = await actor.api.get<{ id: string }>('/accounts/user-info/');
  const rowIds = async () => (await probe.pg<{ id: string }>('SELECT id FROM accounts_orgapikey WHERE organization_id=$1 ORDER BY id', [actor.organizationId])).map(r => r.id);
  const before = await rowIds();
  await testInfo.attach('identity', { contentType: 'application/json', body: JSON.stringify({ organizationId: actor.organizationId, workspaceId: actor.workspaceId, userId: info.id, startingKeyIds: before, name }) });
  const traffic: string[] = [];
  page.on('request', r => {
    const path = new URL(r.url()).pathname;
    if (path === BOOTSTRAP || (path === CREATE && r.method() === 'POST')) traffic.push(path);
  });
  let template = '';
  let pair: Pair;
  // FormCodeEditor.jsx's existing Tooltip labels the code-copy IconButton Copy.
  const copy = () => page.getByRole('button', { name: 'Copy', exact: true });
  const copiedEquals = async (expected: string) => {
    // Clear the real clipboard first so a stale copy cannot satisfy the check.
    await page.evaluate(() => navigator.clipboard.writeText('e2e-clipboard-empty'));
    await copy().click({ timeout: UI_READY });
    await expect.poll(() => page.evaluate(async value => (await navigator.clipboard.readText()) === value, expected), { timeout: UI_READY }).toBe(true);
  };
  await test.step('open the real SDK template without issuance', async () => {
    await page.goto(PAGE, { waitUntil: 'domcontentloaded' });
    // KnowledgeBaseView initially mounts the list, then hides it after the
    // real empty response. Wait for HelpKnowledgeBase before choosing its CTA,
    // otherwise a click can retain the now-hidden list's same-named button.
    await expect(page.getByText('Steps to create knowledge base', { exact: true })).toBeVisible({ timeout: UI_READY });
    // Knowledge Base is OSS baseline in tfc/capabilities/registry.py. A 402 or
    // disabled button fails here; this flow never bypasses the capability gate.
    const templateResponse = page.waitForResponse(r => new URL(r.url()).pathname === TEMPLATE && r.request().method() === 'GET', { timeout: UI_READY });
    await page.getByRole('button', { name: 'Create Knowledge Base', exact: true }).click({ timeout: UI_READY });
    const response = await templateResponse;
    expect(response.status()).toBe(200);
    template = (await response.json()).result.code;
    expect(typeof template).toBe('string');
    expect(/<?YOUR_(?:FI_)?API_KEY>?|<your_api_key>/.test(template)).toBe(true);
    expect(/<?YOUR_(?:FI_)?SECRET_KEY>?|<your_secret_key>/.test(template)).toBe(true);
    await page.getByRole('tab', { name: 'Import from SDK', exact: true }).click();
    await expect(page.getByText('Use your personal API key. The example keeps placeholders until you create one.', { exact: true })).toBeVisible({ timeout: UI_READY });
    await copiedEquals(template);
    expect(traffic).toEqual([]);
    expect(await rowIds()).toEqual(before);
  }, { timeout: UI_READY });
  await test.step('submit once and save the show-once pair', async () => {
    await page.getByRole('button', { name: 'Create a key', exact: true }).click();
    const dialog = page.getByRole('dialog', { name: 'Create a personal key' });
    await expect(dialog.getByLabel('Key name')).toHaveValue(/^Personal key, /, { timeout: UI_READY });
    await dialog.getByLabel('Key name').fill(name);
    expect(traffic).toEqual([]);
    expect(await rowIds()).toEqual(before);
    const pending = page.waitForResponse(r => new URL(r.url()).pathname === CREATE && r.request().method() === 'POST', { timeout: UI_READY });
    await dialog.getByRole('button', { name: 'Create a key', exact: true }).click();
    const response = await pending;
    expect(response.status()).toBe(200);
    expect(response.request().postDataJSON()).toEqual({ key_name: name, workspace_id: null });
    pair = (await response.json()).result;
    expect(/^[0-9a-f]{32}$/i.test(pair.api_key) && /^[0-9a-f]{32}$/i.test(pair.secret_key)).toBe(true);
    await testInfo.attach('issued', { contentType: 'application/json', body: JSON.stringify({ keyId: pair.key_id, name }) });
    const saved = page.getByRole('dialog', { name: 'Save your personal key' });
    await expect(saved).toBeVisible({ timeout: UI_READY });
    expect(await saved.getByLabel('API key', { exact: true }).inputValue() === pair.api_key).toBe(true);
    expect(await saved.getByLabel('Secret key', { exact: true }).inputValue() === pair.secret_key).toBe(true);
    await saved.getByRole('button', { name: 'I saved it', exact: true }).click();
    await expect(saved).toHaveCount(0, { timeout: UI_READY });
    expect(traffic).toEqual([CREATE]);
  }, { timeout: UI_READY });
  await test.step('verify the exact stored and masked identity', async () => {
    expect(await rowIds()).toEqual([...before, pair.key_id].sort());
    expect(await probe.pg('SELECT id, name, user_id, organization_id, workspace_id, type, enabled, deleted FROM accounts_orgapikey WHERE id=$1', [pair.key_id])).toEqual([{ id: pair.key_id, name, user_id: info.id, organization_id: actor.organizationId, workspace_id: null, type: 'user', enabled: true, deleted: false }]);
    const listed = await actor.api.get<{ result: { table: { id: string; key_name: string; type: string; api_key: string; secret_key: string }[] } }>(LIST, { search: name });
    expect(listed.result.table.map(r => [r.id, r.key_name, r.type])).toEqual([[pair.key_id, name, 'user']]);
    const row = listed.result.table[0];
    expect(row.api_key === pair.masked_api_key && row.secret_key === pair.masked_secret_key).toBe(true);
    expect(/^.{4}\*{10}.{4}$/.test(row.api_key) && /^.{4}\*{10}.{4}$/.test(row.secret_key)).toBe(true);
  }, { timeout: UI_READY });
  await test.step('copy the filled example then forget it', async () => {
    // Independent expected text from the actual server template, not editor state.
    const filled = template.replace(/<YOUR_(?:FI_)?API_KEY>|YOUR_(?:FI_)?API_KEY|<your_api_key>/g, pair.api_key).replace(/<YOUR_(?:FI_)?SECRET_KEY>|YOUR_(?:FI_)?SECRET_KEY|<your_secret_key>/g, pair.secret_key);
    expect(filled.includes(pair.api_key) && filled.includes(pair.secret_key)).toBe(true);
    await copiedEquals(filled);
    await page.getByRole('button', { name: 'Forget key', exact: true }).click();
    await copiedEquals(template);
    expect(await page.evaluate(p => {
      const storage = JSON.stringify(localStorage) + JSON.stringify(sessionStorage);
      const rendered = document.body.innerText + Array.from(document.querySelectorAll('input,textarea')).map(n => (n as HTMLInputElement).value).join('\n');
      return ![storage, rendered].some(text => text.includes(p.api_key) || text.includes(p.secret_key));
    }, pair)).toBe(true);
  }, { timeout: UI_READY });
  await test.step('close and reopen with placeholders only', async () => {
    // Drawer onClose is the native Escape action; no dirty upload/name fields.
    await page.keyboard.press('Escape');
    await expect(page.getByRole('tab', { name: 'Import from SDK', exact: true })).toHaveCount(0, { timeout: UI_READY });
    await page.getByRole('button', { name: 'Create Knowledge Base', exact: true }).click();
    await page.getByRole('tab', { name: 'Import from SDK', exact: true }).click();
    await copiedEquals(template);
    await expect(page.getByRole('button', { name: 'Create a key', exact: true })).toBeVisible({ timeout: UI_READY });
    expect(await page.evaluate(p => {
      const storage = JSON.stringify(localStorage) + JSON.stringify(sessionStorage);
      return !storage.includes(p.api_key) && !storage.includes(p.secret_key);
    }, pair)).toBe(true);
    expect(traffic).toEqual([CREATE]);
    expect(await rowIds()).toEqual([...before, pair.key_id].sort());
  }, { timeout: UI_READY });
});
