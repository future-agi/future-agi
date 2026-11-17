import type { Locator } from '@playwright/test';
import { test, expect } from '../../lib/fixtures';
import { flowAnnotation } from '../../lib/flow-meta';
import { sendTrace, type SeededTrace } from '../../lib/otlp';
import { E2E } from '../../lib/env';
import { POLL } from '../../lib/state-probe';
import { browserErrors, shot, UI_READY, SESSIONS } from './native-source';

// saved-views.js / useSessionTurnFields.js use this same list and mutation API.
const SAVED_VIEWS = '/tracer/saved-views/';
type TurnField = { kind: 'value'; label: string; path: string }
  | { kind: 'metric'; metric: string };
const selected: TurnField[] = [
  { kind: 'value', label: 'Answer', path: 'output.answer.text' },
  { kind: 'value', label: 'Question', path: 'input.messages[-1].content' },
  { kind: 'metric', metric: 'total_tokens' },
];
const reduced = [selected[0], selected[2]];
interface Turn extends SeededTrace {
  input: string;
  output: string;
  question: string;
  answer: string;
  ignored: string;
  total: number;
}
interface Session { project_id: string; trace_session_id: string; external_session_id: string }
interface History {
  result: {
    response: { trace_id: string; input: string; output: string;
      system_metrics: { project_id: string; total_tokens: number } }[];
    session_metadata: { session_id: string; total_traces: number; total_tokens: number };
  };
}
interface SavedView {
  id: string;
  name: string;
  tab_type: string;
  visibility: string;
  created_by: { id: string; email: string };
  config: { display: { sessionTurnFields: TurnField[] } };
}
interface ViewList { result: { custom_views: SavedView[] } }

test.use({ viewport: { width: 1440, height: 900 } });

