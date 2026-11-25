import { test, expect, createTraceScenario, installedPythonSdkVersion } from '../../lib/skill-trace-scenario';
import { test as mockTest } from '../../lib/mock-model-fixtures';
import { test as scopeTest } from '../../lib/scope-actors';
import { E2E } from '../../lib/env';
import type { Page, TestInfo } from '@playwright/test';
import { flowAnnotation } from '../../lib/flow-meta';
import { POLL } from '../../lib/state-probe';

const meta = (n: number, goal: string, steps: string[], checks: string[]) => ({ tag: ['@flow'],
  annotation: flowAnnotation({ id: `SKILLS-E2E-${100 + n}`, area: 'skills', userGoal: goal, steps, backendChecks: checks }) });
test.describe.configure({ timeout: 180000 });
test.beforeEach(async ({page}) => { page.setDefaultTimeout(20000); });

// Classify loads after collapsing revisions so an update or deletion cannot leave stale evidence.
const liveLoads = `(SELECT project_id, trace_id, argMax(start_time, _version) AS loaded_at,
    argMax(status, _version) AS load_status,
    argMax(attrs_string['fi.skill.version_id'], _version) AS skill_version_id,
    argMax(attrs_string['fi.skill.id'], _version) AS skill_id,
    argMax(attrs_string['fi.skill.name'], _version) AS skill_name,
    argMax(attrs_string['fi.skill.digest'], _version) AS skill_digest,
    argMax(attrs_string['error.type'], _version) AS error_type,
    argMax(if(attrs_string['gen_ai.agent.name'] != '', attrs_string['gen_ai.agent.name'],
              attrs_string['fi.context.name']), _version) AS agent_name
  FROM spans GROUP BY project_id, trace_id, id
  HAVING argMax(is_deleted, _version) = 0 AND argMax(name, _version) = 'skill.load')`;
const verifiedLoads = `(SELECT * FROM ${liveLoads} WHERE skill_version_id != '')`;

// Capture only after the same populated state is visible again, including after
// theme reload. Empty/skeleton frames are never substitutes for the real surface.
async function captureState(page:Page, info:TestInfo, name:string, ready:()=>Promise<void>) {
  for (const [width,theme] of [[1440,'light'],[1440,'dark'],[390,'light']] as const) {
    await page.setViewportSize({width,height:900});
    await page.evaluate(mode => {
      const current = JSON.parse(localStorage.getItem('settings') || '{}');
      localStorage.setItem('settings',JSON.stringify({...current,themeMode:mode}));
    },theme);
    await page.reload({waitUntil:'domcontentloaded'});
    await ready();
    const shot = `${name}--${width}-${theme}`;
    const path = info.outputPath(`${shot}.png`);
    await page.screenshot({path,animations:'disabled'});
    await info.attach(shot,{path,contentType:'image/png'});
  }
  await page.setViewportSize({width:1440,height:900});
}

