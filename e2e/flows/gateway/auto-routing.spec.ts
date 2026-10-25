import { request } from '@playwright/test';
import type { Request } from '@playwright/test';
import { test, expect } from '../../lib/scope-actors';
import { POLL } from '../../lib/state-probe';
import { E2E } from '../../lib/env';
import { flowAnnotation } from '../../lib/flow-meta';

// Pinned off the running system: the Models page's Auto routing tab
// (frontend/src/sections/gateway/models/ModelsSection.jsx TAB_SLUGS,
// AutoRoutingTab.jsx labels), the update-config action it saves through
// (useManagedModels.js useSaveAutoRouting -> agentcc/views/gateway.py
// update_config, an RFC 7386 merge patch: a null deletes the key,
// models/autoRouting.js autoPatch), the org config table the writer versions
// (agentcc/models/org_config.py, db_table agentcc_org_config), and the
// gateway's admin read of the config it was pushed
// (agentcc-gateway/internal/server/admin_orgs.go GetOrgConfig) with the
// admin token docker-compose.yml gives the gateway (AGENTCC_ADMIN_TOKEN).
const MODELS_PATH = '/dashboard/gateway/models';
const UPDATE_CONFIG = /\/agentcc\/gateways\/[^/]+\/update-config\/$/;
const ADMIN_TOKEN =
  process.env.AGENTCC_ADMIN_TOKEN || 'local-dev-only-admin-token-replace-me';
// Django pushes on save; the gateway's periodic sync (60 s) is the fallback.
const PUSH_VISIBLE = POLL.ASYNC_JOB;
const UI_READY = 60_000;
const STEP_BUDGET = UI_READY + 15_000;
// scopeActors provisioning 120 + four UI steps of STEP_BUDGET 75 (300) +
// two PUSH_VISIBLE polls of 60 (120) + headroom 30.
const FLOW_TIMEOUT = 570_000;

type OrgConfigRow = { version: number; routing: { auto?: Record<string, unknown> } };

// scopeActors signs its users up before the body runs, so its setup falls
// under the file's timeout, not the body's test.setTimeout: without this
// line it gets the 120 s project default.
test.describe.configure({ timeout: FLOW_TIMEOUT });

