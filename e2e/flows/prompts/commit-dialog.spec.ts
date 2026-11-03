import { test, expect } from '../../lib/fixtures';
import { flowAnnotation } from '../../lib/flow-meta';
import { POLL } from '../../lib/state-probe';

// PromptTemplateViewSet.create_draft/commit and SaveAndCommit.jsx.
const PROMPTS = '/model-hub/prompt-templates/';
const DEFAULT_ACTION = 'Commit and set as a default version';
const MESSAGE_PLACEHOLDER = 'Enter a commit message for this version...';
const UI_READY = 60_000;

interface Version {
  id: string;
  commit_message: string;
  is_default: boolean;
  is_draft: boolean;
}

test('PROMPT-E2E-006: commit availability and retry are clear in both themes', {
  tag: ['@flow'],
  annotation: flowAnnotation({
    id: 'PROMPT-E2E-006', area: 'prompts',
    userGoal: 'Commit an existing non-draft prompt version with clear empty, ready, pending and retry states in both themes.',
    steps: [
      'seed a synthetic non-draft version through the public API without executing a model',
      'open Commit in light and dark and enter, clear and re-enter a message',
      'hold the first save, observe loading, and inject one HTTP 500',
      'retry the retained message against the real backend and close on success',
      'reopen the empty dialog and commit the same version as default',
    ],
    backendChecks: [
      'The real commit endpoint stores the exact retry message and preserves the non-default version, then stores the default-action message and sets the same version as default.',
    ],
    browserChecks: [
      'The ordinary Commit label changes from disabled to active theme text after typing, and back on clearing, in light and dark.',
      'Pending disables both actions and shows loading; failure retains the message and permits retry; success closes and resets the dialog.',
    ],
  }),
}, async ({ page, actor, probe }, testInfo) => {
  test.setTimeout(840_000); // 2 themes x (6 UI_READY steps + ASYNC_JOB persistence).
  page.setDefaultTimeout(UI_READY);

  for (const mode of ['light', 'dark']) {
    const name = `e2e-prompt6-${testInfo.workerIndex}-${Date.now().toString(36)}-${mode}`;
    const message = `${name}-retry`;
    const defaultMessage = `${name}-default`;
    // create_draft accepts is_draft and prompt_config. The version has no output:
    // this flow exercises committing an existing version, not Run Prompt.
    const seeded = await actor.api.post<{ result: { root_template: string } }>(`${PROMPTS}create-draft/`, {
      name, is_draft: false,
      prompt_config: [{ messages: [{ role: 'user', content: [{ type: 'text', text: 'Synthetic support instructions' }] }],
        configuration: { model: '', tools: [], output_format: 'string' } }],
    });
    const promptId = seeded.result.root_template;
    const commitPath = `${PROMPTS}${promptId}/commit/`;
    const readVersion = () => probe.pg<Version>(
      `SELECT v.id, v.commit_message, v.is_default, v.is_draft
       FROM model_hub_promptversion v JOIN model_hub_prompttemplate p ON p.id=v.original_template_id
       WHERE p.id=$1 AND p.organization_id=$2 AND v.template_version='v1' AND v.deleted=false`,
      [promptId, actor.organizationId],
    );
    const initial = await readVersion();
    expect(initial).toHaveLength(1);
    expect(initial[0]).toMatchObject({ is_draft: false, is_default: false });
    await testInfo.attach(`${mode}-seed`, { contentType: 'application/json',
      body: JSON.stringify({ promptId, versionId: initial[0].id, name, message, defaultMessage }) });

    // SettingsProvider restores the settings record; all other preferences retain defaults.
    if (mode === 'light') {
      await page.addInitScript(() => {
        if (!localStorage.getItem('settings')) {
          localStorage.setItem('settings', JSON.stringify({ themeMode: 'light' }));
        }
      });
    } else {
      await page.evaluate(() => {
        localStorage.setItem('settings', JSON.stringify({ themeMode: 'dark' }));
      });
    }
    await page.goto(`/dashboard/workbench/create/${promptId}`, { waitUntil: 'domcontentloaded' });
    await expect(page.locator('body')).toHaveAttribute('data-theme', mode, { timeout: UI_READY });
    await page.getByRole('button', { name: 'Commit', exact: true }).click();
    const dialog = page.getByRole('dialog');
    const commit = dialog.getByRole('button', { name: 'Commit', exact: true });
    const setDefault = dialog.getByRole('button', { name: DEFAULT_ACTION, exact: true });
    const field = dialog.getByPlaceholder(MESSAGE_PLACEHOLDER);
    // ThemeProvider publishes these palette tokens; compare actual computed colors,
    // never a CSS class or the stock MUI disabled color.
    const colors = await page.locator('body').evaluate(body => {
      const style = getComputedStyle(body);
      const normalize = (value: string) => {
        const sample = document.createElement('span');
        sample.style.color = value;
        body.append(sample);
        const color = getComputedStyle(sample).color;
        sample.remove();
        return color;
      };
      return { active: normalize(style.getPropertyValue('--text-primary')),
        disabled: normalize(style.getPropertyValue('--text-disabled')) };
    });
    await expect(commit).toBeDisabled({ timeout: UI_READY });
    await expect(setDefault).toBeDisabled({ timeout: UI_READY });
    await expect(commit).toHaveCSS('color', colors.disabled, { timeout: UI_READY });
    await testInfo.attach(`${mode}-empty`, { body: await dialog.screenshot({ animations: 'disabled' }), contentType: 'image/png' });
    await field.fill(message);
    await expect(commit).toBeEnabled({ timeout: UI_READY });
    await expect(setDefault).toBeEnabled({ timeout: UI_READY });
    await testInfo.attach(`${mode}-entered`, { body: await dialog.screenshot({ animations: 'disabled' }), contentType: 'image/png' });
    await expect(commit).toHaveCSS('color', colors.active, { timeout: UI_READY });
    expect(colors.active).not.toBe(colors.disabled);
    await field.clear();
    await expect(commit).toBeDisabled({ timeout: UI_READY });
    await expect(commit).toHaveCSS('color', colors.disabled, { timeout: UI_READY });
    await field.fill(message);

    let releaseFailure!: () => void;
    const pending = new Promise<void>(resolve => { releaseFailure = resolve; });
    // Fault injection only for the first request. Retry and default saves go to Django.
    const failOnce = async (route: import('@playwright/test').Route) => {
      await pending;
      await route.fulfill({ status: 500, contentType: 'application/json',
        body: JSON.stringify({ message: 'Synthetic save failure' }) });
    };
    await page.route(`**${commitPath}`, failOnce, { times: 1 });
    try {
      await commit.click();
      await expect(commit).toBeDisabled({ timeout: UI_READY });
      await expect(setDefault).toBeDisabled({ timeout: UI_READY });
      await expect(dialog.getByRole('progressbar')).toHaveCount(2, { timeout: UI_READY });
      await testInfo.attach(`${mode}-pending`, { body: await dialog.screenshot({ animations: 'disabled' }), contentType: 'image/png' });
    } finally {
      releaseFailure();
    }
    await expect(commit).toBeEnabled({ timeout: UI_READY });
    await expect(setDefault).toBeEnabled({ timeout: UI_READY });
    await expect(field).toHaveValue(message, { timeout: UI_READY });
    await expect(commit).toHaveCSS('color', colors.active, { timeout: UI_READY });
    await expect(dialog.getByRole('progressbar')).toHaveCount(0, { timeout: UI_READY });
    expect(await readVersion()).toEqual(initial);

    for (const [action, savedMessage, isDefault] of [
      ['Commit', message, false], [DEFAULT_ACTION, defaultMessage, true],
    ] as const) {
      if (isDefault) {
        await page.getByRole('button', { name: 'Commit', exact: true }).click();
        await expect(field).toHaveValue('', { timeout: UI_READY });
        await expect(commit).toBeDisabled({ timeout: UI_READY });
        await field.fill(savedMessage);
      }
      const saved = page.waitForResponse(response =>
        new URL(response.url()).pathname === commitPath && response.request().method() === 'POST',
      { timeout: UI_READY });
      await dialog.getByRole('button', { name: action, exact: true }).click();
      const response = await saved;
      expect(response.status()).toBe(200);
      // Captured from the real workbench: retrieve omits is_default, so the
      // ordinary action omits set_default; CommitSerializer defaults it to false.
      expect(response.request().postDataJSON()).toEqual({ message: savedMessage,
        ...(isDefault ? { set_default: true } : {}), is_draft: false, version_name: 'v1' });
      await expect(dialog).not.toBeVisible({ timeout: UI_READY });
      await expect.poll(readVersion, POLL.ASYNC_JOB).toEqual([{
        id: initial[0].id, commit_message: savedMessage, is_default: isDefault, is_draft: false,
      }]);
      await testInfo.attach(`${mode}-${isDefault ? 'default' : 'retry'}-saved`, {
        contentType: 'application/json', body: JSON.stringify(await readVersion()),
      });
    }
  }
});
