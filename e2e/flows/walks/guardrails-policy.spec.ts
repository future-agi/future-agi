import { request, type Locator, type Page } from '@playwright/test';
import { test, expect } from '../../lib/walk/fixtures';
import { flowAnnotation } from '../../lib/flow-meta';
import { E2E } from '../../lib/env';
import { GatewayAdmin } from '../../lib/gateway-admin';
import { MockUpstream } from '../../lib/upstream';
import { POLL } from '../../lib/state-probe';
import { activeRows, checked, fingerprint, frames, frameKind, frameText, gatewaySection,
  logRows, mirroredPolicies, policyRows, storedBytes, type Check } from '../../lib/walk/guardrails';

// Source pins: RLINE-PORT r5 a90ce525ec. Routes from routes/paths.js; catalogue
// from guardrails/catalog/builtin-checks.js, AddCheckDialog, RulesView. Drawer
// labels from RuleDrawer/StoredRuleDrawer/CheckFields. Name-based writes from
// hooks/useRuleActions; payloads from catalog/rule-model + DRF gateway serializers.
const RULES = '/dashboard/gateway/guardrails';
const KEYS = '/dashboard/gateway/keys';
const BASE = '/agentcc/gateways/default';
const POLICIES = '/agentcc/guardrail-policies/';
const DLP = 'data-leakage-prevention';
const DLP_LABEL = 'Data Leakage Prevention';
const UI_READY = 60_000;
// Five actors 150s, 14 UI stages up to 120s, 8 gateway syncs at 75s,
// 3 log flushes at 60s, four calls at 30s + headroom = 2880s.
const TIMEOUT = 2_880_000;
// builtin-checks.js order, minus the seeded Keyword Blocklist. No partner, tool
// permission or MCP entry is runnable without the organization's configuration.
const CATALOG = ['PII Detection', 'Secret Detection', 'Prompt Injection', 'Content Moderation',
  'Topic Restriction', 'Language Detection', 'System Prompt Protection', 'Hallucination Detection', DLP_LABEL];
const blankRule = (name: string, config: Record<string, unknown> = {}): Check =>
  ({ name, mode: 'sync', action: 'block', threshold: 0.8, enabled: true, config });

