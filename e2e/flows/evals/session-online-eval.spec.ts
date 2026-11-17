import { createHash, randomBytes, randomUUID } from 'node:crypto';
import { request } from '@playwright/test';
import { test, expect } from '../../lib/mock-model-fixtures';
import { E2E } from '../../lib/env';
import { sendTrace, type OtlpAttributeValue } from '../../lib/otlp';
import { POLL } from '../../lib/state-probe';
import { MockUpstream } from '../../lib/upstream';
import { flowAnnotation } from '../../lib/flow-meta';

// futureagi/tracer/serializers/eval_task.py:403-579; views/eval_task.py:1949-1994.
const TASK = '/tracer/eval-task/';
// frontend/src/sections/tasks/hooks/useTaskUsage.js:47-59,155-168.
const USAGE = '/tracer/eval-task/get_usage/';
const UI_READY = 60_000;
// futureagi/tracer/utils/eval.py:3089-3136: hash the exact complete JSON, not the bounded transcript.
const REVISION = '_session_conversation_revision';
const BEGIN = 'BEGIN_CONVERSATION\n', END = '\nEND_CONVERSATION';
type Payload = { [key: string]: OtlpAttributeValue };
type Turn = {
  traceId: string; spanIds: [string, string]; session: string; name: string;
  at: number; input: Payload; output: Payload;
};
// futureagi/tracer/models/observation_span.py:316-386,435,518-520.
type ResultRow = {
  id: string; target_type: string; session_id: string; trace_id: string | null;
  observation_span_id: string | null; config_id: string; eval_task_id: string;
  status: string; error: boolean; error_message: string | null; output_bool: boolean;
  eval_explanation: string; output_metadata: Record<string, unknown>; config_hash: string; updated_at: Date;
};
// futureagi/tracer/models/eval_task.py:54-111.
type TaskRow = {
  id: string; project_id: string; run_type: string; row_type: string; status: string;
  start_time: Date; continuous_cursor: Date | null; last_run: Date | null;
};
// futureagi/tracer/serializers/eval_task.py:177-229.
type UsageRun = {
  id: string; session_id: string; trace_id: null; span_id: null; eval_id: string;
  status: string; result: string; reason: string;
  detail: { target_type: string; session_id: string; trace_id: null; span_id: null; output_bool: boolean };
};
const byId = <T extends { id: string }>(rows: T[]) => [...rows].sort((a, b) => a.id.localeCompare(b.id));
const conversation = (turns: Turn[]) => turns.map(t => ({ trace_id: t.traceId, input: t.input, output: t.output }));

