import type { Locator, Page, Request } from '@playwright/test';
import { EDITOR_TABS, CACHE_OFF_LINE, PROFILE_PIPELINE, assertEmptyReadOnlyTab, assertEffectivePolicy } from './settings-visible-tabs';
import { test, expect } from '../../lib/scope-actors';
import { flowAnnotation } from '../../lib/flow-meta';
import { settingsFixtureProfile, type SettingsFixtureProfile } from './settings-fixture-profile';
import { StackLock, STACK_LOCKS } from '../../lib/stack-lock';
import { POLL } from '../../lib/state-probe';

// Pinned off the source at e1ef026f78 (REL-R7 9.5 row E2E-R7-2; lanes OPS,
// CONSOLE-A, CONSOLE-B; contracts K2 and K8):
// - the config read the Settings page makes, GET /agentcc/gateways/default/config/
//   (frontend/src/sections/gateway/providers/hooks/useGatewayConfig.js:130-141,
//   one react-query key, staleTime 60000), which passes the gateway's own
//   GET /-/config blocks through unchanged (futureagi/agentcc/views/gateway.py
//   _GATEWAY_CONFIG_BLOCKS, config) and adds the org's last_health_check /
//   last_health_check_status from a per-org cache record, null until the org's
//   first check (_with_last_health_check :297-305; health_check records "healthy");
// - the node's blocks (agentcc-gateway/internal/server/server.go configHandler:
//   server and logging from the node config, batch literals 'memory' / 10 /
//   10485760, org_sections_enforced from cmd/agentcc/main.go orgSectionsEnforced,
//   org_sections_not_enforced from middleware/clientip.go OrgIPACLClientIPUnknown);
//   the values per profile are e2e/stack/gateway.e2e.yaml (stock) and
//   gateway.r7alt.yaml (= stock + e2e/lib/profile.ts R7ALT_DELTA), as the
//   gateway's DefaultConfig/Load derive them (write 300 s, max request time unset
//   -> the write timeout, content_mode 'full'), confirmed by GET /-/config of the
//   stack built from this branch;
// - the console (frontend/src/sections/gateway/settings/GatewaySettingsSection.jsx):
//   Settings tab cards "Server Configuration", "Gateway Info", "Logging" whose
//   InfoLine is two Typography <p> siblings (label, value), formatMs (< 1000 "N ms",
//   else "N s"), onOff, enforcedSectionsText (settings/gatewayReport.js: "a, b", plus
//   "guardrails" when the config read's guardrail_pipeline.enabled is true:
//   org_sections_enforced has no guardrails entry), notEnforcedSectionsText
//   ("section: reason"), lastHealthCheckText ("<toLocaleString> (<status>)",
//   "No check recorded"); the "Health Check" header action (POST
//   /agentcc/gateways/default/health_check/, then refetchConfig :985-987, snackbar
//   "Health check complete"); the Batch Jobs tab's "Batch API" card (Storage,
//   Concurrency, Largest Submission via formatBytes) at TAB_SLUGS "batch-jobs"
//   (:944); Gateway Info's Name / Endpoint / Status / Providers / Models come from
//   GET /agentcc/gateways/ (views/gateway.py _build_virtual_gateway: name "Agent
//   Command Center Gateway", shown as the product's name "AI Gateway" by
//   gateway/utils/productName.js gatewayDisplayName; base_url = AGENTCC_GATEWAY_URL + "/v1", which the
//   Standalone stack leaves unset so agentcc/services/gateway_client.py
//   resolve_gateway_public_url answers "http://localhost:8090"; status "healthy";
//   _enrich_with_org_provider_counts: 0 and 0 for a fresh org with no provider);
//   Gateway Info's "Org Sections Not Enforced" (settings/gatewayReport.js
//   notEnforcedSectionsText): each section the node names with its reason, then
//   guardrails while guardrail_pipeline.enabled is false and cache while cache is
//   not in org_sections_enforced, "section: reason" joined by "; ";
// - the Org Config editor (settings/OrgConfigSection.jsx "Create Custom Config" for
//   an org with no config; settings/OrgConfigEditor.jsx dialog "Edit Organization
//   Config"; 798/850/917 pins in settings-visible-tabs.ts supersede the old hidden
//   Cache/A2A/Audit expectations). Cache stays editable with its activation note
//   inside the tab while the node is off. A2A/Audit are read-only and never sent.
//   SectionEnforcementNotes (settings/ui/SectionEnforcementNotes.jsx): IP ACL's
//   exact reason, without "saved but not applied", remains unchanged; the Security
//   tab's IP access control (settings/SecurityConfigTab.jsx): while ip_acl is not
//   enforced, the line "IP access control is off on this gateway: <reason>."
//   (data-testid ip-acl-off) in place of its "IP Access Control" panel and its
//   "Enable IP ACL" switch; the Rate Limiting tab's own K2 line "This gateway enforces these
//   limits." (settings/RateLimitingConfigTab.jsx EnforcementState), read first so
//   an empty note list afterwards means "no note", not "read pending".
// Every rewrite-bearing step opens with a full page.goto: the config query's
// staleTime 60000 would otherwise serve a read the Health Check just made, and
// frontend/src/app.jsx builds the one QueryClient with no persister.
// The admin's Health Check also re-pushes every org's config, any org's
// (views/gateway.py health_check -> _repush_org_configs -> push_all_org_configs,
// at most once per 5 minutes); this flow asserts nothing about that push, but it
// holds the stack lock from just before the click until the response (H9,
// lib/stack-lock.ts), so a parallel flow that needs no re-push inside a window
// (GW-E2E-009 #3, GW-E2E-016 #5) never sees this one.
// Settings tabs live in ?tab= (settings/GatewaySettingsSection.jsx tabPath; an old
// /settings/<tab> link is moved there).
const SETTINGS_PATH = '/dashboard/gateway/settings';
const BATCH_JOBS_PATH = '/dashboard/gateway/settings?tab=batch-jobs';
const ORG_CONFIG_PATH = '/dashboard/gateway/settings?tab=org-config';
const GATEWAY_CONFIG = '/agentcc/gateways/default/config/';
const HEALTH_CHECK = /\/agentcc\/gateways\/[^/]+\/health_check\/$/;
const UI_READY = 60_000;
const STEP_BUDGET = UI_READY + 15_000;
// Waits on another worker's hold of the re-push lock: GW-E2E-009 holds it for its
// unstamped precondition (POLL.GATEWAY_SYNC) and its Member Health Check step.
const REPUSH_LOCK_WAIT = POLL.GATEWAY_SYNC.timeout + STEP_BUDGET;
// 15 UI steps: four opening steps, five tab-note steps, policy, A2A, Audit,
// cache save, reopen, unchanged save. Plus the Health Check's lock wait,
// scopeActors provisioning 120s, API work 35s and headroom 30s = 1460s.
const FLOW_TIMEOUT = 15 * STEP_BUDGET + REPUSH_LOCK_WAIT + 120_000 + 35_000 + 30_000;
// frontend/src/utils/axios.js:2448-2449 and providers/hooks/useOrgConfig.js:19-25.
const UPDATE_CONFIG = '/agentcc/gateways/default/update-config/';
const ACTIVE_CONFIG = '/agentcc/org-configs/active/';
// CacheConfigTab.jsx:48-55 accepts a duration; futureagi/agentcc/serializers/
// org_config.py:299 normalizes via org_config_defaults._coerce_ttl_seconds
// (:12-25,55), so 37s reads back as 37. Deliberately unlike the default 5m.
const CACHE_TTL_INPUT = '37s';
const CACHE_TTL_SECONDS = 37;

