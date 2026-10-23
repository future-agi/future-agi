import type { Locator, Page } from '@playwright/test';
import { test, expect } from '../../lib/scope-actors';
import { flowAnnotation } from '../../lib/flow-meta';
import { gatewayProfile, type GatewayProfile } from '../../lib/profile';
import { StackLock, STACK_LOCKS } from '../../lib/stack-lock';
import { POLL } from '../../lib/state-probe';

// Pinned off the source:
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
//   gateway.alt.yaml (= stock + e2e/lib/profile.ts ALT_DELTA), as the
//   gateway's DefaultConfig/Load derive them (write 300 s, max request time unset
//   -> the write timeout, content_mode 'full'), as GET /-/config of the e2e
//   stack reports them;
// - the console (frontend/src/sections/gateway/settings/GatewaySettingsSection.jsx):
//   Settings tab cards "Server Configuration", "Gateway Info", "Logging" whose
//   InfoLine is two Typography <p> siblings (label, value), formatMs (< 1000 "N ms",
//   else "N s"), onOff, enforcedSectionsText ("a, b"), notEnforcedSectionsText
//   ("section: reason"), lastHealthCheckText ("<toLocaleString> (<status>)",
//   "No check recorded"); the Health Check header action (POST
//   /agentcc/gateways/default/health_check/, then refetchConfig :985-987, snackbar
//   "Health check complete"); the Batch Jobs tab's "Batch API" card (Storage,
//   Concurrency, Largest Submission via formatBytes) at TAB_SLUGS "batch-jobs"
//   (:944); Gateway Info's Name / Base URL / Status / Providers / Models come from
//   GET /agentcc/gateways/ (views/gateway.py _build_virtual_gateway: name "Agent
//   Command Center Gateway", base_url = AGENTCC_GATEWAY_URL + "/v1", which the
//   Standalone stack leaves unset so agentcc/services/gateway_client.py
//   resolve_gateway_public_url answers "http://localhost:8090"; status "healthy";
//   _enrich_with_org_provider_counts: 0 and 0 for a fresh org with no provider);
// - the Org Config editor (settings/OrgConfigSection.jsx "Create Custom Config" for
//   an org with no config; settings/OrgConfigEditor.jsx dialog "Edit Organization
//   Config", tabs Cache / Rate Limiting / Budgets / Security / Alerting,
//   EnforcementNotes: one outlined Alert per section the gateway does not enforce,
//   nothing while the config read is pending, enforcementSentence's exact
//   sentences); the Rate Limiting tab's own enforcement line "This gateway enforces these
//   limits." (settings/RateLimitingConfigTab.jsx EnforcementState), read first so
//   an empty note list afterwards means "no note", not "read pending".
// Every rewrite-bearing step opens with a full page.goto: the config query's
// staleTime 60000 would otherwise serve a read the Health Check just made, and
// frontend/src/app.jsx builds the one QueryClient with no persister.
// The admin's Health Check also re-pushes every org's config, any org's
// (views/gateway.py health_check -> _repush_org_configs -> push_all_org_configs,
// at most once per 5 minutes); this flow asserts nothing about that push, but it
// holds the stack lock from just before the click until the response
// (lib/stack-lock.ts), so a parallel flow that needs no re-push inside a window
// (GW-E2E-009 #3, GW-E2E-016 #5) never sees this one.
const SETTINGS_PATH = '/dashboard/gateway/settings';
const BATCH_JOBS_PATH = '/dashboard/gateway/settings/batch-jobs';
const ORG_CONFIG_PATH = '/dashboard/gateway/settings/org-config';
const GATEWAY_CONFIG = '/agentcc/gateways/default/config/';
const HEALTH_CHECK = /\/agentcc\/gateways\/[^/]+\/health_check\/$/;
const UI_READY = 60_000;
const STEP_BUDGET = UI_READY + 15_000;
// Waits on another worker's hold of the re-push lock: GW-E2E-009 holds it for its
// unstamped precondition (POLL.GATEWAY_SYNC) and its Member Health Check step.
const REPUSH_LOCK_WAIT = POLL.GATEWAY_SYNC.timeout + STEP_BUDGET;
// scopeActors provisioning 120 + #1 API 5 + cards step 75 + Health Check step 75 +
// REPUSH_LOCK_WAIT 150 + #2 2 + Batch Jobs step 75 + five editor tab steps 5 x 75
// (375) + headroom 30 = 907 s, rounded up.
const FLOW_TIMEOUT = 930_000;

