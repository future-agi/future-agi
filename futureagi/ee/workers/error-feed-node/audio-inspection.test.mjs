import assert from 'node:assert/strict';
import {mkdtemp, rm, writeFile} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import test from 'node:test';
import {createAudioInspectionTool, createStorageRecordingResolver} from './audio-inspection.mjs';
import {createGatewayProvider} from './gateway-provider.mjs';

const claim = {trace_id: 'trace-1', project_id: 'project-1', organization_id: 'org-1'};
const store = {index: new Map([['span-1', {span_id: 'span-1', parent_span_id: 'root'}]])};

test('question-driven audio call is scoped and uses the shared gateway', async () => {
  const resolved = [];
  const calls = [];
  const inspector = createAudioInspectionTool({claim, store,
    resolveRecording: async scope => {resolved.push(scope); return {url: 'https://media.example.test/a.wav', format: 'wav'};},
    gateway: {inspectAudio: async input => {calls.push(input); return {request_id: 'gateway-1', model_used: 'gemini',
      observation: {answer: 'No date was promised', observations: [{statement: 'The agent gave a range only',
        start_seconds: 4, end_seconds: 7, speaker: 'agent', confidence: 0.9}], metrics: [], uncertainty: null}};}},
    callsRemaining: () => 3});
  const result = await inspector.tool.execute({span_id: 'span-1', question: 'Did the agent promise a date?'});
  assert.equal(result.status, 'observed');
  assert.equal(result.gateway_request_id, 'gateway-1');
  assert.equal(result.source_span_id, 'span-1');
  assert.equal(resolved[0].span_id, 'span-1');
  assert.equal(calls[0].question, 'Did the agent promise a date?');
  assert.equal(calls[0].url, 'https://media.example.test/a.wav');
  assert.equal('url' in resolved[0], false);
  assert.equal((await inspector.tool.execute({span_id: 'span-1', question: 'Another question?'})).status, 'unavailable');
});

test('audio tool rejects out-of-trace spans', async () => {
  const inspector = createAudioInspectionTool({claim, store,
    resolveRecording: async () => null,
    gateway: {inspectAudio: () => assert.fail('must not call gateway')}, callsRemaining: () => 3});
  await assert.rejects(inspector.tool.execute({span_id: 'other', question: 'What happened here?'}), /outside claimed trace/);
  assert.equal((await inspector.tool.execute({span_id: 'span-1', question: 'What happened here?'})).status, 'unavailable');
});

test('audio request shares the investigation call and cost ledger', async () => {
  const gateway = createGatewayProvider({baseUrl: 'https://gateway.invalid/v1', model: 'gemini-test',
    apiKey: 'test-key', maxCalls: 1, maxInputBytesTotal: 10000,
    fetchImpl: async (_url, init) => {
      const request = JSON.parse(init.body);
      assert.equal(request.messages[0].content[0].text.includes('Was the caller interrupted?'), true);
      assert.equal(request.messages[0].content[1].type, 'file');
      assert.equal(request.messages[0].content[1].file.file_id, 'https://media.example.test/a.wav');
      return new Response(JSON.stringify({id: 'completion-1', usage: {prompt_tokens: 123, completion_tokens: 40},
        choices: [{message: {content: JSON.stringify({answer: 'No', observations: [], metrics: [], uncertainty: null})}}]}),
      {status: 200, headers: {'x-agentcc-cost': '0.000123', 'x-request-id': 'request-1'}});
    }});
  const result = await gateway.inspectAudio({url: 'https://media.example.test/a.wav', format: 'wav',
    question: 'Was the caller interrupted?'});
  assert.equal(result.request_id, 'request-1');
  assert.equal(gateway.accounting().model_calls, 1);
  assert.equal(gateway.accounting().cost_usd, 0.000123);
  await assert.rejects(gateway.inspectAudio({url: 'https://media.example.test/a.wav', format: 'wav',
    question: 'What happened?'}), /budget exhausted/);
});

test('recording resolver reads only allowlisted URLs from the scoped span file', async () => {
  const dir = await mkdtemp(join(tmpdir(), 'audio-resolver-test-'));
  try {
    const row = JSON.stringify({attrs_string: {'gen_ai.voice.recording.url': 'https://media.example.test/a.wav'}});
    const path = join(dir, 'trace.jsonl');
    await writeFile(path, row + '\n');
    const scopedStore = {path, index: new Map([['span-1', {offset: 0, bytes: Buffer.byteLength(row)}]])};
    let requests = 0;
    const resolver = createStorageRecordingResolver({allowedOrigins: ['https://media.example.test'],
      fetchImpl: async (_url, init) => {requests++; assert.equal(init.method, 'HEAD');
        return new Response(null, {headers: {'content-type': 'audio/wav', 'content-length': '1000'}});}});
    const result = await resolver({store: scopedStore, span_id: 'span-1'});
    assert.equal(result.url, 'https://media.example.test/a.wav');
    assert.equal(requests, 1);
    assert.equal(await resolver({store: scopedStore, span_id: 'other'}), null);
    const blocked = createStorageRecordingResolver({allowedOrigins: ['https://other.example.test'],
      fetchImpl: () => assert.fail('untrusted origin must not be fetched')});
    assert.equal(await blocked({store: scopedStore, span_id: 'span-1'}), null);
  } finally {await rm(dir, {recursive: true});}
});