// GET /-/config's blocks as this node runs them, per declared profile
// (lib/profile.ts settingsFixtureProfile(): the declaration, never what the stack reports).
// The mcp profile (H-MCP) is stock plus MCP only: MCP is neither a node block
// /-/config shows nor an org section the node enforces (cmd/agentcc/main.go
// orgSectionPlugins), so it expects exactly stock's values; so does mcp-catalog,
// which differs from mcp only in the connector catalogue, and mcp-signin, which differs
// from mcp only in MCP client sign-in.
const STOCK_NODE_BLOCKS: Record<string, unknown> = {
  server: { port: 8080, host: '0.0.0.0', read_timeout_ms: 5000, write_timeout_ms: 300000,
    idle_timeout_ms: 120000, max_request_timeout_ms: 300000 },
  logging: { level: 'info', request_logging: true, include_bodies: false, content_mode: 'full' },
  batch: { storage: 'memory', default_max_concurrency: 10, max_body_bytes: 10485760 },
  org_sections_enforced: ['alerting', 'budgets', 'rate_limiting', 'tool_policy'],
  org_sections_not_enforced: { ip_acl: 'client IP unknown (trusted_proxies not set)' },
};
const NODE_BLOCKS: Record<SettingsFixtureProfile, Record<string, unknown>> = {
  stock: STOCK_NODE_BLOCKS,
  mcp: STOCK_NODE_BLOCKS,
  'mcp-catalog': STOCK_NODE_BLOCKS,
  'mcp-signin': STOCK_NODE_BLOCKS,
  r7alt: {
    server: { port: 8080, host: '0.0.0.0', read_timeout_ms: 7000, write_timeout_ms: 300000,
      idle_timeout_ms: 90000, max_request_timeout_ms: 300000 },
    logging: { level: 'warn', request_logging: true, include_bodies: true, content_mode: 'full' },
    batch: { storage: 'memory', default_max_concurrency: 10, max_body_bytes: 10485760 },
    org_sections_enforced: ['alerting', 'budgets', 'cache', 'rate_limiting', 'tool_policy'],
    org_sections_not_enforced: { ip_acl: 'client IP unknown (trusted_proxies not set)' },
  },
};

