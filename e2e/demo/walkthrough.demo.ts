import { randomUUID } from 'node:crypto';
import { execFileSync } from 'node:child_process';
import { readFileSync, writeFileSync, mkdirSync } from 'node:fs';
import { test, expect } from '@playwright/test';
import type { Locator, Page } from '@playwright/test';
import { E2E } from '../lib/env';
import { sendTrace } from '../lib/otlp';
import { SkillsFixtures } from '../lib/skills-fixtures';
import type { FixtureRule } from '../lib/skills-fixtures';
import { overlayInit, cardHtml, personHtml, terminalHtml } from './overlay';
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
  const USAGE = { 'llm.token_count.prompt': 6412, 'llm.token_count.completion': 118, 'llm.token_count.total': 6530,
    'llm.cost.prompt': 0.01603, 'llm.cost.completion': 0.00118, 'llm.cost.total': 0.01721 };
  if (ONLY.includes(6)) {
    const v1 = state.support.version;
    const trace = await sendTrace(request, { collectorUrl: E2E.collectorUrl, apiKey: priya.apiKey!, secretKey: priya.secretKey!, projectName,
      rootName: 'Refund request: cracked blender jug', childName: 'chat.completion', scope: { name: 'acme-support-agent', version: '1.4.0' },
      // Realistic span shape: an agent root over one model call that took 2.4 s; the root carries the run's usage.
      startTimeUnixNano: BigInt(Date.now() - 2_400) * 1_000_000n, endTimeUnixNano: BigInt(Date.now()) * 1_000_000n,
      childAttributes: { 'llm.model_name': 'gpt-4o', 'llm.provider': 'openai', ...USAGE },
      rootAttributes: { 'fi.span.kind': 'agent', ...USAGE, 'input.value': JSON.stringify({ messages: [
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

  // ---- One browser context and one page for the whole recording. The app frame is 1440x810; postprocess.py
  // adds a 90 px caption band below it, so captions never cover the product UI (final video 1440x900).
  const W = 1440; const H = 810;
  const context = await browser.newContext({ baseURL: E2E.appUrl, viewport: { width: W, height: H }, ignoreHTTPSErrors: true,
    recordVideo: { dir: `${OUT}/video`, size: { width: W, height: H } } });
  await context.grantPermissions(['clipboard-read', 'clipboard-write']);
  const t0 = Date.now();
  const mark = (kind: string, extra: Record<string, unknown> = {}) => events.push({ t: (Date.now() - t0) / 1000, kind, ...extra });
  // Loading placeholders reported by overlay.ts; postprocess.py cuts these frames.
  await context.exposeBinding('__demoPlaceholder', (_source, on: boolean, what: string) => mark(on ? 'ph-start' : 'ph-end', { label: what }));
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
  const cards: Record<string, string> = {
    title: cardHtml('Skills + Agent contexts', 'Acme Support builds, tests and releases a support agent.', 'Demo workspace with sample data.\nModel responses are simulated for this demo; the demo application is a test fixture.'),
    end: cardHtml('Skills + Agent contexts', 'Versioned skills, locked agent contexts, tests on your application, reviewed releases.', 'Demo workspace with sample data. Model responses are simulated for this demo.'),
    priya: personHtml('Priya Shah', 'Workspace owner, reviewer', 'Acme Support'),
  };
  await context.route('**/__demo/card/*', async (route) => {
    const kind = new URL(route.request().url()).pathname.split('/').pop()!;
    await route.fulfill({ status: 200, contentType: 'text/html', body: cards[kind] ?? cardHtml('Skills + Agent contexts', '', '') });
  });
  const page = await context.newPage();
  page.setDefaultTimeout(60_000);
  let shotN = 0;
  const shot = async (name: string) => { if (FAST) await page.screenshot({ path: `${OUT}/shots/${String(++shotN).padStart(3, '0')}-${name}.png` }); };
  const dwell = (ms = 1500) => page.waitForTimeout(FAST ? Math.min(ms, 250) : ms);
  // On-screen audit: any loopback address or test-infrastructure word visible at a caption is logged to events.json.
  const audit = async (at: string) => {
    const hits = await page.evaluate(() => (document.body.innerText.match(/.{0,40}(localhost|127\.0\.0\.1|e2e|fixture|ctxhub|playwright).{0,40}/gi) ?? []).slice(0, 5)).catch(() => []);
    if (hits.length) mark('audit', { label: `${at} :: ${hits.join(' | ')}` });
  };
  // Captions: one line each, held for at least MIN_HOLD of kept (uncut) time before the next one replaces it.
  // Time inside cut stretches (hidden steps, loads, trimmed waits, placeholders) does not count towards the hold.
  const MIN_HOLD = FAST ? 0 : 4200;
  const cuts: [number, number, number][] = []; // [start, end, seconds kept] in recording time
  let lastCaption = 0;
  const keptSince = (from: number) => {
    const now = Date.now() - t0;
    let removed = 0;
    for (const [a, b, kept] of cuts) { const lo = Math.max(a, from); const hi = Math.min(b, now); if (hi > lo) removed += Math.max(0, (hi - lo) - kept * ((hi - lo) / Math.max(1, b - a))); }
    const ph = events.filter((e) => e.kind === 'ph-start' || e.kind === 'ph-end');
    for (let i = 0; i < ph.length; i++) if (ph[i].kind === 'ph-start') { const end = ph.slice(i + 1).find((e) => e.kind === 'ph-end'); const a = ph[i].t * 1000; const b = end ? end.t * 1000 : now; const lo = Math.max(a, from); const hi = Math.min(b, now); if (hi > lo) removed += hi - lo; }
    return now - from - removed;
  };
  const settle = async () => { while (!FAST && lastCaption && keptSince(lastCaption) < MIN_HOLD) await page.waitForTimeout(150); };
  let chip = '';
  const caption = async (text: string) => {
    if (text.length > 92) throw new Error(`caption longer than one line: ${text}`);
    await settle();
    mark('caption', { label: text, chip }); lastCaption = Date.now() - t0;
    await audit(text); await shot(text.slice(0, 40).replace(/[^a-z0-9]+/gi, '-'));
  };
  const chapter = async (n: number, title: string, first: string) => { await settle(); chip = `${n} · ${title}`; mark('chapter', { chapter: n, title }); await caption(first); };
  const center = async (l: Locator) => {
    await l.waitFor({ state: 'visible' });
    await l.evaluate((el) => el.scrollIntoView({ behavior: 'smooth', block: 'center' }));
    await page.waitForTimeout(FAST ? 150 : 450);
  };
  const glide = async (l: Locator) => {
    await center(l);
    const box = await l.boundingBox();
    if (box) await page.mouse.move(box.x + box.width / 2, box.y + box.height / 2, { steps: FAST ? 3 : 12 });
    await page.waitForTimeout(FAST ? 50 : 180);
  };
  // Points at text the viewer should read without covering it: the cursor rests just left of it.
  const point = async (l: Locator) => {
    await center(l);
    const box = await l.boundingBox();
    if (box) await page.mouse.move(Math.max(4, box.x - 22), box.y + Math.min(box.height / 2, 12), { steps: FAST ? 3 : 12 });
    await page.waitForTimeout(FAST ? 50 : 180);
  };
  const click = async (l: Locator, after = 900) => { await glide(l); await l.click(); await dwell(after * 0.55); };
  const typeInto = async (l: Locator, text: string) => { await glide(l); await l.click(); await l.fill(text); await dwell(350); };
  const cutStretch = async (kind: string, keptSeconds: number, fn: () => Promise<unknown>, label = '') => {
    const a = Date.now() - t0; mark(`${kind}-start`, label ? { label } : {}); await fn(); mark(`${kind}-end`, label ? { label } : {}); cuts.push([a, Date.now() - t0, keptSeconds * 1000]);
  };
  // Form filling and consent ticks happen off camera: the edit cuts these stretches to the filled result.
  const hide = (fn: () => Promise<unknown>) => cutStretch('hide', 0, fn);
  // Navigation between the shots (menus, tabs, list rows) is jump-cut to a short beat at each end.
  const quick = (fn: () => Promise<unknown>) => cutStretch('quick', 0.6, fn);
  const scrollTo = async (l: Locator, after = 700) => { await center(l); await dwell(after * 0.6); };
  // Page loads are marked so the edit can drop skeleton/blank frames; ready = first content the viewer should see.
  const loading = (ready: Locator, fn: () => Promise<unknown>) => cutStretch('load', 0.7, async () => { await fn(); await ready.first().waitFor({ state: 'visible', timeout: 90_000 }); await page.waitForTimeout(400); });
  const goto = (url: string, ready: Locator) => loading(ready, async () => { await page.goto(url); await page.waitForLoadState('domcontentloaded'); });
  const waitCut = (label: string, fn: () => Promise<void>) => cutStretch('cut', 3.5, fn, label);
  const as = async (who: 'maya' | 'priya', url: string, ready: Locator) => { await page.evaluate((w) => sessionStorage.setItem('__demo_as', w), who); await goto(url, ready); };
  const noPlaceholders = () => expect.poll(() => page.evaluate(() => document.querySelectorAll('.MuiSkeleton-root, [aria-busy="true"], [aria-label^="Loading"]').length), { timeout: 60_000 }).toBe(0);

  try {
    // ---------------- Title card
    await page.goto('/__demo/card/title');
    mark('title');
    await page.waitForTimeout(FAST ? 300 : 3600);

    // ---------------- 1. Skills library and a skill in the editor
    if (ONLY.includes(1)) {
      const refundLink = page.getByRole('cell').getByRole('link', { name: 'refund-policy', exact: true });
      await goto('/dashboard/skills', refundLink);
      await chapter(1, 'Skills library', 'Acme Support keeps five versioned skills in one workspace library.');
      await glide(refundLink);
      const edit = page.getByRole('link', { name: 'Edit latest v1', exact: true }).or(page.getByRole('button', { name: 'Edit latest v1', exact: true })).first();
      await loading(edit, () => refundLink.click());
      await caption('refund-policy is live in production at v1. Maya opens it in the editor.');
      await glide(edit);
      await loading(page.getByRole('treeitem', { name: 'SKILL.md', exact: true }), () => edit.click());
      await noPlaceholders();
      await click(page.getByRole('treeitem', { name: 'refund-windows.md', exact: true }), 1400);
      await click(page.getByRole('treeitem', { name: 'SKILL.md', exact: true }), 600);
      await click(page.getByRole('tablist', { name: 'Skill document view' }).getByRole('tab', { name: 'Source', exact: true }), 600);
      const editor = page.getByRole('textbox', { name: /Editor content/ });
      await editor.focus(); await editor.press('Control+a');
      await page.evaluate((text) => navigator.clipboard.writeText(text), REFUND_V2);
      await editor.press('Control+v');
      await caption('She updates the policy: refund the item price only, never shipping.');
      await dwell(1500);
      await editor.press('Control+End');
      await editor.press('Enter');
      await page.keyboard.type(FAKE_SECRET_LINE, { delay: FAST ? 0 : 14 });
      await page.keyboard.press('Escape');
      const warnings = page.getByRole('region', { name: 'Warnings', exact: true });
      await expect(warnings.getByText(/possible .* was found/)).toBeVisible();
      await expect(page.getByText(/Checking|Running checks/)).toHaveCount(0, { timeout: 30_000 }).catch(() => undefined);
      await glide(warnings.getByText(/possible .* was found/));
      await caption('Live checks run on every edit: a pasted test credential is flagged (the value is fake).');
      await settle();
      await editor.press('Shift+Home'); await editor.press('Backspace'); await editor.press('Backspace');
      await expect(warnings).not.toBeVisible();
      await expect(page.getByText('Saved', { exact: true })).toBeVisible();
      await expect(page.getByText('No issues found.', { exact: true })).toBeVisible();
      await caption('She removes the line; the warning clears and the draft autosaves.');
      const draft = await api(maya, 'GET', `${ids.refund}/draft/`);
      expect(draft.files.find((f: { path: string }) => f.path === 'SKILL.md').content).toBe(REFUND_V2);
    }

    // ---------------- 2. Publish, compare, move production
    if (ONLY.includes(2)) {
      await settle();
      await click(page.getByRole('button', { name: 'Publish', exact: true }), 700);
      const dialog = page.getByRole('dialog');
      await hide(() => dialog.getByRole('textbox', { name: 'Version note' }).fill('Refund the item price only; never shipping'));
      await chapter(2, 'Versions and labels', 'Publishing creates an immutable v2. Production stays on v1 until moved.');
      const publishButton = dialog.getByRole('button', { name: 'Publish', exact: true });
      await glide(publishButton);
      await loading(page.getByRole('tab', { name: 'Versions', exact: true }), () => publishButton.click());
      await expect(page).toHaveURL(new RegExp(`/dashboard/skills/${ids.refund}(\\?.*)?$`));
      await quick(async () => {
        await click(page.getByRole('tab', { name: 'Versions', exact: true }), 600);
        await click(page.getByRole('button', { name: 'Actions for v2', exact: true }), 400);
        await page.getByRole('menuitem', { name: 'Compare with previous', exact: true }).click();
        await page.getByRole('list', { name: 'Compared files' }).waitFor();
        await noPlaceholders();
      });
      await click(page.getByRole('list', { name: 'Compared files' }).getByRole('button', { name: /SKILL\.md/ }), 900);
      await caption('Compare shows exactly which lines changed between v1 and v2.');
      await settle();
      await quick(async () => {
        await goto(`/dashboard/skills/${ids.refund}?tab=versions`, page.getByRole('button', { name: 'Actions for production', exact: true }));
        await click(page.getByRole('button', { name: 'Actions for production', exact: true }), 400);
        await click(page.getByRole('menuitem', { name: 'Move', exact: true }), 500);
      });
      const move = page.getByRole('dialog');
      await hide(async () => {
        await move.getByRole('combobox', { name: /^Version(?:\s|$)/ }).click();
        await page.getByRole('option', { name: 'v2', exact: true }).click();
        await move.getByRole('textbox', { name: 'Note (optional)' }).fill('Item price only; contexts adopt it after testing');
        await page.waitForTimeout(300);
      });
      await point(move.getByText(/Not tested is not a pass/));
      await caption('Moving production is separate. Contexts stay on v1 until they test and accept v2.');
      await click(move.getByRole('button', { name: 'Move production from v1 to v2', exact: true }), 1000);
      await expect(move).not.toBeVisible();
      await quick(async () => {
        await page.getByText('Production now points to v2.').first().waitFor({ timeout: 15_000 }).catch(() => undefined);
        await click(page.getByRole('button', { name: 'Actions for production', exact: true }), 400);
        await click(page.getByRole('menuitem', { name: 'History', exact: true }), 400);
        await expect(page.getByText('Item price only; contexts adopt it after testing').first()).toBeVisible({ timeout: 30_000 });
        await noPlaceholders();
      });
      await caption('The skill label now serves v2; history records who moved it and why.');
      await settle();
      await page.getByRole('button', { name: 'Close label history' }).click();
    }

    // ---------------- 3. Agent context
    if (ONLY.includes(3)) {
      const open = page.getByRole('table', { name: 'Agent contexts' }).getByRole('button', { name: 'support-agent', exact: true });
      await goto(`/dashboard/skills/contexts`, open);
      await glide(open);
      await loading(page.getByRole('tab', { name: 'Linked skills', exact: true }), () => open.click());
      await noPlaceholders();
      await chapter(3, 'Agent context', 'An agent context bundles standing instructions (AGENTS.md) with linked skills.');
      await settle();
      await quick(async () => { await click(page.getByRole('tab', { name: 'Linked skills', exact: true }), 500); await noPlaceholders(); });
      await point(page.getByText('skills/escalation-tone/', { exact: true }));
      await caption('Four skills follow production; escalation-tone is pinned to v1 on purpose.');
      await point(page.getByRole('row').filter({ hasText: 'skills/refund-policy/' }).getByText('v2 available'));
      await caption('refund-policy v2 is available, but the context stays locked to v1 until accepted.');
    }

    // ---------------- 4. Upstream change: dependents, impact preview, accept, publish
    if (ONLY.includes(4)) {
      const table = page.getByRole('table', { name: 'Published context dependents' });
      await settle();
      await goto(`/dashboard/skills/${ids.refund}?tab=dependents`, table);
      await noPlaceholders();
      await scrollTo(table, 300);
      await chapter(4, 'Upstream change', 'Dependents: support-agent follows production; returns-desk pins v1 and stays.');
      const impact = page.getByText(/refund-policy v1 → v2 in support-agent/);
      await settle();
      await quick(async () => {
        await click(page.getByRole('button', { name: 'Prepare draft updates for 1 context', exact: true }), 600);
        await expect(impact).toBeVisible();
        await scrollTo(impact, 300);
        await noPlaceholders();
      });
      await caption('The update preview shows the exact change, refund-policy v1 to v2, before it applies.');
      await settle();
      await hide(async () => {
        await page.getByRole('checkbox', { name: /^Accept these exact resolved versions/ }).check();
        await page.getByRole('button', { name: 'Apply selected updates to 1 context draft', exact: true }).click();
        await expect(page.getByText(/ draft now uses .+\. Production unchanged\./)).toBeVisible();
        await page.waitForTimeout(300);
      });
      await point(page.getByText(/ draft now uses .+\. Production unchanged\./));
      await caption('Accepting writes a draft. The published context is unchanged until reviewed.');
      const openDraft = page.getByRole('link', { name: 'Open draft for support-agent' }).or(page.getByRole('button', { name: 'Open draft for support-agent' })).first();
      await settle();
      const deps = page.locator('#review-dependencies');
      await quick(async () => {
        await glide(openDraft);
        await openDraft.click();
        await page.getByRole('button', { name: 'Review changes', exact: true }).waitFor({ timeout: 90_000 });
        await expect(page.getByText(/Loading linked and generated files/)).toHaveCount(0, { timeout: 60_000 });
        await page.getByRole('button', { name: 'Review changes', exact: true }).click();
        await deps.waitFor({ timeout: 90_000 });
        await noPlaceholders();
        await deps.evaluate((el) => el.scrollIntoView({ behavior: 'instant', block: 'end' }));
        await page.waitForTimeout(300);
      });
      await caption('Maya reviews the draft (refund-policy v2, nothing else) and publishes support-agent v2.');
      await dwell(2200);
      const reviewed = page.getByRole('checkbox', { name: /^I reviewed the saved candidate/ });
      const warning = page.getByRole('checkbox', { name: 'I acknowledge the static warnings for this exact candidate.', exact: true });
      await hide(async () => {
        await reviewed.check();
        if (await warning.count()) await warning.check();
        await page.getByRole('button', { name: 'Acknowledge reviewed head', exact: true }).click();
        await expect(page.getByRole('button', { name: 'Publish version', exact: true })).toBeVisible();
        await reviewed.check();
        if (await warning.count()) await warning.check();
        await page.waitForTimeout(200);
      });
      await click(page.getByRole('button', { name: 'Publish version', exact: true }), 1200);
      await expect.poll(async () => (await api(maya, 'GET', `contexts/${ids.support}/`)).context.latest_version).toBe(2);
      await noPlaceholders();
    }

    // ---------------- 5. Tests
    if (ONLY.includes(5)) {
      const answers = page.getByRole('radio', { name: /^Check answers with this context/ });
      await settle();
      await goto(`/dashboard/skills/contexts/${ids.support}?version=2&tab=tests`, answers);
      await noPlaceholders();
      await chapter(5, 'Tests', 'Test the whole context before release: candidate v2 against reference v1.');
      await click(answers, 600);
      await hide(async () => {
        await page.getByRole('textbox', { name: 'Test request', exact: true }).fill(REQUESTS.refund);
        await page.getByRole('textbox', { name: 'Expected answer contains', exact: true }).fill('$25');
        await page.getByRole('checkbox', { name: 'I reviewed this request and its expected answer.', exact: true }).check();
        await page.getByRole('combobox', { name: /^Task model/ }).click();
        await page.getByRole('option', { name: 'gpt-4o-mini', exact: true }).click();
        await page.getByRole('textbox', { name: 'Maximum estimated cost (USD)', exact: true }).fill('2');
        await page.getByRole('textbox', { name: 'Historical reference version (optional)', exact: true }).fill('1');
        await page.getByRole('button', { name: 'Preview answer check', exact: true }).click();
        for (const name of ['I know of no bundled answer keys or evaluation material', 'I reviewed the entire inventory and outbound inputs', 'I acknowledge provider processing of this reviewed context and test request.']) {
          const box = page.getByRole('checkbox', { name, exact: true });
          await box.first().waitFor({ timeout: 30_000 }).catch(() => undefined);
          if (await box.count()) await box.check();
        }
        await page.getByRole('button', { name: 'Run answer check', exact: true }).scrollIntoViewIfNeeded();
        await page.waitForTimeout(300);
      });
      await glide(page.getByRole('textbox', { name: 'Test request', exact: true }));
      await caption('One request, expected answer "$25", on a simulated model.');
      await click(page.getByRole('button', { name: 'Run answer check', exact: true }), 600);
      await waitCut('whole-context-run', async () => { await expect(page.getByText(/^Answer: (pass|fail)$/).first()).toBeVisible({ timeout: 240_000 }); await noPlaceholders(); });
      const result = page.getByText('Whole-context result', { exact: true });
      await scrollTo(result, 400);
      await caption('v2 refunds the $25 item price and passes; the v1 reference adds shipping and fails.');

      // Application test
      await settle();
      const verify = page.getByRole('radio', { name: /^Test on the application/ });
      await quick(async () => { await scrollTo(verify, 300); await click(verify, 500); });
      // The pin dialog lists the demo application's measured identities (package entrypoint, "fixture_only"
      // side effects). The review-and-pin step is cut from the take and described in chapters.md instead.
      await hide(async () => {
        await page.getByRole('button', { name: 'Add application target' }).click();
        await page.getByRole('button', { name: 'Show app configuration' }).click();
        await expect(page.getByText('Pin the application configuration')).toBeVisible();
        await page.getByRole('button', { name: 'Pin this configuration' }).click();
        await expect(page.getByText('Configuration pinned')).toBeVisible();
        await noPlaceholders();
        await page.getByLabel('Candidate version').click(); await page.getByRole('option', { name: 'v2', exact: true }).click();
        const prod = page.getByLabel('Production version');
        if (!(await prod.textContent())?.includes('v1')) { await prod.click(); await page.getByRole('option', { name: 'v1', exact: true }).click(); }
        await expect(page.getByLabel('Loading test results')).toHaveCount(0, { timeout: 30_000 });
        await page.getByRole('checkbox', { name: /I accept up to/ }).check();
        await page.getByRole('group', { name: 'Examples to run' }).scrollIntoViewIfNeeded();
        await page.waitForTimeout(300);
      });
      await point(page.getByText('Configuration pinned').first());
      await scrollTo(page.getByRole('group', { name: 'Examples to run' }), 400);
      await caption('Next, the demo application runs three reviewed cases on each version.');
      const run = page.getByRole('button', { name: 'Run application test', exact: true });
      await expect(run).toBeEnabled({ timeout: 30_000 });
      await settle();
      await click(run, 600);
      await waitCut('native-run', async () => {
        await expect(page.getByText(/^v\d+ passed \d+ of \d+ cases\.|Repair verified:|Repair not verified:|Hold this release|The run stopped/).first()).toBeVisible({ timeout: 360_000 });
        await noPlaceholders();
      });
      const shipHold = page.getByText('Ready for release review', { exact: true }).or(page.getByText('Hold this release', { exact: true })).first();
      const verdict = (await shipHold.textContent()) ?? '';
      mark('note', { label: `ch5 ship/hold: ${verdict}; headline: ${await page.getByText(/^v\d+ passed \d+ of \d+ cases\./).first().textContent()}` });
      expect(verdict).toBe('Ready for release review');
      await expect(page.getByText('v2 passed 3 of 3 cases.', { exact: true })).toBeVisible();
      await point(shipHold);
      await caption('v2 passes all three cases on the application, so it is ready for release review.');
      const cells = page.getByRole('table', { name: 'Verification cells' });
      await scrollTo(cells, 400);
      await glide(cells.getByRole('row').filter({ hasText: 'Production v1' }).filter({ hasText: 'Damaged mug refund' }).getByText('Failed').first());
      await caption('This test also checks actions: v1 issues the wrong refund through the business tool.');
    }

    // ---------------- 6. Capture a failing trace
    if (ONLY.includes(6)) {
      await settle();
      await goto('/dashboard/observe', page.getByText(projectName, { exact: true }));
      const row = page.locator('.clean-data-table:visible .ag-row [col-id="trace_name"]').filter({ hasText: 'Refund request: cracked blender jug' }).first();
      await glide(page.getByText(projectName, { exact: true }));
      await loading(row, () => page.getByText(projectName, { exact: true }).click());
      await noPlaceholders();
      await click(row, 1200);
      await noPlaceholders();
      await chapter(6, 'Trace capture', 'A production conversation went wrong: the agent refunded shipping too.');
      await settle();
      const dialog = page.getByRole('dialog');
      await quick(async () => {
        await click(page.getByRole('button', { name: 'Save as test case', exact: true }), 700);
        await click(dialog.getByRole('button', { name: 'Preview selected text', exact: true }), 600);
        await noPlaceholders();
      });
      await caption('The request and the order state come straight from the trace; nothing is retyped.');
      await settle();
      await hide(async () => {
        await dialog.getByRole('textbox', { name: 'Case name', exact: true }).fill('Blender refund included shipping');
        await dialog.getByRole('textbox', { name: 'Expected answer must contain', exact: true }).fill('$24');
        await dialog.getByLabel('Required skill', { exact: true }).click();
        await page.getByRole('option', { name: /refund-policy$/ }).click();
        await dialog.getByLabel('Required business action', { exact: true }).click();
        await page.getByRole('option', { name: 'Issue a refund', exact: true }).click();
        await dialog.getByText('Required application behavior').scrollIntoViewIfNeeded();
        await page.waitForTimeout(300);
      });
      await point(dialog.getByText('Required application behavior'));
      await caption('The case requires refund-policy, a refund action, and the $24 item price in the answer.');
      await hide(async () => {
        await dialog.getByRole('checkbox', { name: /confirmed the expected behavior/ }).check();
        await dialog.getByRole('checkbox', { name: /workspace/ }).check();
        await page.waitForTimeout(200);
      });
      await settle();
      await quick(async () => {
        await click(dialog.getByRole('button', { name: 'Save test case', exact: true }), 700);
        await glide(dialog.getByRole('button', { name: 'Set up application test', exact: true }));
        await dialog.getByRole('button', { name: 'Set up application test', exact: true }).click();
        await page.getByRole('group', { name: 'Examples to run' }).waitFor({ timeout: 90_000 });
      });
      await expect(page).toHaveURL(/reproduce=/);
      // Production is still v1 (the recorded reference) at this point; staging it empty off camera keeps the
      // planner's identical-arms notice out of the take.
      await hide(async () => {
        const production = page.getByLabel('Production version');
        await production.click(); await page.getByRole('option', { name: /^None/ }).click();
        await page.getByText(/Up to \d+ runs/).waitFor({ timeout: 30_000 });
        await page.getByRole('checkbox', { name: /I accept up to/ }).check();
        await noPlaceholders();
        await page.waitForTimeout(300);
      });
      const plan = page.getByText(/Up to \d+ runs/);
      await scrollTo(plan, 300);
      await caption('Reproduce it on the application: candidate v2 against the recorded v1.');
      await click(page.getByRole('button', { name: 'Run application test', exact: true }), 600);
      await waitCut('reproduction run', async () => {
        await expect(page.getByText(/^Repair (not )?verified:|The run stopped/).first()).toBeVisible({ timeout: 360_000 });
        await noPlaceholders();
      });
      const outcome = (await page.getByText(/^Repair (not )?verified:/).first().textContent()) ?? '';
      mark('note', { label: outcome });
      expect(outcome).toMatch(/^Repair verified: v2 passed 1 of 1 cases; the recorded failure reproduced on v1\./);
      await scrollTo(page.getByText(/^Repair (not )?verified:/).first(), 400);
      await caption('Repair verified: the failure reproduces on v1 and does not on v2.');
    }

    // ---------------- 7. Review and release
    if (ONLY.includes(7)) {
      if (!ONLY.includes(6)) await goto(`/dashboard/skills/contexts/${ids.support}?version=2&tab=tests`, page.getByText('Release packet', { exact: true }));
      await settle();
      const packet = page.getByText('Release packet', { exact: true });
      await hide(async () => {
        await page.getByRole('textbox', { name: 'CI run link', exact: true }).fill('https://ci.acme-support.example/runs/1842');
        await page.getByRole('textbox', { name: 'CI pipeline name', exact: true }).fill('support-agent-release');
        await page.getByRole('textbox', { name: 'CI result (pass or hold)', exact: true }).fill('pass');
        await packet.scrollIntoViewIfNeeded();
        await page.waitForTimeout(200);
      });
      await scrollTo(packet, 300);
      await chapter(7, 'Review and release', 'Moving production on a context needs a second person. Maya requests it.');
      await click(page.getByRole('button', { name: 'Share run and attach CI result', exact: true }), 1000);
      const proceed = page.getByRole('link', { name: 'Request release review', exact: true }).or(page.getByRole('button', { name: 'Request release review', exact: true })).first();
      await expect(proceed).toBeVisible();
      await glide(proceed);
      await settle();
      const carried = page.getByRole('group', { name: 'Carried release candidate' });
      await quick(async () => { await proceed.click(); await carried.waitFor({ timeout: 90_000 }); await noPlaceholders(); });
      await hide(async () => {
        await page.getByRole('button', { name: 'Prepare exact review', exact: true }).click();
        await expect(page.getByLabel('Assigned reviewer')).toBeVisible();
        await noPlaceholders();
      });
      const evidence = page.getByText(/^Repair verified: v2 passed 1 of 1 cases; the recorded failure reproduced on v1\.$/).first();
      await point(evidence);
      await caption('The verified run goes with the request: v2 passed, and the v1 failure reproduced.');
      const reviewer = page.getByLabel('Assigned reviewer');
      await click(reviewer, 500);
      await click(page.getByRole('option', { name: /^Priya Shah/ }), 600);
      await hide(() => page.getByRole('checkbox', { name: 'I reviewed this exact candidate, target, evidence and release consequence.', exact: true }).check());
      const send = page.getByRole('button', { name: 'Send review request', exact: true });
      await glide(send);
      await loading(page.getByText(/support-agent · production v1 → v2/), async () => { await send.click(); await page.waitForURL(/mine=1/); });
      await point(page.getByText(/support-agent · production v1 → v2/));
      await caption('Sent to Priya, the workspace owner, by name. Nothing moves until she approves.');
      await settle();
      // The switch to the reviewer is shown explicitly.
      await page.evaluate(() => sessionStorage.setItem('__demo_as', 'priya'));
      await page.goto('/__demo/card/priya');
      mark('card', { label: 'priya' });
      await caption('Now signed in as Priya, the reviewer: the request waits in her review inbox.');
      await dwell(2200);
      const inboxRow = page.getByRole('button', { name: 'support-agent', exact: true }).first();
      await goto('/dashboard/skills/reviews', inboxRow);
      await noPlaceholders();
      const ack = page.getByRole('checkbox', { name: 'I reviewed this exact candidate, target, evidence and release consequence.', exact: true });
      await glide(inboxRow);
      await loading(ack, () => inboxRow.click());
      await expect(ack).toBeEnabled();
      await noPlaceholders();
      const seen = page.getByText(/^Repair verified: v2 passed 1 of 1 cases; the recorded failure reproduced on v1\.$/).first();
      await expect(seen).toBeVisible();
      await point(seen);
      await caption('She sees the exact change and the attached run\'s real outcomes before approving.');
      await settle();
      await hide(() => ack.check());
      await quick(async () => {
        await click(page.getByRole('button', { name: 'Approve release', exact: true }), 1200);
        if (!(await ack.isChecked())) await ack.check();
        await glide(page.getByRole('button', { name: 'Release to production', exact: true }));
        await page.getByRole('button', { name: 'Release to production', exact: true }).click();
      });
      const confirmMove = page.getByRole('button', { name: 'Move production', exact: true });
      await glide(confirmMove);
      await caption('She approves, then releases: production for support-agent moves from v1 to v2.');
      await click(confirmMove, 1200);
      await expect(page.getByText('Release receipt', { exact: true })).toBeVisible();
      await scrollTo(page.getByText('Release receipt', { exact: true }), 400);
      await caption('A durable receipt records the move and who approved and released it.');
    }

    // ---------------- 8. Delivery
    if (ONLY.includes(8)) {
      await settle();
      await as('maya', `/dashboard/skills/contexts/${ids.support}?version=2&tab=links`, page.getByRole('table', { name: 'Linked skills' }));
      await noPlaceholders();
      const lockRow = page.getByRole('row').filter({ hasText: 'skills/refund-policy/' });
      await click(lockRow.getByRole('button', { name: 'Lock details', exact: true }), 600);
      const contextDigest = (await lockRow.getByText(/^[0-9a-f]{12}…$/).first().textContent())?.trim().replace(/…$/, '') ?? '';
      mark('note', { label: `context v2 lock refund-policy digest prefix ${contextDigest}` });
      expect(contextDigest).toHaveLength(12);
      await lockRow.getByText(/^[0-9a-f]{12}…$/).first().evaluate((el) => { el.style.outline = '2px solid #7c5cff'; el.style.borderRadius = '3px'; });
      await chapter(8, 'Delivery', `support-agent v2 is production. Its lock pins refund-policy v2, digest ${contextDigest.slice(0, 8)}.`);
      await settle();
      const useTabReady = page.getByRole('tab', { name: 'Application', exact: true });
      await quick(async () => {
        await goto(`/dashboard/skills/${ids.refund}?tab=use`, useTabReady);
        await noPlaceholders();
        await click(useTabReady, 500);
      });
      await expect(page.getByRole('combobox', { name: /^Application transport/ })).toHaveText(/cURL/);
      await caption('Apps fetch the production skill through the API. The Use tab gives the command.');
      await glide(page.getByRole('button', { name: 'Copy code', exact: true }).first());
      await page.getByRole('button', { name: 'Copy code', exact: true }).first().click();
      const command = await page.evaluate(() => navigator.clipboard.readText());
      writeFileSync(`${OUT}/install-command.txt`, command);
      expect(command).toContain('https://api.acme-support.example/');
      expect(command).not.toMatch(/localhost|127\.0\.0\.1|FAKE|[0-9a-f]{32}/);
      // Run the exact copied command during the take (keys from the environment, never in the command).
      const jq = `jq '.result | {name, version, digest, files: [.files[].path]}'`;
      const output = execFileSync('bash', ['-c', `${command} | ${jq}`], { encoding: 'utf8', timeout: 60_000,
        env: { ...process.env, FI_API_KEY: priya.apiKey!, FI_SECRET_KEY: priya.secretKey!, CURL_CA_BUNDLE: process.env.DEMO_CA ?? `${process.env.HOME}/demo-tls/ca.pem` } });
      writeFileSync(`${OUT}/fetch-output.json`, output);
      const fetched = JSON.parse(output);
      mark('note', { label: `fetched ${fetched.name} v${fetched.version} ${fetched.digest}` });
      expect(fetched.version).toBe(2);
      expect(String(fetched.digest).replace(/^sha256:/, '').slice(0, 12)).toBe(contextDigest);
      cards.fetch = terminalHtml('acme-support · fetch the production skill', `${command} | ${jq}`, output.trimEnd().split('\n'), String(fetched.digest));
      await settle();
      await page.goto('/__demo/card/fetch');
      mark('card', { label: 'fetch' });
      await caption(`It returns refund-policy v2, digest ${contextDigest.slice(0, 8)}: the digest the context lock pins.`);
      await settle();
    }

    // ---------------- End card
    await page.goto('/__demo/card/end');
    mark('end');
    await page.waitForTimeout(FAST ? 300 : 3200);
    mark('stop');
  } finally {
    if (test.info().status !== test.info().expectedStatus) {
      await page.screenshot({ path: `${OUT}/shots/failure.png` }).catch(() => undefined);
      writeFileSync(`${OUT}/shots/failure.aria.txt`, await page.locator('main').first().ariaSnapshot().catch((e) => String(e)));
    }
    writeFileSync(`${OUT}/events.json`, JSON.stringify({ t0, viewport: { width: W, height: H }, events }, null, 2));
    await context.close();
    const video = page.video();
    if (video) { const path = await video.path(); writeFileSync(`${OUT}/video-path.txt`, path); }
    await wholeContext.dispose().catch(() => undefined);
    await native.dispose().catch(() => undefined);
  }
});
