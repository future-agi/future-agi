/* eslint-env node */
// Cloud-only composition smoke with synthetic API hooks, not a backend E2E claim.
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import { spawn } from 'node:child_process';
import { createServer } from 'vite';
import react from '@vitejs/plugin-react-swc';

const output = process.argv[2] || '/tmp/i11fe-visual';
await fs.mkdir(output, { recursive: true });
const api = `
const files = [{ path: 'SKILL.md', content: '---\\nname: refund-policy\\ndescription: Use when answering refund requests using the approved policy.\\n---\\n# Refund policy\\nReview the supplied eligibility facts before advising a refund.' }];
const fileHash = Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256', new TextEncoder().encode(files[0].content))), (byte) => byte.toString(16).padStart(2, '0')).join('');
const labels = [{ name: 'production', version: 1, versionId: 'version-1', isProtected: true, canMove: true, canRemove: true, releaseRevision: 4 }];
const checks = { items: [], summary: { errors: 0, warnings: 0, infos: 0 }, tokens: { skillMd: 40, total: 40, metadata: 10 } };
const version = { id: 'version-2', number: 2, digest: 'sha256:synthetic', note: 'Clarify the eligibility boundary', fileCount: 1, totalBytes: 180, labels: [], createdAt: '2026-11-06T12:00:00Z', createdBy: { name: 'Reviewer' }, files: files.map((file) => ({ path: file.path, sha256: 'synthetic', sizeBytes: 180, tokenEstimate: 40 })), checks };
const skill = { id: 'synthetic', name: 'refund-policy', description: 'Use when answering refund requests using the approved policy.', latestVersion: 2, latest: version, labels, protectedLabelNames: ['production'], releaseRevision: 4, drafts: [], hasDraft: true, permissions: { canArchive: true, canManageLabelProtection: true, canRunTests: true } };
let draft = { files, note: '', revision: 7, baseVersion: 1 };
const q = (data) => ({ data, isLoading: false, isPending: false, isError: false, refetch: async () => ({ data }) });
const mutation = (fn = async () => ({})) => ({ mutateAsync: fn, isPending: false });
const emptyPage = { results: [], count: 0, page: 1, pageSize: 25, totalPages: 1 };
export const useSkillV11Scope = () => ({ identity: 'synthetic:workspace:owner', enabled: true });
export const useSkill = () => q(skill);
export const useSkillsList = () => q(emptyPage);
export const useSkillDraft = () => q(draft);
export const useSkillVersion = () => q(version);
export const useSkillVersions = () => q({ ...emptyPage, results: [version], count: 1 });
export const useSkillFile = () => q({ path: 'SKILL.md', text: files[0].content, sha256: 'synthetic', sizeBytes: 180 });
export const useSkillLabels = () => q(labels);
export const useSkillLabelHistory = () => q(emptyPage);
const comparison = { summary: { added: 0, removed: 0, modified: 1, unchanged: 0 }, files: [{ path: 'SKILL.md', status: 'modified', fromText: 'Earlier policy', toText: files[0].content, toSha256: fileHash }] };
export const useSkillCompare = () => q(comparison);
export const useSaveSkillDraft = () => mutation(async (input) => { draft = { ...input, revision: input.revision + 1 }; return draft; });
export const useRunSkillChecks = () => mutation(async () => checks);
export const useSkillTestSuite = () => q({ revision: 0, cases: [], digest: null });
export const useSkillTestRuns = () => q(emptyPage);
export const useSkillTestResults = () => q(emptyPage);
export const useSkillTestEvidence = () => q({ candidateDigest: 'sha256:synthetic', currentReleaseRevision: 4, runs: [], reasonIfNone: null });
export const useSkillTestingOptions = () => q({ enabled: false, githubEnabled: false, models: [] });
export const useSkillTestRun = () => q(null);
export const useSkillTestAttempt = () => q(null);
export const useSkillTestFile = () => q(null);
export const useSkillAssist = () => q(null);
export const useGitHubPreview = () => q(null);
export const useSkillSource = () => ({ ...q(null), isError: true, error: { code: 'source_not_found' } });
export const useSkillLabelProtection = () => q({ protectedLabelNames: ['production'], releaseRevision: 4, canManage: true });
export const useSkillLabelProtectionHistory = () => q(emptyPage);
export const useSkillAdoption = () => q({ ...emptyPage, target: null, summary: { currentOnly: 0, otherOnly: 0, mixed: 0, notSeen: 0, ambiguous: 0 }, window: { start: '2026-10-31T00:00:00Z', end: '2026-11-07T00:00:00Z', asOf: '2026-11-07T00:00:00Z' } });
export const useSkillUsage = () => q({ days: 30, total: 0, byDay: [], byChannel: {}, byVersion: [], consumers: [], lastFetchedAt: null });
export const useSkillV11Query = () => q([]);
export const isActiveSkillJob = (status) => ['queued', 'running', 'cancelling'].includes(status);
export const getSkillErrorCode = (error) => error?.code;
export const downloadVersionZip = async () => undefined;
${['useDiscardSkillDraft', 'usePublishSkillDraft', 'useSetSkillLabel', 'useRestoreSkillVersion', 'useArchiveSkill', 'useSaveSkillTestSuite', 'useCreateSkillTestRun', 'usePreviewSkillTestRun', 'useCancelSkillTestRun', 'useShareSkillTestRun', 'useApplySkillAssist', 'useCancelSkillAssist', 'useCreateSkillAssist', 'usePreviewSkillAssist', 'useCreateGitHubPreview', 'useStageGitHubImport', 'useCheckSkillSource', 'useStageSkillSourceUpdate', 'useSetSkillLabelProtection', 'useImportPreview', 'useImportSkills'].map((name) => `export const ${name} = () => mutation();`).join('\n')}
`;
const entry = `
import React from 'react';
import { createRoot } from 'react-dom/client';
import { BrowserRouter, Routes, Route } from 'react-router-dom';
import { SnackbarProvider } from 'notistack';
import { loader } from '@monaco-editor/react';
loader.config({ paths: { vs: '/__monaco/vs' } });
import '@fontsource/inter/400.css';
import '@fontsource/inter/500.css';
import '@fontsource/inter/600.css';
import ThemeProvider from 'src/theme';
import { SettingsProvider } from 'src/components/settings/context/settings-provider';
import SkillEditorView from 'src/sections/skills/editor/SkillEditorView';
import SkillDetailView from 'src/sections/skills/detail/SkillDetailView';
import ImportSkillsDialog from 'src/sections/skills/library/ImportSkillsDialog';
const themeMode = new URLSearchParams(location.search).get('theme') || 'light';
localStorage.removeItem('settings');
createRoot(document.getElementById('root')).render(<SettingsProvider defaultSettings={{ themeMode, themeDirection: 'ltr', themeContrast: 'default', themeColorPresets: 'default' }}><ThemeProvider><SnackbarProvider><BrowserRouter><Routes><Route path='/dashboard/skills/synthetic/edit' element={<SkillEditorView />} /><Route path='/dashboard/skills/import' element={<ImportSkillsDialog open onClose={() => {}} />} /><Route path='/dashboard/skills/synthetic' element={<SkillDetailView />} /></Routes></BrowserRouter></SnackbarProvider></ThemeProvider></SettingsProvider>);
`;
const server = await createServer({
  configFile: false, root: process.cwd(),
  plugins: [react(), { name: 'skills-integration-smoke', enforce: 'pre',
    resolveId(id) {
      if (id === '/__integration.jsx') return id;
      if (/^(?:src\/api\/skills|.*\/src\/api\/skills)(?:\/(?:testing|assist|github|governance|adoption))?$/.test(id)) return '\0integration-api';
      return null;
    },
    load(id) { if (id === '/__integration.jsx') return entry; if (id === '\0integration-api') return api; return null; },
    configureServer(vite) {
      vite.middlewares.use(async (request, response, next) => {
        if (request.url.startsWith('/__monaco/')) {
          const root = path.resolve(process.env.SKILLS_MONACO_DIR || '/tmp/i11fe-monaco/package/min');
          const filename = path.resolve(root, request.url.slice('/__monaco/'.length).split('?')[0]);
          if (!filename.startsWith(`${root}/`)) { response.statusCode = 404; response.end(); return; }
          try {
            response.setHeader('Content-Type', filename.endsWith('.css') ? 'text/css' : filename.endsWith('.ttf') ? 'font/ttf' : 'application/javascript');
            response.end(await fs.readFile(filename));
          } catch { response.statusCode = 404; response.end(); }
          return;
        }
        if (!request.url.startsWith('/dashboard/skills/')) return next();
        response.setHeader('Content-Type', 'text/html');
        response.end('<!doctype html><html><head><meta name="viewport" content="width=device-width,initial-scale=1"><title>Skills integration</title><script type="module">import RefreshRuntime from "/@react-refresh";RefreshRuntime.injectIntoGlobalHook(window);window.$RefreshReg$=()=>{};window.$RefreshSig$=()=>type=>type;window.__vite_plugin_react_preamble_installed__=true;</script></head><body><div id="root"></div><script type="module" src="/@vite/client"></script><script type="module" src="/__integration.jsx"></script></body></html>');
      });
    },
  }],
  resolve: { alias: [{ find: /^src\/(.*)/, replacement: `${process.cwd()}/src/$1` }] },
  server: { host: '127.0.0.1', port: 5417, strictPort: true, fs: { allow: [process.cwd(), await fs.realpath('node_modules')] } },
});
await server.listen();
const profile = await fs.mkdtemp(path.join(os.tmpdir(), 'i11fe-chromium-'));
const binary = process.env.CHROME_BIN || path.join(os.homedir(), '.cache/ms-playwright/chromium-1194/chrome-linux/chrome');
const chrome = spawn(binary, ['--headless', '--no-sandbox', '--disable-dev-shm-usage', '--remote-debugging-port=9417', `--user-data-dir=${profile}`, 'about:blank'], { stdio: 'ignore' });
const pause = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
async function wait(fn, label) { for (let n = 0; n < 120; n += 1) { if (await fn()) return; await pause(300); } throw new Error(`Timed out: ${label}`); }
let socket;
try {
  await wait(async () => { try { return (await fetch('http://127.0.0.1:9417/json/version')).ok; } catch { return false; } }, 'Chromium');
  const target = await (await fetch('http://127.0.0.1:9417/json/new', { method: 'PUT' })).json();
  socket = new WebSocket(target.webSocketDebuggerUrl);
  await new Promise((resolve) => socket.addEventListener('open', resolve, { once: true }));
  let id = 0;
  const pending = new Map();
  const errors = [];
  socket.addEventListener('message', ({ data }) => {
    const message = JSON.parse(data);
    if (message.id) { const action = pending.get(message.id); pending.delete(message.id); if (message.error) action.reject(new Error(JSON.stringify(message.error))); else action.resolve(message.result); }
    else if (message.method === 'Runtime.exceptionThrown') errors.push(message.params.exceptionDetails);
  });
  const command = (method, params = {}) => new Promise((resolve, reject) => { const requestId = ++id; pending.set(requestId, { resolve, reject }); socket.send(JSON.stringify({ id: requestId, method, params })); });
  const evaluate = async (expression) => { const value = await command('Runtime.evaluate', { expression, returnByValue: true, awaitPromise: true }); if (value.exceptionDetails) throw new Error(JSON.stringify(value.exceptionDetails)); return value.result.value; };
  const click = async (text) => {
    const expression = `(() => { const b = [...document.querySelectorAll('button')].find((x) => x.textContent.trim() === ${JSON.stringify(text)} && !x.disabled); if (!b) return false; b.click(); return true; })()`;
    await wait(() => evaluate(expression), `button ${text}`);
  };
  const seen = (text) => wait(() => evaluate(`Boolean(document.body?.innerText.includes(${JSON.stringify(text)}))`), text);
  const capture = async (name) => {
    await pause(350);
    assert(await evaluate('document.documentElement.scrollWidth <= window.innerWidth'), `No page overflow: ${name}`);
    const shot = await command('Page.captureScreenshot', { format: 'png', captureBeyondViewport: true });
    await fs.writeFile(path.join(output, `${name}.png`), Buffer.from(shot.data, 'base64'));
  };
  await command('Runtime.enable'); await command('Page.enable'); await command('Network.enable');
  await command('Network.setBlockedURLs', { urls: ['https://*', 'http://api.*'] });
  for (const theme of ['light', 'dark']) {
    for (const width of [1280, 390]) {
      const suffix = `${theme}-${width}`;
      await command('Emulation.setDeviceMetricsOverride', { width, height: 960, deviceScaleFactor: 1, mobile: width < 500 });
      const navigate = async (pathname, params = '') => { await command('Page.navigate', { url: `http://127.0.0.1:5417/dashboard/skills/${pathname}?theme=${theme}${params}` }); };
      await navigate('synthetic', '&tab=tests');
      await seen('Does this skill handle your real requests?');
      assert(await evaluate("!document.querySelector('a.MuiButton-contained')"), 'Tests does not compete with a primary Edit action');
      await capture(`tests-${suffix}`);
      await navigate('synthetic/edit', '&tab=tests');
      await seen('Add a real request');
      await wait(() => evaluate("Boolean(document.querySelector('.monaco-editor'))"), 'local Monaco editor');
      await capture(`editor-tests-${suffix}`);
      await click('Draft with AI'); await seen('Connect a supported workspace model');
      await capture(`editor-assist-${suffix}`);
      await click('Publish'); await seen('No test evidence for these files');
      await capture(`publish-evidence-${suffix}`);
      await click('Summarize changes'); await seen('Connect a supported workspace model');
      await capture(`publish-assist-${suffix}`);
      await command('Input.dispatchKeyEvent', { type: 'keyDown', key: 'Escape', code: 'Escape', windowsVirtualKeyCode: 27 });
      await command('Input.dispatchKeyEvent', { type: 'keyUp', key: 'Escape', code: 'Escape', windowsVirtualKeyCode: 27 });
      await wait(() => evaluate("!document.querySelector('[role=dialog]')"), 'Escape closes publish');
      await navigate('synthetic', '&tab=versions'); await seen('Protected names: production');
      await click('Move'); await seen('No test evidence for these files');
      await capture(`move-evidence-${suffix}`);
      await click('Cancel'); await click('Protection'); await seen('Protection history');
      await capture(`protection-${suffix}`);
      await navigate('synthetic', '&tab=usage'); await seen('Fetches show retrieval');
      await capture(`adoption-${suffix}`);
      await navigate('import'); await click('GitHub'); await seen('Import a public GitHub skill');
      assert(await evaluate("[...document.querySelectorAll('button')].filter((b) => b.textContent.trim() === 'Preview import').every((b) => !b.getClientRects().length)"), 'GitHub suppresses the upload submit');
      await capture(`github-${suffix}`);
    }
  }
  assert.deepEqual(errors, [], 'No uncaught browser exceptions');
  process.stdout.write(`PASS: 36 real-component captures; light/dark at 1280px and 390px, editor Tests/Assist, Publish/Move evidence, Protection, Adoption and GitHub composition; no overflow or page exceptions. ${output}\n`);
} finally {
  socket?.close(); chrome.kill('SIGTERM'); await server.close(); await fs.rm(profile, { recursive: true, force: true }).catch(() => undefined);
}
