import { request } from '@playwright/test';
import type { Page, Request } from '@playwright/test';
import { test, expect } from '../../lib/scope-actors';
import { POLL } from '../../lib/state-probe';
import { E2E } from '../../lib/env';
import { flowAnnotation } from '../../lib/flow-meta';

// Pinned off the source (org-config secrets, including the alert-channel credential URLs
// stored as secrets): the Settings page's Org Config and Full Config tabs
// (frontend/src/sections/gateway/settings/GatewaySettingsSection.jsx
// TAB_SLUGS "org-config" / "full-config", the Full Config search box and its
// "N match(es)" caption), the editor dialog (OrgConfigEditor.jsx: "Edit
// Organization Config", tab "Alerting", "Save & Activate";
// OrgConfigSection.jsx: "Create Custom Config" / "Edit Config"), the
// Alerting tab (AlertingConfigTab.jsx: "Enable per-org alerting", the
// "Channel name" box and "Add Channel", each channel's "URL" field), the
// write-only secret input (components/SecretField.jsx: dots, readonly,
// Replace), the update-config merge patch it saves through
// (hooks/useConfigPatch.js buildConfigPatch: a list is sent whole,
// agentcc/views/gateway.py update_config), which URLs carry a credential
// and so are secrets (agentcc/services/org_config_secrets.py
// alert_url_carries_credential: a path segment with a digit or an
// upper-case letter), the stored ciphertext and the read mask
// (org_config_secrets.py: "enc::" at rest, "__encrypted__" plus a 16-hex
// fingerprint on every read, secrets_set), the org config table
// (agentcc/models/org_config.py, db_table agentcc_org_config), and the
// gateway's admin read of the config it was pushed
// (agentcc-gateway/internal/server/admin_orgs.go GetOrgConfig, the operator's
// view of the config) with the admin token docker-compose.yml gives
// the gateway (AGENTCC_ADMIN_TOKEN).
const ORG_CONFIG_PATH = '/dashboard/gateway/settings/org-config';
const FULL_CONFIG_PATH = '/dashboard/gateway/settings/full-config';
const UPDATE_CONFIG = /\/agentcc\/gateways\/[^/]+\/update-config\/$/;
const ACTIVE_CONFIG = '/agentcc/org-configs/active/';
const GATEWAY_CONFIG = '/agentcc/gateways/default/config/';
const URL_POINTER = '/alerting/channels/0/url';
const READ_MASK = /^__encrypted__[0-9a-f]{16}$/;
const ADMIN_TOKEN =
  process.env.AGENTCC_ADMIN_TOKEN || 'local-dev-only-admin-token-replace-me';
// Django pushes on save; the gateway's periodic sync (60 s) is the fallback.
const PUSH_VISIBLE = POLL.ASYNC_JOB;
const UI_READY = 60_000;
const STEP_BUDGET = UI_READY + 15_000;
// scopeActors provisioning 120 + four UI steps of STEP_BUDGET 75 (300) +
// two PUSH_VISIBLE polls of 60 (120) + headroom 30.
const FLOW_TIMEOUT = 570_000;

type Channel = { name?: string; type?: string; url?: string };
type AlertingRow = { version: number; channels: Channel[] | null };
type ConfigRead = { result: { alerting?: { channels?: Channel[] }; secrets_set?: string[] } };

// scopeActors signs its users up before the body runs, so its setup falls
// under the file's timeout, not the body's test.setTimeout.
test.describe.configure({ timeout: FLOW_TIMEOUT });

