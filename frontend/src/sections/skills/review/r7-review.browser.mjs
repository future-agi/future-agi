/* eslint-env node */
// Cloud-only visual qualification. Synthetic fixtures, not backend release proof.
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import path from 'node:path';
import { createServer } from 'vite';
import react from '@vitejs/plugin-react-swc';
const { chromium } = await import(process.env.L9_PLAYWRIGHT_MODULE || 'playwright');
const output = path.resolve(process.argv[2] || '/tmp/r7-review-shots');
await fs.mkdir(output, { recursive: true });
const fixture = `
import { packet, preview, textEvidence } from 'src/sections/skills/__tests__/r3_l09/fixtures';
const options = { subject: { labels: [{ name: 'production', version: 4 }] }, recipients: [{ id: 'reviewer', name: 'Independent reviewer', email: 'reviewer@example.invalid' }], targets: [], evidence: [{ ...textEvidence, payloadExpiresAt: new Date(Date.now()+3*86400000).toISOString() }] };
export const useSkillV11Scope = () => ({ enabled: true, identity: 'fixture', scope: { userId: 'author' } });
export const spendPolicyReady = () => false;
export const campaignsContractReady = () => false;
export const useContextCampaignAvailability = () => ({ data: { enabled: false } });
export const useContextAuthoring = () => ({ bindings: { library: {}, detail: {}, editor: {}, reviews: {} } });
export const useReviewSubjects = () => ({ data: { results: [], total_pages: 1 } });
export const useReviewCandidates = useReviewSubjects;
export const useReviewOptions = () => ({ data: new URLSearchParams(location.search).get('screen') === 'composer-no-evidence' ? { ...options, evidence: [] } : options, refetch() {} });
export const usePrepareReleaseReview = () => ({ mutateAsync: async () => null });
export const useSubmitReleaseReview = usePrepareReleaseReview;
export const useShareReleaseEvidence = usePrepareReleaseReview;
export const useDecideReleaseRequest = usePrepareReleaseReview;
export const useRenewReleaseRequest = usePrepareReleaseReview;
export const useRenewalPreview = () => ({ data: { revision: 2, fingerprint: 'renewed', packet, changes: [{ item: 'transition.expected_security_revision', before: 1, after: 3 }, { item: 'target.configuration_digest', before: 'sha256:' + 'a'.repeat(64), after: 'sha256:' + 'b'.repeat(64) }], removedEvidence: [{ source: 'skills_text', sourceId: 'expired-comparison', reason: 'evidence_expired' }] } });
`;
const entry = `
import React from 'react';
import { createRoot } from 'react-dom/client';
import { MemoryRouter, Routes, Route } from 'react-router-dom';
import { Box, Typography } from '@mui/material';
import ThemeProvider from 'src/theme';
import { SettingsProvider } from 'src/components/settings/context/settings-provider';
import SkillsWorkspaceLayout from 'src/sections/skills/components/SkillsWorkspaceLayout';
import ObjectHeader from 'src/sections/skills/components/ObjectHeader';
import { paths } from 'src/routes/paths';
import ReleaseInbox from 'src/sections/skills/review/ReleaseInbox';
import ReleaseReceipt from 'src/sections/skills/review/ReleaseReceipt';
import ReleaseRequestPanel from 'src/sections/skills/review/ReleaseRequestPanel';
import ReleaseRequestComposer from 'src/sections/skills/review/ReleaseRequestComposer';
import ReleaseRenewalDialog from 'src/sections/skills/review/ReleaseRenewalDialog';
import { changes, ID, inbox, packet, preview, request } from 'src/sections/skills/__tests__/r3_l09/fixtures';
import '@fontsource/inter/400.css'; import '@fontsource/inter/500.css'; import '@fontsource/inter/600.css';
const params = new URLSearchParams(location.search);
const screen = params.get('screen') || 'inbox';
const state = params.get('state') || 'ready';
try { localStorage.removeItem('settings'); } catch {}
const receipt = { executedByName: 'Erin Releaser', approvedByName: 'Robin Reviewer', recordedAt: new Date(Date.now() - 3600000).toISOString(), previousVersion: 1, requestId: ID, decisionId: ID, executedBy: ID, currentnessAtAction: { release_revision: 2 }, postMoveState: { label: 'production', version: 2, releaseRevision: 3, eventId: ID } };
const req = { ...request, createdAt: new Date(Date.now()-3*3600000).toISOString(), decidedAt: new Date(Date.now()-2*3600000).toISOString(), authorName: 'Alex Author', decidedByName: 'Robin Reviewer', recipients: [{id:'reviewer',name:'Robin Reviewer'}], name: 'Support agent', subjectKind: 'agent_context', subjectId: ID };
const body = { complete: true, changes, dependencyChanges: [], checks: { errors: [], warnings: [] } };
function Scene() { return <Box sx={{ minWidth: 0 }}>
  {screen === 'inbox' && <ReleaseInbox scopeKey="fixture" state={state} data={{ ...inbox, results: inbox.results.map(row => ({ ...row, authorName: 'Alex Author', transition: { label: 'production', expectedVersion: 1, version: 2 }, updatedAt: new Date(Date.now() - 3 * 86400000).toISOString() })) }} onStatusChange={() => {}} onPageChange={() => {}} onOpen={() => {}} onRetry={() => {}} onCompose={() => {}} onToggleAuthored={() => {}} />}
  {screen === 'receipt' && <Box sx={{ px: {xs:2, sm:3}, py:2 }}><ObjectHeader title={req.name} crumbs={[{ label: 'Reviews', to: paths.dashboard.skills.reviews }, {label:req.name}]} /><ReleaseReceipt receipt={receipt} request={req} /></Box>}
  {screen.startsWith('composer') && <ReleaseRequestComposer scopeKey="fixture" state={state} prefill={{ kind: packet.subjectKind, subjectId: packet.subjectId, name: 'Support agent', candidate: { id: packet.candidateVersionId, number: packet.transition.version, digest: packet.candidateDigest } }} />}
  {screen.startsWith('review') && <Box sx={{px:{xs:2,sm:3},py:2}}><ObjectHeader title={req.name + ': production v' + packet.transition.expectedVersion + ' → v' + packet.transition.version} crumbs={[{label:'Reviews',to:paths.dashboard.skills.reviews},{label:req.name+' v7'}]} /><ReleaseRequestPanel scopeKey="fixture" state={state} preview={preview} request={{ ...req, status: screen === 'review-approved' ? 'approved' : 'pending' }} permissions={{ canDecide: true, canExecute: true }} review={body} onDecide={async () => ({})} onExecute={async () => receipt} /></Box>}
  {screen === 'renewal' && <ReleaseRenewalDialog open requestId={ID} onClose={() => {}} />}
</Box>; }
function App() { return <MemoryRouter initialEntries={[paths.dashboard.skills.reviews]}><Routes><Route element={<SkillsWorkspaceLayout />}><Route path="*" element={<Scene />} /></Route></Routes></MemoryRouter>; }
createRoot(document.getElementById('root')).render(<SettingsProvider defaultSettings={{ themeMode: params.get('theme') || 'light', themeDirection: 'ltr', themeContrast: 'default', themeColorPresets: 'default' }}><ThemeProvider><App /></ThemeProvider></SettingsProvider>);
`;
const mocked = new Set(['src/api/skills/review-production', 'src/api/skills/review-authoring', 'src/api/skills/testing', 'src/api/skills/r3-spend', 'src/api/skills/context-campaigns', 'src/sections/skills/contexts/ContextLibraryView']);
const server = await createServer({
  configFile: false, root: process.cwd(),
  plugins: [react(), { name: 'review-visual-fixture', enforce: 'pre',
    resolveId(id) { if (id.endsWith('/ContextLibraryView') || id.endsWith('/ContextLibraryView.jsx')) return '\0review-fixture-api'; if (id.endsWith('SkillsProductionProvider') || id.endsWith('SkillsProductionProvider.jsx')) return '\0review-provider'; if (mocked.has(id) || [...mocked].some((name) => id.endsWith('/' + name) || id.endsWith('/' + name + '.js'))) return '\0review-fixture-api'; if (id === '/__review.jsx') return id; return null; },
    load(id) { if (id === '\0review-provider') return 'export default function Provider({children}) { return children; }'; if (id === '\0review-fixture-api') return fixture; if (id === '/__review.jsx') return entry; return null; },
    configureServer(vite) { vite.middlewares.use((req, res, next) => {
      if (!req.url.startsWith('/r7-review')) { next(); return; }
      res.setHeader('Content-Type', 'text/html');
      res.end('<!doctype html><html><head><meta name="viewport" content="width=device-width,initial-scale=1"><title>Review visual fixture</title><script type="module">import RefreshRuntime from "/@react-refresh";RefreshRuntime.injectIntoGlobalHook(window);window.$RefreshReg$=()=>{};window.$RefreshSig$=()=>type=>type;window.__vite_plugin_react_preamble_installed__=true;</script></head><body><div id="root"></div><script type="module" src="/@vite/client"></script><script type="module" src="/__review.jsx"></script></body></html>');
    }); },
  }],
  resolve: { alias: [{ find: /^src\/(.*)/, replacement: `${process.cwd()}/src/$1` }] },
  server: { host: '127.0.0.1', port: 0, fs: { allow: [process.cwd(), await fs.realpath('node_modules')] } },
});
await server.listen();
const origin = `http://127.0.0.1:${server.httpServer.address().port}`;
let browser;
const errors = [];
try {
  browser = await chromium.launch({ headless: true, args: ['--disable-dev-shm-usage'] });
  const page = await browser.newPage();
  page.on('pageerror', (error) => { errors.push(error.message); console.error(error.message); });
  page.on('console', message => { if (message.type() === 'error') console.error(message.text()); });
  await page.route('**/*', (route) => route.request().url().startsWith(origin) ? route.continue() : route.abort());
  const capture = async (screen, theme, width, state = '') => {
    await page.setViewportSize({ width, height: 900 });
    await page.goto(`${origin}/r7-review?${new URLSearchParams({ screen, theme, state })}`);
    await page.locator('nav[aria-label="Skills navigation"]').waitFor();
    await page.waitForTimeout(250);
    await page.evaluate(() => document.fonts.ready);
    if (screen === 'renewal') await page.getByRole('dialog').waitFor();
    if (screen === 'inbox' && !state) assert.equal(await page.getByText(/production v1 → v2/).count(), 1, 'Inbox must name the version move at every width');
    assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), `Page overflow: ${screen} ${width}`);
    assert.deepEqual(errors, []);
    await page.screenshot({ path: path.join(output, `${screen}${state ? `-${state}` : ''}-${theme}-${width}.png`) });
  };
  for (const screen of ['inbox', 'composer-context-prefilled', 'composer-no-evidence', 'review-pending', 'review-approved', 'receipt', 'renewal']) {
    await capture(screen, 'light', 1440);
    await capture(screen, 'dark', 1440);
    await capture(screen, 'light', 390);
  }
  for (const state of ['loading', 'error', 'empty', 'off', 'denied']) await capture('inbox', 'light', 1440, state);
  for (const state of ['loading', 'error', 'empty']) await capture('composer-context-prefilled', 'light', 1440, state);
  console.log('29 cloud-only visual fixtures captured; no horizontal overflow or browser exceptions.');
} finally { await browser?.close(); await server.close(); }
