/* eslint-env node */
// Cloud-only component smoke check. Set SKILLS_CHROME_PATH to an installed
// Chromium executable; this does not start the product backend or use real data.
import assert from 'node:assert/strict';
import { mkdir } from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import puppeteer from 'puppeteer-core';
import { createServer } from 'vite';
import react from '@vitejs/plugin-react-swc';

const frontend = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../../../..');
const screenshotDir = process.env.SKILLS_SCREENSHOT_DIR || '/tmp/skills-f0-visual';
const executablePath = process.env.SKILLS_CHROME_PATH;
assert(executablePath, 'Set SKILLS_CHROME_PATH to the cloud Chromium executable.');
const fixture = `
import React from 'react';
import {createRoot} from 'react-dom/client';
import {Box, CssBaseline, Typography} from '@mui/material';
import {createTheme, ThemeProvider} from '@mui/material/styles';
import {palette} from 'src/theme/palette';
import {typography} from 'src/theme/typography';
import {dsShape} from 'src/theme/ds-tokens';
import {customShadows} from 'src/theme/custom-shadows';
import {componentsOverrides} from 'src/theme/overrides';
import Iconify from 'src/components/iconify';
import FileTree from 'src/sections/skills/components/FileTree';
import SkillMarkdownPreview from 'src/sections/skills/components/SkillMarkdownPreview';
import TextDiff from 'src/sections/skills/components/TextDiff';
import ChecksList from 'src/sections/skills/components/ChecksList';
import LabelChip from 'src/sections/skills/components/LabelChip';
import {SKILLS_NAV_ICON} from 'src/sections/skills/components/skillIcons';
const h = React.createElement;
const mode = new URLSearchParams(location.search).get('mode') || 'light';
const theme = createTheme({palette:palette(mode), typography, shape:dsShape, customShadows:customShadows(mode)});
theme.components = componentsOverrides(theme);
const markdown = '---\\nname: support-policy\\ndescription: Use when reviewing support requests.\\n---\\n## Review the request\\nRead the [refund policy](references/policy.md) before deciding.\\n\\n- Verify the purchase date.\\n- Explain the decision plainly.';
function App() {
 const [selected, select] = React.useState('SKILL.md');
 const [files, setFiles] = React.useState([{path:'SKILL.md'}, {path:'references/policy.md',status:'modified'}]);
 return h(ThemeProvider,{theme},h(CssBaseline),h(Box,{sx:{p:2,maxWidth:1200,mx:'auto'}},
  h(Box,{sx:{display:'flex',alignItems:'center',gap:1,mb:2}},h(Iconify,{icon:SKILLS_NAV_ICON,'data-testid':'nav-icon'}),h(Typography,{variant:'m1',component:'h1'},'Skills'),h(LabelChip,{name:'production',version:2})),
  h(Box,{sx:{display:'grid',gridTemplateColumns:{xs:'minmax(0,1fr)',md:'260px minmax(0,1fr)'},gap:3}},
   h(FileTree,{files,selectedPath:selected,onSelect:select,readOnly:false,onCreate:(p)=>setFiles([...files,{path:p}]),onRename:(a,b)=>setFiles(files.map(f=>f.path===a?{path:b}:f)),onDelete:(p)=>setFiles(files.filter(f=>f.path!==p))}),
   h(Box,{sx:{minWidth:0}},h(Typography,{variant:'s3','data-testid':'selected'},'Selected: '+selected),h(SkillMarkdownPreview,{content:markdown,currentPath:'SKILL.md',onOpenPath:select}))),
  h(Box,{sx:{my:3}},h(TextDiff,{fromText:'Review the policy.\\nDecline expired requests.\\n',toText:'Review the policy.\\nEscalate exceptional requests.\\nRecord the reason.\\n',fromTitle:'v1',toTitle:'Draft'})),
  h(ChecksList,{checks:{items:[{severity:'warning',code:'description_short',message:'Add enough detail for an agent to know when it should use this skill.',path:'SKILL.md',line:3},{severity:'info',code:'allowed_tools_note',message:'Tool approval does not sandbox the skill.'}]}})
 ));
}
createRoot(document.getElementById('root')).render(h(App));
`;
const server = await createServer({
  configFile: false, root: frontend, logLevel: 'error',
  resolve: { alias: { src: path.join(frontend, 'src') } },
  plugins: [react(), {
    name: 'skills-smoke-fixture',
    resolveId(id) { return id === 'virtual:skills-smoke' ? '\0skills-smoke' : null; },
    load(id) { return id === '\0skills-smoke' ? fixture : null; },
    configureServer(vite) {
      vite.middlewares.use(async (request, response, next) => {
        if (!request.url.startsWith('/__skills_visual__/')) return next();
        response.setHeader('Content-Type', 'text/html');
        response.end(await vite.transformIndexHtml(request.url, '<!doctype html><html><head><meta name="viewport" content="width=device-width, initial-scale=1"></head><body><div id="root"></div><script type="module" src="/@id/virtual:skills-smoke"></script></body></html>'));
      });
    },
  }],
  server: { host: '127.0.0.1', port: 0 },
});
let browser;
try {
  await mkdir(screenshotDir, { recursive: true });
  await server.listen();
  const address = server.httpServer.address();
  browser = await puppeteer.launch({ executablePath, headless: true, args: ['--no-sandbox', '--disable-dev-shm-usage'] });
  const page = await browser.newPage();
  const errors = [];
  page.on('pageerror', (error) => errors.push(error.message));
  await page.setRequestInterception(true);
  page.on('request', (request) => request.url().startsWith(`http://127.0.0.1:${address.port}/`) || request.url().startsWith('data:') ? request.continue() : request.abort());
  for (const mode of ['light', 'dark']) {
    for (const width of [1280, 390]) {
      await page.setViewport({ width, height: 900 });
      await page.goto(`http://127.0.0.1:${address.port}/__skills_visual__/?mode=${mode}`, { waitUntil: 'networkidle0' });
      await page.waitForSelector('[role="treeitem"]');
      assert(await page.$('[data-testid="nav-icon"] svg, svg[data-testid="nav-icon"]'), 'Nav icon must render offline.');
      assert(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth), `${mode}/${width}: page overflow`);
      await page.evaluate(() => [...document.querySelectorAll('button')].find((button) => button.textContent === 'refund policy').click());
      assert.match(await page.$eval('[data-testid="selected"]', (node) => node.textContent), /references\/policy.md/);
      await page.screenshot({ path: path.join(screenshotDir, `${mode}-${width}.png`), fullPage: true });
      await page.evaluate(() => [...document.querySelectorAll('button')].find((button) => button.textContent === 'New file').click());
      await page.waitForSelector('[role="dialog"] input');
      await page.type('[role="dialog"] input', 'references/new.md');
      await page.evaluate(() => [...document.querySelectorAll('[role="dialog"] button')].find((button) => button.textContent === 'Create file').click());
      await page.waitForFunction(() => [...document.querySelectorAll('[role="treeitem"]')].some((node) => node.textContent.includes('new.md')));
    }
  }
  assert.deepEqual(errors, [], 'Browser must not throw runtime errors.');
  process.stdout.write(`PASS: four light/dark desktop/mobile renders, offline icon, relative links, file creation, and no page overflow. Screenshots: ${screenshotDir}\n`);
} finally {
  await browser?.close();
  await server.close();
}
