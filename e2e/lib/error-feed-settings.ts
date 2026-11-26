import type { Browser, Locator, Page } from '@playwright/test';
import { expect, ScopeActors } from './scope-actors';
import type { ScopeActor } from './scope-actors';
import type { StateProbe } from './state-probe';

// tracer/urls.py + serializers/feed_escalation.py; same endpoint as the gear.
export const SETTINGS_PATH = '/tracer/feed/escalation-settings/';
export const UI_READY = 60_000;
export const LABELS = {
  min_findings: 'Escalate findings',
  entry_multiplier: 'Escalate times usual',
  entry_delta: 'Escalate above usual',
  entry_confirmation_minutes: 'Escalate hold for',
  recovery_min: 'Recover findings',
  recovery_multiplier: 'Recover times usual',
  recovery_delta: 'Recover above usual',
  recovery_confirmation_minutes: 'Recover hold for',
} as const;
export type Values = Record<keyof typeof LABELS, number>;
export interface Settings {
  values: Values; defaults: Values; revision: number; workspace_id: string;
  can_edit: boolean;
}
export interface SettingsEnvelope { result: Settings }

export function numbers(values: Values): Values {
  return Object.fromEntries(Object.entries(values).map(([key, value]) => [key, Number(value)])) as Values;
}

export async function readSettings(scopes: ScopeActors, actor: ScopeActor): Promise<Settings> {
  const response = await scopes.send<SettingsEnvelope>(actor, 'GET', SETTINGS_PATH);
  expect(response.status, 'requires an entitled managed Error Feed stack').toBe(200);
  expect(response.body.result.workspace_id).toBe(actor.workspaceId);
  return response.body.result;
}

export async function openSettings(browser: Browser, scopes: ScopeActors, actor: ScopeActor) {
  const context = await scopes.openContext(browser, actor);
  const page = await context.newPage();
  await page.goto('/dashboard/error-feed', { waitUntil: 'domcontentloaded' });
  const dialog = await reopenSettings(page);
  return { page, context, dialog };
}

export async function reopenSettings(page: Page): Promise<Locator> {
  await page.getByRole('button', { name: 'Escalation settings', exact: true }).click();
  const dialog = page.getByRole('dialog', { name: 'Escalation settings', exact: true });
  // All eight values live under the collapsed Fine-tune section.
  const fineTune = dialog.getByRole('button', { name: /^Fine-tune/ });
  await expect(fineTune).toBeVisible({ timeout: UI_READY });
  if ((await fineTune.getAttribute('aria-expanded')) !== 'true') await fineTune.click();
  await expect(dialog.getByRole('spinbutton', { name: LABELS.min_findings, exact: true }))
    .toBeVisible({ timeout: UI_READY });
  return dialog;
}

export async function fillValues(dialog: Locator, values: Values) {
  for (const [key, label] of Object.entries(LABELS)) {
    await dialog.getByRole('spinbutton', { name: label, exact: true }).fill(String(values[key as keyof Values]));
  }
}

export async function expectValues(dialog: Locator, values: Values) {
  for (const [key, label] of Object.entries(LABELS)) {
    await expect(dialog.getByRole('spinbutton', { name: label, exact: true }))
      .toHaveValue(String(values[key as keyof Values]));
  }
}

export async function saveSettings(page: Page, dialog: Locator, status = 200) {
  const response = page.waitForResponse(res => new URL(res.url()).pathname === SETTINGS_PATH
    && res.request().method() === 'PATCH', { timeout: UI_READY });
  await dialog.getByRole('button', { name: 'Save', exact: true }).click();
  expect((await response).status()).toBe(status);
  if (status === 200) await expect(dialog).toBeHidden({ timeout: UI_READY });
}

export async function expectStored(probe: StateProbe, actor: ScopeActor, values: Values, revision: number) {
  // 0117_feed_escalation_policy.py. The write is synchronous; no CDC wait.
  const rows = await probe.pg<Values & { revision: string; updated_by_id: string }>(
    `SELECT min_findings, entry_multiplier, entry_delta, entry_confirmation_minutes,
      recovery_min, recovery_multiplier, recovery_delta, recovery_confirmation_minutes,
      revision, updated_by_id FROM tracer_feed_escalation_policy WHERE workspace_id = $1`,
    [actor.workspaceId]);
  expect(rows).toHaveLength(1);
  const { revision: savedRevision, updated_by_id, ...saved } = rows[0];
  expect(numbers(saved)).toEqual(values);
  expect(Number(savedRevision)).toBe(revision);
  expect(updated_by_id).toBe(actor.userId);
}