test(
  'GW-E2E-005: a saved auto routing preference and price caps reach the gateway and survive a reload',
  {
    tag: ['@flow'],
    annotation: flowAnnotation({
      id: 'GW-E2E-005',
      area: 'gateway',
      userGoal:
        'A platform engineer tunes how model "auto" picks a model, and the gateway serves with the saved tuning',
      steps: [
        'open Models, then the Auto routing tab',
        'choose the Quality preference and type a maximum input and output price',
        'save changes',
        'reload the page and see the saved preference and caps',
        'clear the output cap and save again',
      ],
      backendChecks: [
        'the save sends one update-config patch carrying only routing.auto with the preference and both caps',
        'the org\'s active PG agentcc_org_config version holds routing.auto with the Quality preference and both caps',
        'the gateway\'s own copy of the org config carries the same routing.auto',
        'after a reload the tab shows the saved preference and caps',
        'clearing a cap deletes that key in a new active version, keeps the rest, and reaches the gateway',
      ],
    }),
  },
  async ({ browser, scopeActors: scopes, scopeProbe: probe }, testInfo) => {
    test.setTimeout(FLOW_TIMEOUT);
    const owner = scopes.ownerA;
    const suffix = `${testInfo.workerIndex}-${Date.now().toString(36)}`;
    // Caps minted per run so the stored numbers exist nowhere else.
    const minted = 100 + (Date.now() % 900);
    const maxInput = Number(`1.${minted}`);
    const maxOutput = Number(`7.${minted}`);
    await testInfo.attach('seeded', {
      contentType: 'application/json',
      body: JSON.stringify({ suffix, organizationId: owner.organizationId, maxInput, maxOutput }, null, 2),
    });

    const activeConfig = async () => {
      const rows = await probe.pg<OrgConfigRow>(
        `SELECT version, routing FROM agentcc_org_config
          WHERE organization_id = $1 AND is_active AND NOT deleted`,
        [owner.organizationId],
      );
      expect(rows).toHaveLength(1);
      return rows[0];
    };
    const admin = await request.newContext();
    const gatewayAuto = async () => {
      const res = await admin.get(`${E2E.gatewayUrl}/-/orgs/${owner.organizationId}/config`, {
        headers: { Authorization: `Bearer ${ADMIN_TOKEN}` },
      });
      if (res.status() === 404) return null;
      expect(res.status(), await res.text()).toBe(200);
      return ((await res.json()) as { routing?: { auto?: unknown } }).routing?.auto ?? null;
    };

    const context = await scopes.openContext(browser, owner);
    const page = await context.newPage();
    const preference = page.getByRole('group', { name: 'Auto routing preference' });
    const quality = preference.getByRole('button', { name: /^Quality/ });
    const inputCap = page.getByLabel('Max input price');
    const outputCap = page.getByLabel('Max output price');
    const save = page.getByRole('button', { name: 'Save changes' });
    const patches: Request[] = [];
    page.on('request', (r) => {
      if (r.method() === 'POST' && UPDATE_CONFIG.test(new URL(r.url()).pathname)) patches.push(r);
    });

    let firstVersion = 0;
    try {
      await test.step('UI: choose Quality and both caps on the Auto routing tab, then save', async () => {
        await page.goto(MODELS_PATH, { waitUntil: 'domcontentloaded' });
        await page.getByRole('tab', { name: 'Auto routing' }).click();
        await expect(page).toHaveURL(new RegExp(`${MODELS_PATH}/auto$`), { timeout: UI_READY });
        await expect(quality).toBeEnabled({ timeout: UI_READY });
        await quality.click();
        await expect(quality).toHaveAttribute('aria-pressed', 'true', { timeout: UI_READY });
        await inputCap.fill(String(maxInput));
        await outputCap.fill(String(maxOutput));
        const saved = page.waitForResponse(
          (r) => UPDATE_CONFIG.test(new URL(r.url()).pathname) && r.request().method() === 'POST',
          { timeout: UI_READY },
        );
        await save.click();
        expect((await saved).status()).toBe(200);
        await expect(page.getByText('Auto routing saved')).toBeVisible({ timeout: UI_READY });
        expect(patches).toHaveLength(1);
        expect(patches[0].postDataJSON()).toEqual({
          routing: { auto: { preference: 'quality', max_input_per_mtok: maxInput, max_output_per_mtok: maxOutput } },
        });
      }, { timeout: STEP_BUDGET });

      await test.step('storage + gateway: the active version and the gateway hold the saved tuning', async () => {
        const row = await activeConfig();
        expect(row.routing.auto).toEqual({
          preference: 'quality', max_input_per_mtok: maxInput, max_output_per_mtok: maxOutput,
        });
        firstVersion = row.version;
        await expect.poll(gatewayAuto, PUSH_VISIBLE).toEqual({
          preference: 'quality', max_input_per_mtok: maxInput, max_output_per_mtok: maxOutput,
        });
      });

      await test.step('UI: a reload shows the saved preference and caps', async () => {
        await page.reload({ waitUntil: 'domcontentloaded' });
        await expect(quality).toHaveAttribute('aria-pressed', 'true', { timeout: UI_READY });
        await expect(preference.getByRole('button', { pressed: true })).toHaveCount(1, { timeout: UI_READY });
        await expect(inputCap).toHaveValue(String(maxInput), { timeout: UI_READY });
        await expect(outputCap).toHaveValue(String(maxOutput), { timeout: UI_READY });
        await expect(save).toBeDisabled({ timeout: UI_READY });
      }, { timeout: STEP_BUDGET });

      await test.step('UI: clearing the output cap saves a patch that deletes only that key', async () => {
        await outputCap.fill('');
        const saved = page.waitForResponse(
          (r) => UPDATE_CONFIG.test(new URL(r.url()).pathname) && r.request().method() === 'POST',
          { timeout: UI_READY },
        );
        await save.click();
        expect((await saved).status()).toBe(200);
        expect(patches).toHaveLength(2);
        expect(patches[1].postDataJSON()).toEqual({
          routing: { auto: { preference: 'quality', max_input_per_mtok: maxInput, max_output_per_mtok: null } },
        });
      }, { timeout: STEP_BUDGET });

      await test.step('storage + gateway: a new active version without the output cap', async () => {
        const row = await activeConfig();
        expect(row.version).toBeGreaterThan(firstVersion);
        expect(row.routing.auto).toEqual({ preference: 'quality', max_input_per_mtok: maxInput });
        await expect.poll(gatewayAuto, PUSH_VISIBLE).toEqual({
          preference: 'quality', max_input_per_mtok: maxInput,
        });
      });
    } finally {
      await admin.dispose();
      await context.close();
    }
  },
);