test(
  'GW-E2E-008: a stored org-config alert channel credential URL is write-only: masked to every reader, encrypted at rest, still sent to the gateway',
  {
    tag: ['@flow'],
    annotation: flowAnnotation({
      id: 'GW-E2E-008',
      area: 'gateway',
      userGoal:
        'An org admin stores an alert channel whose webhook URL carries a credential; no member can read it back, and the gateway still gets it',
      steps: [
        'open Gateway Settings, then Org Config, and open the config editor',
        'on the Alerting tab enable per-org alerting, add a webhook channel and type its URL, which carries a token',
        'save and activate',
        'reopen the editor and see the stored URL as dots with a Replace action',
        'add a second channel with a plain URL and save again',
        'as a view-only member, search the Full Config view for the URL',
      ],
      backendChecks: [
        'the first save sends one update-config patch carrying the typed channel and its URL',
        'the active PG agentcc_org_config version stores the URL as enc:: ciphertext, never the typed value',
        'the gateway\'s own copy of the org config carries the typed URL in clear',
        'a view-only member reads the URL on org-configs/active and gateways/default/config only as the read mask, listed in secrets_set',
        'the second save\'s patch carries the stored URL only as its read mask, never the URL; the new active version keeps the same ciphertext and the gateway keeps the URL',
      ],
      browserChecks: [
        'the reopened editor shows the stored URL as read-only dots with Replace, never the value or the mask',
        'the view-only member\'s Full Config search finds the URL\'s pointer once and its value nowhere',
      ],
    }),
  },
  async ({ browser, scopeActors: scopes, scopeProbe: probe }, testInfo) => {
    test.setTimeout(FLOW_TIMEOUT);
    const owner = scopes.ownerA;
    const viewer = scopes.viewer;
    const suffix = `${testInfo.workerIndex}-${Date.now().toString(36)}`;
    // Minted per run so the stored values exist nowhere else. The token
    // segment holds digits and upper-case letters, so the URL carries a
    // credential and is a secret; the second channel's URL is a plain one.
    const channel = `ops-${suffix}`;
    const url = `https://hooks.e2e.invalid/services/T${suffix.replace(/[^a-z0-9]/g, '')}/GW008Secret${Date.now()}`;
    const plainName = `audit-${suffix}`;
    const plainUrl = 'https://alerts.e2e.invalid/hooks/on-call';
    await testInfo.attach('seeded', {
      contentType: 'application/json',
      body: JSON.stringify({ suffix, organizationId: owner.organizationId, channel, plainUrl }, null, 2),
    });

    const activeChannels = async () => {
      const rows = await probe.pg<AlertingRow>(
        `SELECT version, alerting->'channels' AS channels FROM agentcc_org_config
          WHERE organization_id = $1 AND is_active AND NOT deleted`,
        [owner.organizationId],
      );
      expect(rows).toHaveLength(1);
      return rows[0];
    };
    const admin = await request.newContext();
    const gatewayChannels = async () => {
      const res = await admin.get(`${E2E.gatewayUrl}/-/orgs/${owner.organizationId}/config`, {
        headers: { Authorization: `Bearer ${ADMIN_TOKEN}` },
      });
      if (res.status() === 404) return null;
      expect(res.status(), await res.text()).toBe(200);
      const channels = ((await res.json()) as { alerting?: { channels?: Channel[] } }).alerting?.channels;
      return channels ? channels.map((c) => ({ name: c.name ?? null, url: c.url ?? null })) : null;
    };

    const context = await scopes.openContext(browser, owner);
    const page = await context.newPage();
    const patches: Request[] = [];
    page.on('request', (r) => {
      if (r.method() === 'POST' && UPDATE_CONFIG.test(new URL(r.url()).pathname)) patches.push(r);
    });
    const openAlerting = async (p: Page) => {
      await p.goto(ORG_CONFIG_PATH, { waitUntil: 'domcontentloaded' });
      await p
        .getByRole('button', { name: /^(Edit Config|Create Custom Config)$/ })
        .click({ timeout: UI_READY });
      const dialog = p.getByRole('dialog', { name: 'Edit Organization Config' });
      await expect(dialog).toBeVisible({ timeout: UI_READY });
      await dialog.getByRole('tab', { name: 'Alerting', exact: true }).click();
      return dialog;
    };
    const saveAndWait = async (p: Page) => {
      const saved = p.waitForResponse(
        (r) => UPDATE_CONFIG.test(new URL(r.url()).pathname) && r.request().method() === 'POST',
        { timeout: UI_READY },
      );
      await p.getByRole('button', { name: 'Save & Activate' }).click();
      expect((await saved).status()).toBe(200);
    };
    const addChannel = async (dialog: ReturnType<Page['getByRole']>, name: string) => {
      await dialog.getByPlaceholder('Channel name').fill(name);
      await dialog.getByRole('button', { name: 'Add Channel', exact: true }).click();
    };

    let firstVersion = 0;
    let ciphertext = '';
    let mask = '';
    try {
      await test.step('UI: add a webhook channel whose URL carries a token on the Alerting tab, then save', async () => {
        const dialog = await openAlerting(page);
        await dialog.getByLabel('Enable per-org alerting', { exact: true }).check();
        await addChannel(dialog, channel);
        const field = dialog.getByLabel('URL', { exact: true });
        await field.fill(url);
        await expect(field).toHaveValue(url);
        await saveAndWait(page);
        expect(patches).toHaveLength(1);
        expect(patches[0].postDataJSON()).toEqual({
          alerting: { enabled: true, channels: [{ name: channel, type: 'webhook', url }] },
        });
      }, { timeout: STEP_BUDGET });

      await test.step('storage + gateway: ciphertext at rest, the clear URL at the gateway', async () => {
        const row = await activeChannels();
        firstVersion = row.version;
        ciphertext = String(row.channels?.[0]?.url ?? '');
        expect(ciphertext.startsWith('enc::')).toBe(true);
        expect(JSON.stringify(row.channels)).not.toContain(url);
        expect(row.channels?.[0]?.name).toBe(channel);
        await expect.poll(gatewayChannels, PUSH_VISIBLE).toEqual([{ name: channel, url }]);
      });

      await test.step('API: a view-only member reads only the mask, listed in secrets_set', async () => {
        for (const path of [ACTIVE_CONFIG, GATEWAY_CONFIG]) {
          const body = await viewer.api.get<ConfigRead>(path);
          expect(JSON.stringify(body), path).not.toContain(url);
          expect(JSON.stringify(body), path).not.toContain(ciphertext);
          expect(String(body.result.alerting?.channels?.[0]?.url), path).toMatch(READ_MASK);
          expect(body.result.secrets_set, path).toContain(URL_POINTER);
          mask = String(body.result.alerting?.channels?.[0]?.url);
        }
      });

      await test.step('UI: reopened, the stored URL shows as dots with Replace', async () => {
        await page.reload({ waitUntil: 'domcontentloaded' });
        const dialog = await openAlerting(page);
        const field = dialog.getByLabel('URL', { exact: true });
        await expect(field).toHaveValue(/^•+$/, { timeout: UI_READY });
        await expect(field).toHaveAttribute('readonly', '');
        await expect(dialog.getByRole('button', { name: 'Replace', exact: true })).toBeVisible();
        await expect(dialog).not.toContainText(url);
        await expect(dialog).not.toContainText('__encrypted__');
      }, { timeout: STEP_BUDGET });

      await test.step('UI: adding a second channel saves the first one\'s URL as its mask only', async () => {
        const dialog = page.getByRole('dialog', { name: 'Edit Organization Config' });
        await addChannel(dialog, plainName);
        await dialog.getByLabel('URL', { exact: true }).last().fill(plainUrl);
        await saveAndWait(page);
        expect(patches).toHaveLength(2);
        expect(JSON.stringify(patches[1].postDataJSON())).not.toContain(url);
        expect(patches[1].postDataJSON()).toEqual({
          alerting: {
            channels: [
              { name: channel, type: 'webhook', url: mask },
              { name: plainName, type: 'webhook', url: plainUrl },
            ],
          },
        });
      }, { timeout: STEP_BUDGET });

      await test.step('storage + gateway: a new version keeps the same ciphertext; the gateway keeps the URL', async () => {
        const row = await activeChannels();
        expect(row.version).toBeGreaterThan(firstVersion);
        expect(row.channels?.[0]?.url).toBe(ciphertext);
        expect(row.channels?.[1]?.url).toBe(plainUrl);
        await expect
          .poll(gatewayChannels, PUSH_VISIBLE)
          .toEqual([{ name: channel, url }, { name: plainName, url: plainUrl }]);
      });

      await test.step('UI: the view-only member\'s Full Config search finds no URL', async () => {
        const viewerContext = await scopes.openContext(browser, viewer);
        const viewerPage = await viewerContext.newPage();
        await viewerPage.goto(FULL_CONFIG_PATH, { waitUntil: 'domcontentloaded' });
        const search = viewerPage.getByPlaceholder('Search config...');
        await expect(search).toBeVisible({ timeout: UI_READY });
        // The stored secret is in the document: its pointer and its mask.
        await search.fill(URL_POINTER);
        await expect(viewerPage.getByText(/^1 match$/)).toBeVisible({ timeout: UI_READY });
        // Its value is not.
        await search.fill(url);
        await expect(viewerPage.getByText(/^0 matches$/)).toBeVisible({ timeout: UI_READY });
        await viewerContext.close();
      }, { timeout: STEP_BUDGET });
    } finally {
      await admin.dispose();
      await context.close();
    }
  },
);