for (const [flow, language] of [[1, 'python'], [6, 'typescript']] as const) {
  const goal = `Inspect the two skill versions loaded by a ${language} request`;
  test(`SKILLS-E2E-${100 + flow}: F${flow} ${goal}`, meta(flow, goal,
    ['Publish a context with three linked skills and read only two with the installed SDK', 'Filter Observe by the immutable version and inspect its skill load'],
    ['The two stored skill links match the published identities, digests, and agent; the unread link is absent']),
  async ({ scenario: s, page, probe }, info) => {
    const id = s.emit(language, [s.agent('billing', [s.refund, s.tone])])!;
    const rows = () => probe.ch<{ project_id: string; skill_version_id: string; skill_digest: string; agent_name: string }>(
      `SELECT project_id, skill_version_id, skill_digest, agent_name FROM ${verifiedLoads} WHERE trace_id={t:String} ORDER BY skill_version_id`, { t: id });
    await expect.poll(async () => (await rows()).map(r => [r.skill_version_id, r.skill_digest, r.agent_name]), POLL.SPAN_VISIBLE)
      .toEqual([s.refund, s.tone].sort((a,b) => a.version.id.localeCompare(b.version.id)).map(r => [r.version.id, r.version.digest, 'billing']));
    const filter = [{ column_id: 'skill.version_id', filter_config: { col_type: 'SKILL', filter_type: 'text', filter_op: 'equals', filter_value: s.refund.version.id } }];
    await page.goto(`/dashboard/observe/${(await rows())[0].project_id}/llm-tracing?tab=traces&observeLinkFilter=${encodeURIComponent(JSON.stringify(filter))}`);
    // Check the default independently, then continue through the real display
    // controls if it is missing. A missing default still fails this flow, but
    // it cannot hide a second defect in hydration or the identity card.
    await page.setViewportSize({width:1440,height:900});
    await expect(page.locator(`.ag-row[row-id="${id}"]:visible [col-id="trace_name"]`).first()).toBeVisible({timeout:45000});
    await page.getByRole('button',{name:'Display',exact:true}).click();
    await page.getByText('Add custom columns',{exact:true}).click();
    const chooser = page.getByRole('dialog');
    const offered = chooser.getByRole('checkbox',{name:'Skills',exact:true});
    await expect.soft(offered,'Skills is visible by default for a project with real loads').toBeChecked({timeout:3000});
    if (await offered.isChecked()) await chooser.getByRole('button',{name:'Cancel'}).click();
    else { await offered.check(); await chooser.getByRole('button',{name:'Apply'}).click(); }
    // Returning from the chooser leaves Display open.
    await expect(chooser).toBeHidden();
    // AG Grid virtualizes far-right columns; display the two asserted fields.
    await page.getByText('View columns',{exact:true}).click();
    const popup = page.locator('.MuiPopover-paper:visible').filter({has:page.getByPlaceholder('Search',{exact:true})});
    const box = (name:string) => popup.getByText(name,{exact:true}).locator('..').getByRole('checkbox');
    await expect(box('Skills')).toBeChecked();
    const all = popup.getByRole('checkbox',{name:'Toggle Select all',exact:true});
    await all.check(); await all.uncheck();
    await box('Skills').check(); await box('Trace Name').check();
    await page.keyboard.press('Escape'); await page.keyboard.press('Escape');
    const row = page.locator(`.ag-row[row-id="${id}"]:visible`).first();
    await expect(row.getByLabel('Skills', { exact: true })).toContainText(s.refund.name);
    await expect(row.getByLabel('Skills', { exact: true })).toContainText(s.tone.name);
    await page.screenshot({path:info.outputPath('observe--skills-column--1440-light.png'),animations:'disabled'});
    await row.locator('[col-id="trace_name"]').click();
    await page.getByRole('button', { name: /Skills:/ }).click();
    const list = page.getByRole('list', { name: 'Skills loaded' });
    await expect(list).toContainText('billing');
    await expect(list.getByText(s.tone.name, { exact: true })).toBeVisible();
    await list.getByText(s.refund.name, { exact: true }).click();
    const card = page.getByRole('region', { name: 'Skill load', exact: true });
    await expect(card).toContainText(s.refund.name);
    await expect(card).toContainText(s.refund.version.digest);
    await info.attach('trace-detail--skill-load', { body: await page.screenshot(), contentType: 'image/png' });
  });
}

test('SKILLS-E2E-104: F4 distinguish loaded and unread links', meta(4, 'See which linked skills the application actually loaded',
  ['Read two skills in a context with a third linked skill', 'Open its Runtime and Linked skills tabs'], ['The linked but unread skill stays out of the loaded rows']),
async ({ scenario: s, page },info) => {
  s.emit('python', [s.agent('support', [s.refund, s.tone])]);
  // Wait for the ingested loads before the UI fixes its query window.
  await s.waitForTraces(1);
  await page.goto(`/dashboard/skills/contexts/${s.context.id}?tab=runtime`);
  const table = page.getByRole('table', { name: 'Skills loaded at runtime' });
  await expect(table).toContainText(s.refund.name);
  await expect(table).toContainText(s.tone.name);
  await expect(table).not.toContainText(s.unread.name);
  await expect(page.getByText('Linked, no loads recorded', { exact: true })).toBeVisible();
  await expect(page.getByText(s.unread.name, { exact: true }).first()).toBeVisible();
  await captureState(page,info,'context-runtime--populated',() => expect(table).toContainText(s.refund.name));
  await page.getByRole('tab', { name: 'Linked skills' }).click();
  for (const [skill,value] of [[s.refund,'1 trace'],[s.tone,'1 trace'],[s.unread,'No loads recorded']] as const) {
    await expect(page.getByRole('row').filter({hasText:skill.name}).locator('[data-extra-column]')).toContainText(value);
  }
  await captureState(page,info,'context-linked-skills--loaded-column',() => expect(page.getByText('No loads recorded',{exact:true}).first()).toBeVisible());
});

