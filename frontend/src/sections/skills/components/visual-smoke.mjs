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
import StatusBadge from 'src/components/status-badge';
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
  h(Box,{sx:{display:'flex',alignItems:'center',gap:1,mb:2}},h(Iconify,{icon:SKILLS_NAV_ICON,'data-testid':'nav-icon'}),h(Typography,{variant:'m1',component:'h1'},'Skills'),h(StatusBadge,{status:'neutral',label:'Production v2'})),
  h(Box,{sx:{display:'grid',gridTemplateColumns:{xs:'minmax(0,1fr)',md:'260px minmax(0,1fr)'},gap:3}},
   h(FileTree,{files,selectedPath:selected,onSelect:select,readOnly:false,onCreate:(p)=>setFiles([...files,{path:p}]),onRename:(a,b)=>setFiles(files.map(f=>f.path===a?{path:b}:f)),onDelete:(p)=>setFiles(files.filter(f=>f.path!==p))}),
   h(Box,{sx:{minWidth:0}},h(Typography,{variant:'s3','data-testid':'selected'},'Selected: '+selected),h(SkillMarkdownPreview,{content:markdown,currentPath:'SKILL.md',onOpenPath:select}))),
  h(Box,{sx:{my:3}},h(TextDiff,{fromText:'Review the policy.\\nDecline expired requests.\\n',toText:'Review the policy.\\nEscalate exceptional requests.\\nRecord the reason.\\n',fromTitle:'v1',toTitle:'Draft'})),
  h(ChecksList,{checks:{items:[{severity:'warning',code:'description_short',message:'Add enough detail for an agent to know when it should use this skill.',path:'SKILL.md',line:3},{severity:'info',code:'allowed_tools_note',message:'Tool approval does not sandbox the skill.'}]}})
 ));
}
createRoot(document.getElementById('root')).render(h(App));
`;
// The app shell around a Skills page (SH1): the real frame styles and <Main>,
// a header that hides text with a bare `width: 1` (100% in sx, which once
// widened the whole document), a run button that scrolls and focuses its
// result the way the Tests form does, and an Inspect drawer.
const shellFixture = `
import React from 'react';
import {createRoot} from 'react-dom/client';
import {Box, Button, CssBaseline, Drawer, Table, TableBody, TableCell, TableHead, TableRow, Typography} from '@mui/material';
import {createTheme, ThemeProvider} from '@mui/material/styles';
import {palette} from 'src/theme/palette';
import {typography} from 'src/theme/typography';
import {dsShape} from 'src/theme/ds-tokens';
import {customShadows} from 'src/theme/custom-shadows';
import {componentsOverrides} from 'src/theme/overrides';
import Main from 'src/layouts/dashboard/main';
import {dashboardFrameSx, PHONE_BAR_HEIGHT} from 'src/layouts/dashboard/frame';
const h = React.createElement;
const theme = createTheme({palette:palette('light'), typography, shape:dsShape, customShadows:customShadows('light')});
theme.components = componentsOverrides(theme);
function Shell() {
 const [open, setOpen] = React.useState(false);
 const phone = window.innerWidth < 900;
 const run = () => { const el = document.getElementById('result'); el.scrollIntoView({block:'start', inline:'nearest'}); el.focus(); };
 const rows = Array.from({length:40}, (_, i) => h(TableRow,{key:i},h(TableCell,null,'Case '+(i+1)),h(TableCell,null,'Not failing')));
 return h(ThemeProvider,{theme},h(CssBaseline),
  phone && h(Box,{sx:{height:PHONE_BAR_HEIGHT,borderBottom:1,borderColor:'divider'}},'Menu'),
  h(Box,{'data-testid':'frame',sx:dashboardFrameSx({phone,contain:true})},
   h(Box,{sx:{width:{xs:0,md:200},flexShrink:0,height:'100vh',position:'relative'}},h(Typography,{sx:{p:2}},'Sidebar')),
   h(Main,{sx:phone?{height:'calc(100vh - '+PHONE_BAR_HEIGHT+'px)'}:undefined},
    h(Box,{sx:{p:3}},
     h(Button,{variant:'contained',onClick:run},'Run on the application'),
     h(Button,{onClick:()=>setOpen(true),sx:{ml:1}},'Inspect'),
     h(Table,{size:'small','aria-label':'Cases to run'},h(TableHead,null,h(TableRow,null,h(TableCell,null,'Case'),h(TableCell,null,h(Box,{component:'span',sx:{position:'absolute',width:1,height:1,overflow:'hidden',clip:'rect(0 0 0 0)'}},'Was failing')))),h(TableBody,null,rows)),
     h(Box,{id:'result',tabIndex:-1,sx:{mt:4,p:2,border:1,borderColor:'divider',width:'calc(100% + 400px)'}},'Result')))),
  h(Drawer,{anchor:'right',open,onClose:()=>setOpen(false)},h(Box,{sx:{width:{xs:'100vw',sm:460},p:2}},h(Button,{onClick:()=>setOpen(false)},'Close inspect'),h(Typography,null,'Attempt'))));
}
createRoot(document.getElementById('root')).render(h(Shell));
`;
const server = await createServer({
  configFile: false, root: frontend, logLevel: 'error',
  resolve: { alias: { src: path.join(frontend, 'src') } },
  plugins: [react(), {
    name: 'skills-smoke-fixture',
    resolveId(id) { return id === 'virtual:skills-smoke' ? '\0skills-smoke' : id === 'virtual:skills-shell' ? '\0skills-shell' : null; },
    load(id) { return id === '\0skills-smoke' ? fixture : id === '\0skills-shell' ? shellFixture : null; },
    configureServer(vite) {
      vite.middlewares.use(async (request, response, next) => {
        const entry = request.url.startsWith('/__skills_visual__/') ? 'skills-smoke' : request.url.startsWith('/__skills_shell__/') ? 'skills-shell' : null;
        if (!entry) return next();
        response.setHeader('Content-Type', 'text/html');
        response.end(await vite.transformIndexHtml(request.url, `<!doctype html><html><head><meta name="viewport" content="width=device-width, initial-scale=1"></head><body><div id="root"></div><script type="module" src="/@id/virtual:${entry}"></script></body></html>`));
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
      const actions = await page.$eval('[aria-label="File actions"]', (node) => {
        const buttons = [...node.querySelectorAll('button')];
        return {
          wrap: getComputedStyle(node).flexWrap,
          tops: buttons.map((button) => button.getBoundingClientRect().top),
          icons: buttons.filter((button) => button.querySelector('svg')).length,
          labels: buttons.map((button) => button.getAttribute('aria-label')),
          fits: buttons.every((button) => button.getBoundingClientRect().right <= node.getBoundingClientRect().right),
        };
      });
      assert.equal(actions.wrap, 'nowrap');
      assert.equal(new Set(actions.tops).size, 1, 'File actions must stay on one row.');
      // R7 replaced "New folder" with paths ("Add file"; folders come from file paths).
      assert.equal(actions.icons, 3, 'All file-action Solar icons must render offline.');
      assert.deepEqual(actions.labels, ['Add file', 'Rename file', 'Delete file']);
      assert(actions.fits, 'File actions must fit inside the tree pane.');
      await page.click('button[aria-label="Add file"]');
      await page.waitForSelector('[role="dialog"] input');
      await page.type('[role="dialog"] input', 'references/new.md');
      await page.evaluate(() => [...document.querySelectorAll('[role="dialog"] button')].find((button) => button.textContent === 'Create file').click());
      await page.waitForFunction(() => [...document.querySelectorAll('[role="treeitem"]')].some((node) => node.textContent.includes('new.md')));
    }
  }
  // SH1: the shell never scrolls. After "Run on the application" (scrollIntoView
  // plus focus on a wide result) and after Inspect opens and closes, the
  // document and the frame stay at scrollLeft 0, the document does not scroll
  // vertically, and nothing widens it past the viewport.
  const shell = () => page.evaluate(() => {
    const frame = document.querySelector('[data-testid="frame"]');
    const html = document.documentElement;
    return { htmlLeft: html.scrollLeft, htmlTop: html.scrollTop, width: html.scrollWidth, height: html.scrollHeight, inner: window.innerWidth, innerHeight: window.innerHeight, frameLeft: frame.scrollLeft, mainTop: document.querySelector('main').scrollTop };
  });
  const assertShell = (value, label) => {
    assert.equal(value.htmlLeft, 0, `${label}: document scrolled sideways`);
    assert.equal(value.frameLeft, 0, `${label}: frame scrolled sideways`);
    assert.equal(value.htmlTop, 0, `${label}: document scrolled vertically`);
    assert(value.width <= value.inner, `${label}: document wider than the viewport (${value.width} > ${value.inner})`);
    assert(value.height <= value.innerHeight, `${label}: document taller than the viewport (${value.height} > ${value.innerHeight})`);
  };
  const clickText = (text) => page.evaluate((label) => [...document.querySelectorAll('button')].find((button) => button.textContent === label).click(), text);
  for (const width of [1280, 390]) {
    await page.setViewport({ width, height: 900 });
    await page.goto(`http://127.0.0.1:${address.port}/__skills_shell__/`, { waitUntil: 'networkidle0' });
    await page.waitForSelector('main');
    assertShell(await shell(), `shell ${width} on load`);
    await clickText('Run on the application');
    await new Promise((resolve) => { setTimeout(resolve, 300); });
    const afterRun = await shell();
    assertShell(afterRun, `shell ${width} after Run on the application`);
    assert(afterRun.mainTop > 0, 'The result scrolls into view inside <main>.');
    await clickText('Inspect');
    await page.waitForSelector('.MuiDrawer-paper');
    assertShell(await shell(), `shell ${width} with Inspect open`);
    await clickText('Close inspect');
    await page.waitForFunction(() => !document.querySelector('.MuiDrawer-paper'));
    assertShell(await shell(), `shell ${width} after Inspect closed`);
    await page.screenshot({ path: path.join(screenshotDir, `shell-${width}.png`) });
  }
  assert.deepEqual(errors, [], 'Browser must not throw runtime errors.');
  process.stdout.write(`PASS: four light/dark desktop/mobile renders, offline icon, relative links, file creation, no page overflow, and a shell that never scrolls after Run and Inspect at 1280 and 390. Screenshots: ${screenshotDir}\n`);
} finally {
  await browser?.close();
  await server.close();
}
