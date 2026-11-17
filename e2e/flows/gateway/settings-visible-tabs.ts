import { test as playwrightTest, type Locator } from '@playwright/test';
import { expect } from '../../lib/scope-actors';
import type { SettingsFixtureProfile } from './settings-fixture-profile';

// Declared fixtures, never the live report: lib/managed-mock.ts:74-80 admits
// guardrails.enabled=true only; lib/profile.ts deltas leave this policy alone.
// agentcc-gateway/internal/config/config.go:1779-1783 defaults fail_open=true;
// internal/server/server.go:1067-1071 reports mode='sequential' on every node.
const FIXTURE_PIPELINE = { enabled: true, mode: 'sequential', fail_open: true };
export const PROFILE_PIPELINE: Record<SettingsFixtureProfile, typeof FIXTURE_PIPELINE> = {
  stock: FIXTURE_PIPELINE, r7alt: FIXTURE_PIPELINE, mcp: FIXTURE_PIPELINE,
  'mcp-catalog': FIXTURE_PIPELINE, 'mcp-signin': FIXTURE_PIPELINE,
};

// 824c4ea31a: frontend/src/sections/gateway/settings/orgConfigTabs.js:11-33;
// shell/page-status.js:48-53. Identical on cache-on, off and unknown nodes.
export const EDITOR_TABS = ['Providers', 'Guardrails', 'Routing', 'Cache', 'Rate Limiting',
  'Budgets', 'Security', 'Alerting', 'Model Database', 'Model Map', 'A2A', 'Audit'];
// OrgConfigEditor.jsx:213-225; shown INSIDE Cache, never instead of that tab.
export const CACHE_OFF_LINE = 'Response caching is off on this gateway. Changes you save apply when response caching is turned on.';
// ReadOnlyOrgConfigTab.jsx:10-15,22-44. Fresh org: no stored sections or controls.
const READ_ONLY_TABS = [
  ['A2A', 'A2A settings are read-only here. This editor does not send A2A settings when you save.', 'No A2A settings are stored.'],
  ['Audit', 'Audit settings are read-only here. This editor does not send audit settings when you save.', 'No audit settings are stored.'],
] as const;
// GuardrailConfigTab.jsx:891-894 (captions shown while the org stored neither
// setting) and :1315-1316 (always shown). Mode and Fail Open are the org's own
// editable controls here; the node's report only seeds Fail Open while unset.
const FAIL_OPEN_NOT_SET = "Not set for this organization. The gateway's own Fail Open setting applies (on by default).";
const MODE_NOT_SET = 'Not set for this organization. Checks run one at a time, and the first check that blocks stops the rest.';
const FAIL_OPEN_REWRITE = 'Fail Open never applies to a check that rewrites the request: if one does not finish, the request is refused.';
const OLD_POLICY_NOTICE = 'Mode and Fail Open are set by this gateway and are read-only here.';
// e2e/README.md:556-562: one short UI action's first-paint budget.
const UI_READY = 60_000;

const testInfo = () => playwrightTest.info();

export async function assertEmptyReadOnlyTab(dialog: Locator, label: 'A2A' | 'Audit') {
  const [, notice, empty] = READ_ONLY_TABS.find(([tab]) => tab === label)!;
  await dialog.getByRole('tab', { name: label, exact: true }).click();
  const body = dialog.locator('[data-read-only]').filter({ hasText: notice });
  await expect(body).toHaveCount(1, { timeout: UI_READY });
  await expect(body.getByText(notice, { exact: true })).toBeVisible({ timeout: UI_READY });
  await expect(body.getByText(empty, { exact: true })).toBeVisible({ timeout: UI_READY });
  await expect(body.locator('input, textarea, select, button, [contenteditable="true"]')).toHaveCount(0);
}

// Fresh org with no stored guardrails section (settings-enforcement.spec.ts opens
// "Create Custom Config"): GuardrailConfigTab.jsx:1266-1292 renders Mode as an
// editable select (role combobox, text "Not set", :895-900) and Fail Open as an
// editable switch (role checkbox) showing the node's report (:936-942). This only
// reads them; it never changes or saves anything.
export async function assertEffectivePolicy(dialog: Locator, profile: SettingsFixtureProfile) {
  const pipeline = PROFILE_PIPELINE[profile];
  await dialog.getByRole('tab', { name: 'Guardrails', exact: true }).click();
  // Pipeline Settings card (GuardrailConfigTab.jsx:1260): wait for the tab body, then
  // the Mode select (MUI TextField select, aria-labelledby its "Mode" label).
  await expect(dialog.getByText('Pipeline Settings', { exact: true })).toBeVisible({ timeout: UI_READY });
  const mode = dialog.getByRole('combobox', { name: /^Mode\b/ });
  try {
    await expect(mode).toBeVisible({ timeout: UI_READY });
  } catch (error) {
    // Keep the real DOM when this ever fails: every combobox's name source and text.
    const seen = await dialog.getByRole('combobox').evaluateAll((els) => els.map((el) => ({
      labelledby: el.getAttribute('aria-labelledby'),
      label: (el.getAttribute('aria-labelledby') ?? '').split(' ').map((id) => document.getElementById(id)?.textContent ?? '').join('|'),
      text: el.textContent,
    })));
    await testInfo().attach('guardrails-tab-comboboxes', { body: JSON.stringify(seen, null, 2), contentType: 'application/json' });
    throw error;
  }
  await expect(mode).toHaveText('Not set', { timeout: UI_READY });
  const failOpen = dialog.getByRole('checkbox', { name: 'Fail Open', exact: true });
  await expect(failOpen).toBeEnabled({ timeout: UI_READY });
  if (pipeline.fail_open) await expect(failOpen).toBeChecked({ timeout: UI_READY });
  else await expect(failOpen).not.toBeChecked({ timeout: UI_READY });
  await expect(dialog.getByRole('textbox', { name: 'Fail Open', exact: true })).toHaveCount(0);
  await expect(dialog.getByText(MODE_NOT_SET, { exact: true })).toHaveCount(1, { timeout: UI_READY });
  await expect(dialog.getByText(FAIL_OPEN_NOT_SET, { exact: true })).toHaveCount(1, { timeout: UI_READY });
  await expect(dialog.getByText(FAIL_OPEN_REWRITE, { exact: true })).toHaveCount(1, { timeout: UI_READY });
  await expect(dialog.getByText(OLD_POLICY_NOTICE, { exact: true })).toHaveCount(0);
}