test('SKILLS-E2E-105: F5 tracing off retains the request without skill loads', meta(5, 'Understand missing loads for an opted-out application',
  ['Turn off Skills tracing and read an immutable skill inside a traced request', 'Inspect the skill Runtime tab'], ['The flushed request is present with no load spans and the SDK fetch reports off']),
async ({ scenario: s, probe, page },info) => {
  const id = s.emit('python', [s.agent('billing', [s.refund], false)], { off: true })!;
  await expect.poll(() => probe.ch<{name:string}>('SELECT name FROM spans FINAL WHERE trace_id={t:String} ORDER BY name', { t:id }), POLL.SPAN_VISIBLE)
    .toEqual([{name:'Python support request'}, {name:'prepare answer'}]);
  expect(await probe.pg('SELECT sdk_version,tracing_mode FROM model_hub_hubskillusagedaily WHERE skill_id=$1 AND channel=$2', [s.refund.id,'sdk'])).toEqual([{sdk_version:installedPythonSdkVersion(),tracing_mode:'off'}]);
  await page.goto(`/dashboard/skills/${s.refund.id}?tab=runtime`);
  await captureState(page,info,'skill-runtime--tracing-off',() => expect(page.getByText(/ha(?:s|ve) tracing switched off/).first()).toBeVisible());
});

test('SKILLS-E2E-107: F7 read files without OpenTelemetry', meta(7, 'Load a skill without installing a telemetry stack',
  ['Run the plain SDK in an isolated interpreter', 'Read its immutable file'], ['The interpreter has no OpenTelemetry package and the SDK fetch bucket reports no_otel']),
async ({ scenario:s, probe }) => {
  expect(s.emit('noOtel', [s.agent('plain', [s.refund], false)])).toBeNull();
  expect(await probe.pg('SELECT sdk_version,tracing_mode FROM model_hub_hubskillusagedaily WHERE skill_id=$1 AND channel=$2', [s.refund.id,'sdk'])).toEqual([{sdk_version:installedPythonSdkVersion(true),tracing_mode:'no_otel'}]);
});

test('SKILLS-E2E-118: F18 check tracing waits for the next request', meta(18, 'Confirm a new application load reaches the project',
  ['Start Check tracing on Install', 'Serve a fresh request with the installed Python SDK'], ['The receipt identifies the skill version and agent from the new load']),
async ({ scenario:s, page }) => {
  await page.goto(`/dashboard/skills/${s.refund.id}?tab=use`);
  await page.getByRole('button', { name:'Check tracing' }).click();
  await expect(page.locator('[data-check-tracing="waiting"]')).toBeVisible();
  s.emit('python', [s.agent('billing', [s.refund], false)]);
  await expect(page.locator('[data-check-tracing="received"]')).toContainText(`Received ${s.refund.name} v1 from billing`, { timeout: 45000 });
});

test('SKILLS-E2E-119: F19 count SDK fetches on both Activity tabs', meta(19, 'See the SDK version that fetched the skill and context',
  ['Resolve a skill and a context with the packaged Python client', 'Inspect both Activity tabs'], ['Both resources report the SDK channel and actual installed version']),
async ({ scenario:s, page }) => {
  s.emit('python', [s.agent('support', [s.refund])]);
  for (const [path, resource] of [['', s.refund], ['contexts/', s.context]] as const) {
    const kind = resource === s.context ? 'agent_context' : 'skill';
    const activity = await s.call('GET', `${resource.id}/usage/?days=7&kind=${kind}`);
    expect(activity.consumers).toEqual(expect.arrayContaining([expect.objectContaining({ channel:'sdk', sdk_version:installedPythonSdkVersion() })]));
    await page.goto(`/dashboard/skills/${path}${resource.id}?tab=usage`);
    if (kind === 'skill') await page.getByRole('button', {name:'Counts and details',exact:true}).click();
    await expect(page.getByRole('table', {name:kind === 'skill' ? 'Skill consumers' : 'Agent context consumers'}).getByText(`SDK · ${installedPythonSdkVersion()}`, {exact:true})).toBeVisible();
  }
});

test('SKILLS-E2E-109: F9 save and reload a version cohort', meta(9, 'Return to a saved skill cohort from its Runtime metric',
  ['Follow the v1 traces number on Runtime', 'Save the view and reload it'], ['The saved view and runtime link both find the exact emitted trace']),
async ({ scenario:s, page, probe }) => {
  const trace = s.emit('python',[s.agent('billing',[s.refund],false)]);
  await s.waitForTraces(1);
  const [project] = await probe.pg<{id:string}>('SELECT id FROM tracer_project WHERE name=$1',[s.project]);
  const filter = [{ column_id:'skill.version_id',filter_config:{col_type:'SKILL',filter_type:'text',filter_op:'equals',filter_value:s.refund.version.id} }];
  await page.goto(`/dashboard/observe/${project.id}/llm-tracing?tab=traces&observeLinkFilter=${encodeURIComponent(JSON.stringify(filter))}`);
  const row = page.locator(`.ag-row[row-id="${trace}"]:visible`).first();
  await expect(row).toBeVisible({timeout:45000});
  await page.locator('[data-create-view-btn]').click();
  await page.getByLabel(/View Name/).fill(s.project);
  const saved = page.waitForResponse(r => r.url().includes('/tracer/saved-views/') && r.request().method()==='POST');
  await page.getByRole('button',{name:'Save view'}).click();
  expect((await saved).ok()).toBe(true);
  await page.reload();
  await expect(row).toBeVisible({timeout:45000});
  await page.goto(`/dashboard/skills/${s.refund.id}?tab=runtime`);
  await page.getByRole('link',{name:'v1 traces: open traces',exact:true}).click();
  await expect(row).toBeVisible({timeout:45000});
});

