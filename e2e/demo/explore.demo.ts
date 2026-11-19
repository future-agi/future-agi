import { readFileSync, mkdirSync, writeFileSync } from 'node:fs';
import { test } from '@playwright/test';
import { authInitScript } from '../lib/auth';

// Rehearsal helper: screenshot the pages the walkthrough visits for one prepared org.
test('explore', async ({ browser }) => {
  const state = JSON.parse(readFileSync(process.env.DEMO_STATE!, 'utf8'));
  const dir = process.env.DEMO_SHOTS!; mkdirSync(dir, { recursive: true });
  const a = process.env.DEMO_AS === 'owner' ? state.owner : state.author;
  const context = await browser.newContext({ viewport: { width: 1440, height: 900 }, ignoreHTTPSErrors: true });
  await context.addInitScript(authInitScript, { access: a.tokens.access, refresh: a.tokens.refresh, organizationId: a.organizationId, workspaceId: a.workspaceId });
  await context.addInitScript((orgId) => sessionStorage.setItem('workspaceOrgId', orgId), a.organizationId);
  await context.grantPermissions(['clipboard-read', 'clipboard-write']);
  const page = await context.newPage();
  // Screenshot plus the accessibility tree (role/name text), which is what the walkthrough's locators use.
  const shot = async (name: string) => { await page.waitForTimeout(Number(process.env.DEMO_SETTLE_MS ?? 2500)); await page.screenshot({ path: `${dir}/${name}.png` });
    writeFileSync(`${dir}/${name}.aria.txt`, await page.locator('body').ariaSnapshot().catch((e) => String(e))); };
  const refund = state.skills['refund-policy'].id;
  const pages: [string, string][] = (process.env.DEMO_PAGES ?? '').split(',').filter(Boolean).map((p) => [p.slice(0, p.indexOf('=')), p.slice(p.indexOf('=') + 1)] as [string, string]);
  const defaults: [string, string][] = [
    ['01-library', '/dashboard/skills'],
    ['02-skill', `/dashboard/skills/${refund}`],
    ['03-editor', `/dashboard/skills/${refund}/edit`],
    ['04-dependents', `/dashboard/skills/${refund}?tab=dependents`],
    ['05-contexts', '/dashboard/skills/contexts'],
    ['06-context', `/dashboard/skills/contexts/${state.support.id}`],
    ['07-context-links', `/dashboard/skills/contexts/${state.support.id}?tab=links`],
    ['08-context-tests', `/dashboard/skills/contexts/${state.support.id}?tab=tests`],
    ['09-context-versions', `/dashboard/skills/contexts/${state.support.id}?tab=versions`],
    ['10-context-use', `/dashboard/skills/contexts/${state.support.id}?tab=use`],
    ['11-skill-use', `/dashboard/skills/${refund}?tab=use`],
  ];
  for (const [name, url] of pages.length ? pages : defaults) {
    await page.goto(url.replace('{refund}', refund).replace('{support}', state.support.id));
    await shot(name);
  }
  await context.close();
});