// e2e/lib/mock-model-fixtures.ts:8-47 verifies managed background routing BEFORE
// provisioning a fresh org and registering a mock-only model. No real-provider fallback.
test.use({ evalBackground: true, viewport: { width: 1600, height: 1100 } });
test('EVAL-E2E-084: continuously evaluate complete session snapshots once and update the same run after a new turn', {
  tag: ['@flow'],
  annotation: flowAnnotation({
    id: 'EVAL-E2E-084', area: 'evals',
    userGoal: 'A developer continuously evaluates complete conversations once per unchanged session snapshot and sees the same session result update when a new turn arrives',
    steps: [
      'seed two multi-turn sessions, including out-of-order ingestion and a long nested middle turn, then enable continuous session evaluation',
      'inspect both successful session runs in task Usage',
      'observe a completed reconciliation with no source changes and require no duplicate run or judge call',
      'append a turn to A, inspect its updated run and unchanged B, and observe another no-op reconciliation',
      'pause the continuous task and remove the scenario mock rules in cleanup',
    ],
    backendChecks: [
      'ClickHouse FINAL contains the exact project-scoped session membership and complete root/child payloads before evaluation and after append',
      'the managed mock receives exactly one complete timestamp-ordered conversation per initial session and one complete updated A conversation, including the entire long middle input',
      'PG stores exactly one successful session-bound result per task/config/session with null trace/span bindings and the SHA256 revision of the exact judge conversation',
      'a completed reconciliation of each unchanged snapshot adds no judge call and leaves both result snapshots unchanged',
      'appending A updates the same result ID, revision and explanation while B and the two-result census remain unchanged',
      'the browser Usage response and scoped run details expose the exact session identities and explanations before and after append',
    ],
  }),
}, async ({ page, actor, probe, mockModel, managedMock }, testInfo) => {
  test.setTimeout(900_000); // 4 SPAN_VISIBLE + 2 EVAL_RESULT + 2 ASYNC_JOB + 8 UI_READY + 60s headroom.
  const req = await request.newContext();
  const mock = new MockUpstream();
  const suffix = `${testInfo.workerIndex}-${Date.now().toString(36)}`;
  const projectName = `e2e-eval84-${suffix}`;
  const sessionA = `e2e-eval84-session-A-${suffix}`, sessionB = `e2e-eval84-session-B-${suffix}`;
  const scenario = `e2e-eval84-${suffix}`, judgeMarker = `e2e-eval84-judge-${suffix}`;
  const appendedMarker = `e2e-eval84-appended-${suffix}`;
  const initialReason = `e2e-eval84-initial-${suffix}`, appendedReason = `e2e-eval84-updated-${suffix}`;
  const seedTime = Date.now();
  // Five-minute cursor overlap: futureagi/tracer/services/eval_tasks/cursor_policy.py:5-10 and reconciler.py:184-203.
  // A ten-minute-old start floor lets the unchanged-snapshot barrier advance promptly.
  const startTime = new Date(seedTime - 600_000).toISOString();
  // Full mapping vs 60k bounded transcript: tracer/utils/eval.py:2904-2906,3089-3155.
  // Below the custom evaluator's 200k clamp: custom_prompt_evaluator/evaluator.py:275-294.
  const longMiddle = `middle-start-${suffix}\n${'synthetic middle text '.repeat(1700)}\n` +
    `middle-center-${suffix}\n${'synthetic middle text '.repeat(1700)}\nmiddle-end-${suffix}`;
  const turn = (session: string, index: number, text: string, at: number): Turn => ({
    traceId: randomUUID(), spanIds: [randomBytes(8).toString('hex'), randomBytes(8).toString('hex')],
    session, name: `${session}-turn-${index}`, at,
    input: { messages: [{ role: 'system', content: '  preserve\n system whitespace  ' },
      { role: 'user', content: text }], tool: { arguments: { values: ['first', ' second\n'], enabled: true } } },
    output: { messages: [{ role: 'assistant', content: ` answer ${index}\n` },
      { role: 'tool', content: { nested: [index, { value: '  tool result  ' }] } }], complete: true },
  });
  const a = [turn(sessionA, 1, 'first A', seedTime - 90_000),
    turn(sessionA, 2, longMiddle, seedTime - 80_000), turn(sessionA, 3, 'third A', seedTime - 70_000)];
  const b = [turn(sessionB, 1, 'first B', seedTime - 60_000), turn(sessionB, 2, 'second B', seedTime - 50_000)];
  const decoy = (t: Turn) => ({ input: { child: `${t.name}-not-root-input` }, output: { child: `${t.name}-not-root-output` } });
  const attach = (name: string, body: unknown) => testInfo.attach(name, { contentType: 'application/json', body: JSON.stringify(body) });
  const seed = async (t: Turn) => {
    await attach(`seed-${t.traceId}.json`, { projectName, ...t, input: undefined, output: undefined });
    const child = decoy(t);
    // e2e/lib/otlp.ts:27-49; canonical content keys: converter.go:883-912.
    // session.id on both spans: e2e/flows/observe/native-source.ts:205-250,302-333.
    await sendTrace(req, {
      collectorUrl: E2E.collectorUrl, apiKey: actor.apiKey, secretKey: actor.secretKey, projectName,
      traceId: t.traceId, rootSpanId: t.spanIds[0], childSpanId: t.spanIds[1], rootName: t.name, childName: `${t.name}-child`,
      startTimeUnixNano: BigInt(t.at) * 1_000_000n, endTimeUnixNano: BigInt(t.at) * 1_000_000n + 50_000_000n,
      resourceAttributes: { project_type: 'observe' },
      rootAttributes: { 'session.id': t.session, 'input.value': JSON.stringify(t.input), 'output.value': JSON.stringify(t.output) },
      childAttributes: { 'session.id': t.session, 'input.value': JSON.stringify(child.input), 'output.value': JSON.stringify(child.output) },
    });
  };
  let taskId = '', projectId = '', configId = '';
  let primaryError: unknown;
  // Unfiltered task census deliberately includes accidental span/trace targets.
  const results = () => probe.pg<ResultRow>(
    `SELECT id::text AS id,target_type,trace_session_id::text AS session_id,trace_id::text AS trace_id,
      observation_span_id,custom_eval_config_id::text AS config_id,eval_task_id,status,error,error_message,
      output_bool,eval_explanation,output_metadata,config_hash,updated_at
     FROM tracer_eval_logger WHERE eval_task_id=$1 AND deleted=false ORDER BY trace_session_id,id`, [taskId]);
  const task = async () => {
    const rows = await probe.pg<TaskRow>(
      'SELECT id,project_id,run_type,row_type,status,start_time,continuous_cursor,last_run FROM tracer_eval_task WHERE id=$1 AND project_id=$2 AND deleted=false',
      [taskId, projectId]);
    expect(rows).toHaveLength(1);
    return rows[0];
  };
  try {
    await managedMock();
    await mockModel.assertReady();
    // Existing programmable unscoped rules/census: e2e/lib/upstream.ts:80-118; e2e/README.md:718-739.
    await mock.registerReplyRules(scenario, [
      { user_contains: appendedMarker, body: JSON.stringify({ result: 'Pass', explanation: appendedReason }) },
      { user_contains: judgeMarker, body: JSON.stringify({ result: 'Pass', explanation: initialReason }) },
    ]);
    for (const t of [a[2], a[0], a[1], ...b]) await seed(t);
    const projects = await probe.pg<{ id: string }>(
      'SELECT id FROM tracer_project WHERE name=$1 AND organization_id=$2 AND workspace_id=$3',
      [projectName, actor.organizationId, actor.workspaceId]);
    expect(projects).toHaveLength(1);
    projectId = projects[0].id;
    let sessions: { trace_session_id: string; external_session_id: string }[] = [];
    const sourceReady = async (turns: Turn[]) => {
      // Source tables: e2e/flows/observe/native-source.ts:302-333;
      // curated trace session_id: futureagi/tracer/services/clickhouse/schema.py:240.
      // JSON source decoding: futureagi/tracer/services/clickhouse/v2/eval_loader.py:418-437.
      await expect.poll(async () => {
        sessions = await probe.ch('SELECT trace_session_id,external_session_id FROM trace_sessions FINAL WHERE project_id={p:UUID} AND is_deleted=0', { p: projectId });
        const spans = await probe.ch<{ id: string; trace_id: string; parent_span_id: string; name: string; trace_session_id: string; input: string; output: string }>(
          'SELECT id,trace_id,parent_span_id,name,trace_session_id,input,output FROM spans FINAL WHERE project_id={p:UUID}', { p: projectId });
        return {
          sessions: sessions.map(s => s.external_session_id).sort(),
          spans: byId(spans.map(s => ({ ...s, input: JSON.parse(s.input), output: JSON.parse(s.output),
            trace_session_id: sessions.find(x => x.trace_session_id === s.trace_session_id)?.external_session_id }))),
        };
      }, POLL.SPAN_VISIBLE).toEqual({ sessions: [sessionA, sessionB].sort(), spans: byId(turns.flatMap(t => [
        { id: t.spanIds[0], trace_id: t.traceId, parent_span_id: '', name: t.name, trace_session_id: t.session, input: t.input, output: t.output },
        { id: t.spanIds[1], trace_id: t.traceId, parent_span_id: t.spanIds[0], name: `${t.name}-child`, trace_session_id: t.session, ...decoy(t) },
      ])) });
      expect(new Set(sessions.map(s => s.trace_session_id)).size).toBe(2);
      await expect.poll(async () => {
        const traces = await probe.ch<{ id: string; session_id: string; name: string; input: string; output: string }>(
          'SELECT id,session_id,name,input,output FROM traces FINAL WHERE project_id={p:UUID}', { p: projectId });
        return byId(traces.map(t => ({ ...t, input: JSON.parse(t.input), output: JSON.parse(t.output),
          session_id: sessions.find(s => s.trace_session_id === t.session_id)?.external_session_id })));
      }, POLL.SPAN_VISIBLE).toEqual(byId(turns.map(t => ({ id: t.traceId, session_id: t.session, name: t.name, input: t.input, output: t.output }))));
    };
    await sourceReady([...a, ...b]); // No task exists until both whole initial conversations are readable.
    const idA = sessions.find(s => s.external_session_id === sessionA)!.trace_session_id;
    const idB = sessions.find(s => s.external_session_id === sessionB)!.trace_session_id;
    await attach('sessions.json', { projectId, projectName, sessions, longMiddleLength: longMiddle.length });

    // Template body: futureagi/model_hub/serializers/contracts.py:2548-2621.
    // Put the full placeholder inside instructions: evaluator.py:296-351 appends only a 500-char preview.
    const template = await actor.api.post<{ result: { id: string } }>('/model-hub/eval-templates/create-v2/', {
      name: `e2e-eval84-template-${suffix}`, eval_type: 'llm', model: mockModel.model,
      instructions: `${judgeMarker}\n${BEGIN}{{conversation}}${END}`,
      output_type: 'pass_fail', pass_threshold: 0.5, check_internet: false, error_localizer_enabled: false, template_format: 'mustache',
    });
    // futureagi/tracer/serializers/custom_eval_config.py:36-137.
    const config = await actor.api.post<{ result: { id: string } }>('/tracer/custom-eval-config/', {
      project: projectId, eval_template: template.result.id, name: `e2e-eval84-config-${suffix}`, model: mockModel.model,
      mapping: { conversation: 'conversation_complete' }, config: { mapping: { conversation: 'conversation_complete' } }, error_localizer: false,
    });
    configId = config.result.id;
    const created = await actor.api.post<{ result: { id: string } }>(TASK, {
      name: `e2e-eval84-task-${suffix}`, project: projectId, evals: [configId], filters: { project_id: projectId },
      run_type: 'continuous', row_type: 'sessions', sampling_rate: 100, start_time: startTime,
    });
    taskId = created.result.id;
    await attach('eval-identities.json', { taskId, projectId, configId, templateId: template.result.id, idA, idB, scenario });

    const judgeConversations = async () => {
      // custom_prompt_evaluator/evaluator.py:345-351,403-447: text user messages for non-media fixtures.
      // Only request bodies are read. Never attach headers from MockUpstream.requests.
      const calls = await mock.requests(scenario);
      return calls.map(call => {
        const body = call.body as { messages: { role: string; content: string }[] };
        const text = body.messages.filter(m => m.role === 'user').map(m => m.content).join('');
        expect(text).toContain(judgeMarker);
        expect(text.split(BEGIN)).toHaveLength(2);
        const remainder = text.split(BEGIN)[1];
        expect(remainder.split(END)).toHaveLength(2);
        const json = remainder.split(END)[0];
        return { turns: JSON.parse(json) as ReturnType<typeof conversation>, revision: createHash('sha256').update(json).digest('hex') };
      });
    };
    const expectedBinding = (sessionId: string, reason: string) => ({
      target_type: 'session', session_id: sessionId, trace_id: null, observation_span_id: null,
      config_id: configId, eval_task_id: taskId, status: 'completed', error: false,
      output_bool: true, eval_explanation: reason,
    });
    const completed = async (reasonA: string, expectedConversations: ReturnType<typeof conversation>[]) => {
      let rows: ResultRow[] = [];
      await expect.poll(async () => {
        rows = await results();
        return rows.map(r => ({ session_id: r.session_id, status: r.status, reason: r.eval_explanation }))
          .sort((x, y) => x.session_id.localeCompare(y.session_id));
      }, POLL.EVAL_RESULT).toEqual([
        { session_id: idA, status: 'completed', reason: reasonA },
        { session_id: idB, status: 'completed', reason: initialReason },
      ].sort((x, y) => x.session_id.localeCompare(y.session_id)));
      const calls = await judgeConversations();
      // Ordering is independent of ingestion: expected arrays were constructed in timestamp order.
      const sortConversations = (items: ReturnType<typeof conversation>[]) => [...items].sort((x, y) =>
        JSON.stringify(x.map(t => t.trace_id)).localeCompare(JSON.stringify(y.map(t => t.trace_id))));
      expect(sortConversations(calls.map(c => c.turns))).toEqual(sortConversations(expectedConversations));
      for (const [sessionId, turns, reason] of [[idA, a, reasonA], [idB, b, initialReason]] as const) {
        const row = rows.find(r => r.session_id === sessionId)!;
        expect(row).toMatchObject(expectedBinding(sessionId, reason));
        expect(row.error_message).toBeFalsy();
        expect(row.config_hash).toMatch(/^[a-f0-9]{64}$/);
        const matching = calls.filter(c => c.turns[0].trace_id === turns[0].traceId);
        expect(row.output_metadata[REVISION]).toBe(matching[matching.length - 1].revision);
      }
      const state = await task();
      expect(state).toMatchObject({ id: taskId, project_id: projectId, run_type: 'continuous', row_type: 'sessions', status: 'running' });
      expect(state.start_time.toISOString()).toBe(startTime);
      await attach(`completed-${calls.length}.json`, { rows, conversations: calls.map(c => ({ revision: c.revision, traceIds: c.turns.map(t => t.trace_id) })) });
      return rows;
    };
    const unchangedReconciliation = async (snapshot: ResultRow[], expectedCalls: number) => {
      // Cursor advances in the SAME transaction after materialization/requeue:
      // reconciler.py:89-99,130-159,184-203. last_run alone is NOT a no-op-pass barrier.
      const before = await task();
      expect(before.continuous_cursor).not.toBeNull();
      const cursor = before.continuous_cursor!.getTime();
      await expect.poll(async () => (await task()).continuous_cursor?.getTime(), POLL.ASYNC_JOB).toBeGreaterThan(cursor);
      expect(await results(), 'completed reconciliation must leave IDs, revisions, timestamps and all result fields unchanged').toEqual(snapshot);
      expect(await mock.requests(scenario)).toHaveLength(expectedCalls);
      expect((await task()).status).toBe('running');
      await attach(`no-op-${expectedCalls}.json`, { cursorBefore: before.continuous_cursor, cursorAfter: (await task()).continuous_cursor, expectedCalls });
    };
    const usage = async (snapshot: ResultRow[], reload: boolean) => {
      await test.step(reload ? 'reload Usage after append' : 'open task Usage', async () => {
        // TaskDetailPage.jsx:57; real logs (not summary) query: useTaskUsage.js:155-168.
        const responsePromise = page.waitForResponse(r => {
          const url = new URL(r.url());
          return url.pathname === USAGE && r.request().method() === 'GET'
            && url.searchParams.get('eval_task_id') === taskId
            && url.searchParams.get('include_summary') === 'false' && url.searchParams.get('page') === '1';
        }, { timeout: UI_READY });
        // TaskDetailPage.jsx:150 initializes the selected tab to Details on every mount.
        if (reload) await page.reload();
        else await page.goto(`/dashboard/tasks/${taskId}`);
        await page.getByRole('tab', { name: 'Usage', exact: true }).click();
        const response = await responsePromise;
        expect(response.status()).toBe(200);
        const body = await response.json() as { result: { logs: { results: UsageRun[]; count: number } } };
        expect(body.result.logs.count).toBe(2);
        // views/eval_task.py:2536-2538,2618-2711: completed pass -> Passed/success, session FKs are unconstrained.
        expect(byId(body.result.logs.results.map(r => ({ id: r.id, session_id: r.session_id, trace_id: r.trace_id, span_id: r.span_id,
          eval_id: r.eval_id, status: r.status, result: r.result, reason: r.reason,
          detail: { target_type: r.detail.target_type, session_id: r.detail.session_id, trace_id: r.detail.trace_id,
            span_id: r.detail.span_id, output_bool: r.detail.output_bool } }))))
          .toEqual(byId(snapshot.map(r => ({ id: r.id, session_id: r.session_id, trace_id: null, span_id: null,
            eval_id: configId, status: 'success', result: 'Passed', reason: r.eval_explanation,
            detail: { target_type: 'session', session_id: r.session_id, trace_id: null, span_id: null, output_bool: true } }))));
        // DataTable.jsx:740-744 keys rows by id. All two rows fit in the viewport.
        await expect.poll(() => page.locator('.MuiDataGrid-row[data-id]').evaluateAll(rows => rows.map(r => r.getAttribute('data-id')).sort()),
          { timeout: UI_READY }).toEqual(snapshot.map(r => r.id).sort());
      }, { timeout: UI_READY });
      for (const row of snapshot) {
        await test.step(`inspect session ${row.session_id}`, async () => {
          await page.locator(`.MuiDataGrid-row[data-id="${row.id}"]`).click();
          // frontend/src/sections/tasks/components/TaskUsageTab.jsx:659-678,720-750:
          // the named group is only the format toggle; its grandparent is DetailPanelContent,
          // which also contains the session fields/explanation (833-880,936-952), not the grid.
          const detail = page.getByRole('group', { name: 'Run detail view', exact: true }).locator('xpath=../..');
          await expect(detail.getByText('Session ID', { exact: true })).toBeVisible({ timeout: UI_READY });
          await expect(detail.getByText(row.session_id, { exact: true })).toBeVisible({ timeout: UI_READY });
          await expect(detail.getByText(row.eval_explanation, { exact: true })).toBeVisible({ timeout: UI_READY });
          await expect(detail.getByText('Trace ID', { exact: true })).toHaveCount(0, { timeout: UI_READY });
          await expect(detail.getByText('Span ID', { exact: true })).toHaveCount(0, { timeout: UI_READY });
          await page.getByRole('button', { name: 'Close run details', exact: true }).click();
          await expect(detail).toHaveCount(0, { timeout: UI_READY });
        }, { timeout: UI_READY });
      }
    };

    const initial = await completed(initialReason, [conversation(a), conversation(b)]);
    await usage(initial, false);
    await unchangedReconciliation(initial, 2);
    const initialAConversation = conversation(a);
    const append = turn(sessionA, 4, appendedMarker, Date.now());
    await seed(append);
    a.push(append);
    await sourceReady([...a, ...b]);
    const updated = await completed(appendedReason, [initialAConversation, conversation(b), conversation(a)]);
    const oldA = initial.find(r => r.session_id === idA)!, newA = updated.find(r => r.session_id === idA)!;
    expect(newA.id).toBe(oldA.id);
    expect(newA.config_hash).toBe(oldA.config_hash);
    expect(newA.output_metadata[REVISION]).not.toBe(oldA.output_metadata[REVISION]);
    expect(newA.updated_at.getTime()).toBeGreaterThan(oldA.updated_at.getTime());
    expect(updated.find(r => r.session_id === idB)).toEqual(initial.find(r => r.session_id === idB));
    expect(updated.map(r => r.id).sort()).toEqual(initial.map(r => r.id).sort());
    await usage(updated, true);
    await unchangedReconciliation(updated, 3);
  } catch (error) {
    primaryError = error;
    throw error;
  } finally {
    const cleanupErrors: unknown[] = [];
    if (taskId) {
      try {
        // futureagi/tracer/views/eval_task.py:2875-2904. Pause before removing the judge rules.
        const state = await task();
        if (state.status === 'running') {
          const paused = await actor.api.post<{ result: { message: string } }>(`${TASK}pause_eval_task/?eval_task_id=${taskId}`, {});
          expect(paused.result.message).toBe('Eval task paused successfully');
          await expect.poll(async () => (await task()).status, POLL.ASYNC_JOB).toBe('paused');
        } else {
          expect(['paused', 'completed', 'failed', 'cancelled']).toContain(state.status);
        }
      } catch (error) { cleanupErrors.push(error); }
    }
    try { await mock.clearReplies(scenario); } catch (error) { cleanupErrors.push(error); }
    try { await req.dispose(); } catch (error) { cleanupErrors.push(error); }
    if (cleanupErrors.length) {
      // Preserve the primary failure and report cleanup as well, without attaching raw API error/headers.
      await attach('cleanup-failed.json', { taskId, scenario, failures: cleanupErrors.length });
      throw new AggregateError(primaryError ? [primaryError, ...cleanupErrors] : cleanupErrors,
        'Session flow cleanup failed; inspect the primary assertion and pause status');
    }
  }
});
