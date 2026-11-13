import { randomUUID } from 'node:crypto';
import { readFileSync, writeFileSync, mkdirSync } from 'node:fs';
import { test, expect } from '@playwright/test';
import type { Locator, Page } from '@playwright/test';
import { E2E } from '../lib/env';
import { sendTrace } from '../lib/otlp';
import { SkillsFixtures } from '../lib/skills-fixtures';
import type { FixtureRule } from '../lib/skills-fixtures';
import { overlayInit, setCaption, setCaptionTop, cardHtml } from './overlay';
// @ts-expect-error plain ESM content module
import { REFUND_V2, V1_PHRASE, V2_PHRASE, FAKE_SECRET_LINE, REQUESTS } from './content.mjs';

// Recorded product walkthrough. Prerequisites come from demo/setup.mjs (public APIs only);
// everything on camera is the real UI. Env: DEMO_STATE, DEMO_OUT, DEMO_FAST=1 (rehearsal).
const FAST = process.env.DEMO_FAST === '1';
const OUT = process.env.DEMO_OUT!;
const ONLY = (process.env.DEMO_CHAPTERS ?? '1,2,3,4,5,6,7,8').split(',').map(Number);
const S = '/model-hub/skills/';
const NATIVE_NAMESPACE = 'e2e-skills-native-runtime-fixture'; // runtime container's synthetic provider credential (never shown)

type Actor = { tokens: { access: string; refresh: string }; organizationId: string; workspaceId: string; userId: string; apiKey?: string; secretKey?: string };