test('SKILLS-E2E-112: F12 choose a project from a two-project metric', meta(12, 'Open the exact per-project cohort, without a dialog',
  ['Serve one request in each project', 'Choose a line from the traces number'], ['Only the selected project trace is shown']),
async ({ scenario:s, page }) => {
  const other = `${s.project}-two`, a = s.agent('support',[s.refund],false);
  const first = s.emit('python',[a]); const second = s.emit('python',[a],{project:other});
  await s.waitForTraces(2);
  await page.goto(`/dashboard/skills/${s.refund.id}?tab=runtime`);
  await page.getByRole('button',{name:'Choose a project for v1 traces'}).click();
  await expect(page.getByRole('dialog')).toHaveCount(0);
  await expect(page.getByRole('menuitem')).toHaveCount(2);
  const listed = page.waitForResponse(r => r.url().includes('/tracer/trace/list_traces_of_session/') && r.request().method() !== 'OPTIONS', {timeout:60000});
  await page.getByRole('menuitem',{name:new RegExp(`${other}.*1 trace`)}).click();
  const response = await listed;
  expect(response.status()).toBe(200);
  expect((await response.json()).result.table.map((r:{trace_id:string}) => r.trace_id)).toEqual([second]);
  await expect(page.locator(`.ag-row[row-id="${second}"]:visible`).first()).toBeVisible({timeout:45000});
  await expect(page.locator(`.ag-row[row-id="${first}"]:visible`)).toHaveCount(0);
});

test('SKILLS-E2E-113: F13 retain failed load without a verified identity', meta(13, 'Inspect a failed integrity check separately from verified loads',
  ['Read a damaged file with the real verifying SDK', 'Follow the failed-load link to the trace detail'], ['The failed load has no verified link; Observe filters to the actual failing request']),
async ({ scenario:s, page, probe }) => {
  const trace = s.emit('python',[s.agent('support',[s.refund],false)],{damaged:true})!;
  await expect.poll(() => probe.ch(`SELECT skill_name,error_type FROM ${liveLoads}
    WHERE trace_id={t:String} AND load_status = 'ERROR' AND skill_version_id = ''`,{t:trace}),POLL.SPAN_VISIBLE).toEqual([{skill_name:s.refund.name,error_type:'integrity'}]);
  expect(await probe.ch(`SELECT skill_id FROM ${verifiedLoads} WHERE trace_id={t:String}`,{t:trace})).toEqual([]);
  await page.goto(`/dashboard/skills/${s.refund.id}?tab=runtime`);
  const line = page.locator('[data-line="failed-loads"]');
  await expect(line).toContainText('integrity');
  await line.getByRole('link').click();
  expect(new URL(page.url()).searchParams.get('observeLinkFilter')).toContain('skill.load_result');
  await page.locator(`.ag-row[row-id="${trace}"]:visible [col-id="trace_name"]`).first().click();
  await page.locator('[data-trace-skills-chip]').click();
  await expect(page.getByRole('list',{name:'Skills loaded'})).toContainText(`${s.refund.name} · load failed · integrity check`);
});

test('SKILLS-E2E-114: F14 explain historical, disabled and MCP-only fetches', meta(14,'Diagnose first-day fetches without suggesting a runtime load',
  ['Fetch separate skills with a client reporting an old version, tracing off, and MCP only', 'Open Runtime for each resource'], ['The three diagnosis states describe the corresponding transport and cannot claim a load']),
async ({ scenario:s, page, actor },info) => {
  const { readSkillViaMcp } = await import('../../lib/skills-consumption');
  const { E2E } = await import('../../lib/env');
  s.emit('python',[s.agent('billing',[s.refund],false)],{oldVersion:true,fetchOnly:true});
  s.emit('python',[s.agent('billing',[s.tone],false)],{off:true});
  await readSkillViaMcp(E2E.apiUrl,actor,s.unread.name);
  for (const [r,reason,capture] of [[s.refund,/use an SDK older than/,'skill-runtime--fetched-old-sdk'],[s.tone,/tracing switched off/,'skill-runtime--tracing-off'],[s.unread,/through MCP/,'skill-runtime--mcp-only']] as const) {
    await page.goto(`/dashboard/skills/${r.id}?tab=runtime`);
    await captureState(page,info,capture,() => expect(page.getByText(reason).first()).toBeVisible());
  }
});

