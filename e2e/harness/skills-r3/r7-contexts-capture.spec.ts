import { randomUUID } from 'node:crypto';
import { mkdir, mkdtemp, readFile, writeFile } from 'node:fs/promises';
import { execFile } from 'node:child_process';
import { promisify } from 'node:util';
import { tmpdir } from 'node:os';
import { resolve } from 'node:path';
import { test, expect } from '../../lib/scope-actors';
import { E2E } from '../../lib/env';
import { authInitScript } from '../../lib/auth';

// Cloud-only screenshot fixture, not a catalogued flow or measured driver.
test('R7 context surface states', async ({ browser, request, scopeActors }) => {
  test.setTimeout(300_000);
  const actor = scopeActors.member;
  const owner = scopeActors.ownerA;
  const api = async (who: typeof actor, method: string, path: string, data?: unknown) => {
    const response = await request.fetch(`${E2E.apiUrl}/model-hub/skills/${path}`, { method, headers: { ...scopeActors.headers(who), 'Idempotency-Key': randomUUID() }, data });
    expect(response.ok(), `${method} ${path} ${response.status()}`).toBe(true);
    return (await response.json()).result;
  };
  const context = await browser.newContext({ baseURL: E2E.appUrl, viewport: { width: 1440, height: 900 } });
  await context.addInitScript(authInitScript, { access: actor.tokens.access, refresh: actor.tokens.refresh, organizationId: actor.organizationId, workspaceId: actor.workspaceId });
  await context.addInitScript(id => sessionStorage.setItem('workspaceOrgId', id), actor.organizationId);
  await context.grantPermissions(['clipboard-read', 'clipboard-write']);
  const page = await context.newPage();
  page.setDefaultTimeout(60_000);
  const output = `${process.cwd()}/r7-context-shots-critic-fix`;
  await mkdir(output, { recursive: true });
  const shot = async (name: string) => { if (new URL(page.url()).searchParams.get('tab') === 'recovery') await expect(page.getByText('Verify consumers', { exact: true }).or(page.getByText('Recovery view unavailable', { exact: true }))).toBeVisible(); await page.screenshot({ path: `${output}/${name}.png`, animations: 'disabled' }); };
  const overflows = [];
  const labels: Record<string, string> = { context: 'Instructions', links: 'Linked skills', use: 'Install', versions: 'Versions', tests: 'Tests', consumers: 'Connected apps', recovery: 'Rollback' };
  await page.goto('/dashboard/skills/contexts');
  await expect(page.getByRole('button', { name: 'Create agent context', exact: true })).toBeVisible();
  await expect(page.getByRole('textbox', { name: /Search agent contexts/ })).toHaveCount(0);
  await shot('contexts-first-run');
  const leaves = [];
  for (const name of ['refund-policy', 'escalation-tone', 'privacy-policy']) {
    const leaf = await api(owner, 'POST', 'publish/', { files: [{ path: 'SKILL.md', content: `---\nname: ${name}\ndescription: Help customers with clear, consistent support.\n---\nFollow the reviewed support policy.\n` }], labels: ['production'], expected_release_revision: 0, acknowledge_warnings: true });
    leaves.push(leaf.skill);
  }
  const draft = await api(actor, 'POST', 'contexts/', { name: 'support-agent', description: 'Customer support for refunds, disputes and deliveries.', tags: [], files: [{ path: 'AGENTS.md', content: '# Support agent\n\nHelp customers resolve their request with a clear next step.\n\n## Working instructions\n\n- Check the refund policy before discussing a refund.\n- Never promise an exception without approval.\n- Use the escalation tone guide for sensitive requests.\n- Keep customer information private.\n' }] });
  const links = leaves.map((leaf, index) => ({ skill_id: leaf.id, selector: index === 1 ? { version: 1 } : { label: 'production' } }));
  const desired = { revision: draft.revision, links, refresh_skill_ids: [] };
  const preview = await api(actor, 'POST', `contexts/${draft.context_id}/dependencies/preview/`, desired);
  const applied = await api(actor, 'POST', `contexts/${draft.context_id}/dependencies/apply/`, { ...desired, expected_fingerprint: preview.fingerprint });
  const review = await api(actor, 'POST', `contexts/${draft.context_id}/draft/head-review/`, { revision: applied.revision });
  const approval = { revision: applied.revision, expected_digest: review.candidate_digest, expected_head_version: review.expected_head_version, review_fingerprint: review.review_fingerprint };
  const saved = await api(actor, 'POST', `contexts/${draft.context_id}/draft/acknowledge-head/`, approval);
  await api(actor, 'POST', `contexts/${draft.context_id}/draft/publish/`, { ...approval, revision: saved.revision, review_fingerprint: saved.review_fingerprint, base_version: saved.base_version, note: 'Screenshot fixture', acknowledge_warnings: true });
  {
  const draft = await api(actor, 'POST', 'contexts/', { name: 'escalation-agent', description: 'Customer support for refunds, disputes and deliveries.', tags: [], files: [{ path: 'AGENTS.md', content: '# Support agent\n\nHelp customers resolve their request with a clear next step.\n\n## Working instructions\n\n- Check the refund policy before discussing a refund.\n- Never promise an exception without approval.\n- Use the escalation tone guide for sensitive requests.\n- Keep customer information private.\n' }] });
  const links = leaves.map((leaf, index) => ({ skill_id: leaf.id, selector: index === 1 ? { version: 1 } : { label: 'production' } }));
  const desired = { revision: draft.revision, links, refresh_skill_ids: [] };
  const preview = await api(actor, 'POST', `contexts/${draft.context_id}/dependencies/preview/`, desired);
  const applied = await api(actor, 'POST', `contexts/${draft.context_id}/dependencies/apply/`, { ...desired, expected_fingerprint: preview.fingerprint });
  const review = await api(actor, 'POST', `contexts/${draft.context_id}/draft/head-review/`, { revision: applied.revision });
  const approval = { revision: applied.revision, expected_digest: review.candidate_digest, expected_head_version: review.expected_head_version, review_fingerprint: review.review_fingerprint };
  const saved = await api(actor, 'POST', `contexts/${draft.context_id}/draft/acknowledge-head/`, approval);
  await api(actor, 'POST', `contexts/${draft.context_id}/draft/publish/`, { ...approval, revision: saved.revision, review_fingerprint: saved.review_fingerprint, base_version: saved.base_version, note: 'Screenshot fixture', acknowledge_warnings: true });
  }
  await api(owner, 'POST', 'publish/', { files: [{ path: 'SKILL.md', content: `---\nname: refund-policy\ndescription: Help customers with clear, consistent support.\n---\nReview the purchase date before approving a refund.\n` }], labels: ['production'], expected_release_revision: 1, acknowledge_warnings: true });
  const root = `/dashboard/skills/contexts/${draft.context_id}?version=1`;
  for (const [tab, name] of [['context', 'context-detail-instructions'], ['links', 'context-linked-skills'], ['versions', 'context-versions'], ['tests', 'context-tests'], ['consumers', 'context-consumers'], ['recovery', 'context-recovery'], ['use', 'context-use']]) {
    await page.goto(`${root}&tab=${tab}`);
    await expect(page.getByRole('tab', { name: labels[tab], exact: true })).toHaveAttribute('aria-selected', 'true');
    if (tab === 'context') await expect(page.getByRole('heading', { name: 'Working instructions', exact: true })).toBeVisible();
    if (tab === 'links') await expect(page.getByText('v2 available', { exact: true })).toBeVisible();
    if (tab === 'use') {
      await expect(page.getByRole('button', { name: 'Copy plan command', exact: true })).toBeVisible();
      await expect(page.getByLabel('Context install plan command')).toContainText('http://localhost:8100');
    }
    await shot(name);
    await page.evaluate(() => localStorage.setItem('settings', JSON.stringify({ ...JSON.parse(localStorage.getItem('settings') || '{}'), themeMode: 'dark' })));
    await page.reload();
    await expect(page.getByRole('tab', { name: labels[tab], exact: true })).toHaveAttribute('aria-selected', 'true');
    if (tab === 'context') await expect(page.getByRole('heading', { name: 'Working instructions', exact: true })).toBeVisible();
    if (tab === 'links') await expect(page.getByText('v2 available', { exact: true })).toBeVisible();
    if (tab === 'use') await expect(page.getByRole('button', { name: 'Copy plan command', exact: true })).toBeVisible();
    await shot(`${name}-dark`);
    await page.evaluate(() => localStorage.setItem('settings', JSON.stringify({ ...JSON.parse(localStorage.getItem('settings') || '{}'), themeMode: 'light' })));
    await page.reload();
    await page.setViewportSize({ width: 390, height: 900 });
    await expect(page.getByRole('tab', { name: labels[tab], exact: true })).toHaveAttribute('aria-selected', 'true');
    if (tab === 'context') await expect(page.getByRole('heading', { name: 'Working instructions', exact: true })).toBeVisible();
    if (tab === 'links') await expect(page.getByText('v2 available', { exact: true })).toBeVisible();
    if (tab === 'use') await expect(page.getByRole('button', { name: 'Copy plan command', exact: true })).toBeVisible();
    if (tab === 'links') {
      const list = page.getByRole('list', { name: 'Linked skills' });
      await expect(list).toBeVisible();
      await expect(list.getByText('Follows production · locked v1', { exact: true }).first()).toBeInViewport();
      await expect(list.getByRole('link', { name: 'Check update', exact: true })).toBeInViewport();
      await expect(page.getByRole('table', { name: 'Linked skills' })).toHaveCount(0);
    }
    if (tab === 'context') {
      await expect(page.getByRole('combobox', { name: 'Viewed release' })).toBeVisible();
      const panel = await page.getByRole('tabpanel').boundingBox();
      expect(panel?.y).toBeLessThan(300);
    }
    const widths = await page.evaluate(() => ({ document: document.documentElement.scrollWidth, viewport: window.innerWidth, main: document.querySelector('main')?.scrollWidth }));
    if (widths.document > widths.viewport || (widths.main ?? 0) > widths.viewport) overflows.push({ screen: name, ...widths });
    await writeFile(`${output}/overflows.json`, JSON.stringify(overflows, null, 2));
    await page.getByRole('tab', { name: labels[tab], exact: true }).scrollIntoViewIfNeeded();
    await shot(`${name}-mobile`);
    if (tab === 'use') {
      await page.getByRole('button', { name: 'Copy plan command', exact: true }).scrollIntoViewIfNeeded();
      await shot('context-use-mobile-command');
    }
    if (tab === 'links') {
      await page.getByRole('link', { name: 'Check update', exact: true }).scrollIntoViewIfNeeded();
      await shot('context-linked-skills-mobile-actions');
    }
    if (tab === 'context') {
      await page.getByRole('button', { name: 'Open navigation', exact: true }).click();
      await expect(page.locator('.MuiDrawer-paper')).toBeVisible();
      await shot('context-navigation-mobile');
      await page.keyboard.press('Escape');
    }
    await page.setViewportSize({ width: 1440, height: 900 });
  }
  await page.getByRole('button', { name: 'Copy apply command', exact: true }).scrollIntoViewIfNeeded();
  await shot('context-use-apply');
  await page.getByRole('button', { name: 'Before you install', exact: true }).click();
  await shot('context-use-details-open');
  // Network failures exercise the actual unavailable state, not a ?state shell.
  const inspectionRoute = `**/contexts/${draft.context_id}/versions/1/inspection/**`;
  await page.route(inspectionRoute, route => route.fulfill({ status: 503, contentType: 'application/json', body: JSON.stringify({ message: 'Temporarily unavailable' }) }));
  await page.goto(root);
  await expect(page.getByText("Couldn't load v1", { exact: true })).toBeVisible();
  await expect(page.getByRole('button', { name: 'Edit latest version', exact: true })).toBeDisabled();
  await shot('context-detail-error');
  await page.unroute(inspectionRoute);
  const detailRoute = `**/contexts/${draft.context_id}/`;
  await page.route(detailRoute, route => route.fulfill({ status: 403, contentType: 'application/json', body: '{}' }));
  await page.goto(root);
  await expect(page.getByRole('link', { name: 'Back to agent contexts', exact: true })).toBeVisible();
  await shot('context-detail-denied');
  await page.unroute(detailRoute);
  await page.goto(`${root}&state=loading`);
  await expect(page.getByRole('tab', { name: 'Instructions', exact: true })).toBeVisible();
  await shot('context-detail-loading');
  const unpublished = await api(actor, 'POST', 'contexts/', { name: 'onboarding-agent', description: 'Help new team members find their next step.', tags: [], files: [{ path: 'AGENTS.md', content: '# Onboarding agent\nWelcome new teammates.\n' }] });
  await page.goto(`/dashboard/skills/contexts/${unpublished.context_id}`);
  await expect(page.getByText('No releases yet', { exact: true })).toBeVisible();
  await expect(page.getByRole('button', { name: 'Resume your draft', exact: true })).toBeEnabled();
  await shot('context-detail-empty');
  for (const name of ['billing-agent', 'research-agent', 'triage-agent', 'oncall-agent', 'sales-agent', 'quality-agent']) {
    await api(actor, 'POST', 'contexts/', { name, description: 'Reviewed team instructions and reusable skills.', tags: ['team'], files: [{ path: 'AGENTS.md', content: '# Team instructions\nReview the request before taking action.\n' }] });
  }
  await page.goto('/dashboard/skills/contexts');
  await expect(page.getByRole('table', { name: 'Agent contexts', exact: true }).getByRole('row')).toHaveCount(10);
  await shot('contexts-list');
  await page.getByRole('textbox', { name: /Search agent contexts/ }).fill('no-matching-context');
  await expect(page.getByText('No contexts match these filters', { exact: true })).toBeVisible();
  await shot('contexts-filtered-empty');
  const listRoute = '**/model-hub/skills/contexts/?*';
  for (const [status, name] of [[503, 'contexts-error'], [403, 'contexts-denied']] as const) {
    await page.route(listRoute, route => route.fulfill({ status, contentType: 'application/json', body: '{}' }));
    await page.goto('/dashboard/skills/contexts');
    await expect(page.getByText(status === 403 ? "You don't have access to agent contexts." : "Couldn't load agent contexts.", { exact: true })).toBeVisible();
    await shot(name);
    await page.unroute(listRoute);
  }
  for (const state of ['loading', 'off']) {
    await page.goto(`/dashboard/skills/contexts?state=${state}`);
    await expect(page.getByRole('tab', { name: 'Agent contexts', exact: true })).toBeVisible();
    await shot(`contexts-${state}`);
  }
  // Override only the public install address; application traffic still uses the local API.
  for (const [origin, name] of [['https://api.example.invalid', 'context-use-https'], ['http://api.example.invalid', 'context-use-no-public-origin']]) {
    await page.goto(root);
    await expect(page.getByRole('tab', { name: 'Instructions', exact: true })).toHaveAttribute('aria-selected', 'true');
    await page.evaluate(value => { const runtime = window as Window & { __FUTURE_AGI_CONFIG__?: Record<string, unknown> }; runtime.__FUTURE_AGI_CONFIG__ = { ...runtime.__FUTURE_AGI_CONFIG__, VITE_PUBLIC_API_ORIGIN: value }; }, origin);
    await page.getByRole('tab', { name: 'Install', exact: true }).click();
    if (origin.startsWith('https:')) {
      await expect(page.getByLabel('Context install plan command')).toContainText(origin);
      await page.getByRole('button', { name: 'Copy plan command', exact: true }).click();
      expect(await page.evaluate(() => navigator.clipboard.readText())).toContain(origin);
      await page.evaluate(() => navigator.clipboard.writeText(''));
    } else {
      await expect(page.getByText(/no public HTTPS API address configured/)).toBeVisible();
      await expect(page.getByRole('button', { name: 'Copy plan command' })).toHaveCount(0);
    }
    await shot(name);
  }
  const skillRoute = `**/model-hub/skills/${leaves[0].id}/`;
  await page.route(skillRoute, route => route.fulfill({ status: 503, contentType: 'application/json', body: '{}' }));
  await page.goto(`${root}&tab=links`);
  await expect(page.getByText("Couldn't check", { exact: true })).toBeVisible();
  await expect(page.getByText('Up to date', { exact: true })).toBeVisible();
  await shot('context-linked-skills-lookup-failed');
  await page.unroute(skillRoute);
  await page.goto(`/dashboard/skills/${leaves[0].id}?tab=dependents`);
  await expect(page.getByRole('table', { name: 'Published context dependents' })).toBeVisible();
  await shot('context-dependents');
  await page.getByRole('button', { name: 'Prepare draft updates for 2 contexts', exact: true }).click();
  const acknowledgement = page.getByRole('checkbox', { name: 'Accept these skill versions and update rules into 2 context drafts.', exact: true });
  await expect(acknowledgement).toBeVisible();
  await shot('context-impact-preview');
  await acknowledgement.check();
  await page.getByRole('checkbox', { name: 'Select escalation-agent v1 for update', exact: true }).uncheck();
  await expect(page.getByText('Selection changed — prepare again.', { exact: true })).toBeVisible();
  await expect(page.getByRole('checkbox', { name: /Accept these skill versions/ })).toHaveCount(0);
  await expect(page.getByRole('button', { name: 'Apply selected updates', exact: true })).toBeDisabled();
  await shot('context-selection-changed');
  await page.getByRole('checkbox', { name: 'Select support-agent v1 for update', exact: true }).uncheck();
  await expect(page.getByText('Select at least one context to prepare updates.', { exact: true })).toBeVisible();
  await shot('context-selection-empty');
  await page.getByRole('checkbox', { name: 'Select support-agent v1 for update', exact: true }).check();
  await page.getByRole('button', { name: 'Prepare draft updates for 1 context', exact: true }).click();
  await page.getByRole('checkbox', { name: 'Accept these skill versions and update rules into 1 context draft.', exact: true }).check();
  await page.getByRole('button', { name: 'Apply selected updates to 1 context draft', exact: true }).click();
  await expect(page.getByText(/ draft now uses .+\. Production unchanged\./)).toBeVisible();
  await shot('context-draft-updated');
  await page.goto(root);
  await expect(page.getByRole('button', { name: 'Resume your draft', exact: true })).toBeVisible();
  await shot('context-own-draft');
  // Execute the actual copied CLI handoff in fresh, test-owned projects. The
  // scoped one-time secret stays in process memory on this VM, never evidence.
  const inspection = await api(actor, 'GET', `contexts/${draft.context_id}/versions/1/inspection/`);
  const credential = await api(owner, 'POST', 'contexts/credentials/', { purpose: 'consume', grants: [{ kind: 'agent_context', resource_id: draft.context_id, labels: [], version_ids: [inspection.manifest.version_id] }], binding_ids: [], expires_at: new Date(Date.now() + 3600000).toISOString() });
  const run = promisify(execFile);
  const proofs = [];
  for (const client of ['codex', 'claude-code']) {
    const project = await mkdtemp(`${tmpdir()}/context-ui-${client}-`);
    await page.goto(`${root}&tab=use`);
    await page.getByRole('tab', { name: client === 'codex' ? 'Codex' : 'Claude Code', exact: true }).click();
    await page.getByRole('textbox', { name: 'Project directory', exact: true }).fill(project);
    await page.getByRole('button', { name: 'Copy plan command', exact: true }).click();
    await expect(page.getByText('Copied the complete plan command.', { exact: true })).toBeVisible();
    const command = await page.evaluate(() => navigator.clipboard.readText());
    await page.evaluate(() => navigator.clipboard.writeText(''));
    expect(command).toContain('--allow-preview');
    expect(command).toContain("FI_CONTEXT_ORIGIN='http://localhost:8100'");
    const env = { ...process.env, PATH: `${resolve(process.cwd(), '../../context-sdk/.venv/bin')}:${process.env.PATH}`, FI_CONTEXT_CREDENTIAL: credential.secret, FI_CONTEXT_ORIGIN: E2E.apiUrl, FI_CONTEXT_ORGANIZATION_ID: actor.organizationId, FI_CONTEXT_WORKSPACE_ID: actor.workspaceId };
    await run('bash', ['-c', command], { cwd: project, env, timeout: 60000 });
    const plan = JSON.parse(await readFile(`${project}/context-plan.json`, 'utf8'));
    expect(JSON.stringify(plan)).toContain(inspection.manifest.digest);
    await page.getByRole('button', { name: 'Copy apply command', exact: true }).click();
    await expect(page.getByText('Copied the apply command.', { exact: true })).toBeVisible();
    const applyCommand = await page.evaluate(() => navigator.clipboard.readText());
    await page.evaluate(() => navigator.clipboard.writeText(''));
    expect(applyCommand).toBe("python -m fi.context apply --plan 'context-plan.json' --quiescent");
    await run('bash', ['-c', applyCommand], { cwd: project, env, timeout: 60000 });
    expect(await readFile(`${project}/AGENTS.md`, 'utf8')).toContain('Working instructions');
    proofs.push({ client, planned: true, inspected: true, applied: true, digest: inspection.manifest.digest });
  }
  await writeFile(`${output}/overflows.json`, JSON.stringify(overflows, null, 2));
  await writeFile(`${output}/install-proof.json`, JSON.stringify(proofs, null, 2));
  const adminContext = await browser.newContext({ baseURL: E2E.appUrl, viewport: { width: 1440, height: 900 } });
  await adminContext.addInitScript(authInitScript, { access: owner.tokens.access, refresh: owner.tokens.refresh, organizationId: owner.organizationId, workspaceId: owner.workspaceId });
  await adminContext.addInitScript(id => sessionStorage.setItem('workspaceOrgId', id), owner.organizationId);
  const adminPage = await adminContext.newPage();
  await adminPage.goto(`${root}&tab=use`);
  await expect(adminPage.getByRole('button', { name: 'Create key', exact: true })).toBeVisible();
  await expect(adminPage.getByRole('table', { name: 'Pilot credentials for this context' }).getByRole('row')).toHaveCount(2);
  await adminPage.screenshot({ path: `${output}/context-use-admin.png`, animations: 'disabled' });
  await adminContext.close();
  await context.close();
  expect(overflows, 'Global dashboard shell must fit mobile viewports').toEqual([]);
});