// The three Settings cards, label -> value, as the console formats NODE_BLOCKS
// (Gateway Info's Last Health Check line is left out here; B4 reads it).
// Gateway Info names the base URL "Endpoint" and shows the status as its chip
// (settings/GeneralTab.jsx, settings/ui/GatewayHealthChip.jsx: healthy -> "Healthy").
const GATEWAY_INFO_COMMON = { Name: 'AI Gateway', Endpoint: 'http://localhost:8090/v1',
  Status: 'Healthy', Providers: '0', Models: '0' };
// Stock and MCP profiles run no cache; guardrails are enabled (gateway.e2e.yaml
// guardrails.enabled, no rule), so guardrails are listed among the enforced sections
// on both profiles.
const STOCK_SETTINGS_CARDS: Record<string, Record<string, string>> = {
  'Server Configuration': { Host: '0.0.0.0', Port: '8080', 'Read Timeout': '5 s', 'Write Timeout': '300 s',
    'Idle Timeout': '120 s', 'Max Request Time': '300 s' },
  'Gateway Info': { ...GATEWAY_INFO_COMMON, 'Org Sections Enforced': 'alerting, budgets, rate_limiting, tool_policy, guardrails',
    'Org Sections Not Enforced': 'ip_acl: client IP unknown (trusted_proxies not set); cache: response caching is off on this gateway' },
  Logging: { Level: 'info', 'Request Logging': 'Enabled', 'Include Bodies': 'No', 'Content Mode': 'full' },
};
const SETTINGS_CARDS: Record<SettingsFixtureProfile, Record<string, Record<string, string>>> = {
  stock: STOCK_SETTINGS_CARDS,
  mcp: STOCK_SETTINGS_CARDS,
  'mcp-catalog': STOCK_SETTINGS_CARDS,
  'mcp-signin': STOCK_SETTINGS_CARDS,
  r7alt: {
    'Server Configuration': { Host: '0.0.0.0', Port: '8080', 'Read Timeout': '7 s', 'Write Timeout': '300 s',
      'Idle Timeout': '90 s', 'Max Request Time': '300 s' },
    'Gateway Info': { ...GATEWAY_INFO_COMMON,
      'Org Sections Enforced': 'alerting, budgets, cache, rate_limiting, tool_policy, guardrails',
      'Org Sections Not Enforced': 'ip_acl: client IP unknown (trusted_proxies not set)' },
    Logging: { Level: 'warn', 'Request Logging': 'Enabled', 'Include Bodies': 'Yes', 'Content Mode': 'full' },
  },
};

