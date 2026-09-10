import { test, expect } from '../lib/fixtures';
import { sendTrace } from '../lib/otlp';
import { POLL } from '../lib/state-probe';
import { E2E } from '../lib/env';

// Public contracts: model_hub/serializers/{develop_annotations,scores}.py;
// simulate/serializers/requests/agent_definition.py. No execution endpoints.
// Source DDL gap: tracer/services/clickhouse/schema.py omits score.value_history,
// score.tracer_project_id and agent_definition.target_speaks_first. This harness
// requires their actual CDC replication; it never patches schema or inserts SQL.
// The live mirrors have non-nullable deleted_at DateTime64: PG NULL becomes
// epoch, so presence alone is NOT a deletion timestamp. Compare a post-epoch
// timestamp flag below, not raw NULL fidelity (a separately recorded DDL gap).
interface ScoreApi { id: string; value: { value: number }; value_history: unknown[] }
interface ScoreFact {
  id: string; source_type: string; trace_id: string; observation_span_id: string;
  label_id: string; tracer_project_id: string; organization_id: string; workspace_id: string;
  annotator_id: string; queue_item_id: string; score_source: string; notes: string; deleted: number;
  has_delete_timestamp: number;
  value: { value: number }; value_history: { value: { value: number }; at: string }[];
}

