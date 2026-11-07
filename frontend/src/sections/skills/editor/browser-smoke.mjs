/* eslint-env node */
// Cloud-only browser component smoke. API hooks are synthetic; this is not a backend E2E test.
// Run from frontend: node src/sections/skills/editor/browser-smoke.mjs /tmp/f4-visual
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import { spawn } from 'node:child_process';
import { createServer } from 'vite';
import react from '@vitejs/plugin-react-swc';

const output = path.resolve(process.argv[2] || '/tmp/f4-visual');
await fs.mkdir(output, { recursive: true });
const api = `
const root = '---\\nname: support-policy\\ndescription: Use when reviewing a customer refund request against the policy.\\n---\\n# Review a refund\\n\\nRead [the policy](references/policy.md) before recommending a refund.\\n';
let draft = { baseVersion: 1, revision: 4, note: '', files: [{ path: 'SKILL.md', content: root }, { path: 'references/policy.md', content: '# Refund policy\\n\\nReview the purchase date and eligibility before responding.' }] };
const skill = { id: 'synthetic', name: 'support-policy', latestVersion: 2 };
const checks = { items: [{ code: 'description_short', severity: 'warning', message: 'Review the description before publishing.', path: 'SKILL.md', line: 3 }], summary: { errors: 0, warnings: 1, infos: 0 }, tokens: { skillMd: 50 } };
const save = async (value) => { draft = { ...value, revision: value.revision + 1 }; return draft; };
const runChecks = async () => checks;
let publishAttempts = 0;
const publish = async (value) => {
  if (value.revision !== draft.revision) throw new Error('Publication was not bound to the saved draft revision');
  if (!publishAttempts++) {
    if (new URLSearchParams(location.search).has('conflict')) {
      draft = { ...draft, revision: draft.revision + 1, files: [{ path: 'SKILL.md', content: root + '\\nUpdated in another tab.\\n' }] };
      throw { code: 'draft_conflict', detail: { revision: draft.revision } };
    }
    throw { code: 'stale_base', detail: { base_version: 1, latest_version: 2 } };
  }
  return { version: { number: 3 }, unchanged: false };
};
export const useSkill = () => ({ data: skill });
export const useSkillDraft = () => ({ data: draft, refetch: async () => ({ data: draft }) });
export const useSkillVersion = () => ({ data: { files: draft.files } });
export const useSkillFile = (_id, _number, file) => ({ data: { text: draft.files.find((item) => item.path === file).content } });
export const useSaveSkillDraft = () => ({ mutateAsync: save });
export const useDiscardSkillDraft = () => ({ mutateAsync: async () => undefined, isPending: false });
export const useRunSkillChecks = () => ({ mutateAsync: runChecks });
export const useSkillCompare = () => ({ data: { files: draft.files.map((file) => ({ path: file.path, status: 'modified', fromText: 'Earlier instructions', toText: file.content })) } });
export const usePublishSkillDraft = () => ({ mutateAsync: publish });
export const getSkillErrorCode = (error) => error.code;
`;
const entry = `
import React from 'react';
import { createRoot } from 'react-dom/client';
import { BrowserRouter, Routes, Route } from 'react-router-dom';
import { SnackbarProvider } from 'notistack';
import '@fontsource/inter/400.css';
import '@fontsource/inter/500.css';
import '@fontsource/inter/600.css';
import ThemeProvider from 'src/theme';
import { SettingsProvider } from 'src/components/settings/context/settings-provider';
import SkillEditorView from 'src/sections/skills/editor/SkillEditorView';
const themeMode = new URLSearchParams(location.search).get('theme') || 'light';
localStorage.removeItem('settings');
createRoot(document.getElementById('root')).render(<SettingsProvider defaultSettings={{ themeMode, themeDirection: 'ltr', themeContrast: 'default', themeColorPresets: 'default' }}><ThemeProvider><SnackbarProvider><BrowserRouter><Routes><Route path='/dashboard/skills/:skillId/edit' element={<SkillEditorView />} /><Route path='/dashboard/skills/:skillId' element={<div>Published skill detail</div>} /></Routes></BrowserRouter></SnackbarProvider></ThemeProvider></SettingsProvider>);
`;
const server = await createServer({
  configFile: false,
  root: process.cwd(),
  plugins: [react(), {
    name: 'skills-editor-smoke',
    enforce: 'pre',
    resolveId(id) {
      if (id === '/__editor-smoke.jsx') return id;
      if (id === 'src/api/skills' || id.endsWith('/src/api/skills')) return '\0editor-smoke-api';
      return null;
    },
    load(id) {
      if (id === '/__editor-smoke.jsx') return entry;
      if (id === '\0editor-smoke-api') return api;
      return null;
    },
    configureServer(vite) {
      vite.middlewares.use((request, response, next) => {
        if (!request.url.startsWith('/dashboard/skills/')) return next();
        response.setHeader('Content-Type', 'text/html');
        response.end('<!doctype html><html><head><meta name="viewport" content="width=device-width,initial-scale=1"><title>Editor smoke</title><script type="module">import RefreshRuntime from "/@react-refresh";RefreshRuntime.injectIntoGlobalHook(window);window.$RefreshReg$=()=>{};window.$RefreshSig$=()=>type=>type;window.__vite_plugin_react_preamble_installed__=true;</script></head><body><div id="root"></div><script type="module" src="/@vite/client"></script><script type="module" src="/__editor-smoke.jsx"></script></body></html>');
      });
    },
  }],
  resolve: { alias: [{ find: /^src\/(.*)/, replacement: `${process.cwd()}/src/$1` }] },
  server: { host: '127.0.0.1', port: 5414, strictPort: true, fs: { allow: [process.cwd(), await fs.realpath('node_modules')] } },
});
await server.listen();
const profile = await fs.mkdtemp(path.join(os.tmpdir(), 'f4-chromium-'));
const binary = process.env.CHROME_BIN || path.join(os.homedir(), '.cache/ms-playwright/chromium-1194/chrome-linux/chrome');
const chrome = spawn(binary, ['--headless', '--no-sandbox', '--disable-dev-shm-usage', '--remote-debugging-port=9414', `--user-data-dir=${profile}`, 'about:blank'], { stdio: 'ignore' });
let socket;
let captureFailure = async () => undefined;
const pause = (milliseconds) => new Promise((resolve) => setTimeout(resolve, milliseconds));
const wait = async (fn, label) => {
  for (let attempt = 0; attempt < 120; attempt += 1) {
    if (await fn()) return;
    await pause(500);
  }
  throw new Error(`Timed out: ${label}`);
};
try {
  await wait(async () => { try { return (await fetch('http://127.0.0.1:9414/json/version')).ok; } catch { return false; } }, 'Chromium');
  const target = await (await fetch('http://127.0.0.1:9414/json/new', { method: 'PUT' })).json();
  socket = new WebSocket(target.webSocketDebuggerUrl);
  await new Promise((resolve) => socket.addEventListener('open', resolve, { once: true }));
  let id = 0;
  const pending = new Map();
  const errors = [];
  socket.addEventListener('message', ({ data }) => {
    const message = JSON.parse(data);
    if (message.id) {
      const action = pending.get(message.id);
      pending.delete(message.id);
      if (message.error) action.reject(new Error(JSON.stringify(message.error))); else action.resolve(message.result);
    } else if (message.method === 'Runtime.exceptionThrown') errors.push(message.params.exceptionDetails.exception?.description || message.params.exceptionDetails.text);
  });
  const command = (method, params = {}) => new Promise((resolve, reject) => {
    const requestId = ++id;
    pending.set(requestId, { resolve, reject });
    socket.send(JSON.stringify({ id: requestId, method, params }));
  });
  const evaluate = async (expression) => {
    const result = await command('Runtime.evaluate', { expression, returnByValue: true, awaitPromise: true });
    if (result.exceptionDetails) throw new Error(JSON.stringify(result.exceptionDetails));
    return result.result.value;
  };
  const click = (text) => evaluate(`(() => { const button = [...document.querySelectorAll('button')].find((item) => item.textContent.trim() === ${JSON.stringify(text)} && !item.disabled); if (!button) throw new Error('Missing enabled button: ${text}'); button.click(); return true; })()`);
  const screenshot = async (name) => {
    await pause(400);
    const shot = await command('Page.captureScreenshot', { format: 'png', captureBeyondViewport: true });
    await fs.writeFile(path.join(output, `${name}.png`), Buffer.from(shot.data, 'base64'));
  };
  captureFailure = async () => {
    process.stderr.write(JSON.stringify({ errors, body: await evaluate('document.body.innerText'), editor: await evaluate("Boolean(document.querySelector('.monaco-editor'))") }, null, 2));
    await screenshot('failure');
  };
  await command('Runtime.enable');
  await command('Page.enable');
  await command('Emulation.setDeviceMetricsOverride', { width: 1440, height: 1000, deviceScaleFactor: 1, mobile: false });
  await command('Page.navigate', { url: 'http://127.0.0.1:5414/dashboard/skills/synthetic/edit' });
  await wait(() => evaluate("Boolean(document.querySelector('.monaco-editor')) && document.body.textContent.includes('Review the description')"), 'editor and checks');
  await screenshot('editor-light');
  await click('Preview');
  await wait(() => evaluate("Boolean(document.querySelector('h1')?.textContent.includes('support-policy')) && document.body.textContent.includes('Review a refund')"), 'Markdown preview');
  await click('Edit');
  await click('New file');
  await wait(() => evaluate("Boolean(document.querySelector('[role=dialog] input'))"), 'new file dialog');
  await evaluate("(() => { const input = document.querySelector('[role=dialog] input'); Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set.call(input, 'references/notes.md'); input.dispatchEvent(new Event('input', { bubbles: true })); })()");
  await click('Create file');
  await wait(() => evaluate("document.body.textContent.includes('notes.md')"), 'created file');
  await pause(1500);
  await click('Publish');
  await wait(() => evaluate("document.body.textContent.includes('I reviewed the warnings')"), 'publish review');
  assert(await evaluate("[...document.querySelectorAll('[role=dialog] button')].find((item) => item.textContent.trim() === 'Publish').disabled"), 'Warnings must gate publishing');
  await evaluate("[...document.querySelectorAll('label')].find((item) => item.textContent.includes('I reviewed the warnings')).querySelector('input').click()");
  await click('Publish');
  await wait(() => evaluate("document.body.textContent.includes('Publish anyway')"), 'stale base explanation');
  await screenshot('publish-stale-light');
  await click('Publish anyway');
  await wait(() => evaluate("document.body.textContent.includes('Published skill detail')"), 'publish success navigation');
  await command('Page.navigate', { url: 'http://127.0.0.1:5414/dashboard/skills/synthetic/edit?theme=dark' });
  await wait(() => evaluate("document.body.dataset.theme === 'dark' && Boolean(document.querySelector('.monaco-editor'))"), 'dark editor');
  await screenshot('editor-dark');
  await command('Emulation.setDeviceMetricsOverride', { width: 390, height: 844, deviceScaleFactor: 1, mobile: true });
  await pause(500);
  assert(await evaluate('document.documentElement.scrollWidth <= window.innerWidth'), 'No horizontal page overflow at phone width');
  await screenshot('editor-mobile-dark');
  for (const [theme, choice] of [['light', 'Reload theirs'], ['dark', 'Overwrite with mine']]) {
    await command('Emulation.setDeviceMetricsOverride', { width: theme === 'dark' ? 390 : 1440, height: 1000, deviceScaleFactor: 1, mobile: theme === 'dark' });
    await command('Page.navigate', { url: `http://127.0.0.1:5414/dashboard/skills/synthetic/edit?conflict=1&theme=${theme}` });
    await wait(() => evaluate("Boolean(document.querySelector('.monaco-editor'))"), 'conflict editor');
    await click('Publish');
    await wait(() => evaluate("document.body.textContent.includes('I reviewed the warnings')"), 'conflict review');
    await evaluate("[...document.querySelectorAll('label')].find((item) => item.textContent.includes('I reviewed the warnings')).querySelector('input').click()");
    await click('Publish');
    await wait(() => evaluate("document.body.textContent.includes('Nothing was published.')"), 'revision conflict choices');
    assert(await evaluate('document.documentElement.scrollWidth <= window.innerWidth'), 'Conflict dialog fits viewport');
    await screenshot(`publish-conflict-${theme}`);
    await click(choice);
    await wait(() => evaluate("!document.querySelector('[role=dialog]')"), 'return to editor after conflict recovery');
    await click('Preview');
    assert.equal(await evaluate("document.body.textContent.includes('Updated in another tab.')"), choice === 'Reload theirs', 'Recovery chooses the intended files');
    await click('Edit');
    await click('Publish');
    await wait(() => evaluate("document.body.textContent.includes('I reviewed the warnings')"), 'new review after conflict');
    assert(await evaluate("[...document.querySelectorAll('[role=dialog] button')].find((item) => item.textContent.trim() === 'Publish').disabled"), 'Recovery requires a fresh warning acknowledgment');
    await evaluate("[...document.querySelectorAll('label')].find((item) => item.textContent.includes('I reviewed the warnings')).querySelector('input').click()");
    await click('Publish');
    await wait(() => evaluate("document.body.textContent.includes('Published skill detail')"), 'revision-bound publication after recovery');
  }
  assert.deepEqual(errors, [], 'No uncaught browser exceptions');
  process.stdout.write(`PASS: light/dark editor, mobile overflow, file creation, Markdown preview, warning gate, stale-base confirmation, revision-bound publishing, and reload/overwrite recovery in light/dark. Screenshots: ${output}\n`);
} catch (error) {
  await captureFailure();
  throw error;
} finally {
  socket?.close();
  chrome.kill('SIGTERM');
  await server.close();
  await fs.rm(profile, { recursive: true, force: true }).catch(() => undefined);
}
