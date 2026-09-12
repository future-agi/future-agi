import test from 'node:test';
import assert from 'node:assert/strict';
import {mkdtemp, readFile, rm} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {randomUUID, createHash} from 'node:crypto';
import {storeEvidence, createEvidenceReader, downloadEvidence} from './evidence-store.mjs';

export function makeClaim() {
  return {organization_id: randomUUID(), workspace_id: null, project_id: randomUUID(), trace_id: randomUUID(),
    job_id: randomUUID(), attempt_id: randomUUID(), generation: 1, feature_enabled: true,
    contract_version: 'omega-investigation/v1', read_cutoff: '2026-09-12T12:00:00Z',
    limits: {deadline_seconds: 60, max_model_calls: 12, max_children: 2,
      max_input_tokens_total: 100000, max_output_tokens_total: 8000, max_evidence_bytes: 65536, max_tool_result_bytes: 4096}};
}
test('stream stores exact bytes, indexes every span and issues host-owned citations', async () => {
  const dir = await mkdtemp(join(tmpdir(), 'omega-evidence-test-'));
  try {
    const claim = makeClaim();
    const row = {id: '1234567890abcdef', project_id: claim.project_id, trace_id: claim.trace_id, input: '你好', output: 'complete content'};
    const bytes = Buffer.from(JSON.stringify(row) + '\n');
    const path = join(dir, 'trace.jsonl');
    const store = await storeEvidence([bytes.subarray(0, 25), bytes.subarray(25)], path, claim);
    assert.deepEqual(await readFile(path), bytes);
    assert.equal(store.digest, 'sha256:' + createHash('sha256').update(bytes).digest('hex'));
    const reader = createEvidenceReader(store, {maxResultBytes: 4096, maxTotalBytes: 4096});
    assert.equal(reader.inventory().spans.length, 1);
    const read = await reader.read(row.id, 0, 4096);
    assert.equal(read.text, bytes.subarray(0, -1).toString('utf8'));
    assert.equal(read.more, false);
    assert.equal(reader.receipts()[0].evidence_id, read.evidence_id);
    await assert.rejects(reader.read('../another-project', 0, 10));
    await assert.rejects(reader.read(row.id, 0, 4097));
  } finally { await rm(dir, {recursive: true, force: true}); }
});
test('scope errors, byte/row overflow and incomplete responses cannot become complete evidence', async () => {
  const dir = await mkdtemp(join(tmpdir(), 'omega-evidence-test-'));
  try {
    const claim = makeClaim();
    const row = {id: '1234567890abcdef', project_id: claim.project_id, trace_id: claim.trace_id};
    for (const [name, bytes, options] of [
      ['scope', JSON.stringify({...row, project_id: randomUUID()}) + '\n', {}],
      ['bytes', JSON.stringify(row) + '\n', {maxBytes: 4}],
      ['rows', JSON.stringify(row) + '\n', {maxRows: 0}],
      ['partial', JSON.stringify(row) + '\nCode: 241 memory exceeded', {}],
      ['duplicate', (JSON.stringify(row) + '\n').repeat(2), {}],
    ]) await assert.rejects(storeEvidence([Buffer.from(bytes)], join(dir, name), claim, options));
  } finally { await rm(dir, {recursive: true, force: true}); }
});
test('query binds the claimed tenant and cutoff and never silently truncates payloads', async () => {
  const dir = await mkdtemp(join(tmpdir(), 'omega-evidence-test-'));
  try {
    const claim = makeClaim();
    const row = {id: '1234567890abcdef', project_id: claim.project_id, trace_id: claim.trace_id};
    await downloadEvidence(claim, join(dir, 'trace'), {baseUrl: 'http://clickhouse:8123', database: 'default', username: 'readonly', password: 'fixture',
      fetchImpl: async (url, request) => {
        assert.equal(url.searchParams.get('param_project'), claim.project_id);
        assert.equal(url.searchParams.get('param_org'), claim.organization_id);
        assert.match(request.body, /SELECT \*/);
        assert.match(request.body, /result_overflow_mode='throw'/);
        assert.doesNotMatch(request.body, /\breadonly\s*=/);
        assert.doesNotMatch(request.body, /substring|summary/i);
        return new Response(JSON.stringify(row) + '\n');
      }});
  } finally { await rm(dir, {recursive: true, force: true}); }
});