// GET /-/config's blocks as this node runs them, per declared profile
// (lib/profile.ts gatewayProfile(): the declaration, never what the stack reports).
const NODE_BLOCKS: Record<GatewayProfile, Record<string, unknown>> = {
  stock: {
    server: { port: 8080, host: '0.0.0.0', read_timeout_ms: 5000, write_timeout_ms: 300000,
      idle_timeout_ms: 120000, max_request_timeout_ms: 300000 },
    logging: { level: 'info', request_logging: true, include_bodies: false, content_mode: 'full' },
    batch: { storage: 'memory', default_max_concurrency: 10, max_body_bytes: 10485760 },
    org_sections_enforced: ['alerting', 'budgets', 'rate_limiting', 'tool_policy'],
    org_sections_not_enforced: { ip_acl: 'client IP unknown (trusted_proxies not set)' },
  },
  alt: {
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
const GATEWAY_INFO_COMMON = { Name: 'Agent Command Center Gateway', 'Base URL': 'http://localhost:8090/v1',
  Status: 'healthy', Providers: '0', Models: '0' };
const SETTINGS_CARDS: Record<GatewayProfile, Record<string, Record<string, string>>> = {
  stock: {
    'Server Configuration': { Host: '0.0.0.0', Port: '8080', 'Read Timeout': '5 s', 'Write Timeout': '300 s',
      'Idle Timeout': '120 s', 'Max Request Time': '300 s' },
    'Gateway Info': { ...GATEWAY_INFO_COMMON, 'Org Sections Enforced': 'alerting, budgets, rate_limiting, tool_policy',
      'Org Sections Not Enforced': 'ip_acl: client IP unknown (trusted_proxies not set)' },
    Logging: { Level: 'info', 'Request Logging': 'Enabled', 'Include Bodies': 'No', 'Content Mode': 'full' },
  },
  alt: {
    'Server Configuration': { Host: '0.0.0.0', Port: '8080', 'Read Timeout': '7 s', 'Write Timeout': '300 s',
      'Idle Timeout': '90 s', 'Max Request Time': '300 s' },
    'Gateway Info': { ...GATEWAY_INFO_COMMON,
      'Org Sections Enforced': 'alerting, budgets, cache, rate_limiting, tool_policy',
      'Org Sections Not Enforced': 'ip_acl: client IP unknown (trusted_proxies not set)' },
    Logging: { Level: 'warn', 'Request Logging': 'Enabled', 'Include Bodies': 'Yes', 'Content Mode': 'full' },
  },
};

// The Batch API card (its literals, the same on both profiles).
const BATCH_CARD = {
  Storage: "In this gateway node's memory: a restart loses its batches",
  Concurrency: 'Set per submission (10 when a submission sets none)',
  'Largest Submission': '10 MiB',
};

// OrgConfigEditor.jsx enforcementSentence, for the two sections a stock node
// does not enforce.
const CACHE_NOTE = 'Cache: Not enforced on this gateway. Response caching is off on this gateway, so these settings are saved but not applied to requests.';
const IP_ACL_NOTE = 'IP ACL: Not enforced on this gateway: client IP unknown (trusted_proxies not set). These settings are saved but not applied to requests.';
const NOTE = /Not enforced on this gateway|enforcement unknown/;
const RATE_LIMIT_ENFORCED = 'This gateway enforces these limits.';
// Rate Limiting first (its enforcement line shows the dialog's config read resolved).
const TAB_NOTES: Record<GatewayProfile, [string, string[]][]> = {
  stock: [['Rate Limiting', []], ['Cache', [CACHE_NOTE]], ['Budgets', []], ['Security', [IP_ACL_NOTE]], ['Alerting', []]],
  alt: [['Rate Limiting', []], ['Cache', []], ['Budgets', []], ['Security', [IP_ACL_NOTE]], ['Alerting', []]],
};

type ConfigRead = { result: Record<string, unknown> & {
  last_health_check: string | null; last_health_check_status: string | null } };

const pick = (result: Record<string, unknown>) => ({
  server: result.server, logging: result.logging, batch: result.batch,
  org_sections_enforced: result.org_sections_enforced, org_sections_not_enforced: result.org_sections_not_enforced,
});

/** A Settings card's InfoLines (two <p> siblings each) as label -> value. */
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
        'An org admin reads in Gateway Settings what this gateway actually runs (its timeouts, logging, batch limits and last health check) and, in Org Config, which of the organization\'s settings this gateway does not enforce, and only those. This is proven for the cache section, the one a node turns on or off by its own config (stock vs alt); the IP ACL note the stock node shows (it resolves no client IP) is asserted as shown. Runs on the stock and alt gateway profiles; the expected values are the declared profile\'s',
      steps: [
        'read the gateway config the Settings page reads, before opening any page',
        'open Gateway Settings and read the Server Configuration, Gateway Info and Logging cards',
        'click Health Check',
        'open the Batch Jobs tab with a full navigation and read the Batch API card',
        'open Org Config with a full navigation, open the config editor, and read the notes above the Rate Limiting, Cache, Budgets, Security and Alerting tabs',
      ],
      backendChecks: [
        'the config read reports the gateway\'s own server, logging and batch settings and the org sections it enforces and does not, exactly as this node runs them',
        'a health check is recorded for the org: no check is recorded before the click, and after it the config read names one, status healthy',
      ],
      browserChecks: [
        'the Server Configuration, Logging and Gateway Info cards show exactly the profile\'s values (Last Health Check aside)',
        'after the Health Check, Last Health Check ends "(healthy)"',
        'the Batch API card shows memory storage, "10 when a submission sets none" and "10 MiB"',
        'per Org Config tab the enforcement notes are exactly: stock - Cache and IP ACL only; alt - IP ACL only',
      ],
    }),
  },
  async ({ browser, scopeActors: scopes }, testInfo) => {
    test.setTimeout(FLOW_TIMEOUT);
    const profile = gatewayProfile();
    const owner = scopes.ownerA;
    await testInfo.attach('seeded', {
      contentType: 'application/json',
      body: JSON.stringify({ profile, organizationId: owner.organizationId, ownerB: scopes.ownerB.organizationId,
        note: 'no seeding beyond scopeActors: a fresh org with no org config' }, null, 2),
    });

    await test.step('API: the config read reports this node exactly as it runs', async () => {
      const read = await owner.api.get<ConfigRead>(GATEWAY_CONFIG);
      expect(pick(read.result)).toEqual(NODE_BLOCKS[profile]);
    });

    const context = await scopes.openContext(browser, owner);
    const page = await context.newPage();
    let repush: StackLock | undefined;
    try {
      await test.step('UI: the Settings cards show the node\'s values', async () => {
        await page.goto(SETTINGS_PATH, { waitUntil: 'domcontentloaded' });
        await expect(page.getByRole('heading', { name: 'Server Configuration', exact: true })).toBeVisible({ timeout: UI_READY });
        await expect.poll(async () => {
          const cards: Record<string, Record<string, string>> = {};
          for (const title of ['Server Configuration', 'Gateway Info', 'Logging']) cards[title] = await readCard(page, title);
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
        const gatewayInfo = page.locator('.MuiCard-root').filter({ has: page.getByRole('heading', { name: 'Gateway Info', exact: true }) });
        const lastCheck = gatewayInfo.getByText('Last Health Check', { exact: true }).locator('xpath=following-sibling::p[1]');
        await expect(lastCheck).toHaveText(/ \(healthy\)$/, { timeout: UI_READY });
      }, { timeout: STEP_BUDGET + REPUSH_LOCK_WAIT });

      await test.step('UI: the Batch API card shows the node\'s batch limits', async () => {
        await page.goto(BATCH_JOBS_PATH, { waitUntil: 'domcontentloaded' });
        await expect(page.getByRole('heading', { name: 'Batch API', exact: true })).toBeVisible({ timeout: UI_READY });
        await expect.poll(() => readCard(page, 'Batch API'), { timeout: UI_READY }).toEqual(BATCH_CARD);
      }, { timeout: STEP_BUDGET });

      let dialog: Locator | undefined;
      await test.step('UI: open the Org Config editor', async () => {
        await page.goto(ORG_CONFIG_PATH, { waitUntil: 'domcontentloaded' });
        await page.getByRole('button', { name: 'Create Custom Config', exact: true }).click({ timeout: UI_READY });
        dialog = page.getByRole('dialog', { name: 'Edit Organization Config' });
        await expect(dialog).toBeVisible({ timeout: UI_READY });
      }, { timeout: STEP_BUDGET });

      for (const [tab, notes] of TAB_NOTES[profile]) {
        await test.step(`UI: the ${tab} tab's enforcement notes`, async () => {
          const d = dialog!;
          await d.getByRole('tab', { name: tab, exact: true }).click();
          if (tab === 'Rate Limiting') {
            await expect(d.getByText(RATE_LIMIT_ENFORCED, { exact: true })).toBeVisible({ timeout: UI_READY });
          }
          await expect(d.getByRole('alert').filter({ hasText: NOTE })).toHaveText(notes, { timeout: UI_READY });
        }, { timeout: STEP_BUDGET });
      }
    } finally {
      await context.close();
      await repush?.release();
    }
  },
);