test('skills walkthrough', async ({ browser, request }) => {
  const state = JSON.parse(readFileSync(process.env.DEMO_STATE!, 'utf8'));
  mkdirSync(`${OUT}/shots`, { recursive: true });
  const maya: Actor = state.author; const priya: Actor = state.owner;
  const ids = { refund: state.skills['refund-policy'].id, chargeback: state.skills['chargeback-handling'].id, shipping: state.skills['shipping-delays'].id, support: state.support.id };
  const events: { t: number; kind: string; label?: string; chapter?: number; title?: string }[] = [];

  const api = async (actor: Actor, method: string, path: string, data?: unknown) => {
    const response = await request.fetch(`${E2E.apiUrl}${S}${path}`, { method, data, headers: {
      Authorization: `Bearer ${actor.tokens.access}`, 'X-Organization-Id': actor.organizationId, 'X-Workspace-Id': actor.workspaceId, 'Idempotency-Key': randomUUID() } });
    const body = await response.json().catch(() => ({}));
    expect(response.ok(), JSON.stringify({ method, path, status: response.status(), body }).slice(0, 800)).toBeTruthy();
    return body.result;
  };

  // ---- Off-camera prerequisites: deterministic model fixtures and one retained production trace.
  const wholeContext = new SkillsFixtures(state.providerNamespace);
  const say = (request: string, turn: number, content: string, tool_text?: string): FixtureRule => ({ match: { request, turn, ...(tool_text ? { tool_text } : {}) }, response: { message: { role: 'assistant', content }, finish_reason: 'stop' } });
  const tool = (request: string, turn: number, name: string, args: unknown, tool_text?: string): FixtureRule => ({ match: { request, turn, ...(tool_text ? { tool_text } : {}) },
    response: { message: { role: 'assistant', content: null, tool_calls: [{ id: `call-${turn}-${name}`, type: 'function', function: { name, arguments: args } }] }, finish_reason: 'tool_calls' } });
  await wholeContext.dispose().catch(() => undefined);
  await wholeContext.register([
    tool(REQUESTS.refund, 0, 'load_skill', { skill_id: { $skill_named: 'refund-policy' } }),
    say(REQUESTS.refund, 1, 'Refunded $25 for the cracked mug. Shipping is not refundable. A receipt follows by email.', V2_PHRASE),
    say(REQUESTS.refund, 1, 'Refunded $30 including shipping. A receipt follows by email.', V1_PHRASE),
  ]);
  const native = new SkillsFixtures(NATIVE_NAMESPACE);
  native.keep = true;
  await native.dispose().catch(() => undefined);
  const R = REQUESTS;
  await native.register([
    tool(R.refund, 0, 'load_skill', { skill_id: ids.refund }),
    tool(R.refund, 1, 'issue_refund', { amount: 25 }, V2_PHRASE),
    tool(R.refund, 1, 'issue_refund', { amount: 30 }, V1_PHRASE),
    say(R.refund, 2, 'Refund of $25 issued for the cracked mug. Your receipt is on its way.', '"status":"applied"'),
    say(R.refund, 2, 'Your refund has been submitted and a receipt follows by email.', '"status":"declined"'),
    tool(R.chargeback, 0, 'load_skill', { skill_id: ids.chargeback }),
    tool(R.chargeback, 1, 'open_chargeback', {}),
    say(R.chargeback, 2, 'A chargeback has been opened for this charge.', '"action":"open_chargeback"'),
    tool(R.shipping, 0, 'load_skill', { skill_id: ids.shipping }),
    tool(R.shipping, 1, 'track_shipment', {}),
    say(R.shipping, 2, 'Your package is in transit and tracking was updated.', '"action":"track_shipment"'),
    tool(R.captured, 0, 'load_skill', { skill_id: ids.refund }),
    tool(R.captured, 1, 'issue_refund', { amount: 24 }, V2_PHRASE),
    tool(R.captured, 1, 'issue_refund', { amount: 29 }, V1_PHRASE),
    say(R.captured, 2, 'Refund of $24 issued for the blender jug. Your receipt is on its way.', '"status":"applied"'),
    say(R.captured, 2, 'Done! Your refund of $29 including shipping is on its way, with a receipt by email.', '"status":"declined"'),
  ]);
  const projectName = 'acme-support-production';
  if (ONLY.includes(6)) {
    const v1 = state.support.version;
    const trace = await sendTrace(request, { collectorUrl: E2E.collectorUrl, apiKey: priya.apiKey!, secretKey: priya.secretKey!, projectName,
      rootName: 'Refund request: cracked blender jug', childName: 'chat.completion', scope: { name: 'acme-support-agent', version: '1.4.0' },
      rootAttributes: { 'input.value': JSON.stringify({ messages: [
        { role: 'system', content: 'You are the Acme Support agent.' },
        { role: 'user', content: REQUESTS.captured },
        { role: 'tool', content: JSON.stringify({ 'support-order': { age_days: 6, amount: 24, disputed: false } }) },
        { role: 'assistant', content: 'Done! I refunded $29 including shipping to your card.' }] }),
        'output.value': 'Done! I refunded $29 including shipping to your card.',
        'fi.context.id': ids.support, 'fi.context.version_id': v1.id, 'fi.context.digest': v1.digest } });
    await expect.poll(async () => {
      const r = await fetch(`${E2E.chUrl}/?query=${encodeURIComponent(`SELECT count() FROM spans FINAL WHERE trace_id='${trace.traceId}' FORMAT TabSeparated`)}`, { headers: { 'X-ClickHouse-User': 'default', 'X-ClickHouse-Key': E2E.chPassword } });
      return (await r.text()).trim();
    }, { timeout: 180_000, intervals: [2000] }).toBe('2');
  }

  // ---- One browser context and one page for the whole recording.
  const context = await browser.newContext({ baseURL: E2E.appUrl, viewport: { width: 1440, height: 900 }, ignoreHTTPSErrors: true,
    recordVideo: { dir: `${OUT}/video`, size: { width: 1440, height: 900 } } });
  await context.grantPermissions(['clipboard-read', 'clipboard-write']);
  await context.addInitScript(({ seeds }) => {
    try {
      const who = sessionStorage.getItem('__demo_as') || 'maya';
      if (sessionStorage.getItem('__demo_seeded') !== who) {
        const s = seeds[who];
        localStorage.setItem('accessToken', s.access); localStorage.setItem('refreshToken', s.refresh); localStorage.setItem('rememberMe', 'true');
        sessionStorage.setItem('organizationId', s.organizationId); sessionStorage.setItem('workspaceId', s.workspaceId); sessionStorage.setItem('workspaceOrgId', s.organizationId);
        sessionStorage.setItem('__demo_seeded', who);
      }
    } catch { /* storage unavailable on this document */ }
  }, { seeds: Object.fromEntries(Object.entries({ maya, priya }).map(([k, a]) => [k, { access: a.tokens.access, refresh: a.tokens.refresh, organizationId: a.organizationId, workspaceId: a.workspaceId }])) });
  await context.addInitScript(overlayInit);
  await context.route('**/__demo/card/*', async (route) => {
    const kind = new URL(route.request().url()).pathname.split('/').pop();
    const html = kind === 'title'
      ? cardHtml('Skills + Agent contexts', 'Product walkthrough: Acme Support builds, tests and releases a support agent.', 'Demo workspace with sample data')
      : cardHtml('Skills + Agent contexts', 'Versioned skills, locked agent contexts, tests against your application runtime, reviewed releases.', 'Demo workspace with sample data');
    await route.fulfill({ status: 200, contentType: 'text/html', body: html });
  });
  const page = await context.newPage();
  const t0 = Date.now();
  page.setDefaultTimeout(60_000);
  const mark = (kind: string, extra: Record<string, unknown> = {}) => events.push({ t: (Date.now() - t0) / 1000, kind, ...extra });
  let shotN = 0;
  const shot = async (name: string) => { if (FAST) await page.screenshot({ path: `${OUT}/shots/${String(++shotN).padStart(3, '0')}-${name}.png` }); };
  const dwell = (ms = 1500) => page.waitForTimeout(FAST ? Math.min(ms, 250) : ms);
  let chip = '';
  // The caption stays up while the next actions run, so its own dwell is short.
  // On-screen audit: any loopback address or test-infrastructure word visible at a caption is logged to events.json.
  const audit = async (at: string) => {
    const hits = await page.evaluate(() => (document.body.innerText.match(/.{0,40}(localhost|127\.0\.0\.1|e2e|fixture|ctxhub|playwright).{0,40}/gi) ?? []).slice(0, 5)).catch(() => []);
    if (hits.length) mark('audit', { label: `${at} :: ${hits.join(' | ')}` });
  };
  const caption = async (text: string, ms = 1800) => { if (Date.now() - lastGlide > 5000) await setCaptionTop(page, false); await setCaption(page, chip, text); mark('caption', { label: text }); await audit(text); await dwell(Math.max(1000, ms * 0.5)); await shot(text.slice(0, 40).replace(/[^a-z0-9]+/gi, '-')); };
  const chapter = async (n: number, title: string, first: string) => { chip = `${n} · ${title}`; mark('chapter', { chapter: n, title }); await caption(first, 2200); };
  const center = async (l: Locator) => {
    await l.waitFor({ state: 'visible' });
    await l.evaluate((el) => el.scrollIntoView({ behavior: 'smooth', block: 'center' }));
    await page.waitForTimeout(FAST ? 150 : 450);
  };
  let lastGlide = 0;
  const glide = async (l: Locator) => {
    lastGlide = Date.now();
    await center(l);
    const box = await l.boundingBox();
    // Keep the caption off the thing being shown: it moves to the top while the target is in the bottom band.
    if (box) await setCaptionTop(page, box.y + box.height > 900 - 140);
    if (box) await page.mouse.move(box.x + box.width / 2, box.y + box.height / 2, { steps: FAST ? 3 : 12 });
    await page.waitForTimeout(FAST ? 50 : 180);
  };
  const click = async (l: Locator, after = 1200) => { await glide(l); await l.click(); await dwell(after * 0.55); };
  // Fields are filled in one step (like a paste); character-by-character typing was dead time on camera.
  const typeInto = async (l: Locator, text: string) => { await glide(l); await l.click(); await l.fill(text); await dwell(350); };
  const hide = async (fn: () => Promise<unknown>) => { mark('hide-start'); await fn(); mark('hide-end'); };
  const scrollTo = async (l: Locator, after = 900) => { await center(l); await dwell(after * 0.6); };
  // Page loads are marked so the edit can drop skeleton/blank frames; ready = first content the viewer should see.
  const loading = async (ready: Locator, fn: () => Promise<unknown>) => { mark('load-start'); await fn(); await ready.first().waitFor({ state: 'visible', timeout: 90_000 }); await page.waitForTimeout(400); mark('load-end'); };
  const goto = async (url: string, ready: Locator, next?: string) => {
    // The caption for the next page is set before leaving, so the new page never opens under the previous caption.
    if (next) { mark('caption', { label: next }); await setCaption(page, chip, next); }
    await loading(ready, async () => { await page.goto(url); await page.waitForLoadState('domcontentloaded'); });
    await setCaption(page, chip, events.filter((e) => e.kind === 'caption').at(-1)?.label ?? ''); };
  const waitCut = async (label: string, fn: () => Promise<void>) => { mark('cut-start', { label }); await fn(); mark('cut-end', { label }); };
  // The result card asks for human reviews while the run is still running (409 until terminal) and does not ask again,
  // so the finished card can show "Couldn't load human reviews". Reloading inside the cut shows the settled result.
  const refreshResult = async () => {
    if (await page.getByText("Couldn't load human reviews", { exact: true }).count()) {
      mark('note', { label: 'reloaded the finished result (human-review request had raced the running job)' });
      await page.reload(); await page.waitForLoadState('domcontentloaded');
      await page.getByText(/Application checks passed for these cases|Repair verified:|Repair not verified:|Hold this release/).first().waitFor({ timeout: 90_000 });
      await expect(page.getByText("Couldn't load human reviews", { exact: true })).toHaveCount(0, { timeout: 30_000 });
      await page.waitForTimeout(3000); // longer than the 2 s tail postprocess.py keeps before a cut ends
    }
  };
  const as = async (who: 'maya' | 'priya', url: string, ready: Locator) => { await page.evaluate((w) => sessionStorage.setItem('__demo_as', w), who); await goto(url, ready); };

  try {
    // ---------------- Title card
    await page.goto('/__demo/card/title');
    mark('title');
    await page.waitForTimeout(FAST ? 300 : 2800);

    // ---------------- 1. Skills library and a skill in the editor
    if (ONLY.includes(1)) {
      await goto('/dashboard/skills', page.getByRole('cell').getByRole('link', { name: 'refund-policy', exact: true }));
      await expect(page.getByRole('cell').getByRole('link', { name: 'refund-policy', exact: true })).toBeVisible();
      await chapter(1, 'Skills library', 'Acme Support keeps five versioned skills in one workspace library.');
      await glide(page.getByRole('cell').getByRole('link', { name: 'refund-policy', exact: true }));
      await caption('Each skill is a SKILL.md plus reference files, with a production label.', 1600);
      await glide(page.getByRole('cell').getByRole('link', { name: 'refund-policy', exact: true }));
      await loading(page.getByRole('link', { name: 'Edit latest v1', exact: true }).or(page.getByRole('button', { name: 'Edit latest v1', exact: true })), () => page.getByRole('cell').getByRole('link', { name: 'refund-policy', exact: true }).click());
      await dwell(800);
      await caption('refund-policy is live in production at v1.', 1400);
      const edit = page.getByRole('link', { name: 'Edit latest v1', exact: true }).or(page.getByRole('button', { name: 'Edit latest v1', exact: true })).first();
      await glide(edit);
      await loading(page.getByRole('treeitem', { name: 'SKILL.md', exact: true }), () => edit.click());
      await dwell(800);
      await expect(page.getByRole('treeitem', { name: 'SKILL.md', exact: true })).toBeVisible();
      await caption('The editor shows every file in the skill, including its references.', 1500);
      await click(page.getByRole('treeitem', { name: 'refund-windows.md', exact: true }), 1600);
      await click(page.getByRole('treeitem', { name: 'SKILL.md', exact: true }), 800);
      await click(page.getByRole('tablist', { name: 'Skill document view' }).getByRole('tab', { name: 'Source', exact: true }), 800);
      await caption('Maya updates the policy: refund the item price only, with a 25 USD cap.', 1200);
      const editor = page.getByRole('textbox', { name: /Editor content/ });
      await editor.focus(); await editor.press('Control+a');
      await page.evaluate((text) => navigator.clipboard.writeText(text), REFUND_V2);
      await editor.press('Control+v');
      await dwell(1800);
      await caption('Live checks run on every edit. Here she pastes a test credential by mistake.', 900);
      await editor.press('Control+End');
      await editor.press('Enter');
      await page.keyboard.type(FAKE_SECRET_LINE, { delay: FAST ? 0 : 18 });
      await page.keyboard.press('Escape');
      const warnings = page.getByRole('region', { name: 'Warnings', exact: true });
      await expect(warnings.getByText(/possible .* was found/)).toBeVisible();
      await expect(page.getByText(/Checking|Running checks/)).toHaveCount(0, { timeout: 30_000 }).catch(() => undefined);
      await scrollTo(warnings, 600);
      await expect(warnings.getByText(/possible .* was found/)).toBeVisible();
      await glide(warnings.getByText(/possible .* was found/));
      await caption('A possible secret is flagged before anything is published (the value is fake).', 2400);
      await dwell(1800); // the rendered Warning row stays on screen for over 3 s
      await editor.press('Shift+Home'); await editor.press('Backspace'); await editor.press('Backspace');
      await expect(warnings).not.toBeVisible();
      await expect(page.getByText('Saved', { exact: true })).toBeVisible();
      await expect(page.getByText('No issues found.', { exact: true })).toBeVisible();
      await caption('She removes the line; the warning clears and the draft autosaves.', 2000);
      const draft = await api(maya, 'GET', `${ids.refund}/draft/`);
      expect(draft.files.find((f: { path: string }) => f.path === 'SKILL.md').content).toBe(REFUND_V2);
    }

    // ---------------- 2. Publish, compare, move production, history
    if (ONLY.includes(2)) {
      await chapter(2, 'Versions and labels', 'Publishing creates an immutable v2. Production stays on v1 until moved.');
      await click(page.getByRole('button', { name: 'Publish', exact: true }), 900);
      const dialog = page.getByRole('dialog');
      await typeInto(dialog.getByRole('textbox', { name: 'Version note' }), 'Refund item price only; cap at 25 USD');
      const publishButton = dialog.getByRole('button', { name: 'Publish', exact: true });
      await glide(publishButton);
      await loading(page.getByRole('tabpanel', { name: 'Files' }).getByRole('treeitem', { name: 'SKILL.md', exact: true }), () => publishButton.click());
      await expect(page).toHaveURL(new RegExp(`/dashboard/skills/${ids.refund}(\\?.*)?$`));
      await caption('v2 is published. The production label still points at v1.', 1800);
      await click(page.getByRole('tab', { name: 'Versions', exact: true }), 1200);
      await click(page.getByRole('button', { name: 'Actions for v2', exact: true }), 700);
      await glide(page.getByRole('menuitem', { name: 'Compare with previous', exact: true }));
      await loading(page.getByRole('list', { name: 'Compared files' }), () => page.getByRole('menuitem', { name: 'Compare with previous', exact: true }).click());
      await expect(page.getByRole('list', { name: 'Compared files' })).toBeVisible();
      await caption('Compare shows exactly which files changed between v1 and v2.', 1400);
      await click(page.getByRole('list', { name: 'Compared files' }).getByRole('button', { name: /SKILL\.md/ }), 2000);
      await goto(`/dashboard/skills/${ids.refund}?tab=versions`, page.getByRole('button', { name: 'Actions for production', exact: true }));
      await caption('Promotion is a separate, explicit step.', 1000);
      await click(page.getByRole('button', { name: 'Actions for production', exact: true }), 600);
      await click(page.getByRole('menuitem', { name: 'Move', exact: true }), 900);
      const move = page.getByRole('dialog');
      await click(move.getByRole('combobox', { name: /^Version(?:\s|$)/ }), 600);
      await click(page.getByRole('option', { name: 'v2', exact: true }), 600);
      await typeInto(move.getByRole('textbox', { name: 'Note (optional)' }), 'Approved by support ops');
      await click(move.getByRole('button', { name: 'Move production from v1 to v2', exact: true }), 1500);
      await expect(move).not.toBeVisible();
      await page.getByText(/production\s*·\s*v2/).first().waitFor({ timeout: 15_000 }).catch(() => undefined);
      await dwell(900);
      await caption('Production now serves v2.', 1400);
      await click(page.getByRole('button', { name: 'Actions for production', exact: true }), 500);
      await click(page.getByRole('menuitem', { name: 'History', exact: true }), 600);
      await expect(page.getByText('Approved by support ops').first()).toBeVisible({ timeout: 30_000 });
      await caption('Label history records every move, who made it and why.', 2600);
      await dwell(600);
      await click(page.getByRole('button', { name: 'Close label history' }), 800);
    }

    // ---------------- 3. Agent context
    if (ONLY.includes(3)) {
      await goto(`/dashboard/skills/contexts`, page.getByRole('table', { name: 'Agent contexts' }).getByRole('button', { name: 'support-agent', exact: true }));
      const open = page.getByRole('table', { name: 'Agent contexts' }).getByRole('button', { name: 'support-agent', exact: true });
      await expect(open).toBeVisible();
      await chapter(3, 'Agent context', 'An agent context bundles standing instructions with linked skills.');
      await glide(open);
      await loading(page.getByRole('tab', { name: 'Linked skills', exact: true }), () => open.click());
      await dwell(800);
      await expect(page.getByRole('tab', { name: 'Linked skills', exact: true })).toBeVisible();
      await caption('support-agent: AGENTS.md is the standing instruction file.', 3600);
      await click(page.getByRole('tab', { name: 'Linked skills', exact: true }), 1400);
      await caption('Four skills follow production; escalation-tone is pinned to v1 on purpose.', 3600);
      await glide(page.getByText('skills/escalation-tone/', { exact: true }));
      await dwell(1400);
      const refundLock = page.getByRole('row').filter({ hasText: 'skills/refund-policy/' }).getByRole('button', { name: 'Lock details', exact: true });
      await click(refundLock, 1200);
      await glide(page.getByRole('row').filter({ hasText: 'skills/refund-policy/' }).getByText(/^[0-9a-f]{12}…$/).first());
      await caption('The resolved lock records the exact version and digest of every skill.', 3600);
      await dwell(800);
      await glide(page.getByRole('row').filter({ hasText: 'skills/refund-policy/' }).getByText('v2 available'));
      await caption('refund-policy v2 is available, but the context stays on v1 until someone accepts the change.', 3600);
    }

    // ---------------- 4. Upstream change: dependents, impact preview, accept
    if (ONLY.includes(4)) {
      await goto(`/dashboard/skills/${ids.refund}?tab=dependents`, page.getByRole('table', { name: 'Published context dependents' }));
      const table = page.getByRole('table', { name: 'Published context dependents' });
      await expect(table).toBeVisible();
      await chapter(4, 'Upstream change', 'refund-policy v2 is in production. Which contexts depend on it?');
      await scrollTo(table, 600);
      await caption('support-agent follows production and has an update; returns-desk pins v1 and stays put.', 2600);
      await click(page.getByRole('button', { name: 'Prepare draft updates for 1 context', exact: true }), 1800);
      const impact = page.getByText(/refund-policy v1 → v2 in support-agent/);
      await expect(impact).toBeVisible();
      await scrollTo(impact, 600);
      await caption('The impact preview shows the exact lock change, v1 to v2, before anything moves.', 2400);
      await click(page.getByRole('checkbox', { name: /^Accept these exact resolved versions and selector policies/ }), 700);
      await click(page.getByRole('button', { name: 'Apply selected updates to 1 context draft', exact: true }), 1600);
      await expect(page.getByText('Draft updated for 1 context. Production unchanged.', { exact: true })).toBeVisible();
      await caption('Accepting writes a draft. The published context is unchanged until reviewed.', 1800);
      await glide(page.getByRole('link', { name: 'Open draft for support-agent' }).or(page.getByRole('button', { name: 'Open draft for support-agent' })).first());
      await loading(page.getByRole('button', { name: 'Review changes', exact: true }), () => page.getByRole('link', { name: 'Open draft for support-agent' }).or(page.getByRole('button', { name: 'Open draft for support-agent' })).first().click());
      await expect(page.getByRole('button', { name: 'Review changes', exact: true })).toBeVisible();
      await expect(page.getByText(/Loading linked and generated files/)).toHaveCount(0, { timeout: 60_000 });
      await glide(page.getByText('skills/refund-policy/', { exact: true }).first());
      await caption('The draft now locks refund-policy v2. Review the change before publishing it.', 2200);
      await glide(page.getByRole('button', { name: 'Review changes', exact: true }));
      await loading(page.locator('#review-dependencies'), () => page.getByRole('button', { name: 'Review changes', exact: true }).click());
      const deps = page.locator('#review-dependencies');
      await scrollTo(deps, 600);
      await caption('The accepted lock: refund-policy v2; escalation-tone stays pinned at v1.', 2800);
      const reviewed = page.getByRole('checkbox', { name: 'I reviewed the saved candidate against its content origin and current publication head.', exact: true });
      const warning = page.getByRole('checkbox', { name: 'I acknowledge the static warnings for this exact candidate.', exact: true });
      await click(reviewed, 500);
      if (await warning.count()) await click(warning, 500);
      await click(page.getByRole('button', { name: 'Acknowledge reviewed head', exact: true }), 1200);
      await expect(page.getByRole('button', { name: 'Publish version', exact: true })).toBeVisible();
      await click(reviewed, 400);
      if (await warning.count()) await click(warning, 400);
      await click(page.getByRole('button', { name: 'Publish version', exact: true }), 1500);
      await expect.poll(async () => (await api(maya, 'GET', `contexts/${ids.support}/`)).context.latest_version).toBe(2);
      await caption('support-agent v2 is published. Production still serves v1.', 1800);
    }

    // ---------------- 5. Tests
    if (ONLY.includes(5)) {
      const answers = page.getByRole('radio', { name: /^Check answers with this context/ });
      await goto(`/dashboard/skills/contexts/${ids.support}?version=2&tab=tests`, answers);
      await chapter(5, 'Tests', 'Before release, test the whole context: AGENTS.md and the locked skills together.');
      await click(answers, 900);
      await typeInto(page.getByRole('textbox', { name: 'Test request', exact: true }), REQUESTS.refund);
      await typeInto(page.getByRole('textbox', { name: 'Expected answer contains', exact: true }), '$25');
      await click(page.getByRole('checkbox', { name: 'I reviewed this request and its expected answer.', exact: true }), 400);
      await click(page.getByRole('combobox', { name: /^Task model/ }), 500);
      await click(page.getByRole('option', { name: 'gpt-4o-mini', exact: true }), 400);
      await typeInto(page.getByRole('textbox', { name: 'Maximum estimated cost (USD)', exact: true }), '2');
      await typeInto(page.getByRole('textbox', { name: 'Historical reference version (optional)', exact: true }), '1');
      await caption('Candidate v2 runs against v1 as the reference.', 900);
      await click(page.getByRole('button', { name: 'Preview whole-context test', exact: true }), 1500);
      for (const name of ['I know of no bundled answer keys or evaluation material', 'I reviewed the entire inventory and outbound inputs', 'I acknowledge provider processing of this reviewed context and test request.']) {
        const box = page.getByRole('checkbox', { name, exact: true });
        if (await box.count()) await click(box, 300);
      }
      await click(page.getByRole('button', { name: 'Run whole-context test', exact: true }), 800);
      await caption('Running the whole-context test…', 600);
      await waitCut('whole-context-run', async () => { await expect(page.getByText(/^Answer: (pass|fail)$/).first()).toBeVisible({ timeout: 240_000 }); });
      await setCaption(page, chip, 'v2 answers with the capped $25 refund and passes; the v1 reference fails the same check.');
      const result = page.getByText('Whole-context result', { exact: true });
      await scrollTo(result, 600);
      await caption('v2 answers with the capped $25 refund and passes; the v1 reference fails the same check.', 3200);

      // Application verification
      const verify = page.getByRole('radio', { name: /^Verify on the application/ });
      await scrollTo(verify, 400);
      await click(verify, 900);
      const target = page.getByText('Application target', { exact: true });
      await scrollTo(target, 600);
      await caption('Now verify on the real application: the same cases, run with each context version.', 1600);
      await click(page.getByRole('button', { name: 'Add application target' }), 1000);
      await glide(page.getByRole('button', { name: 'Review runtime configuration' }));
      // The pin dialog lists the synthetic application's measured identities (package entrypoint, "fixture_only"
      // side effects). The review-and-pin step is cut from the take and described in chapters.md instead.
      await hide(async () => {
        await page.getByRole('button', { name: 'Review runtime configuration' }).click();
        await expect(page.getByText('Pin the application configuration')).toBeVisible();
        await page.getByRole('button', { name: 'Pin this configuration' }).click();
        await expect(page.getByText('Configuration pinned')).toBeVisible();
        await expect(page.getByLabel(/^Loading/)).toHaveCount(0, { timeout: 30_000 });
        await page.waitForTimeout(400);
      });
      await glide(page.getByText('Configuration pinned').first());
      await caption('The configuration the application reports is pinned, so every version runs on the same setup.', 2000);
      await click(page.getByLabel('Candidate version'), 500); await click(page.getByRole('option', { name: 'v2', exact: true }), 500);
      const prod = page.getByLabel('Production version');
      if (!(await prod.textContent())?.includes('v1')) { await click(prod, 500); await click(page.getByRole('option', { name: 'v1', exact: true }), 500); }
      await scrollTo(page.getByRole('group', { name: 'Examples to run' }), 600);
      await expect(page.getByLabel('Loading test results')).toHaveCount(0, { timeout: 30_000 });
      await caption('Three reviewed cases: a damaged-item refund, a card dispute and a late package.', 2000);
      await click(page.getByRole('checkbox', { name: /I accept up to/ }), 500);
      await click(page.getByRole('button', { name: 'Run on the application', exact: true }), 800);
      await caption('Running candidate v2 and production v1 on the application…', 600);
      await waitCut('native-run', async () => {
        await expect(page.getByText(/Application checks passed for these cases|Repair verified:|Repair not verified:|Hold this release|The run stopped/).first()).toBeVisible({ timeout: 360_000 });
        await refreshResult();
      });
      const shipHold = page.getByText('Ready for release review', { exact: true }).or(page.getByText('Hold this release', { exact: true })).first();
      const verdict = (await shipHold.textContent()) ?? '';
      mark('note', { label: `ch5 ship/hold: ${verdict}` });
      expect(verdict).toBe('Ready for release review');
      await setCaption(page, chip, 'Ship/hold: v2 passes all three cases on the application, so it is ready for release review.');
      await glide(shipHold);
      await caption('Ship/hold: v2 passes all three cases on the application, so it is ready for release review.', 3000);
      await caption('No recorded failure was part of this run, so there is no repair to verify yet. That comes next.', 2600);
      const cells = page.getByRole('table', { name: 'Verification cells' });
      await scrollTo(cells, 600);
      await glide(cells.getByRole('row').filter({ hasText: 'Production v1' }).filter({ hasText: 'Damaged mug refund' }).getByText('Failed', { exact: true }));
      await caption('Per-case grades: production v1 fails the business-tool check on the refund; v2 passes every check.', 3400);
    }

    // ---------------- 6. Capture a failing trace
    if (ONLY.includes(6)) {
      await goto('/dashboard/observe', page.getByText(projectName, { exact: true }));
      await chapter(6, 'Trace capture', 'A production conversation went wrong. Turn its trace into a test case.');
      await glide(page.getByText(projectName, { exact: true }));
      await loading(page.locator('.clean-data-table:visible .ag-row [col-id="trace_name"]').filter({ hasText: 'Refund request: cracked blender jug' }).first(), () => page.getByText(projectName, { exact: true }).click());
      await dwell(800);
      await click(page.locator('.clean-data-table:visible .ag-row [col-id="trace_name"]').filter({ hasText: 'Refund request: cracked blender jug' }).first(), 1800);
      await caption('The agent refunded shipping too, against policy.', 1600);
      await click(page.getByRole('button', { name: 'Create context regression case', exact: true }), 1400);
      const dialog = page.getByRole('dialog');
      await click(dialog.getByRole('button', { name: 'Preview selected text', exact: true }), 1200);
      await caption('The request and the order state come straight from the trace; nothing is retyped.', 1800);
      await typeInto(dialog.getByRole('textbox', { name: 'Example title', exact: true }), 'Blender refund refunded shipping');
      await typeInto(dialog.getByRole('textbox', { name: 'Expected answer must contain', exact: true }), 'receipt');
      // Skill and action are one choice on camera: the half-filled state ("choose both or neither") is cut.
      await click(dialog.getByLabel('Required skill', { exact: true }), 500);
      await hide(async () => {
        await click(page.getByRole('option', { name: /refund-policy$/ }), 300);
        await click(dialog.getByLabel('Required business action', { exact: true }), 300);
      });
      await click(page.getByRole('option', { name: 'Issue a refund', exact: true }), 600);
      await scrollTo(dialog.getByText('Required application behavior'), 400);
      await caption('Choose the skill the app must load and the business action it must take.', 1800);
      await click(dialog.getByRole('checkbox', { name: /confirmed the expected behavior/ }), 400);
      await click(dialog.getByRole('checkbox', { name: /workspace/ }), 400);
      await click(dialog.getByRole('button', { name: 'Save reviewed example', exact: true }), 1500);
      await caption('Saved as a reviewed regression case on support-agent.', 1400);
      await glide(dialog.getByRole('button', { name: 'Reproduce on the application', exact: true }));
      await loading(page.getByRole('group', { name: 'Examples to run' }), () => dialog.getByRole('button', { name: 'Reproduce on the application', exact: true }).click());
      await expect(page).toHaveURL(/reproduce=/);
      // Production is still v1 (the recorded reference) at this point; staging it empty off camera keeps the
      // planner's identical-arms error out of the take. TODO(rehearsal): confirm no error frame survives the cut.
      await hide(async () => {
        const production = page.getByLabel('Production version');
        await production.click(); await page.getByRole('option', { name: /^None/ }).click();
        await page.getByText(/Up to \d+ runs/).waitFor({ timeout: 30_000 });
        await page.waitForTimeout(300);
      });
      const group = page.getByRole('group', { name: 'Examples to run' });
      await scrollTo(group, 600);
      await caption('The captured case is marked as a known failure, with v1 as the reference.', 2600);
      const plan = page.getByText(/Up to \d+ runs/);
      await expect(plan).toBeVisible();
      await scrollTo(plan, 400);
      await caption('The plan: candidate v2 against recorded v1 on the captured case.', 1800);
      await click(page.getByRole('checkbox', { name: /I accept up to/ }), 500);
      await click(page.getByRole('button', { name: 'Run on the application', exact: true }), 800);
      await caption('Reproducing the recorded failure on the application…', 600);
      await waitCut('reproduction run', async () => {
        await expect(page.getByText(/^Repair (not )?verified:|The run stopped/).first()).toBeVisible({ timeout: 360_000 });
        await refreshResult();
      });
      await setCaption(page, chip, 'The failure reproduces on v1 and does not on v2: the repair is verified.');
      const outcome = (await page.getByText(/^Repair (not )?verified:/).first().textContent()) ?? '';
      mark('note', { label: outcome });
      await scrollTo(page.getByText(/^Repair (not )?verified:/).first(), 600);
      expect(outcome).toMatch(/^Repair verified: v2 passed 1 of 1 cases; the recorded failure reproduced on v1\./);
      await caption('The failure reproduces on v1 and does not on v2: the repair is verified.', 3000);
    }

    // ---------------- 7. Review and release
    if (ONLY.includes(7)) {
      if (!ONLY.includes(6)) await goto(`/dashboard/skills/contexts/${ids.support}?version=2&tab=tests`, page.getByText('Release packet', { exact: true }));
      const packet = page.getByText('Release packet', { exact: true });
      await scrollTo(packet, 400);
      await chapter(7, 'Review and release', 'Moving production on a context needs a second person. The verified run goes with the request.');
      await caption('Maya adds her CI record (sample values) and shares this run as evidence.', 1600);
      await typeInto(page.getByRole('textbox', { name: 'CI run link', exact: true }), 'https://ci.acme-support.example/runs/1842');
      await typeInto(page.getByRole('textbox', { name: 'CI configuration ID', exact: true }), 'support-agent-release');
      await typeInto(page.getByRole('textbox', { name: 'CI result (pass or hold)', exact: true }), 'pass');
      await click(page.getByRole('button', { name: 'Share evidence and prepare packet', exact: true }), 1400);
      const proceed = page.getByRole('link', { name: 'Continue to release review', exact: true }).or(page.getByRole('button', { name: 'Continue to release review', exact: true })).first();
      await expect(proceed).toBeVisible();
      await glide(proceed);
      const carried = page.getByRole('group', { name: 'Carried release candidate' });
      await loading(carried, () => proceed.click());
      await glide(carried);
      await caption('The request carries the exact candidate (production v1 → v2) and the application target it was verified on.', 2400);
      const suggested = page.getByText('Suggested', { exact: true }).first();
      await expect(suggested).toBeVisible();
      await glide(suggested);
      await caption('The verified run is attached as evidence, already shared with the workspace.', 2400);
      await click(page.getByRole('button', { name: 'Prepare exact review', exact: true }), 1400);
      const reviewer = page.getByLabel('Assigned reviewer');
      await expect(reviewer).toBeVisible();
      await caption('Pick the reviewer by name: Priya, the workspace owner.', 1200);
      await click(reviewer, 600);
      await click(page.getByRole('option', { name: /^Priya Shah/ }), 700);
      const authorAck = page.getByRole('checkbox', { name: 'I reviewed this exact candidate, target, evidence and release consequence.', exact: true });
      await click(authorAck, 500);
      const send = page.getByRole('button', { name: 'Send review request', exact: true });
      await glide(send);
      // The composer itself shows "production v1 → v2", so readiness is checked only after the URL changes.
      await loading(page.getByText(/support-agent · production v1 → v2/), async () => { await send.click(); await page.waitForURL(/mine=1/); });
      await setCaption(page, chip, 'The request is sent. Nothing has moved yet.');
      await glide(page.getByText(/support-agent · production v1 → v2/));
      await caption('The request is sent. Nothing has moved yet.', 1800);
      await as('priya', '/dashboard/skills', page.getByRole('cell').getByRole('link', { name: 'refund-policy', exact: true }));
      await caption('Now signed in as the reviewer: the request waits in her review inbox.', 1600);
      await glide(page.getByRole('tab', { name: 'Reviews', exact: true }));
      await loading(page.getByRole('button', { name: 'support-agent', exact: true }), () => page.getByRole('tab', { name: 'Reviews', exact: true }).click());
      await dwell(800);
      const ack = page.getByRole('checkbox', { name: 'I reviewed this exact candidate, target, evidence and release consequence.', exact: true });
      await glide(page.getByRole('button', { name: 'support-agent', exact: true }).first());
      await loading(ack, () => page.getByRole('button', { name: 'support-agent', exact: true }).first().click());
      await expect(ack).toBeEnabled();
      await caption('She sees the exact candidate, its lock changes and the attached evidence.', 2600);
      await click(ack, 500);
      await glide(page.getByRole('button', { name: 'Approve release', exact: true }));
      await caption('She approves the exact packet…', 900);
      await click(page.getByRole('button', { name: 'Approve release', exact: true }), 1500);
      await expect(ack).toBeChecked();
      await glide(page.getByRole('button', { name: 'Release to production', exact: true }));
      await caption('…then releases it: production moves to v2.', 900);
      await click(page.getByRole('button', { name: 'Release to production', exact: true }), 1000);
      await click(page.getByRole('button', { name: 'Move production', exact: true }), 1600);
      await expect(page.getByText('Release receipt', { exact: true })).toBeVisible();
      await scrollTo(page.getByText('Release receipt', { exact: true }), 600);
      await caption('A durable receipt records the move from v1 to v2 and who approved and released it.', 3000);
    }

    // ---------------- 8. Install
    if (ONLY.includes(8)) {
      await as('maya', `/dashboard/skills/contexts/${ids.support}?version=2&tab=links`, page.getByRole('table', { name: 'Linked skills' }));
      await chapter(8, 'Install', 'support-agent v2 is now production. Its lock pins refund-policy v2.');
      const lockRow = page.getByRole('row').filter({ hasText: 'skills/refund-policy/' });
      await click(lockRow.getByRole('button', { name: 'Lock details', exact: true }), 1000);
      const contextDigest = (await lockRow.getByText(/^[0-9a-f]{12}…$/).first().textContent())?.trim() ?? '';
      mark('note', { label: `context v2 lock refund-policy digest prefix ${contextDigest}` });
      await glide(lockRow.getByText(/^[0-9a-f]{12}…$/).first());
      await caption('Note the refund-policy v2 digest in the context lock.', 2600);
      await goto(`/dashboard/skills/contexts/${ids.support}?version=2&tab=use`, page.getByRole('tab', { name: 'Claude Code', exact: true }), 'The context Use tab plans a v2-pinned install against the public API (a preview installer in this build).');
      await click(page.getByRole('tab', { name: 'Claude Code', exact: true }), 900);
      await glide(page.getByText(/^API: /));
      await caption('The context Use tab plans a v2-pinned install against the public API (a preview installer in this build).', 3000);
      await goto(`/dashboard/skills/${ids.refund}?tab=use`, page.getByRole('combobox', { name: /^Coding client/ }), 'The skill Use tab gives a copyable Claude Code install command.');
      await expect(page.getByRole('combobox', { name: /^Coding client/ })).toHaveText(/Claude Code/);
      await glide(page.getByRole('combobox', { name: /^Coding client/ }));
      await caption('The skill Use tab gives a copyable Claude Code install command.', 1400);
      await click(page.getByRole('button', { name: 'Copy code', exact: true }).first(), 1400);
      const command = await page.evaluate(() => navigator.clipboard.readText());
      writeFileSync(`${OUT}/install-command.txt`, command);
      expect(command).toContain('https://api.acme-support.example/');
      expect(command).not.toMatch(/localhost|127\.0\.0\.1/);
      await caption('It uses the public API address. Keys come from your environment; none are in the command.', 2400);
      await click(page.getByRole('tab', { name: 'Versions', exact: true }), 1200);
      const v2row = page.getByRole('row').filter({ hasText: 'Refund item price only' });
      const skillDigest = (await v2row.getByText(/^[0-9a-f]{12}…$/).first().textContent())?.trim().replace(/…$/, '') ?? '';
      mark('note', { label: `skill production v2 digest prefix ${skillDigest}` });
      expect(skillDigest.length).toBe(12);
      expect(contextDigest.replace(/…$/, '')).toBe(skillDigest);
      await glide(v2row.getByText(/^[0-9a-f]{12}…$/).first());
      await caption('It installs production v2, whose digest is the one the context lock pins.', 3200);
    }

    // ---------------- End card
    await setCaption(page, '', '');
    await page.goto('/__demo/card/end');
    mark('end');
    await page.waitForTimeout(FAST ? 300 : 2800);
    mark('stop');
  } finally {
    if (test.info().status !== test.info().expectedStatus) {
      await page.screenshot({ path: `${OUT}/shots/failure.png` }).catch(() => undefined);
      writeFileSync(`${OUT}/shots/failure.aria.txt`, await page.locator('main').first().ariaSnapshot().catch((e) => String(e)));
    }
    writeFileSync(`${OUT}/events.json`, JSON.stringify({ t0, events }, null, 2));
    await context.close();
    const video = page.video();
    if (video) { const path = await video.path(); writeFileSync(`${OUT}/video-path.txt`, path); }
    await wholeContext.dispose().catch(() => undefined);
    await native.dispose().catch(() => undefined);
  }
});