test('bounded tool pages reconstruct escaped and multibyte payloads without dropped content', async () => {
  const dir = await mkdtemp(join(tmpdir(), 'omega-evidence-test-'));
  try {
    const claim = makeClaim();
    const row = {id: '1234567890abcdef', project_id: claim.project_id, trace_id: claim.trace_id,
      input: ('你好\\"line\n').repeat(1000)};
    const raw = JSON.stringify(row);
    const store = await storeEvidence([Buffer.from(raw + '\n')], join(dir, 'trace'), claim);
    const reader = createEvidenceReader(store, {maxResultBytes: 1024, maxTotalBytes: 65536});
    let offset = 0, reconstructed = '';
    for (;;) {
      const part = await reader.read(row.id, offset, 1024);
      assert.ok(Buffer.byteLength(JSON.stringify(part)) <= 1024);
      assert.ok(part.next_offset > offset);
      reconstructed += part.text;
      if (!part.more) break;
      offset = part.next_offset;
    }
    assert.equal(reconstructed, raw);
  } finally { await rm(dir, {recursive: true, force: true}); }
});

test('absent, null and empty external payload URLs preserve complete coverage', async () => {
  const dir = await mkdtemp(join(tmpdir(), 'omega-evidence-test-'));
  try {
    const claim = makeClaim();
    const rows = [
      {id: 'span-absent', project_id: claim.project_id, trace_id: claim.trace_id},
      {id: 'span-null', project_id: claim.project_id, trace_id: claim.trace_id,
        input_gcs_url: null, output_gcs_url: null},
      {id: 'span-empty', project_id: claim.project_id, trace_id: claim.trace_id,
        input_gcs_url: '', output_gcs_url: '   '},
    ];
    const store = await storeEvidence(
      [Buffer.from(rows.map(row => JSON.stringify(row)).join('\n') + '\n')],
      join(dir, 'trace'), claim);
    const reader = createEvidenceReader(store, {maxResultBytes: 4096, maxTotalBytes: 4096});

    assert.equal(store.coverage.read_complete, true);
    assert.equal(reader.inventory().spans.every(row => !('unresolved_external_payloads' in row)), true);
  } finally { await rm(dir, {recursive: true, force: true}); }
});

test('external payload references are visible as unresolved without fetching URLs', async () => {
  const dir = await mkdtemp(join(tmpdir(), 'omega-evidence-test-'));
  const originalFetch = globalThis.fetch;
  const networkCalls = [];
  globalThis.fetch = async url => { networkCalls.push(String(url)); throw new Error('Unexpected network call'); };
  try {
    const claim = makeClaim();
    const row = {id: 'span-external', project_id: claim.project_id, trace_id: claim.trace_id,
      input: 'inline request', output: 'inline response',
      input_gcs_url: 'https://untrusted.invalid/input', output_gcs_url: 'gs://tenant-bucket/output'};
    const store = await storeEvidence(
      [Buffer.from(JSON.stringify(row) + '\n')], join(dir, 'trace'), claim);
    const reader = createEvidenceReader(store, {maxResultBytes: 4096, maxTotalBytes: 4096});

    assert.equal(store.coverage.read_complete, false);
    assert.deepEqual(reader.inventory().spans[0].unresolved_external_payloads,
      ['input_gcs_url', 'output_gcs_url']);
    assert.deepEqual(networkCalls, []);
  } finally {
    globalThis.fetch = originalFetch;
    await rm(dir, {recursive: true, force: true});
  }
});
