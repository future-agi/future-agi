/* eslint-env node */
// Synthetic real-component render qualification, not a full application journey.
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import path from 'node:path';
import { createServer } from 'vite';
import react from '@vitejs/plugin-react-swc';
const { chromium } = await import(process.env.INT_FE_PLAYWRIGHT_MODULE || 'playwright');
const output = path.resolve(process.argv[2] || '/tmp/int-fe-shots');
await fs.mkdir(output, { recursive: true });
const entry = `
import React from 'react';
import { createRoot } from 'react-dom/client';
import { MemoryRouter } from 'react-router-dom';
import { Box, Stack, Typography } from '@mui/material';
import { SnackbarProvider } from 'notistack';
import ThemeProvider from 'src/theme';
import { SettingsProvider } from 'src/components/settings/context/settings-provider';
import FileTree from 'src/sections/skills/components/FileTree';
import TextDiff from 'src/sections/skills/components/TextDiff';
import SkillMarkdownPreview from 'src/sections/skills/components/SkillMarkdownPreview';
import UseSkillPanel from 'src/sections/skills/detail/UseSkillPanel';
import '@fontsource/inter/400.css';
import '@fontsource/inter/500.css';
import '@fontsource/inter/600.css';
const params = new URLSearchParams(location.search);
localStorage.removeItem('settings');
const files = [{path:'AGENTS.md'}, {path:'references/policy.md'}, {path:'skills/refunds/SKILL.md',readOnly:true}, {path:'.futureagi/context.json',readOnly:true}];
function Files() {
 const [selected, setSelected] = React.useState('AGENTS.md');
 return <Stack spacing={2}><FileTree files={files} selectedPath={selected} onSelect={setSelected} readOnly={false} protectedPaths={['AGENTS.md']} pinned={['AGENTS.md']} onCreate={()=>{}} onDelete={()=>{throw new Error('Unexpected deletion')}} onRename={()=>{throw new Error('Unexpected rename')}} /><Typography variant='s3'>Selected: {selected}</Typography><SkillMarkdownPreview literal currentPath='AGENTS.md' content={'---\\nStanding instructions, not skill frontmatter.\\n---\\n# Approved policy\\n[Open retained policy](references/policy.md)\\n![Not loaded](https://example.invalid/private.png)'} onOpenPath={setSelected}/></Stack>;
}
const scenes = {
 files: <Files />,
 diff: <TextDiff fromText={'# Refund policy\\nShared clause.\\nUse seven days.\\n'} toText={'# Refund policy\\nShared clause.\\nUse fourteen days.\\nKeep neighboring cases unchanged.\\n'} fromTitle='Content origin v4' toTitle='Saved candidate r12' />,
 use: <UseSkillPanel skill={{id:'synthetic-skill',name:'reviewed-refund-policy',latestVersion:7,labels:[{name:'production',version:4}],tags:['policy']}} />,
};
createRoot(document.getElementById('root')).render(<SettingsProvider defaultSettings={{themeMode:params.get('theme')||'light',themeDirection:'ltr',themeContrast:'default',themeColorPresets:'default'}}><ThemeProvider><SnackbarProvider><MemoryRouter><Box sx={{p:2,minWidth:0}}><Typography variant='s3'>Synthetic component fixture · no publication or installation</Typography><Box sx={{mt:2,minWidth:0}}>{scenes[params.get('scene')]}</Box></Box></MemoryRouter></SnackbarProvider></ThemeProvider></SettingsProvider>);
`;
const server = await createServer({
  configFile: false, root: process.cwd(),
  plugins: [react(), {
    name: 'int-fe-fixture',
    resolveId(id) { return id === '/__int-fe.jsx' ? id : null; },
    load(id) { return id === '/__int-fe.jsx' ? entry : null; },
    configureServer(vite) {
      vite.middlewares.use((request, response, next) => {
        if (!request.url.startsWith('/int-fe')) { next(); return; }
        response.setHeader('Content-Type', 'text/html');
        response.end('<!doctype html><html><head><meta name="viewport" content="width=device-width,initial-scale=1"><title>Integration fixture</title><script type="module">import RefreshRuntime from "/@react-refresh";RefreshRuntime.injectIntoGlobalHook(window);window.$RefreshReg$=()=>{};window.$RefreshSig$=()=>type=>type;window.__vite_plugin_react_preamble_installed__=true;</script></head><body><div id="root"></div><script type="module" src="/@vite/client"></script><script type="module" src="/__int-fe.jsx"></script></body></html>');
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
let captures = 0;
try {
  browser = await chromium.launch({ headless: true, args: ['--disable-dev-shm-usage'] });
  const page = await browser.newPage();
  page.on('pageerror', (error) => errors.push(error.message));
  await page.route('**/*', (route) => route.request().url().startsWith(origin) ? route.continue() : route.abort());
  for (const theme of ['light', 'dark']) {
    for (const width of [1280, 390, 320]) {
      await page.setViewportSize({ width, height: 960 });
      for (const scene of ['files', 'diff', 'use']) {
        await page.goto(`${origin}/int-fe?theme=${theme}&scene=${scene}`);
        await page.getByText('Synthetic component fixture · no publication or installation').waitFor();
        if (scene === 'files') {
          assert(await page.getByRole('button', { name: 'Delete', exact: true }).isDisabled());
          await page.getByRole('button', { name: 'Open retained policy' }).click();
          await page.getByText('Selected: references/policy.md').waitFor();
          await page.getByRole('treeitem', { name: 'SKILL.md', exact: true }).click();
          assert(await page.getByRole('button', { name: 'Rename', exact: true }).isDisabled());
          assert.equal(await page.locator('img').count(), 0);
        }
        if (scene === 'diff' && width < 600) {
          const region = page.getByRole('region', { name: 'Unified file changes' });
          await region.waitFor();
          assert.equal(await region.locator('[data-status="filler"]').count(), 0);
          assert.equal(await region.getByText('Shared clause.', { exact: true }).count(), 1);
          assert(await region.getByLabel('removed', { exact: true }).count() > 0);
          assert(await region.getByLabel('added', { exact: true }).count() > 0);
        }
        if (scene === 'use') {
          await page.getByText(/Legacy ZIP installation, not a safe updater/).waitFor();
          await page.getByRole('tab', { name: 'Application', exact: true }).click();
          await page.getByRole('combobox', { name: 'Application transport' }).waitFor();
          await page.getByRole('tab', { name: 'Coding agent', exact: true }).click();
        }
        await page.evaluate(() => document.fonts.ready);
        assert(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth), `Page overflow: ${scene}/${theme}/${width}`);
        await page.screenshot({ path: path.join(output, `${scene}-${theme}-${width}.png`), fullPage: true });
        captures += 1;
      }
    }
  }
  assert.deepEqual(errors, []);
  process.stdout.write(`PASS ${captures} component captures; Chromium ${browser.version()}; light/dark, 1280/390/320 CSS px. No full-app/E2E claim.\n`);
} finally { await browser?.close(); await server.close(); }