test('SKILLS-E2E-117: F17 inspect the same agent on three surfaces', meta(17,'Inspect the same agent and its skills from Runtime and Observe',
  ['Open the agent in Loaded by', 'Inspect the same agent from the grid and trace detail'], ['All three panels show both actual loaded versions in the same project']),
async ({ scenario:s, page, probe }) => {
  const trace = s.emit('python',[s.agent('billing',[s.refund,s.tone],false)])!;
  await s.waitForTraces(1);
  const check = async () => {
    const agent = page.getByRole('dialog',{name:'billing'});
    await expect(agent).toContainText(s.refund.name);
    await expect(agent).toContainText(s.tone.name);
    await agent.getByRole('button',{name:'Close agent panel'}).click();
  };
  await page.goto(`/dashboard/skills/${s.refund.id}?tab=runtime`);
  const versionRow = page.locator('tr[data-row="version"]').first();
  await expect(versionRow).toBeVisible();
  if (await versionRow.getAttribute('aria-expanded') !== 'true') await versionRow.getByRole('cell').first().click();
  await page.locator('tr[data-row="agent"]').getByText('billing', {exact:true}).click();
  await check();
  const [project] = await probe.pg<{id:string}>('SELECT id FROM tracer_project WHERE name=$1',[s.project]);
  await page.goto(`/dashboard/observe/${project.id}/llm-tracing?tab=traces`);
  const row = page.locator(`.ag-row[row-id="${trace}"]:visible`).first();
  await page.getByRole('button',{name:'Display',exact:true}).click();
  await page.getByText('Add custom columns',{exact:true}).click();
  const chooser = page.getByRole('dialog');
  await chooser.getByRole('checkbox',{name:'Agent',exact:true}).check();
  await chooser.getByRole('button',{name:'Apply',exact:true}).click();
  await expect(chooser).toBeHidden();
  await expect(page.getByText('View columns',{exact:true})).toBeVisible();
  await page.keyboard.press('Escape');
  await expect(page.getByText('View columns',{exact:true})).toBeHidden();
  const scroll = page.locator('.ag-body-horizontal-scroll-viewport:visible').first();
  await scroll.evaluate(el => { el.scrollLeft = el.scrollWidth; });
  await row.getByRole('button',{name:'billing',exact:true}).click();
  await check();
  await scroll.evaluate(el => { el.scrollLeft=0; });
  await row.locator('[col-id="trace_name"]').click();
  await page.locator('[data-trace-skills-chip]').click();
  await page.getByRole('list',{name:'Skills loaded'}).getByText('billing',{exact:true}).first().click();
  await check();
});

