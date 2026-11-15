/* eslint-env node */
// Cloud-only composition checks with synthetic data. Not a backend or action-count qualification.
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import path from 'node:path';
import { createServer } from 'vite';
import react from '@vitejs/plugin-react-swc';
const { chromium } = await import(process.env.PLAYWRIGHT_MODULE || 'playwright');
const output = path.resolve(process.argv[2] || '/tmp/ops-r7-shots');
const captureOnly = process.env.OPS_CAPTURE_ONLY;
await fs.mkdir(output, { recursive: true });
const mocks = `
import { useEffect, useState } from 'react';
const params = new URLSearchParams(location.search);
const candidate = (number, extra = {}) => ({ id: 'release-' + number, number, digest: 'sha256:' + String(number).repeat(64), selectable: true, eligible: true, reasons: [], children: [{ skillId: 'policy', mount: 'skills/refund-policy/' }], unavailableChildren: [], ...extra });
export const cohort = [
 { binding: { id: 'runtime-a', name: 'Support application', kind: 'runtime', selector: { label: 'production' } }, desired: { versionId: 'release-2', observedAt: '2026-11-13T01:46:04.943460Z' }, loaded: { versionId: 'release-2', observedAt: '2026-11-13T01:46:01.495709Z' }, latestFailure: { versionId: 'release-2', diagnosticCodes: ['refund_window_regression'], traceRef: { trace_id: 'incident-013cc96f-4444' } }, states: ['unknown'], observationsComplete: false, nextActionCode: 'observe_runtime' },
 { binding: { id: 'repo-b', name: 'Support repository', kind: 'repository', selector: { version: 1 } }, desired: { versionId: 'release-1' }, loaded: { versionId: 'release-1' }, states: ['pinned_as_intended', 'matches_target'], observationsComplete: true, nextActionCode: 'none' }
];

export const useContextRecovery = () => { const [verify, setVerify] = useState(false); useEffect(() => { if (params.get('screen') === 'recovery-verify') { const timer = setTimeout(() => setVerify(true), 500); return () => clearTimeout(timer); } }, []); return ({ data: { asOf: '2026-11-13T01:46:04.943460Z', canPromote: true, canIssueCredentials: true, lastReleaserId: 'reviewer', releaseRevision: 3, candidates: [candidate(2, { isProduction: !verify, eligible: verify, reasons: verify ? [] : ['current_production'] }), candidate(1, { isProduction: verify, eligible: !verify, reasons: verify ? ['current_production'] : [] })], cohort: verify ? cohort.map((row) => ({ ...row, loaded: { versionId: 'release-1' }, states: row.binding.id === 'repo-b' ? ['pinned_as_intended', 'matches_target'] : ['unknown'] })) : cohort }, refetch: () => {} }); };
export const useRecoveryReviewers = () => ({ data: [{ id: 'reviewer', name: 'Independent reviewer', email: 'reviewer@example.invalid' }] });
export const useRecoveryClosure = () => ({ data: { rows: [{ path: 'AGENTS.md', status: 'changed', sizeBytes: 146, sha256: 'a'.repeat(64) }, { path: 'skills/refund-policy/SKILL.md', status: 'unchanged', sizeBytes: 88, sha256: 'b'.repeat(64) }], changes: [{ path: 'AGENTS.md', status: 'changed', lines: [{ kind: 'removed', text: 'Refund shipping for every return.' }, { kind: 'added', text: 'Refund shipping only when the shipment is damaged.' }] }] } });
export const useRefreshContextFacts = () => () => {};
export const usePreviewRecoveryRelease = () => { const [data, setData] = useState(null); const [isPending, setPending] = useState(false); return { data, isPending, mutate: () => params.get('screen') === 'recovery-preparing' ? setPending(true) : setData({ fingerprint: 'sha256:' + 'f'.repeat(64), packet: {} }), reset: () => setData(null) }; };
export const useSubmitRecoveryRequest = () => { const [data, setData] = useState(null); return { data, mutate: () => setData({ id: 'request-1' }) }; };
export const useIssueRecoveryCredential = () => ({ mutate: () => {} });
export const useReviewOptions = () => ({ data: { evidence: [], recipients: [{ id: 'reviewer', name: 'Independent reviewer' }] } });
export const DESCRIPTOR_PATH = '.futureagi/context.json';
`;
const entry = `
import React, { useState } from 'react';
import { createRoot } from 'react-dom/client';
import { BrowserRouter } from 'react-router-dom';
import { Box, Button, Typography } from '@mui/material';
import ThemeProvider from 'src/theme';
import { SettingsProvider } from 'src/components/settings/context/settings-provider';
import ContextCampaignList from 'src/sections/skills/contexts/ContextCampaignList';
import ContextCampaignReview from 'src/sections/skills/contexts/ContextCampaignReview';
import ContextConsumersView from 'src/sections/skills/contexts/ContextConsumersView';
import J6RecoveryPanel from 'src/sections/skills/contexts/J6RecoveryPanel';
import { cohort } from '/__ops-mocks';
import '@fontsource/inter/400.css';
import '@fontsource/inter/500.css';
import '@fontsource/inter/600.css';
const params = new URLSearchParams(location.search);
const screen = params.get('screen');
localStorage.removeItem('settings');
const campaign = { id: 'rollout', name: 'Support policy rollout', status: 'draft', revision: 3, targetCount: 40, enabled: true, canAuthorize: true, manifestDigest: 'sha256:' + 'a'.repeat(64) };
const targets = Array.from({ length: 40 }, (_, i) => ({ bindingId: 'repo-' + i, name: 'support-repository-' + String(i + 1).padStart(2, '0'), desiredVersion: 2, desiredVersionId: 'release-2', desiredDigest: 'sha256:' + 'b'.repeat(64), selector: { label: 'production' }, repository: { repositoryId: String(i), defaultBranch: 'main' }, planDigest: 'sha256:' + 'c'.repeat(64), decision: 'pending', reason: '', prState: 'not_started', mergeState: 'unknown', plan: { repositoryId: String(i), baseCommit: 'a'.repeat(40), previousLockDigest: 'sha256:' + 'd'.repeat(64), materializationDigest: 'sha256:' + 'e'.repeat(64), localReviewRequired: false, blockers: i === 3 ? ['Local modifications need review'] : [], changes: [{ path: 'AGENTS.md', action: 'replace', ownership: 'managed', beforeSha256: 'a'.repeat(64), afterSha256: 'b'.repeat(64), diffPreview: '- Refund shipping for every return.\\n+ Refund shipping only when the shipment is damaged.' }] } }));
function App() {
 const [selected, setSelected] = useState(null);
 let content;
 if (screen.startsWith('rollouts')) content = <ContextCampaignList query={{ data: { count: screen === 'rollouts-list' ? 1 : 0, results: screen === 'rollouts-list' ? [campaign] : [] }, refetch: () => {} }} availability={{}} enabled={screen !== 'rollouts-off'} state={params.get('state')} onOpen={() => {}} page={1} onPageChange={() => {}} />;
 else if (screen.startsWith('rollout-')) content = <ContextCampaignReview campaign={campaign} targets={targets} onAuthorize={async () => {}} onStatus={async () => {}} onRollback={async () => {}} selectedTarget={selected} onSelectTarget={setSelected} observations={{ data: {} }} />;
 else if (screen.startsWith('consumers')) content = <ContextConsumersView registrationAction={<Box><Button variant='outlined'>Connect an app</Button></Box>} scopeKey='fixture' state={params.get('state') || 'ready'} data={{ results: cohort.map((row) => ({ ...row, binding: { ...row.binding, name: screen === 'consumers-long-name' ? row.binding.name.toLowerCase().replaceAll(' ', '-') + '-regional-runtime-with-a-long-deployment-identifier' : row.binding.name }, resolved: null, installed: null, discovered: null, observationAgeSeconds: 3.447751 })), asOf: '2026-11-13T01:46:04.943460Z', count: 2, page: 1, pageSize: 25 }} versionNumbers={{ 'release-1': 1, 'release-2': 2 }} onWindowChange={() => {}} onPageChange={() => {}} onRetry={() => {}} />;
 else content = <J6RecoveryPanel contextId='context' contextName='Support policy' />;
 return <Box sx={{ px: { xs: 2, md: 3 }, py: 2, minWidth: 0 }}><Typography variant='s3' color='text.secondary' sx={{ display: 'block', mb: 1.5 }}>Synthetic UI fixture · no repository writes or production changes</Typography>{content}</Box>;
}
createRoot(document.getElementById('root')).render(<SettingsProvider defaultSettings={{ themeMode: params.get('theme') || 'light', themeDirection: 'ltr', themeContrast: 'default', themeColorPresets: 'default' }}><ThemeProvider><BrowserRouter><App /></BrowserRouter></ThemeProvider></SettingsProvider>);
`;
const server = await createServer({ configFile: false, root: process.cwd(), plugins: [react(), {
  name: 'ops-r7', enforce: 'pre',
  resolveId(id) {
    if (id === '/__ops.jsx' || id === '/__ops-mocks') return id;
    if (/src\/api\/skills\/(j6-recovery|review-authoring)$/.test(id)) return '/__ops-mocks';
    return null;
  },
  load(id) { if (id === '/__ops.jsx') return entry; if (id === '/__ops-mocks') return mocks; return null; },
  configureServer(vite) { vite.middlewares.use((req, res, next) => {
    if (!req.url.startsWith('/ops-r7')) { next(); return; }
    res.setHeader('Content-Type', 'text/html');
    res.end('<!doctype html><html><head><meta name="viewport" content="width=device-width,initial-scale=1"><title>Operations fixture</title><script type="module">import RefreshRuntime from "/@react-refresh";RefreshRuntime.injectIntoGlobalHook(window);window.$RefreshReg$=()=>{};window.$RefreshSig$=()=>type=>type;window.__vite_plugin_react_preamble_installed__=true;</script></head><body><div id="root"></div><script type="module" src="/@vite/client"></script><script type="module" src="/__ops.jsx"></script></body></html>');
  }); },
}], resolve: { alias: [{ find: /^src\/(.*)/, replacement: `${process.cwd()}/src/$1` }] }, server: { host: '127.0.0.1', port: 0, warmup: { clientFiles: [] }, fs: { allow: [process.cwd(), await fs.realpath('node_modules')] } } });
await server.listen();
const origin = `http://127.0.0.1:${server.httpServer.address().port}`;
const browser = await chromium.launch({ headless: true, args: ['--disable-dev-shm-usage'] });
const errors = [];
const captures = [];
try {
  const cases = [['rollouts-off'], ['rollouts-list'], ['rollouts-empty', 'empty'], ['rollouts-loading', 'loading'], ['rollouts-error', 'error'], ['rollout-review'], ['rollout-target-drawer'], ['recovery-review'], ['recovery-await'], ['recovery-preparing'], ['recovery-verify'], ['recovery-empty', 'empty'], ['recovery-loading', 'loading'], ['recovery-error', 'error'], ['recovery-limit', 'inventory-limit'], ['recovery-candidates'], ['consumers'], ['consumers-drawer'], ['consumers-empty', 'empty'], ['consumers-loading', 'loading'], ['consumers-error', 'error']];
  for (const theme of ['light', 'dark']) {
    for (const [screen, state] of cases) {
      if (captureOnly && screen !== captureOnly) continue;
      const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
      process.stdout.write(`Capture ${screen} ${theme}\n`);
      page.on('console', (message) => { if (message.type() === 'error') process.stderr.write(`${screen}: ${message.text()}\n`); });
      page.on('response', (response) => { if (response.status() >= 400) process.stderr.write(`${screen}: HTTP ${response.status()} ${response.url()}\n`); });
      page.on('pageerror', (error) => { errors.push(error.message); process.stderr.write(`${screen}: ${error.message}\n`); });
      await page.goto(`${origin}/ops-r7?${new URLSearchParams({ screen, theme, ...(state ? { state } : {}) })}`);
      await page.getByText('Synthetic UI fixture · no repository writes or production changes').waitFor({ timeout: 60000 });
      if (screen === 'consumers-drawer') await page.getByRole('button', { name: 'Inspect Support application' }).click();
      if (screen === 'rollout-target-drawer') await page.getByRole('button', { name: 'Review target support-repository-01' }).click();
      if (screen === 'recovery-await') {
        await page.getByRole('checkbox', { name: /I reviewed the complete v1 closure/ }).check();
        await page.getByRole('button', { name: 'Submit rollback for review' }).click();
        await page.getByText(/Rollback to v1 requested/).waitFor();
        assert.equal(await page.getByRole('button', { name: 'Submit rollback for review' }).count(), 0);
        await page.evaluate(() => window.scrollTo(0, 0));
      }
      if (screen === 'recovery-preparing') {
        await page.getByRole('checkbox', { name: /I reviewed the complete v1 closure/ }).check();
        await page.getByText('Preparing the rollback request…').waitFor();
        assert(await page.getByRole('button', { name: 'Submit rollback for review' }).isDisabled());
        await page.getByRole('button', { name: 'Submit rollback for review' }).scrollIntoViewIfNeeded();
      }
      if (screen === 'recovery-verify') await page.getByText('Production moved from v2 to v1 · 1 of 2 consumers confirmed').waitFor();
      if (screen === 'consumers') { await page.getByText(/As of 13 Nov 2026, 01:46 UTC/).waitFor(); assert.equal(await page.getByText('Updated 3s ago', { exact: true }).count(), 2); }
      if (screen === 'recovery-candidates') {
        assert.equal(await page.getByRole('button', { name: 'Choose v1' }).getAttribute('aria-pressed'), 'true');
        assert.equal(await page.getByRole('button', { name: 'Technical details', exact: true }).count(), 1);
        await page.getByText('1 file changes, 1 stays the same. Linked skills keep their pinned versions.').waitFor();
      }
      if (screen === 'rollout-review') {
        await page.getByRole('button', { name: '1 blocked', exact: true }).click();
        await page.getByText('Blocked: local changes', { exact: true }).waitFor();
        await page.getByText('Local modifications need review', { exact: true }).waitFor();
        assert.equal(await page.getByRole('button', { name: 'Review target support-repository-01', exact: true }).count(), 0);
        const blockedName = `rollout-blocked${page.viewportSize().width === 390 ? '-390' : new URL(page.url()).searchParams.get('theme') === 'dark' ? '-dark' : ''}.png`;
        await page.screenshot({ path: path.join(output, blockedName), animations: 'disabled' }); captures.push(blockedName);
        await page.getByRole('combobox', { name: 'Target status' }).click();
        await page.getByRole('option', { name: 'All targets', exact: true }).click();
      }
      if (screen === 'rollout-target-drawer') assert(await page.getByRole('dialog').locator('[data-status="added"]').count() > 0, 'shared diff has added rows');
      if (screen === 'recovery-limit') {
        await page.getByRole('link', { name: 'Open CLI restore instructions' }).waitFor();
        assert.equal(await page.getByRole('button', { name: 'Retry', exact: true }).count(), 0);
      }
    await page.evaluate(() => document.fonts.ready);
      assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), `${screen} desktop overflow`);
      const name = `${screen}${theme === 'dark' ? '-dark' : ''}.png`;
      await page.screenshot({ path: path.join(output, name), animations: 'disabled' }); captures.push(name);
      if (screen === 'recovery-verify') {
        const threadName = `recovery-threads${theme === 'dark' ? '-dark' : ''}.png`;
        await page.getByRole('button', { name: 'Advanced recovery', exact: true }).click();
        await page.getByText('Check apps that are mid-conversation', { exact: true }).evaluate((heading) => heading.scrollIntoView({ block: 'start' }));
        await page.screenshot({ path: path.join(output, threadName), animations: 'disabled' }); captures.push(threadName);
      }
      if (screen === 'consumers-drawer') {
        const close = page.getByRole('button', { name: 'Close delivery receipts' });
        assert.equal(await close.locator('svg').count(), 1, 'Receipt close glyph renders on first load');
        const bounds = await close.boundingBox();
        const viewport = page.viewportSize();
        assert(bounds && bounds.x >= 0 && bounds.x + bounds.width <= viewport.width && bounds.y + bounds.height <= viewport.height, 'Receipt close stays in view');
        await close.click();
        await page.getByRole('dialog', { name: 'Support application' }).waitFor({ state: 'hidden' });
      }
      await page.close();
    }
  }
  for (const screen of ['rollouts-off', 'rollouts-list', 'rollout-review', 'rollout-target-drawer', 'recovery-review', 'recovery-candidates', 'consumers', 'consumers-long-name', 'consumers-drawer']) {
    if (captureOnly && screen !== captureOnly) continue;
    const page = await browser.newPage({ viewport: { width: 390, height: 900 } });
    page.on('pageerror', (error) => { errors.push(error.message); process.stderr.write(`${screen}: ${error.message}\n`); });
    await page.goto(`${origin}/ops-r7?screen=${screen}`);
    await page.getByText('Synthetic UI fixture · no repository writes or production changes').waitFor({ timeout: 60000 });
    if (screen === 'consumers-drawer') await page.getByRole('button', { name: 'Inspect Support application' }).click();
    if (screen === 'rollout-target-drawer') await page.getByRole('button', { name: 'Review target support-repository-01' }).click();
      if (screen === 'recovery-candidates') {
        assert.equal(await page.getByRole('button', { name: 'Choose v1' }).getAttribute('aria-pressed'), 'true');
        assert.equal(await page.getByRole('button', { name: 'Technical details', exact: true }).count(), 1);
        await page.getByText('1 file changes, 1 stays the same. Linked skills keep their pinned versions.').waitFor();
      }
      if (screen === 'rollout-review') {
        await page.getByRole('button', { name: '1 blocked', exact: true }).click();
        await page.getByText('Blocked: local changes', { exact: true }).waitFor();
        await page.getByText('Local modifications need review', { exact: true }).waitFor();
        assert.equal(await page.getByRole('button', { name: 'Review target support-repository-01', exact: true }).count(), 0);
        const blockedName = `rollout-blocked${page.viewportSize().width === 390 ? '-390' : new URL(page.url()).searchParams.get('theme') === 'dark' ? '-dark' : ''}.png`;
        await page.screenshot({ path: path.join(output, blockedName), animations: 'disabled' }); captures.push(blockedName);
        await page.getByRole('combobox', { name: 'Target status' }).click();
        await page.getByRole('option', { name: 'All targets', exact: true }).click();
      }
      if (screen === 'rollout-target-drawer') assert(await page.getByRole('dialog').locator('[data-status="added"]').count() > 0, 'shared diff has added rows');
      if (screen === 'recovery-limit') {
        await page.getByRole('link', { name: 'Open CLI restore instructions' }).waitFor();
        assert.equal(await page.getByRole('button', { name: 'Retry', exact: true }).count(), 0);
      }
    await page.evaluate(() => document.fonts.ready);
    await page.screenshot({ path: path.join(output, `${screen}-390.png`), animations: 'disabled' });
    assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), `${screen} mobile overflow`);
    assert(await page.locator('.MuiTableContainer-root').evaluateAll((containers) => containers.every((container) => container.scrollWidth <= container.clientWidth)), `${screen} mobile table clipping`);
    if (screen === 'rollout-target-drawer') {
      const next = await page.getByRole('button', { name: 'Next target needing review' }).boundingBox();
      assert(next && next.y + next.height <= 900, 'Next target stays in the mobile drawer footer');
      const close = page.getByRole('button', { name: 'Close target' });
      assert.equal(await close.locator('svg').count(), 1, 'Target close glyph renders');
    }
    captures.push(`${screen}-390.png`);
    if (screen === 'consumers-long-name') {
      await page.getByRole('button', { name: 'Inspect support-application-regional-runtime-with-a-long-deployment-identifier', exact: true }).click();
      await page.screenshot({ path: path.join(output, 'consumers-long-drawer-390.png'), animations: 'disabled' });
      assert(await page.getByRole('dialog').evaluate((drawer) => drawer.scrollWidth <= drawer.clientWidth), 'Long consumer receipt title stays inside the mobile drawer');
      captures.push('consumers-long-drawer-390.png');
    }
      if (screen === 'consumers-drawer') {
        const close = page.getByRole('button', { name: 'Close delivery receipts' });
        assert.equal(await close.locator('svg').count(), 1, 'Receipt close glyph renders on first load');
        const bounds = await close.boundingBox();
        const viewport = page.viewportSize();
        assert(bounds && bounds.x >= 0 && bounds.x + bounds.width <= viewport.width && bounds.y + bounds.height <= viewport.height, 'Receipt close stays in view');
        await close.click();
        await page.getByRole('dialog', { name: 'Support application' }).waitFor({ state: 'hidden' });
      }
    await page.close();
  }
  assert.deepEqual(errors, []);
  await fs.writeFile(path.join(output, 'result.json'), JSON.stringify({ synthetic: true, captures, errors }, null, 2));
  process.stdout.write(`PASS ${captures.length} operations component screenshots; no page or mobile table overflow.\n`);
} finally { await browser.close(); await server.close(); }
