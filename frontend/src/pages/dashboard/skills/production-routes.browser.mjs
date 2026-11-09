/* eslint-env node */
// Cloud-only synthetic transport. Real production providers/controllers and theme;
// this is visual/interaction evidence, not authenticated backend qualification.
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import path from 'node:path';
import { createServer } from 'vite';
import react from '@vitejs/plugin-react-swc';

const { chromium } = await import(process.env.PLAYWRIGHT_MODULE || 'playwright');
const output = path.resolve(process.argv[2] || '/tmp/ctxhub-production-route-shots');
await fs.mkdir(output, { recursive: true });
const scope = `
export const useAuthContext = () => ({ authenticated: true, user: { id: 'author' } });
export const useOrganization = () => ({ isReady: true, currentOrganizationId: '00000000-0000-4000-8000-000000000001', orgLevel: 8 });
export const useWorkspace = () => ({ isReady: true, currentWorkspaceId: '00000000-0000-4000-8000-000000000002', wsLevel: 8 });
`;
const entry = `
import React from 'react';
import { createRoot } from 'react-dom/client';
import { BrowserRouter, Route, Routes } from 'react-router-dom';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { HelmetProvider } from 'react-helmet-async';
import { Box, Typography } from '@mui/material';
import ThemeProvider from 'src/theme';
import { SettingsProvider } from 'src/components/settings/context/settings-provider';
import axios from 'src/utils/axios';
import SkillsWorkspaceLayout from 'src/sections/skills/components/SkillsWorkspaceLayout';
import ContextLibraryPage from 'src/pages/dashboard/skills/contexts/ContextLibraryPage';
import ContextDetailPage from 'src/pages/dashboard/skills/contexts/ContextDetailPage';
import SkillsReviewsPage from 'src/pages/dashboard/skills/SkillsReviewsPage';
import SkillsSpendPolicyPage from 'src/pages/dashboard/skills/SkillsSpendPolicyPage';
import '@fontsource/inter/400.css';
import '@fontsource/inter/500.css';
import '@fontsource/inter/600.css';
const params = new URLSearchParams(location.search);
localStorage.removeItem('settings');
const id = '00000000-0000-4000-8000-000000000003';
const version = '00000000-0000-4000-8000-000000000004';
const digest = 'sha256:' + 'a'.repeat(64);
const context = { id, kind: 'agent_context', name: 'support-policy', description: 'Standing support policy with explicit release identity', tags: [], labels: [], latest_version: 4, metadata_revision: 1, release_revision: 1, security_revision: 0, protected_label_names: [], has_draft: false };
const envelope = (result) => ({ data: { result } });
const page = (results) => ({ results, count: results.length, page: 1, page_size: 25, total_pages: results.length ? 1 : 0 });
axios.get = async (url) => {
 if (url.endsWith('/capabilities/')) return envelope({ campaigns_enabled: false });
 if (url.endsWith('/review-inbox/')) return envelope({ ...page([]), unread_count: 0 });
 if (url.endsWith('/spend-policy/')) return envelope({ revision: 0, enabled: false, period_limit_usd: null, starts_at: '2026-11-01', ends_at: '2026-12-01', committed_usd: '0', remaining_usd: null, known_estimate_usd: '0', unknown_reserved_usd: '0', prior_period_unknown_usd: '0' });
 if (url === '/model-hub/skills/contexts/') return envelope(page([context]));
 if (url.endsWith('/contexts/' + id + '/')) return envelope({ context, release_state: { latest_version: 4, release_revision: 1, security_revision: 0, protected_label_names: [], labels: [], permissions: {} }, latest: { id: version, number: 4, digest }, own_draft: null });
 if (url.endsWith('/versions/')) return envelope(page([{ id: version, number: 4, digest }]));
 if (url.endsWith('/consumers/')) return envelope({ ...page([]), as_of: '2026-11-09', coverage: 'declared_bindings' });
 if (url.endsWith('/inspection/')) return envelope({ descriptor: { kind: 'agent_context', name: context.name, description: context.description, links: [] }, manifest: { kind: 'agent_context', name: context.name, version: 4, version_id: version, digest, files: [{ path: 'AGENTS.md', sha256: 'hash', size_bytes: 48, origin: { kind: 'owned' } }], next_file_offset: null }, distribution_blocked: false });
 if (url.endsWith('/inspection/files/')) return envelope({ path: 'AGENTS.md', version_id: version, digest, sha256: 'hash', total_chars: 48, offset: 0, next_offset: null, eof: true, text: '# Support policy\\nEscalate privileged changes.' });
 throw new Error('Unexpected fixture read ' + url);
};
const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
createRoot(document.getElementById('root')).render(<QueryClientProvider client={client}><HelmetProvider><SettingsProvider defaultSettings={{ themeMode: params.get('theme') || 'light', themeDirection: 'ltr', themeContrast: 'default', themeColorPresets: 'default' }}><ThemeProvider><BrowserRouter><Box sx={{ minWidth: 0 }}><Typography variant="s3">Synthetic transport · production route controllers · no backend proof</Typography><Routes><Route path="/dashboard/skills" element={<SkillsWorkspaceLayout />}><Route path="contexts" element={<ContextLibraryPage />} /><Route path="contexts/:contextId" element={<ContextDetailPage />} /><Route path="reviews" element={<SkillsReviewsPage />} /><Route path="allowance" element={<SkillsSpendPolicyPage />} /></Route></Routes></Box></BrowserRouter></ThemeProvider></SettingsProvider></HelmetProvider></QueryClientProvider>);
`;
const server = await createServer({
  configFile: false, root: process.cwd(),
  plugins: [react(), {
    name: 'production-route-fixture', enforce: 'pre',
    resolveId(id) {
      if (id === '/__production.jsx') return id;
      if (/^(?:.*\/)?src\/(?:auth\/hooks|contexts\/(?:OrganizationContext|WorkspaceContext))$/.test(id)) return '\0production-scope';
      return null;
    },
    load(id) { if (id === '/__production.jsx') return entry; if (id === '\0production-scope') return scope; return null; },
    configureServer(vite) {
      vite.middlewares.use((request, response, next) => {
        if (!request.url.startsWith('/dashboard/skills')) { next(); return; }
        response.setHeader('Content-Type', 'text/html');
        response.end('<!doctype html><html><head><meta name="viewport" content="width=device-width,initial-scale=1"><title>Production route fixture</title><script type="module">import RefreshRuntime from "/@react-refresh";RefreshRuntime.injectIntoGlobalHook(window);window.$RefreshReg$=()=>{};window.$RefreshSig$=()=>type=>type;window.__vite_plugin_react_preamble_installed__=true;</script></head><body><div id="root"></div><script type="module" src="/@vite/client"></script><script type="module" src="/__production.jsx"></script></body></html>');
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
try {
  browser = await chromium.launch({ headless: true, args: ['--disable-dev-shm-usage'] });
  for (const theme of ['light', 'dark']) {
    for (const width of [1280, 390]) {
      const page = await browser.newPage({ viewport: { width, height: 900 } });
      page.on('pageerror', (error) => errors.push(String(error)));
      await page.goto(`${origin}/dashboard/skills/contexts?theme=${theme}`);
      await page.getByRole('button', { name: 'support-policy' }).click();
      await page.getByLabel('AGENTS.md read-only source').waitFor();
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);
      await page.screenshot({ path: path.join(output, `context-${theme}-${width}.png`) });
      await page.getByRole('tab', { name: 'Consumers', exact: true }).click();
      await page.getByText('No declared consumers').waitFor();
      await page.screenshot({ path: path.join(output, `consumers-${theme}-${width}.png`) });
      await page.getByRole('tab', { name: 'Reviews', exact: true }).click();
      await page.getByText('No assigned reviews').waitFor();
      await page.screenshot({ path: path.join(output, `reviews-${theme}-${width}.png`) });
      await page.getByRole('tab', { name: 'Workspace allowance', exact: true }).click();
      await page.getByLabel('Period allowance (USD)').waitFor();
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);
      await page.screenshot({ path: path.join(output, `allowance-${theme}-${width}.png`) });
      await page.close();
    }
  }
  assert.deepEqual(errors, []);
  console.log(JSON.stringify({ captures: 16, themes: ['light', 'dark'], widths: [1280, 390], errors, transport: 'synthetic', output }));
} finally { await browser?.close(); await server.close(); }
