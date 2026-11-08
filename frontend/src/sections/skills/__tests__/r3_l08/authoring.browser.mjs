/* eslint-env node */
// Cloud-only synthetic component qualification. No backend, production data or E2E claim.
import assert from "node:assert/strict";
import fs from "node:fs/promises";
import path from "node:path";
import { createServer } from "vite";
import react from "@vitejs/plugin-react-swc";
const { chromium } = await import(process.env.L8_PLAYWRIGHT_MODULE || "playwright");
const output = path.resolve(process.argv[2] || "/tmp/l8-authoring-shots");
await fs.mkdir(output, { recursive: true });
const entry = `
import React from 'react';
import { createRoot } from 'react-dom/client';
import { Box, Typography } from '@mui/material';
import { SnackbarProvider } from 'notistack';
import ThemeProvider from 'src/theme';
import { SettingsProvider } from 'src/components/settings/context/settings-provider';
import ContextLibraryView from 'src/sections/skills/contexts/ContextLibraryView';
import ContextDetailView from 'src/sections/skills/contexts/ContextDetailView';
import ContextLinksPanel from 'src/sections/skills/contexts/ContextLinksPanel';
import ContextImportDialog from 'src/sections/skills/contexts/ContextImportDialog';
import ContextSafetyDialog from 'src/sections/skills/contexts/ContextSafetyDialog';
import HotfixHeadReview from 'src/sections/skills/editor/HotfixHeadReview';
import RecoverableDraftChanges from 'src/sections/skills/editor/RecoverableDraftChanges';
import MigrationPlanPanel from 'src/sections/skills/library/MigrationPlanPanel';
import ArchivedSkillsView from 'src/sections/skills/library/ArchivedSkillsView';
import '@fontsource/inter/400.css';
import '@fontsource/inter/500.css';
import '@fontsource/inter/600.css';
const params = new URLSearchParams(location.search);
localStorage.removeItem('settings');
const scopeKey = 'synthetic:workspace:author:context';
const digest = 'sha256:' + 'a'.repeat(64);
const context = { id: 'context', name: 'support-agent', description: 'Approved standing policy for refund and chargeback triage.', latestVersion: 6, labels: [{ name: 'production', version: 3 }], hasDraft: true };
const session = { revision: 12, baseVersion: 4, digest, description: context.description, editSequence: 0, dirty: false, files: [{path:'AGENTS.md',content:'# Policy\\nRefer refund requests to the approved eligibility skill.\\n'}], contextSpec: { links: [{ skillId: 'child', selector: { label: 'production' } }] }, resolvedLock: { links: [{skillId:'child',mount:'skills/refunds/',requested:{label:'production'},version:8,digest}] } };
session.flush = async () => session;
session.mutateDraft = async (operation) => operation(session);
const query = { scopeKey, data: { results: [context], count: 1, page: 1, pageSize: 25 } };
const version = { id: 'version4', number: 4, digest };
const preview = {review:{subjectId:'context',draftRevision:12,candidateDigest:digest,baseVersion:4,expectedHeadVersion:6,reviewFingerprint:digest},originChanges:[{path:'AGENTS.md',status:'modified',fromText:'Approve every refund.',toText:'Use the reviewed eligibility policy.'}],headChanges:[{path:'AGENTS.md',status:'modified',fromText:'Unrelated v6 workflow.',toText:'Use the reviewed eligibility policy.'}]};
const item = {key:'source',root:'refunds/',name:'refund-policy',kind:'skill',sourceOrigin:'langfuse',sourceProject:'synthetic-project',sourceResource:'immutable-resource',sourceRevision:'v2',destinationId:'destination',expectedHeadVersion:6,expectedHeadDigest:digest,diverged:true,hasDraft:true,sourceChanges:preview.originChanges,hubChanges:preview.headChanges,files:[{path:'SKILL.md',sizeBytes:120}]};
const scenes = {
 library: <ContextLibraryView scopeKey={scopeKey} query={query} onOpen={()=>{}} onFilterChange={()=>{}} />,
 detail: <ContextDetailView scopeKey={scopeKey} query={{scopeKey,data:{context,ownDraft:{revision:12,baseVersion:4}}}} versionQuery={{scopeKey,data:{version,descriptor:{description:context.description,links:session.resolvedLock.links},files:[{path:'AGENTS.md',origin:{kind:'owned'}},{path:'.futureagi/context.json',origin:{kind:'generated'}}]}}} fileQuery={{scopeKey,data:{path:'AGENTS.md',versionId:'version4',text:session.files[0].content}}} versions={[version]} onResumeDraft={()=>{}} onEditViewed={()=>{}} onPreserveDraft={()=>{}} />,
 links: <ContextLinksPanel scopeKey={scopeKey} session={session} availableSkills={[{id:'child',name:'refunds',kind:'skill',latestVersion:9}]} upstream={{child:{version:9}}} onPreview={async()=>({revision:12,fingerprint:digest,candidateDigest:digest,checks:{summary:{errors:0}},changes:[{skillId:'child',mount:'skills/refunds/',change:'version_changed',before:{version:8},after:{version:9}}]})} onApply={async()=>session} />,
 head: <HotfixHeadReview scopeKey={scopeKey} session={session} onPreview={async()=>preview} onAcknowledge={async()=>session} />,
 recovery: <RecoverableDraftChanges scopeKey={scopeKey} session={session} baseFiles={[{path:'AGENTS.md',content:'Original policy.'}]} serverDraft={{...session,revision:14}} onSuspend={async()=>({id:'checkpoint'})} onReload={()=>{}} targetVersion={4} />,
 migration: <MigrationPlanPanel scopeKey={scopeKey} plan={{id:'plan',fingerprint:digest,expiresAt:'2099-01-01T00:00:00Z',items:[item],closure:{fileCount:4,totalBytes:520,linkCount:1}}} onAccept={async()=>({id:'receipt'})} onPreserveDraft={()=>{}} />,
 archived: <ArchivedSkillsView scopeKey={scopeKey} query={query} canRecover onInspect={()=>{}} onReview={async()=>({subjectId:'context',releaseRevision:7,labels:context.labels})} onUnarchive={async()=>({id:'context'})} />,
 import: <ContextImportDialog scopeKey={scopeKey} open onClose={()=>{}} onCreate={async()=>({context})} onPreview={async()=>({})} />,
 safety: <ContextSafetyDialog scopeKey={scopeKey} open onClose={()=>{}} action='unarchive' resource={context} review={{subjectId:'context',releaseRevision:7,labels:[{name:'production',version:3,isProtected:true}]}} canAct onApply={async()=>({id:'context'})} />,
};
createRoot(document.getElementById('root')).render(<SettingsProvider defaultSettings={{themeMode:params.get('theme')||'light',themeDirection:'ltr',themeContrast:'default',themeColorPresets:'default'}}><ThemeProvider><SnackbarProvider><Box sx={{p:2,minWidth:0}}><Typography variant='s3' color='text.secondary'>Synthetic component fixture · no backend or publication</Typography><Box sx={{mt:2,minWidth:0}}>{scenes[params.get('scene')]}</Box></Box></SnackbarProvider></ThemeProvider></SettingsProvider>);
`;
const server = await createServer({
  configFile: false, root: process.cwd(),
  plugins: [react(), {
    name: "l8-authoring-fixture",
    resolveId(id) { return id === "/__l8.jsx" ? id : null; },
    load(id) { return id === "/__l8.jsx" ? entry : null; },
    configureServer(vite) {
      vite.middlewares.use((request, response, next) => {
        if (!request.url.startsWith("/l8-authoring")) { next(); return; }
        response.setHeader("Content-Type", "text/html");
        response.end('<!doctype html><html><head><meta name="viewport" content="width=device-width,initial-scale=1"><title>Authoring fixture</title><script type="module">import RefreshRuntime from "/@react-refresh";RefreshRuntime.injectIntoGlobalHook(window);window.$RefreshReg$=()=>{};window.$RefreshSig$=()=>type=>type;window.__vite_plugin_react_preamble_installed__=true;</script></head><body><div id="root"></div><script type="module" src="/@vite/client"></script><script type="module" src="/__l8.jsx"></script></body></html>');
      });
    },
  }],
  resolve: { alias: [{ find: /^src\/(.*)/, replacement: `${process.cwd()}/src/$1` }] },
  server: { host: "127.0.0.1", port: 0, fs: { allow: [process.cwd(), await fs.realpath("node_modules")] } },
});
await server.listen();
const origin = `http://127.0.0.1:${server.httpServer.address().port}`;
let browser;
const errors = [];
let captures = 0;
try {
  browser = await chromium.launch({ headless: true, args: ["--disable-dev-shm-usage"] });
  const page = await browser.newPage();
  page.on("pageerror", (error) => errors.push(error.message));
  await page.route("**/*", (route) => route.request().url().startsWith(origin) ? route.continue() : route.abort());
  for (const theme of ["light", "dark"]) {
    for (const width of [1280, 640, 390, 320]) {
      await page.setViewportSize({ width, height: 960 });
      for (const scene of ["library", "detail", "links", "head", "recovery", "migration", "archived", "import", "safety"]) {
        await page.goto(`${origin}/l8-authoring?theme=${theme}&scene=${scene}`);
        await page.getByText("Synthetic component fixture · no backend or publication").waitFor({ state: "attached" });
        if (scene === "head") { await page.getByRole("button", { name: "Review against current latest" }).click(); await page.getByText(/v4-derived/).waitFor(); }
        if (scene === "links") { await page.getByRole("button", { name: "Preview dependency changes" }).click(); await page.getByText(/Compiled candidate/).waitFor(); }
        if (scene === "migration") { await page.getByRole("combobox", { name: "Review item" }).click(); await page.getByRole("option", { name: "refund-policy" }).click(); await page.getByRole("listbox").waitFor({ state: "hidden" }); }
        await page.evaluate(() => document.fonts.ready);
        assert(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth), `Page overflow: ${scene}/${theme}/${width}`);
        await page.screenshot({ path: path.join(output, `${scene}-${theme}-${width}.png`), fullPage: true });
        captures += 1;
      }
    }
  }
  assert.deepEqual(errors, [], "No uncaught browser errors");
  process.stdout.write(`PASS ${captures} authoring component captures, light/dark at 1280/640/390/320 CSS px. Browser ${browser.version()}. No full-app or backend conformance claim.\n`);
} finally { await browser?.close(); await server.close(); }
