/* eslint-env node */
// Cloud-only visual fixtures. These screenshots exercise real components with synthetic data,
// not a server-backed verification run; the standalone flows remain a separate gate.
import assert from 'node:assert/strict';
import { mkdir } from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { createRequire } from 'node:module';
import { createServer } from 'vite';
import react from '@vitejs/plugin-react-swc';

assert(process.platform === 'linux', 'Browser checks run only on the cloud VM.');
const frontend = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../../../..');
const require = createRequire(path.join(frontend, '../e2e/package.json'));
const { chromium } = require('@playwright/test');
const output = process.env.SKILLS_SCREENSHOT_DIR || '/tmp/r7-tests-shots';
const mocks = {
  'src/api/skills/context-testing': `
import {useState} from 'react';
const state = new URLSearchParams(location.search).get('state');
const digest = 'sha256:' + 'a'.repeat(64);
const cases = [{id:'refund',title:'Refund excludes shipping',prompt:'My mug arrived cracked. Refund order 7.',reviewed:true,checks:[],historical:true}, ...['chargeback','late','cancel'].map((id)=>({id,title:id+' request',prompt:'Check the order policy.',reviewed:true,checks:[]}))];
const arms = [{variant:'candidate',version:2,versionId:'candidate-id',digest},{variant:'reference',version:1,versionId:'reference-id',digest:'sha256:'+'b'.repeat(64)}];
const run = {id:'run',createdAt:'2026-11-12T12:00:00Z',status:state==='running'?'running':'completed',jobState:state==='running'?'provisioning':'completed',arms,cases:[cases[0]],planned:2,received:state==='running'?0:2,configurationDigest:digest,executionFingerprint:digest,decision:state==='running'?null:{state:'release_ready',reasons:[]},result:state==='running'?null:{reproduction:'reproduced',repair_verified:true},cells:arms.map((arm)=>({caseId:'refund',variant:arm.variant,repeatIndex:0,status:state==='running'?'queued':'completed',attemptId:state==='running'?null:arm.variant,answer:arm.variant==='candidate'?'pass':'fail',selection:'pass',delivery:'pass',tools:'pass',observed:{selected:['refund-policy'],actions:[{action:'refund',status:'succeeded'}],answer:'Refunded $25. Shipping is excluded.',context_digest:digest,session_id:'session-id'}}))};
const target = {bindingId:'binding',revision:2,qualified:true,name:'Support application',configurationDigest:digest};
const idle = ()=>({mutate:()=>{},mutateAsync:async()=>{},reset:()=>{},isPending:false});
export const useContextTestSuite = ()=>({data:{revision:1,cases:state==='first-use'?[]:cases}});
export const useSaveContextTestSuite = idle;
export const contextTestCandidate = (draft)=>draft;
export const useNativeTargets = ()=>({data:{runtime:{available:true,runtime:'container',evidenceClass:'synthetic fixture'},targets:state==='first-use'?[]:[target]},refetch:()=>{}});
export const useNativeRuns = ()=>({data:['first-use','empty'].includes(state)?[]:[run]});
export const useNativeRun = (_,id)=>({data:id?run:undefined});
export const useCreateNativeTarget = idle;
export const useQualifyNativeTarget = idle;
export const useRuntimeConfiguration = ()=>({data:{code_commit:'reviewed-commit',entrypoint:'support',runtime_version:'1',packages:[],model:{model_id:'Fixture model'},side_effect_policy:'Read only'}});
export function usePreviewNativeRun(){const [data,setData]=useState();return {data,reset:()=>setData(undefined),mutate:(plan,options)=>{setData({executionFingerprint:digest,reproducible:true,cells:2,ceilingUsd:'0.05',arms,missingInputs:[]});options?.onSuccess?.();}};}
export const useCreateNativeRun = ()=>({mutateAsync:async()=>'run'});
export function usePrepareReleasePacket(){const [data,setData]=useState();return {data,mutate:()=>setData({fingerprint:digest,targetDigest:digest}),isPending:false};}
export const usePreviewContextTest = idle;
export const useCreateContextTest = idle;
export const useCancelContextTest = idle;
export const useContextTestRuns = ()=>({data:{results:[]}});
export const useContextTestRun = ()=>({});
export const useContextTestResults = ()=>({});
export const useContextTestAttempt = ()=>({});`,
  'src/api/skills/testing': `export const useSkillV11Scope=()=>({identity:'visual-fixture'}); export const isActiveSkillJob=()=>false; export const useSkillTestingOptions=()=>({data:{enabled:true,models:[{modelId:'Fixture model',canRun:true}],limits:{profiles:{quick:{maxTotalTokens:1000}}}}}); export const useSkillV11Query=()=>({data:'hello'}); export const skillV11Result=(v)=>v.data.result;`,
  'src/api/skills/evidence-annotations': `export const useEvidenceReference=()=>({data:{annotationRevision:1}}); export const useAppendEvidenceAnnotation=()=>({mutateAsync:async()=>{}}); export const useAllEvidenceAnnotations=()=>({data:{results:[{id:'review',attemptId:'candidate',actorId:'reviewer-id',verdict:'disagree',comment:'The refund is correct, but the explanation needs review.',createdAt:'2026-11-12T12:04:00Z'}]}});`,
  'src/api/skills/context-authoring': `export const useContextDetail=()=>({data:{name:'Support context'}});`,
  'src/api/skills/r3-spend': `export const useSkillsSpendPolicy=()=>({});`,
  'src/api/skills/j6-recovery': `export const useRecoveryReviewers=()=>({data:[{id:'reviewer-id',name:'Fixture reviewer'}]});`,
  'src/api/skills/context-production': `export const readContextText=async()=>({text:'hello'});`,
  'src/api/skills': `export const readSkillFile=async()=>({text:'hello'});`,
  'src/auth/hooks': `export const useAuthContext=()=>({user:{id:'author'}});`,
};
const fixture = `
import React from 'react';
import {createRoot} from 'react-dom/client';
import {MemoryRouter} from 'react-router-dom';
import {Box,CssBaseline,Typography} from '@mui/material';
import {createTheme,ThemeProvider} from '@mui/material/styles';
import {palette} from 'src/theme/palette';
import {typography} from 'src/theme/typography';
import {dsShape} from 'src/theme/ds-tokens';
import {customShadows} from 'src/theme/custom-shadows';
import {componentsOverrides} from 'src/theme/overrides';
import ContextTestsPanel from 'src/sections/skills/contexts/ContextTestsPanel';
import TestInventoryReview from 'src/sections/skills/tests/TestInventoryReview';
const params=new URLSearchParams(location.search), state=params.get('state'), mode=params.get('mode')||'light';
const theme=createTheme({palette:palette(mode),typography,shape:dsShape,customShadows:customShadows(mode)}); theme.components=componentsOverrides(theme);
const digest='sha256:'+'a'.repeat(64);
const preview={fingerprint:'preview',candidate:{skillId:'context',version:2,digest},inventory:[{skillId:'context',digest,files:[{path:'AGENTS.md',sha256:'2cf24dba5fb0a30e26e83b2ac5b9e29e1b161e5c1fa7425e73043362938b9824',sizeBytes:5,flags:[]}]}],outboundFindings:[]};
function App(){const [review,setReview]=React.useState({reviewedAllFiles:false,noKnownAnswerKeys:false,classifications:[],falsePositiveSecretFindings:[]});return <ThemeProvider theme={theme}><CssBaseline/><MemoryRouter><Box sx={{px:{xs:2,md:3},py:3,maxWidth:1400,mx:'auto'}}><Typography variant="s3" color="text.secondary">UI fixture · synthetic data · no application run</Typography><Typography component="h1" variant="m1" sx={{mb:3}}>Support context / Tests</Typography>{state==='inventory'?<TestInventoryReview subjectId="context" subjectKind="agent_context" preview={preview} value={review} onChange={({review:r})=>setReview(r)}/>:<ContextTestsPanel contextId="context" version={{id:'candidate-id',number:2,digest}} versions={[{id:'candidate-id',number:2},{id:'reference-id',number:1}]} productionVersion={1} seed={state==='earlier'?undefined:{caseId:'refund',reference:1}} canWrite state={state} skills={[{id:'skill',label:'Refund policy'}]}/>}</Box></MemoryRouter></ThemeProvider>};
createRoot(document.getElementById('root')).render(<App/>);`;
const server = await createServer({
  configFile: false, root: frontend, logLevel: 'error',
  resolve: { alias: { src: path.join(frontend, 'src') } },
  plugins: [{ name: 'r7-fixture', enforce: 'pre', resolveId(id) {
    if (id === 'virtual:r7-fixture') return '\0r7-fixture.jsx';
    const source = Object.keys(mocks).find((key) => id === key || id === path.join(frontend, key));
    return source ? `\0mock:${source}` : null;
  }, load(id) { if (id === '\0r7-fixture.jsx') return fixture; if (id.startsWith('\0mock:')) return mocks[id.slice(6)]; return null; },
  configureServer(vite) { vite.middlewares.use(async (req, res, next) => { if (!req.url.startsWith('/__r7__/')) return next(); res.setHeader('Content-Type', 'text/html'); res.end(await vite.transformIndexHtml(req.url, '<!doctype html><html><head><meta name="viewport" content="width=device-width,initial-scale=1"></head><body><div id="root"></div><script type="module" src="/@id/virtual:r7-fixture"></script></body></html>')); }); } }, react()],
  server: { host: '127.0.0.1', port: 0 },
});
let browser;
try {
  await mkdir(output, { recursive: true }); await server.listen();
  browser = await chromium.launch({ headless: true });
  const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
  const errors = []; page.on('pageerror', (error) => errors.push(error.message));
  const base = `http://127.0.0.1:${server.httpServer.address().port}`;
  await page.route('**/*', (route) => route.request().url().startsWith(base) || route.request().url().startsWith('data:') ? route.continue() : route.abort());
  for (const [state, name] of [['current','tests-result-current'],['first-use','tests-first-use'],['running','tests-running'],['earlier','tests-result-earlier'],['loading','tests-loading'],['error','tests-error'],['empty','tests-empty'],['off','tests-off']]) {
    for (const mode of ['light','dark']) {
      await page.goto(`${base}/__r7__/?state=${state}&mode=${mode}`); await page.getByRole('heading', {name:'Support context / Tests'}).waitFor();
      await page.screenshot({path:path.join(output,`${name}-${mode}.png`),animations:'disabled'});
    }
  }
  await page.goto(`${base}/__r7__/?state=current`); await page.getByRole('button',{name:'Inspect Refund excludes shipping Candidate repeat 1'}).click();
  await page.getByRole('heading',{name:'Refund excludes shipping',level:2}).waitFor();
  await page.screenshot({path:path.join(output,'tests-disagreement.png')});
  await page.getByRole('button',{name:'Close',exact:true}).click();
  await page.getByText('Recorded reference v1 · also production').scrollIntoViewIfNeeded();
  await page.screenshot({path:path.join(output,'tests-repro-same-version.png')});
  await page.goto(`${base}/__r7__/?state=inventory`); await page.getByRole('button',{name:'View all files sent to the model'}).click(); await page.getByRole('row',{name:'View AGENTS.md'}).click(); await page.getByText('hello',{exact:true}).waitFor();
  await page.screenshot({path:path.join(output,'inventory-file-drawer.png')});
  await page.setViewportSize({width:390,height:900}); await page.goto(`${base}/__r7__/?state=current`); await page.getByRole('heading',{name:'Support context / Tests'}).waitFor();
  await page.screenshot({path:path.join(output,'tests-390.png')});
  const overflow = await page.evaluate(()=>[...document.querySelectorAll('body *')].filter((node)=>node.getBoundingClientRect().right>innerWidth+1 && getComputedStyle(node).position!=='absolute').slice(0,15).map((node)=>({tag:node.tagName,text:node.textContent.slice(0,70),width:node.getBoundingClientRect().width})));
  assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),JSON.stringify(overflow));
  await page.screenshot({path:path.join(output,'tests-390.png')});
  assert.deepEqual(errors,[]); process.stdout.write(`PASS: 20 visual fixture screenshots in ${output}\n`);
} finally { await browser?.close(); await server.close(); }