for (const [flow, goal] of [[3,'Read production adoption and the reason an agent stays on v1'],[11,'Open failing requests after a production change'],[16,'Distinguish an unreleased follower from a pinned skill']] as const) {
  test(`SKILLS-E2E-${100+flow}: F${flow} ${goal}`,meta(flow,goal,['Serve production requests on v1', 'Move the production label in the UI', 'Serve fresh and version-pinned requests and inspect Runtime'],
    ['The version and agent cohort comes from actual reads before and after the move']),async ({scenario:s,page,probe}) => {
    test.setTimeout(300000);
    const main = s.agent('support',[s.refund],false);
    main.skills[0].selector = {label:'production'};
    const pinned = s.agent('pinned',[s.refund],false);
    const follower = s.agent('follower',[]);
    follower.context!.reads = [`skills/${s.refund.name}/SKILL.md`];
    s.emit('python',[main],{requests:flow === 11 ? 32 : 1});
    if (flow !== 11) s.emit('python',[pinned, follower]);
    const v2 = await s.publishV2();
    await page.goto(`/dashboard/skills/${s.refund.id}?tab=versions`);
    await page.getByRole('button',{name:'Actions for production'}).click();
    await page.getByRole('menuitem',{name:'Move',exact:true}).click();
    const dialog = page.getByRole('dialog');
    await dialog.getByRole('combobox',{name:/^Version/}).click();
    await page.getByRole('option',{name:'v2',exact:true}).click();
    await dialog.getByRole('button',{name:'Move production from v1 to v2'}).click();
    await expect(dialog).toHaveCount(0);
    const failed = s.emit('python',[main],{requests:flow === 11 ? 32 : 1,failedRequest:flow === 11});
    if (flow !== 11) s.emit('python',[pinned, follower]);
    // Runtime pins its window at mount; wait for every post-move request first.
    await s.waitForTraces(flow === 11 ? 64 : 4);
    await page.goto(`/dashboard/skills/${s.refund.id}?tab=runtime`);
    if (flow === 11) {
      await expect(page.getByRole('link', { name: /v2: failed requests up so far · 1 agent crossed the threshold · \d+ of 24 h/i })).toBeVisible();
      await expect(page.locator('[data-section="same-agents"]')).toBeVisible();
      const listed = page.waitForResponse(r => r.url().includes('/tracer/trace/list_traces_of_session/') && r.request().method() !== 'OPTIONS', {timeout:60000});
      await page.getByRole('link',{name:'Review 32 failed requests'}).click();
      const listResponse = await listed;
      expect(listResponse.status(), 'Observe must execute the Runtime cohort link').toBe(200);
      const firstPage = (await listResponse.json()).result;
      let result = firstPage;
      const seen = firstPage.table.map((row:{trace_id:string}) => row.trace_id);
      // A full first page reports a lower bound, not the cohort total. Read
      // the next signed page through Observe before asserting the exact 32.
      if (firstPage.metadata.total_rows_is_lower_bound) {
        expect(firstPage.metadata.has_more).toBe(true);
        const next = page.waitForResponse(r => r.url().includes('/tracer/trace/list_traces_of_session/') && r.request().method() !== 'OPTIONS' &&
          (r.request().postData() || r.url()).includes('cursor'));
        await page.getByRole('button',{name:'Next page',exact:true}).click();
        const response = await next;
        expect(response.status()).toBe(200);
        result = (await response.json()).result;
        seen.push(...result.table.map((row:{trace_id:string}) => row.trace_id));
      }
      expect(result.metadata.total_rows_is_lower_bound).toBe(false);
      expect(result.metadata.total_rows).toBe(32);
      const actual = await probe.ch<{trace_id:string}>(`SELECT DISTINCT trace_id FROM ${verifiedLoads} WHERE skill_version_id={version:String}`, {version:v2.id});
      expect(actual).toHaveLength(32);
      expect(actual.map(r => r.trace_id)).toContain(failed);
      expect(result.table.length).toBeGreaterThan(0);
      expect(seen.sort()).toEqual(actual.map(r => r.trace_id).sort());
      const params = new URL(page.url()).searchParams;
      expect(params.get('observeLinkFilter')).toContain('skill.trace_cohort');
      expect(params.get('skillSource')).toBeTruthy();
      await page.getByRole('button',{name:'Compare with v1 before the move'}).click();
      await expect.poll(() => new URL(page.url()).searchParams.get('observeLinkFilter')).not.toBe(params.get('observeLinkFilter'));
      await page.getByRole('link',{name:/back/i}).first().click();
      await expect(page.locator('[data-section="same-agents"]')).toBeVisible();
    } else if (flow === 16) {
      const reason = page.locator('[data-reason="waiting_for_context_release"]').filter({hasText:'follower'});
      await expect(reason).toContainText(`Still on v1: waiting for a ${s.context.name} release`);
      await expect(reason).not.toContainText('pinned in');
    } else {
      await expect(page.getByText('Still on v1: the app asks for v1').first()).toBeVisible();
      for (const version of [1,2]) await expect(page.locator('tr[data-row="version"]').filter({hasText:`v${version}`})).toBeVisible();
      const [row] = await probe.ch<{count:number}>(`SELECT uniqExact(skill_version_id) AS count FROM ${verifiedLoads} WHERE skill_id={skill:String}`, {skill:s.refund.id});
      expect(Number(row.count)).toBe(2);
    }
  });
}

