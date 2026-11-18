import type { Page } from '@playwright/test';
import { test, expect } from '../../lib/skill-trace-scenario';
import { POLL } from '../../lib/state-probe';
import { flowAnnotation } from '../../lib/flow-meta';

const item = (field:string, value:string, group = 0) => ({ column_id:`skill.${field}`, group,
  filter_config:{ col_type:'SKILL', filter_type:'text', filter_op:'equals', filter_value:value } });

/** Wait for the actual filtered response, not an empty grid during navigation. */
async function list(page: Page, project:string, filters: ReturnType<typeof item>[]) {
  const pending = page.waitForResponse(r => {
    if (!r.url().includes('/tracer/trace/list_traces_of_session/') || r.request().method() === 'OPTIONS') return false;
    const body = r.request().method() === 'POST' ? r.request().postDataJSON()?.filters : new URL(r.url()).searchParams.get('filters');
    const items = typeof body === 'string' ? JSON.parse(body) : body;
    return Array.isArray(items) && filters.every(f => items.some(i => i.column_id === f.column_id && i.filter_config?.filter_value === f.filter_config.filter_value));
  });
  await page.goto(`/dashboard/observe/${project}/llm-tracing?tab=traces&observeLinkFilter=${encodeURIComponent(JSON.stringify(filters))}`);
  const res = await pending;
  expect(res.status()).toBe(200);
  return (await res.json()).result.table.map((row: { trace_id: string }) => row.trace_id);
}

test('OBS-E2E-059: F2 correlate the skill and agent within one load', { tag:['@flow'], annotation:flowAnnotation({
  id:'OBS-E2E-059', area:'observe', userGoal:'Find traces where the chosen agent loaded the chosen skill',
  steps:['Serve one trace with independent agent scopes A and B', 'Filter first for A, then B, then a separate also-loaded group'],
  backendChecks:['The two SDK loads belong to different agents', 'The real list response rejects a cross-agent match but accepts the separate load group'],
}) }, async ({ scenario:s, probe, page }) => {
  test.setTimeout(180000);
  const t = s.emit('python', [s.agent('agent-a',[s.refund],false),s.agent('agent-b',[s.tone],false)])!;
  const rows = () => probe.ch<{ project_id:string; agent_name:string; skill_id:string }>('SELECT project_id,agent_name,skill_id FROM skill_trace_links FINAL WHERE trace_id={t:String} ORDER BY agent_name',{t});
  await expect.poll(async () => (await rows()).map(r => [r.agent_name,r.skill_id]), POLL.SPAN_VISIBLE).toEqual([['agent-a',s.refund.id],['agent-b',s.tone.id]]);
  const p = (await rows())[0].project_id;
  expect(await list(page,p,[item('version_id',s.refund.version.id),item('agent','agent-a')])).toEqual([t]);
  expect(await list(page,p,[item('version_id',s.refund.version.id),item('agent','agent-b')])).toEqual([]);
  expect(await list(page,p,[item('version_id',s.refund.version.id),item('version_id',s.tone.version.id,1)])).toEqual([t]);
});

test('OBS-E2E-060: reconcile scoped spans and skill-version cohorts', { tag:['@flow'], annotation:flowAnnotation({
  id:'OBS-E2E-060',area:'observe',userGoal:'Tell which spans ran after a skill was loaded, and inspect the overlapping version cohorts',
  steps:['Run requests for two agents that load different versions', 'Open the filtered span list and compare it with storage', 'Switch to the trace cohort and split the graph by version'],
  backendChecks:['Only spans stamped with the selected version are in the scoped span list','The version trace filter, cohort strip and split graph identify the same application request'],
}) }, async ({scenario:s,page,probe}) => {
  test.setTimeout(180000); page.setDefaultTimeout(30000);
  const t = s.emit('python',[s.agent('support',[s.refund],false)])!;
  const other = s.emit('python',[s.agent('writing',[s.tone],false)])!;
  const [project] = await probe.pg<{id:string}>('SELECT id FROM tracer_project WHERE name=$1',[s.project]);
  const params = {t,v:s.refund.version.id};
  const read = () => probe.ch<{id:string;name:string}>('SELECT id,name FROM spans FINAL WHERE trace_id={t:String} AND has(skill_version_ids,{v:String}) ORDER BY id',params);
  await expect.poll(async () => (await read()).map(r=>r.name),POLL.SPAN_VISIBLE).toContain('prepare answer');
  const filter = [item('in_scope',s.refund.version.id)];
  const versionFilter = [item('version_id',s.refund.version.id)];
  const path = '/tracer/observation-span/list_spans_observe/';
  const pending = page.waitForResponse(r => r.url().includes(path) && r.request().method()!=='OPTIONS' && (r.request().postData()||r.url()).includes(s.refund.version.id));
  await page.goto(`/dashboard/observe/${project.id}/llm-tracing?tab=traces&selectedTab=spans&observeLinkFilter=${encodeURIComponent(JSON.stringify(filter))}`);
  const res = await pending;
  expect(res.status()).toBe(200);
  const rows = (await res.json()).result.table as {span_id:string;trace_id:string}[];
  expect(rows.map(row=>row.span_id).sort()).toEqual((await read()).map(row=>row.id));
  expect(rows.some(row=>row.trace_id===other)).toBe(false);
  expect(await list(page,project.id,versionFilter)).toEqual([t]);
  await expect(page.getByRole('region',{name:'Traces matching these filters'})).toContainText(/Traces\s*1/);
  const split = page.getByLabel('Split by skill version',{exact:true});
  if (!await split.isVisible()) {
    await page.getByTestId('graph-metric-picker-trigger').first().click();
    await page.getByPlaceholder('Search metrics...').fill('Latency');
    await page.getByRole('button',{name:'Latency',exact:true}).click();
  }
  await split.check();
  await expect(page.getByText('Cohorts overlap; not additive.',{exact:false})).toBeVisible({timeout:45000});
  await page.getByText('View values and sample counts',{exact:true}).click();
  await expect(page.getByRole('table',{name:/by skill version/})).toContainText(s.refund.name);
});
