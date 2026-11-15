/* eslint-env node */
// Cloud-only visual evidence through the real authenticated route. No component mocks,
// custom theme, injected styles or fabricated results. Input stays on the disposable VM.
import { readFile, mkdir, writeFile } from 'node:fs/promises';
import { createRequire } from 'node:module';
import { resolve } from 'node:path';

if (process.platform !== 'linux') throw new Error('Run only on the labelled cloud test VM');
const require = createRequire(resolve('e2e/package.json'));
const { chromium, expect: baseExpect } = require('@playwright/test');
const expect = baseExpect.configure({ timeout: 60000 });
const input = JSON.parse(await readFile(process.env.J2_DRIVER_INPUT, 'utf8'));
const output = resolve(process.env.SHOTS_DIR || 'r7-shell-shots');
await mkdir(output, { recursive: true });
const browser = await chromium.launch();
const context = await browser.newContext({ baseURL: input.appUrl, viewport: { width: 1440, height: 1000 } });
await context.addInitScript((actor) => {
  try {
    localStorage.setItem('accessToken', actor.access);
    localStorage.setItem('refreshToken', actor.refresh);
    localStorage.setItem('rememberMe', 'true');
    sessionStorage.setItem('organizationId', actor.organizationId);
    sessionStorage.setItem('workspaceId', actor.workspaceId);
    sessionStorage.setItem('workspaceOrgId', actor.organizationId);
  } catch { /* about:blank has no storage; the app navigation does */ }
}, input.actor);
const page = await context.newPage();
page.setDefaultTimeout(60000);
const checks = [];
const shot = async (name) => {
  await page.evaluate(() => document.fonts.ready);
  const viewport = page.viewportSize();
  // The real shell owns an inner scroll region. Increase the browser viewport,
  // rather than injecting styles or clipping the lower sections out of evidence.
  const overflow = await page.evaluate(() => Math.max(0, ...[...document.querySelectorAll('main, main *')].filter((el) => ['auto', 'scroll'].includes(getComputedStyle(el).overflowY)).map((el) => el.scrollHeight - el.clientHeight)));
  if (overflow > 0 && !await page.getByRole('dialog').count()) await page.setViewportSize({ ...viewport, height: Math.min(6000, viewport.height + overflow + 64) });
  await page.screenshot({ path: resolve(output, `${name}.png`), fullPage: true, animations: 'disabled' });
  await page.setViewportSize(viewport);
  checks.push({ name, route: new URL(page.url()).pathname, viewport: page.viewportSize(), documentWidth: await page.evaluate(() => document.documentElement.scrollWidth), font: await page.locator('body').evaluate((el) => getComputedStyle(el).fontFamily) });
};
const theme = async (mode) => {
  await page.evaluate((value) => { const settings = JSON.parse(localStorage.getItem('settings') || '{}'); settings.themeMode = value; localStorage.setItem('settings', JSON.stringify(settings)); }, mode);
};
const route = (state = '') => `/dashboard/skills/contexts/${input.contextId}?tab=tests&version=${process.env.TEST_VERSION || input.versions?.head || 6}${state ? `&state=${state}` : ''}`;
try {
  await page.goto(route());
  await expect(page.getByText('Latest result', { exact: true })).toBeVisible();
  await theme('light'); await page.reload();
  await expect(page.getByText('Latest result', { exact: true })).toBeVisible();
  if (process.env.TEST_VERSION) await expect(page.getByRole('table', { name: 'Verification cells' })).toBeVisible();
  await shot('tests-landing');
  await page.getByLabel('Test type', { exact: true }).click();
  await page.getByRole('option', { name: 'Answer checks', exact: true }).click();
  await expect(page.getByText('No matching test runs', { exact: true })).toBeVisible();
  await shot('tests-filtered-empty');
  await page.getByRole('button', { name: 'Clear filters', exact: true }).click();
  const inspect = page.getByRole('button', { name: /Inspect Original refund failure Version tested repeat 1/ });
  if (await inspect.count()) {
    await inspect.focus(); await page.keyboard.press('Enter');
    await expect(page.getByText('Observed by the application', { exact: true })).toBeVisible();
    await shot('tests-inspect-keyboard');
    const reviewComment = `Synthetic review ${Date.now()}: verify the refund receipt before accepting this answer.`;
    await page.getByRole('radio', { name: 'Disagree with the automatic outcome', exact: true }).check();
    await page.getByLabel('Why', { exact: true }).fill(reviewComment);
    const savedReview = page.waitForResponse((response) => response.url().includes('/annotations/') && response.request().method() === 'POST');
    await page.getByRole('button', { name: 'Record review', exact: true }).click();
    if (!(await savedReview).ok()) throw new Error('Synthetic review was not saved');
    await expect(page.getByText('Disagrees', { exact: true }).first()).toBeVisible();
    await expect(page.getByText(reviewComment, { exact: true })).toBeVisible();
    await shot('tests-disagreement');
    await page.getByRole('button', { name: 'Close', exact: true }).click();
    await expect(inspect).toBeFocused();
  }
  const ceiling = page.getByRole('checkbox', { name: /I accept up to/ });
  if (await ceiling.count()) {
    await ceiling.check();
    await expect(page.getByRole('button', { name: 'Run application test', exact: true })).toBeEnabled();
    await shot('tests-application-run-enabled');
    await ceiling.uncheck();
  }
  for (const mode of ['light', 'dark']) {
    await theme(mode);
    for (const state of ['', 'loading', 'error', 'empty']) {
      await page.goto(route(state));
      await expect(page.getByText('Run a test', { exact: true })).toBeVisible();
      if (!state && process.env.TEST_VERSION) await expect(page.getByRole('table', { name: 'Verification cells' })).toBeVisible();
      // TST2: the empty state has no separate first-run button; the setup's own primary leads.
      if (state === 'empty') await expect(page.getByRole('button', { name: 'Run your first test', exact: true })).toHaveCount(0);
      if (!state || state === 'empty') await expect(page.getByRole('checkbox', { name: /I accept up to/ })).toBeEnabled();
      if (state === 'error') await expect(page.getByText("Couldn't load application test options.", { exact: true })).toBeVisible();
      await shot(`tests-${state || 'current'}-${mode}`);
    }
  }
  await page.goto(route());
  await page.getByLabel('Candidate version', { exact: true }).click();
  await page.getByRole('option', { name: 'v6', exact: true }).click();
  await expect(page.getByText('Earlier result', { exact: true })).toBeVisible();
  await shot('tests-earlier-selection');
  await page.getByRole('button', { name: 'Run on current selection', exact: true }).click();
  await expect(page.getByRole('combobox', { name: /^Candidate version/ })).toBeFocused();
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto(route());
  await expect(page.getByText('Latest result', { exact: true })).toBeVisible();
  if (process.env.TEST_VERSION) await expect(page.getByRole('table', { name: 'Verification cells' })).toBeVisible();
  await shot('tests-390');
  await page.setViewportSize({ width: 1440, height: 1000 });
  await theme('light');
  await page.goto('/dashboard/observe');
  await page.getByText(input.projectName, { exact: true }).click();
  await page.locator('.clean-data-table:visible .ag-row [col-id="trace_name"]').filter({ hasText: 'Synthetic refund failure' }).click();
  await page.getByRole('button', { name: 'Save as test case', exact: true }).click();
  const dialog = page.getByRole('dialog');
  await expect(dialog.getByRole('checkbox')).toHaveCount(2);
  for (const box of await dialog.getByRole('checkbox').all()) await expect(box).toBeChecked();
  await expect(dialog.getByRole('button', { name: 'Source identity' })).toHaveAttribute('aria-expanded', 'false');
  await shot('capture-selected-segments');
  await dialog.getByRole('button', { name: 'Cancel', exact: true }).click();
  await page.goto(route());
  await page.getByRole('radio', { name: /^Check answers with this context/ }).check();
  await page.getByRole('textbox', { name: 'Test request', exact: true }).fill('Synthetic refund check: receipt required');
  await page.getByRole('textbox', { name: 'Expected answer contains', exact: true }).fill('receipt');
  await page.getByRole('textbox', { name: 'Maximum estimated cost (USD)', exact: true }).fill('0.50');
  await page.getByRole('checkbox', { name: 'I reviewed this request and its expected answer.', exact: true }).check();
  const preview = page.getByRole('button', { name: 'Preview answer check', exact: true });
  await expect(preview).toBeEnabled();
  await shot('tests-answer-preview-enabled');
  await preview.click();
  await expect(page.getByRole('table', { name: 'Model-visible inventory' })).toBeVisible();
  await shot('inventory-before-drawer');
  const view = page.getByRole('button', { name: 'View AGENTS.md', exact: true });
  await view.focus(); await page.keyboard.press('Enter');
  await expect(page.getByText(/Matches the reviewed file/)).toBeVisible();
  await shot('inventory-file-keyboard');
  await page.getByRole('button', { name: 'Close file', exact: true }).click();
  await expect(view).toBeFocused();
  await page.getByRole('checkbox', { name: 'I reviewed the entire inventory and outbound inputs', exact: true }).check();
  await page.getByRole('checkbox', { name: 'I acknowledge provider processing of this reviewed context and test request.', exact: true }).check();
  await expect(page.getByRole('button', { name: 'Run answer check', exact: true })).toBeEnabled();
  await shot('tests-answer-run-enabled');
} finally {
  await writeFile(resolve(output, 'checks.json'), JSON.stringify(checks, null, 2));
  await browser.close();
}
