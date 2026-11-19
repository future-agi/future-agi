/* eslint-env node */
// Cloud-only real-component qualification. No backend, Git writes or E2E claim.
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import path from 'node:path';
import { createServer } from 'vite';
import react from '@vitejs/plugin-react-swc';

const { chromium } = await import(process.env.L12_PLAYWRIGHT_MODULE || 'playwright');
const output = path.resolve(process.argv[2] || '/tmp/l12-campaign-shots');
await fs.mkdir(output, { recursive: true });
const entry = `
import React, { useState } from 'react';
import { createRoot } from 'react-dom/client';
import { Box, Typography } from '@mui/material';
import ThemeProvider from 'src/theme';
import { SettingsProvider } from 'src/components/settings/context/settings-provider';
import ContextCampaignReview from 'src/sections/skills/contexts/ContextCampaignReview';
import '@fontsource/inter/400.css';
import '@fontsource/inter/500.css';
import '@fontsource/inter/600.css';
const params = new URLSearchParams(location.search);
localStorage.removeItem('settings');
const campaign = { id: 'fixture', name: 'Review repository policy update', revision: 41, status: 'draft', targetCount: 40, manifestDigest: 'sha256:' + 'a'.repeat(64), enabled: true, canAuthorize: true };
const targets = Array.from({length:40}, (_, index) => ({
  bindingId: 'binding-' + index, name: 'synthetic-repository-' + String(index).padStart(2,'0'),
  bindingRevision: 1, selector: index >= 32 && index < 36 ? {version:2} : {label:'production'},
  desiredVersion: 4, desiredVersionId: 'fixture-version', desiredDigest: 'sha256:' + 'b'.repeat(64),
  repository: {repositoryId:String(100+index), defaultBranch:'main', projectScope:''},
  planDigest: 'sha256:' + 'c'.repeat(64), decision:'pending', reason:'', prState:index === 39 ? 'unknown' : 'not_started', mergeState:'unknown', attempt:null,
  plan: {repositoryId:String(100+index),baseCommit:'a'.repeat(40), previousLockDigest:null, materializationDigest:'sha256:' + 'd'.repeat(64),
    localReviewRequired:index === 0, blockers:index >= 36 && index <= 38 ? [index === 38 ? 'repository_unavailable' : 'local_modifications'] : [],
    changes:[{path:'AGENTS.md',action:'replace',ownership:'adopted',beforeSha256:'a'.repeat(64),afterSha256:'b'.repeat(64),diffPreview:' Local policy: escalate privileged changes.\\n+ Reviewed managed context: use approved support instructions.'}]}
}));
function App(){
 const [selected, setSelected] = useState(null);
 return <Box sx={{p:2,minWidth:0}}><Typography variant='s3' color='text.secondary'>Synthetic component fixture · no backend or Git writes</Typography><ContextCampaignReview campaign={campaign} targets={targets} onAuthorize={async(input)=>{window.lastAuthorization=input}} onStatus={async()=>{}} onRollback={async()=>{}} selectedTarget={selected} onSelectTarget={setSelected} observations={{data:{installed:null,discovered:null,delivered:null}}}/></Box>;
}
createRoot(document.getElementById('root')).render(<SettingsProvider defaultSettings={{themeMode:params.get('theme') || 'light',themeDirection:'ltr',themeContrast:'default',themeColorPresets:'default'}}><ThemeProvider><App/></ThemeProvider></SettingsProvider>);
`;
const server = await createServer({
  configFile: false, root: process.cwd(),
  plugins: [react(), {
    name: 'l12-campaign-fixture',
    resolveId(id) { return id === '/__l12.jsx' ? id : null; },
    load(id) { return id === '/__l12.jsx' ? entry : null; },
    configureServer(vite) {
      vite.middlewares.use((request, response, next) => {
        if (!request.url.startsWith('/l12-campaign')) { next(); return; }
        response.setHeader('Content-Type', 'text/html');
        response.end('<!doctype html><html><head><meta name="viewport" content="width=device-width,initial-scale=1"><title>Campaign fixture</title><script type="module">import RefreshRuntime from "/@react-refresh";RefreshRuntime.injectIntoGlobalHook(window);window.$RefreshReg$=()=>{};window.$RefreshSig$=()=>type=>type;window.__vite_plugin_react_preamble_installed__=true;</script></head><body><div id="root"></div><script type="module" src="/@vite/client"></script><script type="module" src="/__l12.jsx"></script></body></html>');
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
    for (const width of [1280, 390, 320]) {
      const page = await browser.newPage({ viewport: { width, height: 900 } });
      page.on('pageerror', (error) => errors.push(String(error)));
      await page.goto(`${origin}/l12-campaign?theme=${theme}`);
      await page.getByRole('heading', { name: 'Review repository policy update' }).waitFor();
      assert.equal(await page.getByRole('button', { name: /^Review target/ }).count(), 40);
      assert.equal(await page.getByRole('button', { name: 'Authorize reviewed PR plans' }).isDisabled(), true);
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);
      await page.screenshot({ path: path.join(output, `review-${theme}-${width}.png`) });
      const open = page.getByRole('button', { name: 'Review target synthetic-repository-00', exact: true });
      const bounds = await open.boundingBox();
      assert.ok(bounds.x >= 0 && bounds.x + bounds.width <= width, 'Inspect target remains visible without horizontal scrolling');
      await open.focus();
      await page.keyboard.press('Enter');
      const dialog = page.getByRole('dialog', { name: 'synthetic-repository-00' });
      await dialog.waitFor();
      assert.equal(await dialog.getByText('Not observed', { exact: true }).count(), 3);
      await dialog.getByLabel('Include this target').click();
      await page.getByRole('option', { name: 'Include exact plan' }).click();
      await dialog.getByLabel('Review or exclusion reason').fill('Reviewed and preserved the local instruction segment.');
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);
      const closeBounds = await dialog.getByRole('button', { name: 'Close target' }).boundingBox();
      assert.ok(closeBounds.y >= 0 && closeBounds.y + closeBounds.height <= 900, 'Close target stays reachable after scrolling');
      await page.screenshot({ animations: 'disabled', path: path.join(output, `target-${theme}-${width}.png`) });
      await page.keyboard.press('Escape');
      await dialog.waitFor({ state: 'hidden' });
      assert.equal(await open.evaluate((element) => document.activeElement === element), true);
      await page.close();
    }
  }
  assert.deepEqual(errors, []);
  process.stdout.write('PASS: 40 targets; six theme/viewport combinations; keyboard drawer/focus return; no page overflow; 12 component screenshots. Not full-app or GitHub E2E.\n');
} finally {
  await browser?.close();
  await server.close();
}
