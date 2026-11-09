import type { Locator } from '@playwright/test';
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
// PipelinePolicyFields.jsx:12-51: node values, not stored org policy.
const POLICY_NOTICE = 'Mode and Fail Open are set by this gateway and are read-only here.';
const FAIL_OPEN_COPY = {
  on: "With guardrails on, this gateway's default is to skip a check that times out or panics and continue. Failed synchronous request rewrites are still refused, and a remote-service error still follows the check's threshold and action.",
  off: "With guardrails on, this gateway's default is to treat check errors or timeouts as failures. A check's action can block, warn or log. Failed synchronous request rewrites are still refused.",
};
// e2e/README.md:556-562: one short UI action's first-paint budget.
const UI_READY = 60_000;

export async function assertEmptyReadOnlyTab(dialog: Locator, label: 'A2A' | 'Audit') {
  const [, notice, empty] = READ_ONLY_TABS.find(([tab]) => tab === label)!;
  await dialog.getByRole('tab', { name: label, exact: true }).click();
  const body = dialog.locator('[data-read-only]').filter({ hasText: notice });
  await expect(body).toHaveCount(1, { timeout: UI_READY });
  await expect(body.getByText(notice, { exact: true })).toBeVisible({ timeout: UI_READY });
  await expect(body.getByText(empty, { exact: true })).toBeVisible({ timeout: UI_READY });
  await expect(body.locator('input, textarea, select, button, [contenteditable="true"]')).toHaveCount(0);
}

export async function assertEffectivePolicy(dialog: Locator, profile: SettingsFixtureProfile) {
  const pipeline = PROFILE_PIPELINE[profile];
  await dialog.getByRole('tab', { name: 'Guardrails', exact: true }).click();
  await expect(dialog.getByText(POLICY_NOTICE, { exact: true })).toBeVisible({ timeout: UI_READY });
  const mode = pipeline.mode === 'sequential' ? 'Sequential' : pipeline.mode;
  const failOpen = pipeline.fail_open ? 'On' : 'Off';
  for (const [label, value] of [['Mode', mode], ['Fail Open', failOpen]]) {
    const field = dialog.getByRole('textbox', { name: label, exact: true });
    await expect(field).toHaveValue(value, { timeout: UI_READY });
    await expect(field).toHaveJSProperty('readOnly', true, { timeout: UI_READY });
    await field.press('End');
    await field.pressSequentially('-attempted-edit');
    await expect(field).toHaveValue(value, { timeout: UI_READY });
  }
  await expect(dialog.getByText(FAIL_OPEN_COPY[pipeline.fail_open ? 'on' : 'off'], { exact: true }))
    .toBeVisible({ timeout: UI_READY });
}
