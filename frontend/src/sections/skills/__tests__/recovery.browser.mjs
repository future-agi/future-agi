/* eslint-env node */
// Cloud-only, synthetic transport. This qualifies UI composition, not backend E2E.
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import path from 'node:path';
import { createServer } from 'vite';
import react from '@vitejs/plugin-react-swc';

const { chromium } = await import(process.env.PLAYWRIGHT_MODULE || 'playwright');
const output = process.argv[2] || '/tmp/ctxhub-fe-recovery-shots';
await fs.mkdir(output, { recursive: true });
const mocks = `
export const useSkillV11Scope = () => ({ enabled: true, identity: 'org:workspace:owner', scope: { organizationId: 'org', workspaceId: 'workspace' } });
export const useOrganization = () => ({ isReady: true, currentOrganizationId: 'org', orgLevel: 10 });
export const useWorkspace = () => ({ isReady: true, currentWorkspaceId: 'workspace', wsLevel: 8 });
export const spendPolicyReady = () => true;
export const useSkillsSpendPolicy = () => ({ data: { revision: 0, enabled: false, periodLimitUsd: null, startsAt: '2026-11-01', endsAt: '2026-12-01', committedUsd: '0', remainingUsd: null, knownEstimateUsd: '0', unknownReservedUsd: '0', priorPeriodUnknownUsd: '0' } });
export const useUpdateSkillsSpendPolicy = () => ({ mutateAsync: async (input) => input });
export const getSkillErrorCode = (error) => error.code;
export const useSaveSkillDraft = () => ({ mutateAsync: async (draft) => ({ ...draft, revision: draft.revision + 1 }) });
`;
const entry = `
import React from 'react';
import { createRoot } from 'react-dom/client';
import { BrowserRouter, Route, Routes } from 'react-router-dom';
import { HelmetProvider } from 'react-helmet-async';
import { Box, Typography } from '@mui/material';
import ThemeProvider from 'src/theme';
import { SettingsProvider } from 'src/components/settings/context/settings-provider';
import SkillsWorkspaceLayout from 'src/sections/skills/components/SkillsWorkspaceLayout';
import SkillsSpendPolicyPage from 'src/pages/dashboard/skills/SkillsSpendPolicyPage';
import ContextImportDialog from 'src/sections/skills/contexts/ContextImportDialog';
import ContextLinksPanel from 'src/sections/skills/contexts/ContextLinksPanel';
import HotfixHeadReview from 'src/sections/skills/editor/HotfixHeadReview';
import useDraftSession from 'src/sections/skills/editor/useDraftSession';
import '@fontsource/inter/400.css';
import '@fontsource/inter/500.css';
import '@fontsource/inter/600.css';
const params = new URLSearchParams(location.search);
localStorage.removeItem('settings');
const initial = { revision: 4, baseVersion: 2, files: [{ path: 'AGENTS.md', content: 'Rules' }], note: '', description: 'Purpose', contextSpec: { links: [] }, resolvedLock: { links: [] } };
const resourceApi = { saveDraft: async (draft) => ({ ...draft, revision: draft.revision + 1 }), saveInput: (draft, revision) => ({ ...draft, revision }) };
let attempts = [];
const send = async (...args) => { attempts.push(args); window.attempts = attempts; if (attempts.length === 1) throw new Error('Synthetic lost response'); return { ...initial, revision: 5 }; };
function Recovery() {
 const session = useDraftSession('context', initial, true, resourceApi);
 return <Box sx={{ p: 2 }}><Typography variant="s3">Draft revision {session.revision}</Typography>{params.get('screen') === 'head'
 ? <HotfixHeadReview scopeKey="fixture" session={session} onPreview={async () => ({ review: { draftRevision: 4, baseVersion: 2, candidateDigest: 'digest', expectedHeadVersion: 3, reviewFingerprint: 'head' }, originChanges: [], headChanges: [] })} onAcknowledge={send} />
 : <ContextLinksPanel scopeKey="fixture" session={session} onPreview={async () => ({ revision: 4, candidateDigest: 'digest', fingerprint: 'links', changes: [], checks: { summary: { errors: 0 } } })} onApply={send} />}</Box>;
}
function App() {
 const screen = params.get('screen');
 if (screen === 'create') return <ContextImportDialog scopeKey="fixture" open onClose={() => {}} onCreate={async () => { throw { statusCode: 409, code: 'name_taken' }; }} />;
 if (screen === 'links' || screen === 'head') return <Recovery />;
 return <Routes><Route path="/dashboard/skills" element={<SkillsWorkspaceLayout />}><Route index element={<Typography>Library</Typography>} /><Route path="allowance" element={<SkillsSpendPolicyPage />} /></Route></Routes>;
}
createRoot(document.getElementById('root')).render(<HelmetProvider><SettingsProvider defaultSettings={{ themeMode: params.get('theme') || 'light', themeDirection: 'ltr', themeContrast: 'default', themeColorPresets: 'default' }}><ThemeProvider><BrowserRouter><App /></BrowserRouter></ThemeProvider></SettingsProvider></HelmetProvider>);
`;
const server = await createServer({
  configFile: false, root: process.cwd(),
  plugins: [react(), {
    name: 'recovery-fixture', enforce: 'pre',
    resolveId(id) {
      if (id === '/__recovery.jsx') return id;
      if (/^(?:.*\/)?src\/(?:api\/skills(?:\/(?:testing|r3-spend))?|contexts\/(?:OrganizationContext|WorkspaceContext))$/.test(id)) return '\0recovery-mocks';
      return null;
    },
    load(id) { if (id === '/__recovery.jsx') return entry; if (id === '\0recovery-mocks') return mocks; return null; },
    configureServer(vite) {
      vite.middlewares.use((req, res, next) => {
        if (!req.url.startsWith('/dashboard/skills')) { next(); return; }
        res.setHeader('Content-Type', 'text/html');
        res.end('<!doctype html><html><head><meta name="viewport" content="width=device-width,initial-scale=1"><title>Recovery fixture</title><script type="module">import RefreshRuntime from "/@react-refresh";RefreshRuntime.injectIntoGlobalHook(window);window.$RefreshReg$=()=>{};window.$RefreshSig$=()=>type=>type;window.__vite_plugin_react_preamble_installed__=true;</script></head><body><div id="root"></div><script type="module" src="/@vite/client"></script><script type="module" src="/__recovery.jsx"></script></body></html>');
      });
    },
  }],
  resolve: { alias: [{ find: /^src\/(.*)/, replacement: `${process.cwd()}/src/$1` }] },
  server: { host: '127.0.0.1', port: 0, fs: { allow: [process.cwd(), await fs.realpath('node_modules')] } },
});
await server.listen();
const origin = `http://127.0.0.1:${server.httpServer.address().port}`;
let browser;
const errors = [];
const captures = [];
try {
  browser = await chromium.launch({ headless: true, args: ['--disable-dev-shm-usage'] });
  const page = await browser.newPage();
  page.on('pageerror', (error) => errors.push(error.message));
  await page.route('**/*', (route) => route.request().url().startsWith(origin) ? route.continue() : route.abort());
  for (const theme of ['light', 'dark']) {
    for (const width of [1440, 390]) {
      await page.setViewportSize({ width, height: 1000 });
      for (const screen of ['allowance', 'create', 'links', 'head']) {
        await page.goto(`${origin}/dashboard/skills?${new URLSearchParams({ theme, screen })}`);
        if (screen === 'allowance') {
          await page.getByRole('tab', { name: 'Workspace allowance', exact: true }).click();
          await page.getByLabel('Period allowance (USD)').fill('12.3400');
          await page.getByLabel('Enable paid-work admission under this allowance').check();
          await page.getByRole('button', { name: 'Review policy change' }).click();
          await page.getByRole('button', { name: 'Apply policy change' }).waitFor();
        } else if (screen === 'create') {
          await page.getByLabel('AGENTS.md', { exact: true }).fill('Standing instructions retained after rejection.');
          await page.getByLabel('Immutable name').fill('taken');
          await page.getByLabel('Purpose').fill('Preserved purpose');
          await page.getByRole('button', { name: 'Create draft' }).click();
          await page.getByText(/This name is already in use/).waitFor();
          assert(await page.getByLabel('Immutable name').isEnabled());
          assert.equal(await page.getByLabel('Purpose').inputValue(), 'Preserved purpose');
        } else {
          const head = screen === 'head';
          await page.getByRole('button', { name: head ? 'Review against current latest' : 'Preview dependency changes' }).click();
          await page.getByRole('checkbox').check();
          await page.getByRole('button', { name: head ? 'Acknowledge reviewed head' : 'Apply selected updates to draft' }).click();
          await page.getByRole('button', { name: head ? 'Retry head acknowledgment' : 'Retry dependency update' }).waitFor();
          assert(await page.getByRole('button', { name: head ? 'Review against current latest' : 'Preview dependency changes' }).isDisabled());
        }
        await page.evaluate(() => document.fonts.ready);
        assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), `${screen} overflows at ${width}`);
        const name = `${screen}-${theme}-${width}`;
        await page.screenshot({ path: path.join(output, `${name}.png`), fullPage: true });
        captures.push(name);
        if (screen === 'head' || screen === 'links') {
          await page.getByRole('button', { name: screen === 'head' ? 'Retry head acknowledgment' : 'Retry dependency update' }).click();
          await page.getByText('Draft revision 5', { exact: true }).waitFor();
          assert.deepEqual(await page.evaluate(() => window.attempts[1]), await page.evaluate(() => window.attempts[0]));
        }
      }
    }
  }
  assert.deepEqual(errors, []);
  await fs.writeFile(path.join(output, 'result.json'), JSON.stringify({ synthetic: true, captures, errors }, null, 2));
  process.stdout.write(`PASS: ${captures.length} synthetic UI captures; light/dark, desktop/mobile; exact-operation recovery.\n`);
} finally { await browser?.close(); await server.close(); }
