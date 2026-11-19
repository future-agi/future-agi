import { randomUUID } from 'node:crypto';
import { test, expect } from '../../lib/scope-actors';
import { E2E } from '../../lib/env';
import { flowAnnotation } from '../../lib/flow-meta';

// frontend/src/routes/paths.js:skills; model_hub/urls_skills_hub.py (archived endpoints, A8).
// Integration-only: it needs the archived-skills backend and regenerated API contracts.
const LIBRARY = '/dashboard/skills';
const SKILLS = '/model-hub/skills/';
const UI_READY = 60_000;

type Envelope<T> = { status: boolean; result: T };
type Label = { name: string; version: number; version_id: string };
type Detail = { id: string; name: string; release_revision: number; latest_version: number; labels: Label[] };
type Version = { id: string; number: number; digest: string };
type Page<T> = { results: T[]; count: number };

test('SKILLS-E2E-014: an admin archives a skill and restores it with its versions and production label', {
  tag: ['@flow'],
  annotation: flowAnnotation({
    id: 'SKILLS-E2E-014', area: 'skills',
    userGoal: 'A workspace admin archives a published skill from the library, finds it under Archived skills and restores it, and the same versions and production label come back',
    steps: [
      'publish a skill with production and archive it from the library row menu',
      'open More, then Archived skills, and review the labels that come back',
      'tick the acknowledgment and restore the skill',
      'open the restored skill from the library',
      'confirm a workspace member cannot list archived skills and has no menu item',
    ],
    backendChecks: [
      'archiving removes the skill from the live library list and lists it under archived skills',
      'restoring makes the skill live again and raises release_revision by exactly one',
      'production points at the same version id and digest after restore',
      'the version count is unchanged by archive and restore',
      'a workspace member gets 403 on the archived list',
    ],
  }),
}, async ({ browser, request, scopeActors }, testInfo) => {
  test.setTimeout(360_000);
  const admin = scopeActors.ownerA;
  const member = scopeActors.member;
  const name = `e2e-archived-${testInfo.workerIndex}-${Date.now().toString(36)}`;
  const api = async <T>(actor: typeof admin, method: string, path: string, data?: unknown): Promise<T> => {
    const response = await request.fetch(`${E2E.apiUrl}${SKILLS}${path}`, {
      method, data, headers: { ...scopeActors.headers(actor), 'Idempotency-Key': randomUUID() },
    });
    const body = await response.json() as Envelope<T>;
    expect(response.ok(), `${method} ${path}: ${response.status()}`).toBe(true);
    return body.result;
  };
  const files = (text: string) => [{ path: 'SKILL.md', content: `---\nname: ${name}\ndescription: Use when answering a synthetic refund question for the archive and restore flow.\n---\n${text}\n` }];

  const first = await api<{ skill: { id: string } }>(admin, 'POST', 'publish/', { files: files('Refund policy v1.'), labels: ['production'], expected_release_revision: 0, acknowledge_warnings: true });
  const id = first.skill.id;
  const before = await api<Detail>(admin, 'GET', `${id}/`);
  await api(admin, 'POST', 'publish/', { files: files('Refund policy v2.'), labels: [], expected_release_revision: before.release_revision, acknowledge_warnings: true });
  const published = await api<Detail>(admin, 'GET', `${id}/`);
  const versionsBefore = await api<Page<Version>>(admin, 'GET', `${id}/versions/`);
  const production = published.labels.find((label) => label.name === 'production');
  expect(production?.version).toBe(1);
  const productionDigest = versionsBefore.results.find((version) => version.id === production?.version_id)?.digest;
  expect(productionDigest).toMatch(/^sha256:[0-9a-f]{64}$/);
  await testInfo.attach('archived-restore-fixture', { contentType: 'application/json', body: JSON.stringify({ id, name, releaseRevision: published.release_revision, versions: versionsBefore.count }) });

  const context = await scopeActors.openContext(browser, admin);
  const page = await context.newPage();
  await page.setViewportSize({ width: 1440, height: 900 });
  page.setDefaultTimeout(UI_READY);

  // 1. Archive from the row menu: it leaves the library.
  await page.goto(`${LIBRARY}?search=${name}`);
  await expect(page.getByRole('link', { name, exact: true })).toBeVisible();
  await page.getByRole('button', { name: `Actions for ${name}` }).click();
  await page.getByRole('menuitem', { name: 'Archive', exact: true }).click();
  const confirm = page.getByRole('dialog');
  await expect(confirm.getByText('You can restore it from More → Archived skills.')).toBeVisible();
  // The open dialog hides the library from the accessibility tree, so wait for the archive itself and the
  // dialog to close before reading the list; otherwise the API read can race the delete.
  const archivedResponse = page.waitForResponse((response) => response.url().endsWith(`${SKILLS}${id}/`) && response.request().method() === 'DELETE');
  await confirm.getByRole('button', { name: 'Archive', exact: true }).click();
  expect((await archivedResponse).ok()).toBe(true);
  await expect(confirm).toBeHidden();
  await expect(page.getByRole('link', { name, exact: true })).toHaveCount(0);
  const live = await api<Page<{ id: string }>>(admin, 'GET', '', undefined);
  expect(live.results.some((skill) => skill.id === id)).toBe(false);

  // 2. More -> Archived skills -> Restore, with the explicit acknowledgment.
  await page.getByRole('button', { name: 'More Skills pages' }).click();
  await page.getByRole('menuitem', { name: 'Archived skills' }).click();
  await expect(page.getByRole('heading', { name: 'Archived skills' })).toBeVisible();
  const row = page.getByRole('row', { name: new RegExp(name) });
  await expect(row.getByText('Production v1')).toBeVisible();
  await row.getByRole('button', { name: `Restore ${name}` }).click();
  const dialog = page.getByRole('dialog');
  await expect(dialog.getByRole('button', { name: 'Restore skill', exact: true })).toBeDisabled();
  await dialog.getByRole('checkbox').check();
  const restored = page.waitForResponse((response) => response.url().endsWith(`${SKILLS}archived/${id}/restore/`) && response.request().method() === 'POST');
  await dialog.getByRole('button', { name: 'Restore skill', exact: true }).click();
  expect((await restored).ok()).toBe(true);
  await expect(page.getByText(`${name} restored`)).toBeVisible();

  // 3. The same skill is live again, one release revision later, with the same production version and digest.
  const after = await api<Detail>(admin, 'GET', `${id}/`);
  expect(after.release_revision).toBe(published.release_revision + 1);
  const afterProduction = after.labels.find((label) => label.name === 'production');
  expect(afterProduction?.version_id).toBe(production?.version_id);
  const versionsAfter = await api<Page<Version>>(admin, 'GET', `${id}/versions/`);
  expect(versionsAfter.count).toBe(versionsBefore.count);
  expect(versionsAfter.results.find((version) => version.id === afterProduction?.version_id)?.digest).toBe(productionDigest);
  await page.getByRole('button', { name: 'Open skill' }).click();
  await expect(page).toHaveURL(new RegExp(`${LIBRARY}/${id}(?:\\?.*)?$`));

  // 4. A member is refused, and the page offers no way in.
  const denied = await scopeActors.send(member, 'GET', `${SKILLS}archived/`);
  expect(denied.status).toBe(403);
  const memberContext = await scopeActors.openContext(browser, member);
  const memberPage = await memberContext.newPage();
  memberPage.setDefaultTimeout(UI_READY);
  await memberPage.goto(LIBRARY);
  await expect(memberPage.getByRole('heading', { name: 'Skills', exact: true })).toBeVisible();
  const more = memberPage.getByRole('button', { name: 'More Skills pages' });
  if (await more.count()) {
    await more.click();
    await expect(memberPage.getByRole('menuitem', { name: 'Archived skills' })).toHaveCount(0);
  }
  await memberPage.goto(`${LIBRARY}/archived`);
  await expect(memberPage.getByText('Only workspace admins can see archived skills.')).toBeVisible();
});