test(
  'OBS-E2E-065: session turn fields persist per project and follow into the drawer',
  {
    tag: ['@flow'],
    annotation: flowAnnotation({
      id: 'OBS-E2E-065',
      area: 'observe',
      userGoal: 'Choose ordered per-turn JSON fields and a metric, retain them across reloads, and inspect the same project preferences in session drawers',
      steps: [
        'ingest two distinct JSON turns in one session and a same-named sibling-project session, then open the two-turn session from the grid',
        'open Turn fields, choose input and output JSON paths plus Total tokens, move Answer before Question, and apply',
        'inspect every turn for exactly the ordered Answer and Question values and the sole selected metric',
        'reload the page, reopen the session, and verify the saved selection and every turn',
        'remove Question and apply, then verify its absence from every turn and the saved configuration',
        'reopen from the project grid and the cross-project user grid, verify inherited fields and the real project, and confirm the sibling project retains defaults',
      ],
      backendChecks: [
        'Postgres projects belong to the actor organization and workspace; ClickHouse FINAL spans and sessions retain exact seeded membership and JSON values',
        'the session list and history APIs return exactly the seeded turns, JSON input/output, real project IDs and token totals',
        'UI POST and PUT requests persist the ordered descriptors in config.display.sessionTurnFields through the saved-views API',
        'saved-view API reads and the same Postgres row retain the personal project-scoped configuration after reload and field removal',
        'the cross-project drawer reads preferences for the loaded turn project, while the sibling project has no saved view and still shows defaults',
      ],
    }),
  },
  async ({ page, request, actor, probe }, testInfo) => {
    // Two collector polls, fourteen short UI chains, plus seeding/API headroom.
    test.setTimeout(30_000 + 14 * UI_READY + 90_000);
    page.setDefaultTimeout(UI_READY);
    const checkBrowser = browserErrors(page, testInfo);
    try {
      const prefix = `e2e-obs65-${testInfo.workerIndex}-${Date.now().toString(36)}`;
      const projectName = `${prefix}-project`;
      const siblingName = `${prefix}-sibling`;
      const externalSession = `${prefix}-session`;
      const user = `${prefix}-user`;
      const turns: Turn[] = [];
      // Same sendTrace/grouping pattern as catalog-session-parity.spec.ts;
      // native-source.ts supplies browser checks, screenshots and UI budgets.
      const start = BigInt(Date.now() - 60_000) * 1_000_000n;
      for (const [index, name] of [projectName, projectName, siblingName].entries()) {
        const question = `${prefix}-question-${index}`;
        const answer = `${prefix}-answer-${index}`;
        const ignored = `${prefix}-unselected-${index}`;
        const input = JSON.stringify({ messages: [{ content: ignored }, { content: question }] });
        const output = JSON.stringify({ answer: { text: answer }, debug: ignored });
        const identity = { 'session.id': externalSession, 'user.id': user };
        const values = { 'input.value': input, 'output.value': output };
        const prompt = 7 + index * 4, completion = 3 + index * 2;
        const seeded = await sendTrace(request, {
          collectorUrl: E2E.collectorUrl, apiKey: actor.apiKey, secretKey: actor.secretKey,
          projectName: name, rootName: `${prefix}-turn-${index}`,
          resourceAttributes: { project_type: 'observe' },
          startTimeUnixNano: start + BigInt(index) * 2_000_000_000n,
          endTimeUnixNano: start + BigInt(index) * 2_000_000_000n + 50_000_000n,
          rootAttributes: { ...identity, ...values },
          childAttributes: { ...identity, ...values,
            'gen_ai.usage.input_tokens': prompt, 'gen_ai.usage.output_tokens': completion,
            'gen_ai.cost.total': 0.01 },
        });
        turns.push({ ...seeded, input, output, question, answer, ignored, total: prompt + completion });
      }
      const projects = await probe.pg<{ id: string; name: string }>(
        'SELECT id, name FROM tracer_project WHERE name = ANY($1) AND organization_id=$2 AND workspace_id=$3',
        [[projectName, siblingName], actor.organizationId, actor.workspaceId],
      );
      expect(projects.map(p => p.name).sort()).toEqual([projectName, siblingName].sort());
      const projectId = projects.find(p => p.name === projectName)!.id;
      const siblingId = projects.find(p => p.name === siblingName)!.id;
      let sessions: Session[] = [];
      await expect.poll(async () => {
        sessions = await probe.ch<Session>(
          `SELECT project_id, trace_session_id, external_session_id FROM trace_sessions FINAL
           WHERE project_id IN ({a:UUID}, {b:UUID}) AND is_deleted=0`,
          { a: projectId, b: siblingId },
        );
        return sessions.map(s => `${s.project_id}:${s.external_session_id}`).sort();
      }, POLL.SPAN_VISIBLE).toEqual([`${projectId}:${externalSession}`, `${siblingId}:${externalSession}`].sort());
      const sid = sessions.find(s => s.project_id === projectId)!.trace_session_id;
      const siblingSid = sessions.find(s => s.project_id === siblingId)!.trace_session_id;
      expect(sid).not.toBe(siblingSid);
      const expectedSpans = turns.flatMap(turn => turn.spanIds.map(id => ({
        id, trace_id: turn.traceId, project_id: turn.projectName === projectName ? projectId : siblingId,
        trace_session_id: turn.projectName === projectName ? sid : siblingSid,
        input: turn.input, output: turn.output,
      }))).sort((a, b) => a.id.localeCompare(b.id));
      await expect.poll(() => probe.ch(
        `SELECT id, trace_id, project_id, trace_session_id, input, output FROM spans FINAL
         WHERE project_id IN ({a:UUID}, {b:UUID}) ORDER BY id`,
        { a: projectId, b: siblingId },
      ), POLL.SPAN_VISIBLE).toEqual(expectedSpans);
      const members = turns.slice(0, 2);
      await testInfo.attach('seeded-session-turns', {
        body: JSON.stringify({ projects, sessions, turns }), contentType: 'application/json',
      });

      const drawer = page.locator('.MuiDrawer-paper:visible').filter({
        has: page.getByRole('tablist', { name: 'session drawer tabs', exact: true }),
      });
      const dialog = page.getByRole('dialog', { name: 'Turn fields', exact: true });
      const cards = drawer.locator('.MuiGrid-container').filter({
        has: page.getByRole('button', { name: 'View Trace', exact: true }),
      });
      const field = (index: number) => dialog.getByRole('group', { name: `Field ${index}`, exact: true });
      const sessionCells = page.locator('.ag-row [col-id="session_id"]:visible');
      const gridIds = () => sessionCells.evaluateAll(cells =>
        cells.map(cell => cell.closest('.ag-row')!.getAttribute('row-id')).sort());
      const readViews = (id: string) => actor.api.get<ViewList>(SAVED_VIEWS, { project_id: id });
      let savedId: string;
      let creatorId: string;

      const assertSaved = async (fields: TurnField[]) => {
        await expect.poll(async () => {
          const views = (await readViews(projectId)).result.custom_views;
          return views.map(v => ({ id: v.id, name: v.name, tab_type: v.tab_type,
            visibility: v.visibility, creator: v.created_by.email,
            fields: v.config.display.sessionTurnFields }));
        }, POLL.ASYNC_JOB).toEqual([{ id: savedId, name: 'Default sessions view', tab_type: 'sessions',
          visibility: 'personal', creator: actor.email, fields }]);
        expect(await probe.pg(
          `SELECT id, project_id, workspace_id, created_by_id, name, tab_type, visibility,
           config->'display'->'sessionTurnFields' AS fields FROM tracer_saved_view
           WHERE project_id=$1 AND deleted=false`, [projectId],
        )).toEqual([{ id: savedId, project_id: projectId, workspace_id: actor.workspaceId,
          created_by_id: creatorId, name: 'Default sessions view', tab_type: 'sessions', visibility: 'personal', fields }]);
        expect((await readViews(siblingId)).result.custom_views).toEqual([]);
      };
      const openSession = async (sessionId: string, expected: Turn[], actualProject: string, crossProject = false) => {
        const pending = page.waitForResponse(r =>
          new URL(r.url()).pathname === `/tracer/trace-session/${sessionId}/query/`
          && r.request().method() !== 'OPTIONS', { timeout: UI_READY });
        await page.locator(`.ag-row[row-id="${sessionId}"] [col-id="session_id"]:visible`).click();
        const response = await pending;
        expect(response.status()).toBe(200);
        const params = response.request().method() === 'POST' ? response.request().postDataJSON()
          : Object.fromEntries(new URL(response.url()).searchParams);
        const context = JSON.parse(params.navigation_context);
        expect(context.project_id).toBe(crossProject ? null : actualProject);
        expect(context.workspace_id).toBe(actor.workspaceId);
        if (crossProject) expect(params.user_id).toBe(user);
        const history: History = await response.json();
        expect(history.result.response.map(t => ({ trace_id: t.trace_id, input: t.input, output: t.output,
          project_id: t.system_metrics.project_id, total: t.system_metrics.total_tokens })))
          .toEqual(expected.map(t => ({ trace_id: t.traceId, input: t.input, output: t.output,
            project_id: actualProject, total: t.total })));
        expect(history.result.session_metadata).toMatchObject({ session_id: sessionId,
          total_traces: expected.length, total_tokens: expected.reduce((sum, t) => sum + t.total, 0) });
        await expect(drawer.getByRole('tab', { name: 'Session History', exact: true })).toBeVisible();
        await expect(cards).toHaveCount(expected.length);
        await expect(drawer.getByRole('button', { name: 'Turn fields', exact: true })).toBeEnabled();
      };
      const assertTurns = async (fields: TurnField[]) => {
        await expect(cards).toHaveCount(members.length);
        for (const [index, turn] of members.entries()) {
          const card = cards.nth(index);
          await card.scrollIntoViewIfNeeded();
          await expect(card.getByText('Trace ID:', { exact: true }).locator('..')
            .getByText(turn.traceId, { exact: true })).toBeVisible();
          // ShowTrace's first Grid item is Conversation; subtitle2 is its field-label variant.
          const values = card.locator('.MuiGrid-item').first();
          const labels = fields.filter((f): f is Extract<TurnField, { kind: 'value' }> => f.kind === 'value');
          await expect(values.locator('.MuiTypography-subtitle2')).toHaveText(labels.map(f => f.label));
          for (const descriptor of labels) {
            const bubble = values.getByText(descriptor.label, { exact: true }).locator('..');
            await expect(bubble.getByText(descriptor.label === 'Answer' ? turn.answer : turn.question,
              { exact: true })).toBeVisible();
          }
          await expect(values.getByText('Unavailable in this turn', { exact: true })).toHaveCount(0);
          await expect(values.getByText(turn.ignored, { exact: false })).toHaveCount(0);
          if (fields === reduced) {
            await expect(values.getByText('Question', { exact: true })).toHaveCount(0);
            await expect(values.getByText(turn.question, { exact: true })).toHaveCount(0);
          }
          await card.getByRole('tab', { name: 'Metadata', exact: true }).click();
          const metricRow = card.getByText('Total Token Count:', { exact: true }).locator('..');
          await expect(metricRow).toHaveText(`Total Token Count:${turn.total}`);
          // MetadataContent's parent has exactly one row: no default metrics linger.
          await expect(metricRow.locator('..').locator(':scope > .MuiStack-root')).toHaveCount(1);
          await expect(metricRow).toBeVisible();
        }
      };
      const apply = async (fields: TurnField[], creating: boolean) => {
        const path = creating ? SAVED_VIEWS : `${SAVED_VIEWS}${savedId}/`;
        const pending = page.waitForResponse(r => new URL(r.url()).pathname === path
          && r.request().method() === (creating ? 'POST' : 'PUT'), { timeout: UI_READY });
        await dialog.getByRole('button', { name: 'Apply & save', exact: true }).click();
        const response = await pending;
        expect(response.status()).toBe(200);
        const payload = response.request().postDataJSON();
        expect(payload.config.display.sessionTurnFields).toEqual(fields);
        if (creating) expect(payload).toMatchObject({ project_id: projectId,
          name: 'Default sessions view', tab_type: 'sessions', visibility: 'personal' });
        else expect(new URL(response.url()).searchParams.get('project_id')).toBe(projectId);
        const saved: { result: SavedView & { project: string } } = await response.json();
        expect(saved.result.project).toBe(projectId);
        expect(saved.result.config.display.sessionTurnFields).toEqual(fields);
        expect(saved.result.created_by.email).toBe(actor.email);
        if (!creating) expect(saved.result.id).toBe(savedId);
        savedId = saved.result.id;
        creatorId = saved.result.created_by.id;
        await expect(dialog).toBeHidden();
        await assertSaved(fields);
      };

      await test.step('Open the exact seeded two-turn session', async () => {
        const list = await actor.api.get<{ result: { table: { session_id: string; total_traces_count: number }[] } }>(
          SESSIONS, { project_id: projectId, page_size: 25, page_number: 0 });
        expect(list.result.table.map(row => ({ id: row.session_id, count: row.total_traces_count })))
          .toEqual([{ id: sid, count: 2 }]);
        expect((await readViews(projectId)).result.custom_views).toEqual([]);
        await page.goto(`/dashboard/observe/${projectId}/sessions`, { waitUntil: 'domcontentloaded' });
        await expect.poll(gridIds, { timeout: UI_READY }).toEqual([sid]);
        await openSession(sid, members, projectId);
      });
      await test.step('Choose two JSON paths and one metric in Turn fields', async () => {
        await drawer.getByRole('button', { name: 'Turn fields', exact: true }).click();
        await expect(dialog).toBeVisible();
        await expect(dialog.getByText('Changes are saved to your sessions view for this project.', { exact: true })).toBeVisible();
        await expect(dialog.getByRole('group', { name: /^Field \d+$/ })).toHaveCount(9);
        // Keep the two values and first metric, removing the other defaults from the end.
        for (let index = 9; index >= 4; index--) {
          await dialog.getByRole('button', { name: `Remove field ${index}`, exact: true }).click();
        }
        await field(1).getByRole('textbox', { name: 'Label', exact: true }).fill('Question');
        await field(1).getByRole('textbox', { name: 'Path', exact: true }).fill('input.messages[-1].content');
        await field(2).getByRole('textbox', { name: 'Label', exact: true }).fill('Answer');
        await field(2).getByRole('textbox', { name: 'Path', exact: true }).fill('output.answer.text');
        // Each field row has two MUI selects, "Field type" then "Metric" (SessionTurnFieldsDialog.jsx:104-149);
        // MUI names a select by its label AND its shown value, so "Metric" also matches the Field type select
        // while it shows Metric. Open the second select of the row and pick from its own listbox.
        await expect(field(3).getByRole('combobox')).toHaveCount(2);
        await field(3).getByRole('combobox').nth(1).click();
        const metricList = page.getByRole('listbox');
        await expect(metricList.getByRole('option')).toHaveText(['Latency', 'Total tokens', 'Input tokens', 'Output tokens', 'Cost', 'User ID', 'Start time']);
        await metricList.getByRole('option', { name: 'Total tokens', exact: true }).click();
        await expect(field(3).getByRole('combobox').nth(1)).toHaveText('Total tokens');
      });
      await test.step('Reorder and apply the selection', async () => {
        await dialog.getByRole('button', { name: 'Move field 2 up', exact: true }).click();
        await expect(field(1).getByRole('textbox', { name: 'Label', exact: true })).toHaveValue('Answer');
        await expect(field(2).getByRole('textbox', { name: 'Label', exact: true })).toHaveValue('Question');
        await expect(dialog.getByRole('group', { name: /^Field \d+$/ })).toHaveCount(3);
        await apply(selected, true);
      });
      await test.step('Every turn renders exactly the ordered values and selected metric', async () => {
        await assertTurns(selected);
        await shot(page, testInfo, 'ordered-session-turn-fields');
      });
      await test.step('Reload and hydrate the same saved project choice', async () => {
        const pending = page.waitForResponse(r => new URL(r.url()).pathname === SAVED_VIEWS
          && new URL(r.url()).searchParams.get('project_id') === projectId
          && r.request().method() === 'GET', { timeout: UI_READY });
        await page.reload({ waitUntil: 'domcontentloaded' });
        const response = await pending;
        expect(response.status()).toBe(200);
        const views: ViewList = await response.json();
        expect(views.result.custom_views.map(v => ({ id: v.id, fields: v.config.display.sessionTurnFields })))
          .toEqual([{ id: savedId, fields: selected }]);
        await expect.poll(gridIds, { timeout: UI_READY }).toEqual([sid]);
        await openSession(sid, members, projectId);
        await assertTurns(selected);
        await assertSaved(selected);
      });
      await test.step('Remove Question from every turn and the same saved row', async () => {
        await drawer.getByRole('button', { name: 'Turn fields', exact: true }).click();
        await expect(field(2).getByRole('textbox', { name: 'Path', exact: true }))
          .toHaveValue('input.messages[-1].content');
        await dialog.getByRole('button', { name: 'Remove field 2', exact: true }).click();
        await expect(dialog.getByRole('group', { name: /^Field \d+$/ })).toHaveCount(2);
        await apply(reduced, false);
        await assertTurns(reduced);
      });
      await test.step('Reopening from the project grid inherits the reduced choice', async () => {
        await page.goto(`/dashboard/observe/${projectId}/sessions`, { waitUntil: 'domcontentloaded' });
        await expect.poll(gridIds, { timeout: UI_READY }).toEqual([sid]);
        await openSession(sid, members, projectId);
        await assertTurns(reduced);
      });
      await test.step('Cross-project user grid loads preferences from the real turn project', async () => {
        await page.goto(`/dashboard/users/${encodeURIComponent(user)}`, { waitUntil: 'domcontentloaded' });
        await expect.poll(gridIds, { timeout: UI_READY }).toEqual([sid, siblingSid].sort());
        const pending = page.waitForResponse(r => new URL(r.url()).pathname === SAVED_VIEWS
          && new URL(r.url()).searchParams.get('project_id') === projectId
          && r.request().method() === 'GET', { timeout: UI_READY });
        await openSession(sid, members, projectId, true);
        const response = await pending;
        expect(response.status()).toBe(200);
        const views: ViewList = await response.json();
        expect(views.result.custom_views.map(v => ({ id: v.id, fields: v.config.display.sessionTurnFields })))
          .toEqual([{ id: savedId, fields: reduced }]);
        await assertTurns(reduced);
        await shot(page, testInfo, 'cross-project-session-turn-fields');
      });
      await test.step('The sibling project does not inherit another project configuration', async () => {
        await page.goto(`/dashboard/users/${encodeURIComponent(user)}`, { waitUntil: 'domcontentloaded' });
        await expect.poll(gridIds, { timeout: UI_READY }).toEqual([sid, siblingSid].sort());
        await openSession(siblingSid, [turns[2]], siblingId, true);
        const values: Locator = cards.locator('.MuiGrid-item').first();
        await expect(values.locator('.MuiTypography-subtitle2')).toHaveText(['Human', 'AI']);
        await drawer.getByRole('button', { name: 'Turn fields', exact: true }).click();
        await expect(dialog.getByRole('group', { name: /^Field \d+$/ })).toHaveCount(9);
        await expect(field(1).getByRole('textbox', { name: 'Label', exact: true })).toHaveValue('Human');
        await expect(field(1).getByRole('textbox', { name: 'Path', exact: true })).toHaveValue('input');
        await expect(field(2).getByRole('textbox', { name: 'Label', exact: true })).toHaveValue('AI');
        await expect(field(2).getByRole('textbox', { name: 'Path', exact: true })).toHaveValue('output');
        await dialog.getByRole('button', { name: 'Cancel', exact: true }).click();
        await expect(dialog).toBeHidden();
        await assertSaved(reduced);
      });
    } finally {
      await checkBrowser();
    }
  },
);