// The Batch API card (K8 literals; the same on both profiles).
const BATCH_CARD = {
  Storage: "In this gateway node's memory: a restart loses its batches",
  Concurrency: 'Set per submission (10 when a submission sets none)',
  'Largest Submission': '10 MiB',
};

// sectionEnforcement.js enforcementSentence: the IP ACL reason alone, since
// the editor still offers no IP ACL control when this node cannot apply it.
const IP_ACL_NOTE = 'IP ACL: Not enforced on this gateway: client IP unknown (trusted_proxies not set).';
// OrgConfigEditor.jsx:213-225; profile expectations are NODE_BLOCKS above.
const CACHE_OFF_LINES: Record<SettingsFixtureProfile, number> = { stock: 1, mcp: 1, r7alt: 0, 'mcp-catalog': 1, 'mcp-signin': 1 };
// SecurityConfigTab.jsx: the line shown in place of the IP access control panel.
const IP_ACL_OFF_LINE = 'IP access control is off on this gateway: client IP unknown (trusted_proxies not set).';
const NOTE = /Not enforced on this gateway|enforcement unknown/;
const RATE_LIMIT_ENFORCED = 'This gateway enforces these limits.';
// Rate Limiting first (its K2 line shows the dialog's config read resolved).
const STOCK_TAB_NOTES: [string, string[]][] = [['Rate Limiting', []], ['Cache', []], ['Budgets', []],
  ['Security', [IP_ACL_NOTE]], ['Alerting', []]];
const TAB_NOTES: Record<SettingsFixtureProfile, [string, string[]][]> = {
  stock: STOCK_TAB_NOTES,
  mcp: STOCK_TAB_NOTES,
  'mcp-catalog': STOCK_TAB_NOTES,
  'mcp-signin': STOCK_TAB_NOTES,
  r7alt: [['Rate Limiting', []], ['Cache', []], ['Budgets', []], ['Security', [IP_ACL_NOTE]], ['Alerting', []]],
};

type ConfigRead = { result: Record<string, unknown> & {
  guardrail_pipeline?: { mode?: string; fail_open?: boolean };
  last_health_check: string | null; last_health_check_status: string | null } };
type ActiveConfig = { result: { cache: { enabled: boolean; default_ttl: number }; a2a: object; audit: object } };

const pick = (result: Record<string, unknown>) => ({
  server: result.server, logging: result.logging, batch: result.batch,
  org_sections_enforced: result.org_sections_enforced, org_sections_not_enforced: result.org_sections_not_enforced,
});

/**
 * A Settings panel's rows as label -> value: the panel is a section named by its
 * title (settings/ui/SettingsPanel.jsx) holding a dt/dd list (settings/ui/KeyValueList.jsx).
 */
const readPanel = async (page: Page, title: string) =>
  page.getByRole('region', { name: title, exact: true }).locator('dl').first().evaluate((dl) => {
    const out: Record<string, string> = {};
    dl.querySelectorAll(':scope > dt').forEach((dt) => {
      const dd = dt.nextElementSibling;
      if (dd?.tagName === 'DD') out[(dt.textContent ?? '').trim()] = (dd.textContent ?? '').trim();
    });
    return out;
  });

/** The Batch API card's InfoLines (two <p> siblings each) as label -> value (settings/batch/BatchJobsTab.jsx). */
const readCard = async (page: Page, title: string) => {
  const card = page.locator('.MuiCard-root').filter({ has: page.getByRole('heading', { name: title, exact: true }) });
  return card.locator('.MuiStack-root').evaluateAll((stacks) => Object.fromEntries(stacks
    .map((s) => Array.from(s.children))
    .filter((c) => c.length === 2 && c.every((e) => e.tagName === 'P'))
    .map(([label, value]) => [label.textContent ?? '', value.textContent ?? ''])));
};

test.describe.configure({ timeout: FLOW_TIMEOUT });