test('native CDC replicates public annotation score creation, correction and soft deletion',
  async ({ request, actor, probe }, testInfo) => {
    // SPAN_VISIBLE + three CDC_VISIBLE barriers + 45 s API/provisioning headroom.
    test.setTimeout(600_000);
    const prefix = `e2e-native-score-${testInfo.workerIndex}-${Date.now().toString(36)}`;
    const trace = await sendTrace(request, {
      collectorUrl: E2E.collectorUrl, apiKey: actor.apiKey, secretKey: actor.secretKey,
      projectName: prefix, resourceAttributes: { project_type: 'observe' },
    });
    await testInfo.attach('seeded-trace', { body: JSON.stringify(trace), contentType: 'application/json' });
    await expect.poll(async () => (await probe.ch<{ id: string }>(
      'SELECT id FROM spans FINAL WHERE trace_id = {t:String} ORDER BY id',
      { t: trace.traceId })).map(row => row.id), POLL.SPAN_VISIBLE).toEqual([...trace.spanIds].sort());
    const projects = await probe.pg<{ id: string }>(
      'SELECT id FROM tracer_project WHERE name = $1 AND organization_id = $2 AND workspace_id = $3',
      [prefix, actor.organizationId, actor.workspaceId]);
    expect(projects).toHaveLength(1);
    const projectId = projects[0].id;
    const label = await actor.api.post<{ result: { id: string } }>('/model-hub/annotations-labels/', {
      // bulk_create only persists per-score notes when the label opts in
      // (model_hub/views/scores.py:542).
      name: `${prefix}-label`, type: 'numeric', project: projectId, allow_notes: true,
      settings: { min: 0, max: 100, step_size: 1, display_type: 'slider' },
    });
    const created = await actor.api.post<{ result: { scores: ScoreApi[]; errors: string[] } }>(
      '/model-hub/scores/bulk/', {
        source_type: 'observation_span', source_id: trace.spanIds[1],
        scores: [{ label_id: label.result.id, value: { value: 25 }, notes: `${prefix}-initial`, score_source: 'human' }],
      });
    expect(created.result.errors).toEqual([]);
    expect(created.result.scores).toHaveLength(1);
    const scoreId = created.result.scores[0].id;
    await testInfo.attach('seeded-score', { contentType: 'application/json', body: JSON.stringify({
      scoreId, labelId: label.result.id, projectId, organizationId: actor.organizationId,
      workspaceId: actor.workspaceId, spanId: trace.spanIds[1], traceId: trace.traceId,
    }) });
    const fields = `id, source_type, trace_id, observation_span_id, label_id, tracer_project_id,
      organization_id, workspace_id, annotator_id, queue_item_id, score_source, notes`;
    const readPG = () => probe.pg<ScoreFact>(
      `SELECT ${fields}, value, value_history, deleted::integer AS deleted,
        CASE WHEN deleted_at > to_timestamp(0) THEN 1 ELSE 0 END AS has_delete_timestamp
        FROM model_hub_score WHERE id = $1`,
      [scoreId]);
    const readCH = async () => (await probe.ch<Omit<ScoreFact, 'value' | 'value_history'> & {
      value: string; value_history: string;
    }>(`SELECT ${fields}, value, value_history, toUInt8(deleted) AS deleted,
        toUInt8(ifNull(deleted_at > toDateTime64(0, 6, 'UTC'), false)) AS has_delete_timestamp
        FROM model_hub_score FINAL WHERE id = {id:UUID} AND _peerdb_is_deleted = 0`, { id: scoreId }))
      .map(row => ({ ...row, value: JSON.parse(row.value), value_history: JSON.parse(row.value_history) }));
    const fresh = await readPG();
    expect(fresh).toHaveLength(1);
    expect(fresh[0]).toMatchObject({
      id: scoreId, source_type: 'observation_span', trace_id: trace.traceId,
      observation_span_id: trace.spanIds[1], label_id: label.result.id, tracer_project_id: projectId,
      organization_id: actor.organizationId, workspace_id: actor.workspaceId,
      score_source: 'human', notes: `${prefix}-initial`, value: { value: 25 }, value_history: [], deleted: 0,
      has_delete_timestamp: 0,
    });
    expect(fresh[0].queue_item_id).toMatch(/^[0-9a-f-]{36}$/);
    const users = await probe.pg<{ id: string }>('SELECT id FROM accounts_user WHERE email = $1', [actor.email]);
    expect(users.map(row => row.id)).toEqual([fresh[0].annotator_id]);
    await testInfo.attach('fresh-score-postgres', { contentType: 'application/json', body: JSON.stringify(fresh) });
    await expect.poll(readCH, POLL.CDC_VISIBLE).toEqual(fresh);
    const freshReplica = await readCH();
    await testInfo.attach('fresh-score-pg-ch', { contentType: 'application/json',
      body: JSON.stringify({ postgres: fresh, clickhouse: freshReplica }) });
    expect(freshReplica.map(row => row.value)).toEqual([{ value: 25 }]);

    const corrected = await actor.api.patch<ScoreApi>(`/model-hub/scores/${scoreId}/`, {
      value: { value: 75 }, notes: `${prefix}-corrected`,
    });
    expect(corrected.id).toBe(scoreId);
    expect(corrected.value).toEqual({ value: 75 });
    const updated = await readPG();
    expect(updated).toHaveLength(1);
    expect(updated[0]).toEqual({ ...fresh[0], value: { value: 75 }, notes: `${prefix}-corrected`,
      value_history: [{ value: { value: 25 }, at: expect.any(String) }] });
    expect(Number.isFinite(Date.parse(updated[0].value_history[0].at))).toBe(true);
    await testInfo.attach('updated-score-postgres', { contentType: 'application/json', body: JSON.stringify(updated) });
    await expect.poll(readCH, POLL.CDC_VISIBLE).toEqual(updated);
    const replicated = await readCH();
    await testInfo.attach('updated-score-pg-ch', { contentType: 'application/json',
      body: JSON.stringify({ postgres: updated, clickhouse: replicated }) });
    expect(replicated.map(row => row.value)).toEqual([{ value: 75 }]);

    // ScoreViewSet.destroy soft-deletes via UPDATE. Therefore its product
    // tombstone is deleted=1, NOT PeerDB's physical-delete flag. Keep that row
    // visible in readCH and independently prove it is absent from active reads.
    await actor.api.delete(`/model-hub/scores/${scoreId}/`);
    const removed = await readPG();
    expect(removed).toEqual([{ ...updated[0], deleted: 1, has_delete_timestamp: 1 }]);
    await testInfo.attach('deleted-score-postgres', { contentType: 'application/json', body: JSON.stringify(removed) });
    await expect.poll(readCH, POLL.CDC_VISIBLE).toEqual(removed);
    const tombstone = await readCH();
    await testInfo.attach('deleted-score-pg-ch', { contentType: 'application/json',
      body: JSON.stringify({ postgres: removed, clickhouse: tombstone }) });
    expect(tombstone.map(row => row.deleted)).toEqual([1]);
    expect(await probe.ch<{ id: string }>(`SELECT id FROM model_hub_score FINAL
      WHERE id = {id:UUID} AND deleted = 0 AND _peerdb_is_deleted = 0`, { id: scoreId })).toEqual([]);
    await expect(actor.api.get(`/model-hub/scores/${scoreId}/`)).rejects.toMatchObject({ status: 404 });
  });

interface AgentFact {
  id: string; agent_name: string; agent_type: string; description: string;
  target_speaks_first: boolean; organization_id: string; workspace_id: string; deleted: number;
  has_delete_timestamp: number;
}