mockTest.describe('F10 background-safe eval fixture', () => {
  mockTest.use({ evalBackground:true });
  mockTest('SKILLS-E2E-110: F10 show evaluation score with coverage',meta(10,'Read the score and coverage on the skill version cohort',
    ['Run a real application request', 'Register a local mock-routed evaluator and run a historical evaluation', 'Read the eval cell in Loaded by'],
    ['The durable task completes and only the single evaluated trace contributes to coverage']),async ({actor,request,page,probe,mockModel}) => {
    mockTest.setTimeout(300000);
    const s = await createTraceScenario(actor,request);
    try {
      const trace = s.emit('python',[s.agent('billing',[s.refund],false)])!;
      const [project] = await probe.pg<{id:string}>('SELECT id FROM tracer_project WHERE organization_id=$1 AND name=$2',[actor.organizationId,s.project]);
      await expect.poll(() => probe.ch("SELECT name FROM spans FINAL WHERE project_id={p:UUID} AND trace_id={t:String} AND parent_span_id=''", {p:project.id,t:trace}), POLL.SPAN_VISIBLE)
        .toEqual([{name:'Python support request'}]);
      await mockModel.assertReady();
      const path = '/model-hub/eval-templates/create-v2/';
      const name = `${s.project}-review`;
      const tmpl = await actor.api.post<{result:{id:string}}>(path,{name,eval_type:'llm', instructions:'Reply with exactly this JSON: {"result":"Pass","explanation":"reviewed {{answer}}"}', model:mockModel.model,output_type:'pass_fail',pass_threshold:0.5,check_internet:false,error_localizer_enabled:false});
      const mapping = {answer:'name'};
      const cfg = await actor.api.post<{result:{id:string}}>('/tracer/custom-eval-config/',{project:project.id,eval_template:tmpl.result.id,name,model:mockModel.model,mapping,config:{mapping},error_localizer:false});
      await mockModel.assertReady();
      const task = await actor.api.post<{result:{id:string}}>('/tracer/eval-task/',{name,project:project.id,evals:[cfg.result.id],filters:{project_id:project.id,trace_id:[trace],date_range:[new Date(Date.now()-3600000).toISOString(),new Date(Date.now()+3600000).toISOString()]},run_type:'historical',row_type:'traces',spans_limit:1,sampling_rate:100});
      await expect.poll(() => probe.pg('SELECT status FROM tracer_eval_task WHERE id=$1',[task.result.id]),POLL.EVAL_RESULT).toEqual([{status:'completed'}]);
      await expect.poll(() => probe.pg('SELECT status,error FROM tracer_eval_logger WHERE eval_task_id=$1',[task.result.id]),POLL.EVAL_RESULT).toEqual([{status:'completed',error:false}]);
      // Runtime reads the selected ClickHouse mirror. PG task completion alone
      // can race its arrival and cache a legitimate pre-score snapshot in the UI.
      await expect.poll(() => probe.ch('SELECT toString(trace_id) AS trace, toUInt8(output_bool) AS score FROM tracer_eval_logger FINAL WHERE eval_task_id={task:String} AND status=\'completed\' AND error=0',
        {task:task.result.id}),POLL.CDC_VISIBLE).toEqual([{trace,score:1}]);
      await page.goto(`/dashboard/skills/${s.refund.id}?tab=runtime`);
      await expect(page.getByRole('table',{name:'Loaded by'})).toContainText(/1\.00 · 100% evaluated/);
    } finally { await s.revoke(); }
  });
});

scopeTest('SKILLS-E2E-115: F15 inspect the changed skill on both release cards',meta(15,'Verify the skill that changed across two reviewed context releases',
  ['Release context v1, move one linked skill and refresh its link in v2', 'Independently approve the new release', 'Inspect Runtime and the receipt'],
  ['The refreshed dependency references the new immutable skill version and both cards identify that same skill']),async ({scopeActors,browser,request},info) => {
  scopeTest.setTimeout(240000);
  const s = await createTraceScenario(scopeActors.owners[0],request);
  const module = '../../lib/skills-r3/j6-scenario.mjs';
  const {actorOf, publicApi, publishContextVersion, moveProduction} = await import(module);
  const {api} = publicApi(E2E.apiUrl), author = actorOf(scopeActors.member), reviewer = actorOf(scopeActors.ownerA);
  try {
    s.emit('python',[s.agent('billing',[s.refund])]);
    await moveProduction({api,author,reviewer,contextId:s.context.id,version:s.context.version});
    await s.publishV2();
    const skill = await s.call('GET',`${s.refund.id}/`);
    await s.call('PUT',`${s.refund.id}/labels/production/`,{version:2,expected_version:1,expected_release_revision:skill.release_revision});
    const path = `contexts/${s.context.id}/`;
    const draft = await api(author,'POST',`${path}draft/start/`,{revision:0,from_version:1});
    const input = {revision:draft.revision, links:[s.refund,s.tone,s.unread].map(r => ({skill_id:r.id,selector:{label:'production'}})),refresh_skill_ids:[s.refund.id]};
    const preview = await api(author,'POST',`${path}dependencies/preview/`,input);
    await api(author,'POST',`${path}dependencies/apply/`,{...input,expected_fingerprint:preview.fingerprint});
    const version = await publishContextVersion({api,author,contextId:s.context.id,draft:await api(author,'GET',`${path}draft/`),base:1});
    const packet = await api(author,'POST','release-requests/preview/',{resource_id:s.context.id,kind:'agent_context',candidate_version_id:version.id,label:'production',target:null,evidence:[],external_pipeline:null});
    const sent = await api(author,'POST','release-requests/',{packet:packet.packet,expected_fingerprint:packet.fingerprint,recipient_ids:[reviewer.userId],revision:0});
    const rev = {revision:sent.revision,expected_fingerprint:packet.fingerprint};
    await api(reviewer,'POST',`release-requests/${sent.id}/decisions/`,{...rev,decision:'approve',comment:'Reviewed fixture'});
    await api(reviewer,'POST',`release-requests/${sent.id}/execute/`,rev);
    await s.advanceContext(version);
    const app = s.agent('billing', []);
    app.context!.selector = {version:2};
    app.context!.reads = [`skills/${s.refund.name}/SKILL.md`];
    s.emit('python',[app]);
    await s.waitForTraces(2);
    const ctx = await scopeActors.openContext(browser,scopeActors.ownerA);
    const page = await ctx.newPage();
    for (const url of [`/dashboard/skills/contexts/${s.context.id}?tab=runtime`,`/dashboard/skills/contexts/${s.context.id}/review?request=${sent.id}`]) {
      await page.goto(url);
      const changed = page.locator('[data-section="changed-skills"]');
      await expect(changed).toContainText(s.refund.name);
      await expect(changed).not.toContainText(s.tone.name);
      if (url.endsWith('tab=runtime')) await captureState(page,info,'context-runtime--release-card',() => expect(changed).toContainText(s.refund.name));
    }
  } finally { await s.revoke(); }
});