test.use({ scopeRoster: { admin: true }, walkName: 'W18' });
test.describe.configure({ timeout: TIMEOUT });
test(
  'WALK-E2E-018: W18 guardrail catalogue and saved policy controls match enforcement, refusals and role isolation',
  { tag: ['@walk', '@walk-W18', '@flow'], annotation: flowAnnotation({
    id: 'WALK-E2E-018', area: 'walks',
    userGoal: 'An Admin enables a runnable guardrail, sees streams enforced and saved-only checks honestly, edits lists and confirms removal without ambiguous or unauthorized writes',
    steps: [
      'seed an org provider and personal gateway key, a null-enabled stored partner and two same-name policy checks',
      'Admin opens Guardrails: exact runnable catalogue; enable Data Leakage Prevention in block mode and stream a passing and blocked answer',
      'read the stored-only partner Off with its actual gateway threshold; disable DLP and call with no guardrail metadata',
      'attempt the duplicate-name edit in the UI and read the refusal; save Topic Restriction and Language Detection lists; cancel then confirm removal',
      'refuse count_tokens under an organization provider lock before any mock dispatch',
      'Owner reaches the named key by keyboard with visible focus; Member and Viewer cannot save; org B cannot read or change A policy',
      'repeat read-only Guardrails checks at 390px and in dark mode; collect value-free H1 evidence',
    ],
    backendChecks: [
      'exact active guardrails section and policy rows match every named UI save; the gateway copy matches the same expected checks',
      'passing stream is complete, blocked stream has only the guardrail error and DONE, and both reached the mock',
      'the two correlated request logs are 200 and 403; the blocked row has guardrail_triggered true and guardrail_action blocked',
      'the null-enabled stored-only check never runs: a third call has no guardrail metadata and its local partner recorder stays empty',
      'the duplicate-name UI edit returns 400 guardrail_name_ambiguous and leaves all versions and policies byte-identical',
      'Topic Restriction and Language Detection lists persist exactly; removal creates no write before confirmation and removes only the chosen check',
      'an ineligible count_tokens call is 403 model_not_permitted with org_provider_denied and makes zero upstream requests',
      'Member and Viewer writes return 403 without state change; org B sees no A policy and B state is unchanged',
    ], browserChecks: [
      'exact catalogue set, stored-only Off and threshold, reason for ambiguous edit, and confirmation controls',
      'named key grid cell reached with Tab, visible focus and Enter opens its named drawer',
      'Chromium and Firefox, desktop and 390px, light and dark; non-local browser requests are blocked and classified',
    ],
  }) },
  async ({ scopeActors: scopes, scopeProbe: probe, walk }, testInfo) => {
    test.setTimeout(TIMEOUT);
    const { receipt } = walk;
    const A = scopes.ownerA.organizationId, B = scopes.ownerB.organizationId;
    const suffix = `${testInfo.project.name}-${testInfo.workerIndex}-${Date.now().toString(36)}`;
    const scenario = `w18-chat-${suffix}`, partner = `w18-partner-${suffix}`, countScenario = `w18-count-${suffix}`;
    const provider = `w18-provider-${suffix}`, native = `w18-native-${suffix}`, model = `w18-model-${suffix}`;
    const countModel = `w18-count-model-${suffix}`, policyName = `w18-policy-${suffix}`, keyName = `w18-key-${suffix}`;
    const word = `w18word${Date.now().toString(36)}`;
    const upstream = new MockUpstream(), admin = await GatewayAdmin.open(), caller = await request.newContext();
    const context = await walk.open(scopes.admin!, 'admin'), page = await context.newPage();
    const verify = <T>(name: string, lane: 'ui' | 'api' | 'storage' | 'gateway', fn: () => Promise<T>) =>
      test.step(name, () => checked(receipt, name, lane, fn));
    const row = (p: Page, name: string) => p.locator('.ag-row').filter({ has: p.getByText(name, { exact: true }) });
    const openRule = async (name: string) => {
      await row(page, name).getByText(name, { exact: true }).click({ timeout: UI_READY });
      const drawer = page.getByRole('dialog', { name, exact: true });
      await expect(drawer).toBeVisible({ timeout: UI_READY });
      return drawer;
    };
    const addRule = async (name: string) => {
      await page.getByRole('button', { name: 'Add check', exact: true }).click({ timeout: UI_READY });
      await page.getByRole('dialog', { name: 'Add a check' }).getByText(name, { exact: true }).click();
      return page.getByRole('dialog', { name, exact: true });
    };
    const save = async (drawer: Locator, label: string, operation = 'update-guardrail') => {
      const [res] = await Promise.all([
        page.waitForResponse(r => r.request().method() === 'POST' &&
          new URL(r.url()).pathname.endsWith(`/${operation}/`), { timeout: UI_READY }),
        drawer.getByRole('button', { name: label, exact: true }).click({ timeout: UI_READY }),
      ]);
      expect(res.status()).toBe(200);
      expect((await res.json()).result.gateway_synced).toBe(true);
      await expect(drawer).toBeHidden({ timeout: UI_READY });
      return res.request().postDataJSON();
    };
    try {
      const inspection = walk.inspection();
      expect(inspection.error).toBeUndefined();
      expect(inspection.profile).toBe('stock');
      receipt.extra.orgSet = fingerprint([A, B]);
      receipt.extra.contactSheet = `e2e/walk-results/w18/contact-sheets/${testInfo.project.name}-01.png`;
      // RLINE-PORT is a locator/wire rehearsal; only DSX on the composed head
      // can supply the strict boundary and per-backend-check fallibility evidence.
      if (process.env.E2E_WALK_STRICT !== '1') receipt.notes.push('Rehearsal only. Strict partner-egress acceptance and fallibility run on the composed product head.');
      const bBefore = { stored: await storedBytes(probe, B), gateway: await admin.orgConfig(B),
        policies: (await scopes.ownerB.api.get<{ result: unknown[] }>(POLICIES)).result };
      // Public fixture seams: provider serializer, personal key API, gateway merge
      // patch, policy serializer. No SQL mutations and no replacement browser actions.
      for (const [name, format, route, models] of [[provider, 'openai', scenario, [model]],
        [native, 'anthropic', countScenario, [countModel]]] as const) {
        await scopes.ownerA.api.post('/agentcc/provider-credentials/', { provider_name: name,
          credentials: { api_key: `synthetic-w18-${suffix}` }, base_url: upstream.baseUrl(route),
          api_format: format, models_list: models, extra_config: { api_path_prefix: '/v1' } });
      }
      const key = await scopes.ownerA.api.post<{ result: { key: string; gateway_key_id: string } }>(
        '/agentcc/api-keys/', { name: keyName });
      await scopes.ownerA.api.post(`${BASE}/update-config/`, { guardrails: {} });
      const seededChecks: Check[] = [
        { name: 'keyword-blocklist', enabled: false, action: 'block', threshold: 0.2, config: { words: [word] } },
        { name: 'keyword-blocklist', enabled: false, action: 'log', threshold: 0.4, config: { words: [word] } },
        { name: 'presidio-pii', enabled: null, action: 'block', threshold: 0.37,
          config: { endpoint: upstream.baseUrl(partner), language: 'en', score_threshold: 0.6 } },
      ];
      const seeded = await scopes.ownerA.api.post<{ result: { id: string; gateway_synced: boolean } }>(POLICIES,
        { name: policyName, scope: 'global', mode: 'enforce', priority: 100, is_active: true, checks: seededChecks });
      expect(seeded.result.gateway_synced).toBe(true);
      const seed = { id: seeded.result.id, name: policyName, checks: seededChecks };
      await testInfo.attach('seeded-W18', { contentType: 'application/json', body: JSON.stringify({
        orgSet: fingerprint([A, B]), record: fingerprint(seed.id), caller: fingerprint(key.result.gateway_key_id),
        scenarios: fingerprint([scenario, partner, countScenario]), anchors: fingerprint([word, keyName, model]),
      }) });
      const consoleChecks: Check[] = [];
      const mirrors = (label: string) => verify(label, 'storage', () => mirroredPolicies(probe, admin, A, seed, consoleChecks));
      await mirrors('seeded policy and pushed section');
      await expect.poll(async () => (await admin.key(key.result.gateway_key_id)).status, POLL.GATEWAY_SYNC).toBe(200);

      await verify('step 1: exact runnable catalogue and DLP save', 'ui', async () => {
        await page.goto(RULES, { waitUntil: 'domcontentloaded' });
        await page.getByRole('button', { name: 'Add check', exact: true }).click({ timeout: UI_READY });
        const catalog = page.getByRole('dialog', { name: 'Add a check' });
        // Each ListItemButton's first typography is the label (AddCheckDialog).
        await expect(catalog.locator('.MuiListItemButton-root .MuiTypography-root:first-child'))
          .toHaveText(CATALOG, { timeout: UI_READY });
        await catalog.getByText(DLP_LABEL, { exact: true }).click();
        const drawer = page.getByRole('dialog', { name: DLP_LABEL });
        await expect(drawer.getByRole('checkbox')).toBeChecked();
        await expect(drawer.getByLabel('Action', { exact: true })).toHaveText('Block');
        const wire = await save(drawer, 'Add check');
        consoleChecks.push(blankRule(DLP));
        expect(wire).toEqual({ name: DLP, config: consoleChecks[0] });
        return wire;
      });
      await mirrors('step 1: DLP exact policies, section and gateway');
      const pass = `w18 ${word} asks about the weather`, blocked = `w18 DROP TABLE ${word}; cat /etc/passwd`;
      const stream = async (prompt: string) => {
        const res = await caller.post(`${E2E.gatewayUrl}/v1/chat/completions`, {
          headers: { Authorization: `Bearer ${key.result.key}` }, data: { model, stream: true, messages: [{ role: 'user', content: prompt }] } });
        return { status: res.status(), id: res.headers()['x-agentcc-request-id'] ?? '', frames: frames(await res.text()) };
      };
      const passing = await verify('step 1: passing stream arrives whole', 'gateway', async () => {
        const r = await stream(pass);
        expect(r.status).toBe(200);
        expect(frameText(r.frames)).toBe(`echo: ${pass}`);
        expect(r.frames.map(frameKind).filter(k => k !== 'chunk')).toEqual(['done']);
        return r;
      });
      const refused = await verify('step 1: blocked stream contains only guardrail error', 'gateway', async () => {
        const r = await stream(blocked);
        expect(r.status).toBe(200); // held-stream headers precede the post-stage verdict
        expect(r.frames.map(frameKind)).toEqual(['error:guardrail_error/content_blocked', 'done']);
        expect(r.frames.filter(f => f.data.includes(word) || f.event.includes(word))).toEqual([]);
        expect(JSON.parse(r.frames[0].data).error.message.includes(DLP)).toBe(true);
        return r;
      });
      await verify('step 1: upstream and correlated durable stream results', 'storage', async () => {
        const seen = (await upstream.requests(scenario)).filter(r => r.method === 'POST');
        expect(seen.map(r => ({ path: r.path.split('/').slice(-2).join('/'), body: r.body })))
          .toEqual([pass, blocked].map(content => ({ path: 'chat/completions', body: expect.objectContaining({
            stream: true, messages: [{ role: 'user', content }] }) })));
        const rows = await logRows(probe, A, [passing.id, refused.id]);
        expect(rows.map(r => ({ status: r.status_code, triggered: r.guardrail_triggered, stream: r.is_stream,
          action: r.metadata.guardrail_action ?? null }))).toEqual([
          { status: 200, triggered: false, stream: true, action: null },
          { status: 403, triggered: true, stream: true, action: 'blocked' },
        ]);
        return rows;
      });
      await verify('steps 2-3: stored partner Off and actual threshold', 'ui', async () => {
        const drawer = await openRule('Presidio PII');
        await expect(drawer.getByText('Off', { exact: true })).toBeVisible();
        await expect(drawer.getByText('Threshold: 0.37', { exact: true })).toBeVisible();
        await expect(drawer.getByRole('checkbox')).toHaveCount(0);
        await expect(drawer.getByRole('button', { name: 'Turn off', exact: true })).toHaveCount(0);
        await receipt.shot(page, 'stored-partner-off');
        await drawer.getByRole('button', { name: 'Close', exact: true }).last().click();
        const dlp = await openRule(DLP_LABEL);
        await dlp.getByRole('checkbox').uncheck();
        await save(dlp, 'Save');
        consoleChecks[0] = { ...consoleChecks[0], enabled: false };
        return { enabled: false, threshold: 0.37 };
      });
      await mirrors('step 2: DLP off is mirrored without changing stored null');
      await verify('step 2: stored-only check does not run or call its partner', 'storage', async () => {
        const r = await stream(blocked);
        expect(r.status).toBe(200);
        expect(frameText(r.frames)).toBe(`echo: ${blocked}`);
        const [log] = await logRows(probe, A, [r.id]);
        expect(log.status_code).toBe(200);
        expect(log.guardrail_triggered).toBe(false);
        expect(Object.keys(log.metadata).filter(k => k.startsWith('guardrail_'))).toEqual([]);
        expect(await upstream.requests(partner)).toEqual([]);
        return log;
      });
      await verify('step 4: ambiguous UI edit is refused without changing any row', 'storage', async () => {
        const before = await storedBytes(probe, A), mirror = await admin.orgConfig(A);
        const drawer = await openRule('Keyword Blocklist');
        await drawer.getByLabel('Blocked keywords').fill(`${word}\n${word}next`);
        const response = page.waitForResponse(r => r.request().method() === 'POST' &&
          new URL(r.url()).pathname.endsWith('/update-guardrail/'), { timeout: UI_READY });
        await drawer.getByRole('button', { name: 'Save', exact: true }).click();
        const r = await response, body = await r.json();
        expect(r.status()).toBe(400);
        expect(body.code).toBe('guardrail_name_ambiguous');
        const reason = 'Guardrail name "keyword-blocklist" matches 2 stored entries. PATCH the owning policy by id with the intended checks list.';
        expect(body.message).toBe(reason);
        await expect(page.getByText(reason, { exact: true })).toBeVisible({ timeout: UI_READY });
        expect(await storedBytes(probe, A)).toEqual(before);
        expect(await admin.orgConfig(A)).toEqual(mirror);
        await drawer.getByRole('button', { name: 'Cancel', exact: true }).click();
        return { status: r.status(), before };
      });
      await verify('step 4: Topic Restriction and Language Detection lists save', 'ui', async () => {
        const topic = await addRule('Topic Restriction');
        await expect(topic.getByRole('button', { name: 'Add check', exact: true })).toBeDisabled();
        await topic.getByLabel('Forbidden topics').fill(`${word}: ${word}, ${word}next`);
        await save(topic, 'Add check');
        consoleChecks.push(blankRule('topic-restriction', { forbidden_topics: [{ name: word, keywords: [word, `${word}next`] }] }));
        const language = await addRule('Language Detection');
        await language.getByRole('combobox', { name: 'Allowed scripts' }).click();
        await page.getByRole('option', { name: 'greek', exact: true }).click();
        // Autocomplete closes on selection; Escape here closes the parent Drawer.
        await expect(language).toBeVisible({ timeout: UI_READY });
        await save(language, 'Add check');
        consoleChecks.push(blankRule('language-detection', { allowed_scripts: ['latin', 'greek'] }));
        return consoleChecks;
      });
      await mirrors('step 4: exact list policy rows and pushed lists');
      await verify('step 4: deletion requires confirmation and removes only Topic Restriction', 'storage', async () => {
        const before = await storedBytes(probe, A), original = (await activeRows(probe, A))[0];
        const policiesBefore = await policyRows(probe, A);
        const drawer = await openRule('Topic Restriction');
        await drawer.getByRole('button', { name: 'Remove', exact: true }).click();
        await expect(drawer.getByText('Remove Topic Restriction from this gateway?', { exact: true })).toBeVisible();
        expect(await storedBytes(probe, A)).toEqual(before);
        await drawer.getByRole('button', { name: 'Keep', exact: true }).click();
        expect(await storedBytes(probe, A)).toEqual(before);
        await drawer.getByRole('button', { name: 'Remove', exact: true }).click();
        await save(drawer, 'Remove', 'update-config');
        const after = (await activeRows(probe, A))[0];
        expect(after.version).toBe(original.version + 1);
        // removeRule uses guardrailsForSave: checks list -> rules, preserving the
        // original entries and policy refs. It does not mutate reusable policies.
        const expectedRules = original.guardrails.checks.filter((c: Check) => c.name !== 'topic-restriction')
          .map((c: Check & { _policy: string }) => c.name === 'presidio-pii' ? c : {
            name: c.name, stage: 'pre', mode: c.mode ?? 'sync', action: c.action,
            threshold: c.threshold, enabled: c.enabled !== false, config: c.config, _policy: c._policy,
          });
        expect(after.guardrails).toEqual({ enabled: true, checks: [], rules: expectedRules });
        expect(await policyRows(probe, A)).toEqual(policiesBefore);
        const expected = gatewaySection(policiesBefore.map(p => ({ ...p, checks: p.checks.filter(c => c.name !== 'topic-restriction') })));
        await expect.poll(async () => (await admin.orgConfig(A))?.guardrails, POLL.GATEWAY_SYNC).toEqual(expected);
        await expect(row(page, 'Topic Restriction')).toHaveCount(0, { timeout: UI_READY });
        return after;
      });
      await verify('step 5: ineligible count_tokens refuses before counting', 'gateway', async () => {
        const routing = { provider_lock: { enabled: true, deny_providers: [native] } };
        await scopes.admin!.api.post(`${BASE}/update-config/`, { routing });
        await expect.poll(async () => (await admin.orgConfig(A))?.routing?.provider_lock, POLL.GATEWAY_SYNC).toEqual(routing.provider_lock);
        const before = await upstream.requests(countScenario);
        const res = await caller.post(`${E2E.gatewayUrl}/v1/messages/count_tokens`, {
          headers: { 'x-api-key': key.result.key, 'anthropic-version': '2023-06-01' },
          data: { model: countModel, messages: [{ role: 'user', content: `count ${word}` }] } });
        const body = await res.json();
        expect(res.status()).toBe(403);
        expect(body.error.code).toBe('model_not_permitted');
        expect(body.error.metadata.reason).toBe('org_provider_denied');
        expect(await upstream.requests(countScenario)).toEqual(before);
        return { status: res.status(), body, before };
      });
      await verify('step 6: keyboard key details with visible focus', 'ui', async () => {
        const ownerContext = await walk.open(scopes.ownerA, 'owner-keyboard'), p = await ownerContext.newPage();
        await p.goto(KEYS, { waitUntil: 'domcontentloaded' });
        const name = p.getByRole('button', { name: `View details for ${keyName}`, exact: true });
        // GatewayGrid.jsx enables cell focus and maps Enter/Space to its row.
        // Real AG Grid consumes Tab before embedded NameCell buttons receive it;
        // unlike the grid mock, the native keyboard stop is the named gridcell.
        const target = p.getByRole('gridcell').filter({ has: name });
        await expect(target).toBeVisible({ timeout: UI_READY });
        const paint = () => target.evaluate(el => {
          const s = getComputedStyle(el);
          return { outline: s.outlineStyle, outlineWidth: parseFloat(s.outlineWidth), outlineColor: s.outlineColor,
            border: s.borderTopStyle, borderWidth: parseFloat(s.borderTopWidth), borderColor: s.borderTopColor,
            shadow: s.boxShadow };
        });
        const before = await paint();
        for (let i = 0; i < 150 && !(await target.evaluate(el => el === document.activeElement)); i++) await p.keyboard.press('Tab');
        await expect(target).toBeFocused({ timeout: UI_READY });
        expect(await target.evaluate(el => el.matches(':focus-visible'))).toBe(true);
        const focus = await paint();
        const transparent = (color: string) => color === 'transparent' || /rgba\([^)]*,\s*0\)$/.test(color);
        const outline = focus.outline !== 'none' && focus.outlineWidth > 0 && !transparent(focus.outlineColor);
        const border = focus.border !== 'none' && focus.borderWidth > 0 && !transparent(focus.borderColor) &&
          (focus.borderColor !== before.borderColor || focus.borderWidth !== before.borderWidth || focus.border !== before.border);
        const shadow = focus.shadow !== 'none' && focus.shadow !== before.shadow;
        expect(outline || border || shadow).toBe(true);
        await p.keyboard.press('Enter');
        await expect(p.getByRole('dialog', { name: keyName, exact: true })).toBeVisible({ timeout: UI_READY });
        // No key-page screenshots: even displayed prefixes are not receipt data.
        await ownerContext.close();
        return focus;
      });
      await verify('step 6: lower roles read only and all direct saves refused', 'storage', async () => {
        const before = await storedBytes(probe, A);
        for (const [label, actor] of [['member', scopes.member], ['viewer', scopes.viewer]] as const) {
          const ctx = await walk.open(actor, label), p = await ctx.newPage();
          await p.goto(RULES, { waitUntil: 'domcontentloaded' });
          await expect(p.getByRole('button', { name: 'Add check', exact: true })).toBeDisabled({ timeout: UI_READY });
          await expect(p.getByRole('checkbox', { name: 'Language Detection on', exact: true })).toBeDisabled();
          for (const [path, body] of [[`${BASE}/update-config/`, { guardrails: { rules: [] } }],
            [`${BASE}/update-guardrail/`, { name: DLP, config: { enabled: true } }],
            [POLICIES, { name: `denied-${suffix}`, checks: [] }]] as const) {
            expect((await scopes.send(actor, 'POST', path, body)).status).toBe(403);
          }
          await ctx.close();
        }
        expect(await storedBytes(probe, A)).toEqual(before);
        return before;
      });
      await verify('step 6: org B isolation and phone dark-mode readback', 'storage', async () => {
        expect((await scopes.send(scopes.ownerB, 'GET', `${POLICIES}${seed.id}/`)).status).toBe(404);
        expect((await scopes.send(scopes.ownerB, 'PATCH', `${POLICIES}${seed.id}/`, { checks: [] })).status).toBe(404);
        const ctx = await walk.open(scopes.ownerB, 'org-b'), p = await ctx.newPage();
        await p.goto(RULES, { waitUntil: 'domcontentloaded' });
        await expect(p.getByRole('button', { name: 'Add check', exact: true })).toBeVisible({ timeout: UI_READY });
        await expect(p.locator('.ag-row')).toHaveCount(0);
        expect({ stored: await storedBytes(probe, B), gateway: await admin.orgConfig(B),
          policies: (await scopes.ownerB.api.get<{ result: unknown[] }>(POLICIES)).result }).toEqual(bBefore);
        await ctx.close();
        await page.reload({ waitUntil: 'domcontentloaded' });
        for (const width of [1440, 390]) for (const colorScheme of ['light', 'dark'] as const) {
          await page.setViewportSize({ width, height: 900 }); await page.emulateMedia({ colorScheme });
          await expect(row(page, 'Language Detection')).toHaveCount(1, { timeout: UI_READY });
          await expect(row(page, 'Topic Restriction')).toHaveCount(0);
          await receipt.icons(page, 'admin', `guardrails-${width}-${colorScheme}`, walk.boundary.iconHostsAborted('admin'));
          await receipt.shot(page, `guardrails-${width}-${colorScheme}`);
        }
        expect(await upstream.requests(partner)).toEqual([]);
        return bBefore;
      });
    } finally {
      await caller.dispose(); await admin.dispose(); await context.close();
    }
  },
);