test(
  'GW-E2E-010: Gateway Settings show what this gateway runs, and Org Config notes only the org sections it does not enforce',
  {
    tag: ['@flow'],
    annotation: flowAnnotation({
      id: 'GW-E2E-010',
      area: 'gateway',
      userGoal:
        'An org admin reads what this gateway actually runs (timeouts, logging, batch limits and health), distinguishes stored settings from enforcement, and saves Cache settings even while node caching is off. All org tabs remain visible; A2A/Audit and effective Mode/Fail Open are read-only. Stock shows the Cache activation note, r7alt does not; the unchanged IP ACL note replaces controls the node cannot apply. This is a save/display proof, not node-enable-after-save or TTL expiry coverage.',
      steps: [
        'read the gateway config the Settings page reads, before opening any page',
        'open Gateway Settings and read the Server Configuration, Gateway Info and Logging cards',
        'click Health Check',
        'open the Batch Jobs tab with a full navigation and read the Batch API card',
        'open Org Config with a full navigation, read all tabs, the Rate Limiting, Cache, Budgets, Security and Alerting notes, and the unchanged IP ACL line',
        'inspect read-only effective Mode/Fail Open and empty A2A/Audit tabs; enable Response Cache, set TTL 37s and Save & Activate',
        'read the saved active config, reopen Cache to verify its controls rehydrate, and save an unchanged read-only tab without sending another mutation',
      ],
      backendChecks: [
        'the config read reports the gateway\'s own server, logging and batch settings and the org sections it enforces and does not, exactly as this node runs them',
        'a health check is recorded for the org: no check is recorded before the click, and after it the config read names one, status healthy',
        'the sole update-config request contains only cache enabled=true and default_ttl=37s, never A2A/Audit or effective node policy',
        'the active org config stores cache enabled=true and TTL 37 seconds; A2A/Audit stay empty, and an unchanged read-only save sends no request',
      ],
      browserChecks: [
        'the Server Configuration, Logging and Gateway Info cards show exactly the profile\'s values (Last Health Check aside), guardrails among the enforced sections',
        'after the Health Check, Last Health Check ends "(healthy)"',
        'the Batch API card shows memory storage, "10 when a submission sets none" and "10 MiB"',
        'the Org Config editor shows the same exact twelve tabs on every profile; Cache is editable and only cache-off nodes show its two-sentence activation note inside the tab',
        'A2A/Audit show exact read-only notices and empty states with no edit controls; effective Mode/Fail Open match the declared fixture policy and resist typing',
        'per Org Config tab the enforcement notes are exactly IP ACL only, on both profiles, with its reason and no "saved but not applied"',
        'the Security tab shows "IP access control is off on this gateway: client IP unknown (trusted_proxies not set)." and no IP access control panel or Enable IP ACL switch',
      ],
    }),
  },
  async ({ browser, scopeActors: scopes }, testInfo) => {
    test.setTimeout(FLOW_TIMEOUT);
    const profile = settingsFixtureProfile();
    const owner = scopes.ownerA;
    await testInfo.attach('seeded', {
      contentType: 'application/json',
      body: JSON.stringify({ profile, organizationId: owner.organizationId, ownerB: scopes.ownerB.organizationId,
        note: 'no seeding beyond scopeActors: a fresh org with no org config' }, null, 2),
    });

    await test.step('API: the config read reports this node exactly as it runs', async () => {
      const read = await owner.api.get<ConfigRead>(GATEWAY_CONFIG);
      expect(pick(read.result)).toEqual(NODE_BLOCKS[profile]);
      expect(read.result.guardrail_pipeline).toEqual(PROFILE_PIPELINE[profile]);
    });

    const context = await scopes.openContext(browser, owner);
    const page = await context.newPage();
    const patches: Request[] = [];
    page.on('request', (r) => {
      if (r.method() === 'POST' && new URL(r.url()).pathname === UPDATE_CONFIG) patches.push(r);
    });
    let repush: StackLock | undefined;
    try {
      await test.step('UI: the Settings cards show the node\'s values', async () => {
        await page.goto(SETTINGS_PATH, { waitUntil: 'domcontentloaded' });
        await expect(page.getByRole('heading', { name: 'Server Configuration', exact: true })).toBeVisible({ timeout: UI_READY });
        await expect.poll(async () => {
          const cards: Record<string, Record<string, string>> = {};
          for (const title of ['Server Configuration', 'Gateway Info', 'Logging']) cards[title] = await readPanel(page, title);
          delete cards['Gateway Info']['Last Health Check'];
          return cards;
        }, { timeout: UI_READY }).toEqual(SETTINGS_CARDS[profile]);
      }, { timeout: STEP_BUDGET });

      await test.step('Health Check: recorded for the org, and shown as healthy', async () => {
        const before = (await owner.api.get<ConfigRead>(GATEWAY_CONFIG)).result.last_health_check;
        repush = await StackLock.acquire(STACK_LOCKS.ORG_CONFIG_REPUSH, { timeoutMs: REPUSH_LOCK_WAIT });
        const checked = page.waitForResponse(
          (r) => r.request().method() === 'POST' && HEALTH_CHECK.test(new URL(r.url()).pathname), { timeout: UI_READY });
        await page.getByRole('button', { name: 'Health Check', exact: true }).click();
        expect((await checked).status()).toBe(200);
        await repush.release();
        const after = (await owner.api.get<ConfigRead>(GATEWAY_CONFIG)).result;
        const at = after.last_health_check;
        expect({ beforeAt: before, afterStatus: after.last_health_check_status,
          afterAtIsIso: typeof at === 'string' && /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}/.test(at) && !Number.isNaN(Date.parse(at)) })
          .toEqual({ beforeAt: null, afterStatus: 'healthy', afterAtIsIso: true });
        await expect(page.getByText('Health check complete', { exact: true })).toBeVisible({ timeout: UI_READY });
        const lastCheck = page.getByRole('region', { name: 'Gateway Info', exact: true })
          .getByText('Last Health Check', { exact: true }).locator('xpath=following-sibling::dd[1]');
        await expect(lastCheck).toHaveText(/ \(healthy\)$/, { timeout: UI_READY });
      }, { timeout: STEP_BUDGET + REPUSH_LOCK_WAIT });

      await test.step('UI: the Batch API card shows the node\'s batch limits', async () => {
        await page.goto(BATCH_JOBS_PATH, { waitUntil: 'domcontentloaded' });
        await expect(page.getByRole('heading', { name: 'Batch API', exact: true })).toBeVisible({ timeout: UI_READY });
        await expect.poll(() => readCard(page, 'Batch API'), { timeout: UI_READY }).toEqual(BATCH_CARD);
      }, { timeout: STEP_BUDGET });

      let dialog: Locator | undefined;
      await test.step('UI: open the Org Config editor and read its tabs', async () => {
        await page.goto(ORG_CONFIG_PATH, { waitUntil: 'domcontentloaded' });
        await page.getByRole('button', { name: 'Create Custom Config', exact: true }).click({ timeout: UI_READY });
        dialog = page.getByRole('dialog', { name: 'Edit Organization Config' });
        await expect(dialog).toBeVisible({ timeout: UI_READY });
        await expect(dialog.getByRole('tab')).toHaveText(EDITOR_TABS, { timeout: UI_READY });
        // The activation note belongs to Cache, not the Providers tab or tab bar.
        await expect(dialog.getByText(CACHE_OFF_LINE, { exact: true })).toHaveCount(0);
      }, { timeout: STEP_BUDGET });

      for (const [tab, notes] of TAB_NOTES[profile]) {
        await test.step(`UI: the ${tab} tab's enforcement notes`, async () => {
          const d = dialog!;
          await d.getByRole('tab', { name: tab, exact: true }).click();
          if (tab === 'Rate Limiting') {
            await expect(d.getByText(RATE_LIMIT_ENFORCED, { exact: true })).toBeVisible({ timeout: UI_READY });
          }
          await expect(d.getByRole('alert').filter({ hasText: NOTE })).toHaveText(notes, { timeout: UI_READY });
          if (tab === 'Cache') {
            await expect(d.getByText(CACHE_OFF_LINE, { exact: true })).toHaveCount(CACHE_OFF_LINES[profile], { timeout: UI_READY });
            await expect(d.getByLabel('Enable Response Cache', { exact: true })).toBeEnabled({ timeout: UI_READY });
          }
          if (tab === 'Security') {
            // Neither node applies an org's IP ACL: one line says so, and no IP control is offered.
            await expect(d.getByTestId('ip-acl-off')).toHaveText([IP_ACL_OFF_LINE], { timeout: UI_READY });
            await expect(d.getByText('IP Access Control', { exact: true })).toHaveCount(0);
            await expect(d.getByLabel('Enable IP ACL', { exact: true })).toHaveCount(0);
          }
        }, { timeout: STEP_BUDGET });
      }
      await test.step('UI: Mode and Fail Open are effective node values, read-only', async () => {
        await assertEffectivePolicy(dialog!, profile);
      }, { timeout: STEP_BUDGET });
      for (const label of ['A2A', 'Audit'] as const) {
        await test.step(`UI: ${label} remains visible and read-only`, async () => {
          await assertEmptyReadOnlyTab(dialog!, label);
        }, { timeout: STEP_BUDGET });
      }
      await test.step('UI + API: Cache saves on both off and on nodes, without read-only sections', async () => {
        expect(patches).toHaveLength(0);
        const d = dialog!;
        await d.getByRole('tab', { name: 'Cache', exact: true }).click();
        await d.getByLabel('Enable Response Cache', { exact: true }).check();
        const ttl = d.getByRole('textbox', { name: 'Default TTL', exact: true });
        await expect(ttl).toBeEditable({ timeout: UI_READY });
        await ttl.fill(CACHE_TTL_INPUT);
        const saved = page.waitForResponse((r) => r.request().method() === 'POST' &&
          new URL(r.url()).pathname === UPDATE_CONFIG, { timeout: UI_READY });
        await d.getByRole('button', { name: 'Save & Activate', exact: true }).click();
        expect((await saved).status()).toBe(200);
        await expect(d).toBeHidden({ timeout: UI_READY });
        expect(patches.map((r) => r.postDataJSON())).toEqual([
          { cache: { enabled: true, default_ttl: CACHE_TTL_INPUT } },
        ]);
        const { result } = await owner.api.get<ActiveConfig>(ACTIVE_CONFIG);
        expect({ enabled: result.cache.enabled, ttl: result.cache.default_ttl,
          a2a: result.a2a, audit: result.audit }).toEqual({
          enabled: true, ttl: CACHE_TTL_SECONDS, a2a: {}, audit: {},
        });
      }, { timeout: STEP_BUDGET });
      await test.step('UI: reopening Cache retains the saved values and node-off note', async () => {
        await page.goto(ORG_CONFIG_PATH, { waitUntil: 'domcontentloaded' });
        await page.getByRole('button', { name: 'Edit Config', exact: true }).click({ timeout: UI_READY });
        const d = dialog!;
        await expect(d.getByRole('tab')).toHaveText(EDITOR_TABS, { timeout: UI_READY });
        await d.getByRole('tab', { name: 'Cache', exact: true }).click();
        await expect(d.getByLabel('Enable Response Cache', { exact: true })).toBeChecked({ timeout: UI_READY });
        await expect(d.getByRole('textbox', { name: 'Default TTL', exact: true })).toHaveValue(String(CACHE_TTL_SECONDS), { timeout: UI_READY });
        await expect(d.getByText(CACHE_OFF_LINE, { exact: true })).toHaveCount(CACHE_OFF_LINES[profile], { timeout: UI_READY });
      }, { timeout: STEP_BUDGET });
      await test.step('UI + API: an unchanged read-only save sends nothing', async () => {
        await assertEmptyReadOnlyTab(dialog!, 'Audit');
        await dialog!.getByRole('button', { name: 'Save & Activate', exact: true }).click();
        await expect(page.getByText('No changes to save', { exact: true })).toBeVisible({ timeout: UI_READY });
        await expect(dialog!).toBeHidden({ timeout: UI_READY });
        expect(patches).toHaveLength(1);
      }, { timeout: STEP_BUDGET });
    } finally {
      await context.close();
      await repush?.release();
    }
  },
);