test('native CDC replicates public simulation definition creation, edit and soft deletion without a run',
  async ({ request, actor, probe }, testInfo) => {
    // Three CDC_VISIBLE barriers + 45 s API/provisioning headroom.
    test.setTimeout(585_000);
    const prefix = `e2e-native-agent-${testInfo.workerIndex}-${Date.now().toString(36)}`;
    const created = await actor.api.post<{ agent: { id: string } }>('/simulate/agent-definitions/create/', {
      agent_name: prefix, agent_type: 'text', commit_message: 'H3 local definition only',
      description: `${prefix}-initial`, target_speaks_first: false, observability_enabled: false,
    });
    const agentId = created.agent.id;
    await testInfo.attach('seeded-agent', { contentType: 'application/json', body: JSON.stringify({
      agentId, organizationId: actor.organizationId, workspaceId: actor.workspaceId,
    }) });
    const fields = `id, agent_name, agent_type, description, target_speaks_first, organization_id, workspace_id`;
    const readPG = () => probe.pg<AgentFact>(
      `SELECT ${fields}, deleted::integer AS deleted,
       CASE WHEN deleted_at > to_timestamp(0) THEN 1 ELSE 0 END AS has_delete_timestamp
       FROM simulate_agent_definition WHERE id = $1`, [agentId]);
    const readCH = () => probe.ch<AgentFact>(
      `SELECT ${fields}, toUInt8(deleted) AS deleted,
       toUInt8(ifNull(deleted_at > toDateTime64(0, 6, 'UTC'), false)) AS has_delete_timestamp
       FROM simulate_agent_definition FINAL
       WHERE id = {id:UUID} AND _peerdb_is_deleted = 0`, { id: agentId });
    const fresh = await readPG();
    expect(fresh).toEqual([{
      id: agentId, agent_name: prefix, agent_type: 'text', description: `${prefix}-initial`,
      target_speaks_first: false, organization_id: actor.organizationId, workspace_id: actor.workspaceId, deleted: 0,
      has_delete_timestamp: 0,
    }]);
    await testInfo.attach('fresh-agent-postgres', { contentType: 'application/json', body: JSON.stringify(fresh) });
    await expect.poll(readCH, POLL.CDC_VISIBLE).toEqual(fresh);
    const freshReplica = await readCH();
    await testInfo.attach('fresh-agent-pg-ch', { contentType: 'application/json',
      body: JSON.stringify({ postgres: fresh, clickhouse: freshReplica }) });
    expect(freshReplica.map(row => row.target_speaks_first)).toEqual([false]);

    // This public edit endpoint is PUT-only. ApiClient has no PUT method;
    // use the existing Playwright request fixture with the actor's exact scope.
    const response = await request.put(`${E2E.apiUrl}/simulate/agent-definitions/${agentId}/edit/`, {
      headers: { Authorization: `Bearer ${actor.tokens.access}`,
        'X-Organization-Id': actor.organizationId, 'X-Workspace-Id': actor.workspaceId },
      data: { agent_name: `${prefix}-renamed`, description: `${prefix}-edited`, target_speaks_first: true },
    });
    expect(response.status(), await response.text()).toBe(200);
    const edited = await response.json();
    expect(edited.agent.id).toBe(agentId);
    const updated = await readPG();
    expect(updated).toEqual([{ ...fresh[0], agent_name: `${prefix}-renamed`,
      description: `${prefix}-edited`, target_speaks_first: true }]);
    await testInfo.attach('updated-agent-postgres', { contentType: 'application/json', body: JSON.stringify(updated) });
    await expect.poll(readCH, POLL.CDC_VISIBLE).toEqual(updated);
    const replicated = await readCH();
    await testInfo.attach('updated-agent-pg-ch', { contentType: 'application/json',
      body: JSON.stringify({ postgres: updated, clickhouse: replicated }) });
    expect(replicated.map(row => row.agent_name)).toEqual([`${prefix}-renamed`]);

    // DeleteAgentDefinitionView calls soft_delete_agent_definition_and_versions;
    // only this test's API-created definition/version is removed, never SQL rows.
    await actor.api.delete(`/simulate/agent-definitions/${agentId}/delete/`);
    const removed = await readPG();
    expect(removed).toEqual([{ ...updated[0], deleted: 1, has_delete_timestamp: 1 }]);
    await testInfo.attach('deleted-agent-postgres', { contentType: 'application/json', body: JSON.stringify(removed) });
    await expect.poll(readCH, POLL.CDC_VISIBLE).toEqual(removed);
    const tombstone = await readCH();
    await testInfo.attach('deleted-agent-pg-ch', { contentType: 'application/json',
      body: JSON.stringify({ postgres: removed, clickhouse: tombstone }) });
    expect(tombstone.map(row => row.deleted)).toEqual([1]);
    expect(await probe.ch<{ id: string }>(`SELECT id FROM simulate_agent_definition FINAL
      WHERE id = {id:UUID} AND deleted = 0 AND _peerdb_is_deleted = 0`, { id: agentId })).toEqual([]);
    await expect(actor.api.get(`/simulate/agent-definitions/${agentId}/`)).rejects.toMatchObject({ status: 404 });
  });