test('SKILLS-E2E-108: F8 native verification emits no customer loads',meta(8,'Keep native test runs out of production attribution',
  ['Prove the customer project receives a real SDK load', 'Run two matched native cells against production-like fixture inputs', 'Inspect the job window after completion'],
  ['Both native cells ran to completion without adding a skill link to the customer project']),async ({scenario:s,probe,nativeNamespace}) => {
  test.setTimeout(480000);
  const {seedApplication,neighborCase,modelRules} = await import('../../lib/skills-r3/j2-fixture');
  const {measuredConfiguration} = await import('../../lib/skills-native-fixtures');
  const {SkillsFixtures} = await import('../../lib/skills-fixtures');
  const control = s.emit('python',[s.agent('support',[s.refund],false)])!;
  const lookup = () => probe.ch<{project_id:string}>(`SELECT project_id FROM ${verifiedLoads} WHERE trace_id={t:String}`,{t:control});
  await expect.poll(async () => (await lookup()).length,POLL.SPAN_VISIBLE).toBe(1);
  const project = (await lookup())[0].project_id;
  const seeded = await seedApplication(s.call, s.project);
  const root = `contexts/${seeded.contextId}/`;
  const policy = await s.call('GET','spend-policy/');
  await s.call('PUT','spend-policy/',{revision:policy.revision,enabled:true,period_limit_usd:'20.00'});
  const cases = await s.call('PUT',`${root}tests/`,{revision:0,cases:[neighborCase('shipping',seeded.skills)]});
  const fixtures = new SkillsFixtures(nativeNamespace);
  // The nativeNamespace fixture holds the shared runtime until cleanup.
  // A conflict is a setup failure, never a reason to delete another run's rules.
  await fixtures.register(modelRules(seeded.skills));
  try {
    const configuration = await measuredConfiguration(s.call,seeded.contextId);
    const target = await s.call('POST',`${root}native/targets/`,{version:4});
    const qualified = await s.call('PUT',`${root}native/targets/${target.binding_id}/`,{revision:target.revision,configuration});
    const plan = {binding_id:target.binding_id,binding_revision:qualified.revision,candidate_version:6,baselines:{reference:4},case_ids:[cases.cases[0].id],repetitions:1,historical_case_ids:[]};
    const preview = await s.call('POST',`${root}native/preview/`,plan);
    const start = Date.now();
    const run = await s.call('POST',`${root}native/runs/`,{...plan,execution_fingerprint:preview.execution_fingerprint,acknowledge_spend:true});
    const status = () => s.call('GET',`${root}native/runs/${run.evidence_id}/`);
    await expect.poll(async () => (await status()).status,{timeout:240000}).toMatch(/^(completed|failed|canceled)$/);
    const done = await status();
    expect(done.status).toBe('completed');
    expect(done.cells).toHaveLength(2);
    expect(done.cells.every((c:{status:string}) => c.status === 'completed')).toBe(true);
    // Send a second real SDK load down the same customer ingestion path after the job.
    // Query only after its link is visible; the two control traces are not native traffic.
    const end = Date.now();
    const watermark = s.emit('python', [s.agent('support', [s.refund], false)])!;
    expect(watermark).not.toBe(control);
    await expect.poll(() => probe.ch(`SELECT project_id FROM ${verifiedLoads} WHERE trace_id={t:String}`,
      {t:watermark}), POLL.SPAN_VISIBLE).toEqual([{project_id:project}]);
    expect(await probe.ch(`SELECT trace_id FROM ${verifiedLoads} WHERE project_id={p:UUID}
      AND loaded_at BETWEEN fromUnixTimestamp64Milli({start:Int64}) AND fromUnixTimestamp64Milli({end:Int64}) + INTERVAL 1 MILLISECOND
      AND trace_id NOT IN ({before:String}, {after:String})`, {p:project,start,end,before:control,after:watermark})).toEqual([]);
  } finally { await fixtures.dispose(); }
});
