import assert from 'node:assert/strict';
import {mkdtemp, rm, writeFile} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import test from 'node:test';
import {createAudioInspectionTool, createStorageRecordingResolver} from './audio-inspection.mjs';
import {createGatewayProvider} from './gateway-provider.mjs';

const claim = {trace_id: 'trace-1', project_id: 'project-1', organization_id: 'org-1'};
const store = {index: new Map([['span-1', {span_id: 'span-1', parent_span_id: 'root'}]])};

test('audio conversation replays follow-ups, mints citable receipts and reserves the verifier turn', async () => {
  const resolved = [];
  const calls = [];
  let phase = 'controller';
  const inspector = createAudioInspectionTool({claim, store, phase: () => phase,
    resolveRecording: async scope => {resolved.push(scope); return {url: 'https://media.example.test/a.wav', format: 'wav'};},
    gateway: {inspectAudio: async input => {calls.push(structuredClone(input)); return {request_id: `gateway-${calls.length}`,
      model_used: 'gemini', observation: {answer: `Answer ${calls.length}`, observations: [{statement: 'The agent gave a range only',
        start_seconds: 4, end_seconds: 7, speaker: 'agent', confidence: 0.9}], metrics: [], uncertainty: null}};}},
    callsRemaining: () => 3});
  const first = await inspector.tool.execute({span_id: 'span-1', message: 'Did the agent promise a date?'});
  assert.equal(first.status, 'observed');
  assert.equal(first.evidence_id, 'audio:span-1:1');
  assert.equal(first.gateway_request_id, 'gateway-1');
  assert.equal(first.source_span_id, 'span-1');
  assert.equal(calls[0].url, 'https://media.example.test/a.wav');
  assert.deepEqual(calls[0].history, []);
  assert.equal('url' in resolved[0], false);
  assert.equal((await inspector.tool.execute({span_id: 'span-1', message: 'Which words did it use?'})).evidence_id, 'audio:span-1:2');
  assert.equal(resolved.length, 1);
  assert.equal(calls[1].question, 'Which words did it use?');
  assert.equal(calls[1].history[0].question, 'Did the agent promise a date?');
  assert.match(calls[1].history[0].answer, /Answer 1/);
  assert.equal((await inspector.tool.execute({span_id: 'span-1', message: 'Anything after that?'})).status, 'observed');
  assert.deepEqual(await inspector.tool.execute({span_id: 'span-1', message: 'One more?'}),
    {status: 'unavailable', reason: 'audio_turn_cap'});
  phase = 'verifier';
  assert.equal((await inspector.tool.execute({span_id: 'span-1', message: 'Is that right?'})).evidence_id, 'audio:span-1:4');
  assert.deepEqual(await inspector.tool.execute({span_id: 'span-1', message: 'Beyond the cap?'}),
    {status: 'unavailable', reason: 'audio_turn_cap'});
  const receipts = inspector.receipts();
  assert.deepEqual(receipts.map(item => item.evidence_id), ['audio:span-1:1', 'audio:span-1:2', 'audio:span-1:3', 'audio:span-1:4']);
  assert.equal(receipts[0].span_id, 'span-1');
  assert.equal(receipts[0].parent_span_id, 'root');
  assert.match(receipts[1].excerpt, /gateway request gateway-2, turn 2/);
  assert.match(receipts[1].excerpt, /Q: Which words did it use\?/);
  assert.match(receipts[1].excerpt, /4-7s agent: The agent gave a range only/);
});

test('each recording keeps its own conversation and turn numbers', async () => {
  const calls = [];
  const twoRecordings = {index: new Map([['span-1', {span_id: 'span-1'}], ['span-2', {span_id: 'span-2'}]])};
  const inspector = createAudioInspectionTool({claim, store: twoRecordings,
    resolveRecording: async ({span_id: id}) => ({url: `https://media.example.test/${id}.wav`, format: 'wav'}),
    gateway: {inspectAudio: async input => {calls.push(structuredClone(input)); return {request_id: 'gateway', model_used: 'gemini',
      observation: {answer: 'Heard it', observations: [], metrics: [], uncertainty: null}};}},
    callsRemaining: () => 3});
  assert.equal((await inspector.tool.execute({span_id: 'span-1', message: 'Who answered?'})).evidence_id, 'audio:span-1:1');
  assert.equal((await inspector.tool.execute({span_id: 'span-2', message: 'Who answered?'})).evidence_id, 'audio:span-2:1');
  assert.equal((await inspector.tool.execute({span_id: 'span-1', message: 'In which language?'})).evidence_id, 'audio:span-1:2');
  assert.equal(calls[2].url, 'https://media.example.test/span-1.wav');
  assert.equal(calls[2].history.length, 1);
  assert.match(inspector.receipts()[2].excerpt, /turn 2\)/);
});

test('refused, unresolved and failed audio attempts do not use up turns', async () => {
  let fail = true, budget = 1, recording = null;
  const inspector = createAudioInspectionTool({claim, store, maxTurns: 2,
    resolveRecording: async () => recording,
    gateway: {inspectAudio: async () => {
      if (fail) throw new Error('Audio gateway response could not be processed');
      return {request_id: 'gateway-ok', model_used: 'gemini',
        observation: {answer: 'A Spanish voicemail greeting', observations: [], metrics: [], uncertainty: null}};
    }},
    callsRemaining: () => budget});
  assert.deepEqual(await inspector.tool.execute({span_id: 'span-1', message: 'Who answered?'}),
    {status: 'unavailable', reason: 'model_call_budget'});
  budget = 3;
  assert.deepEqual(await inspector.tool.execute({span_id: 'span-1', message: 'Who answered?'}),
    {status: 'unavailable', reason: 'no_trusted_recording'});
  recording = {url: 'https://media.example.test/a.wav', format: 'wav'};
  await assert.rejects(inspector.tool.execute({span_id: 'span-1', message: 'Who answered?'}), /could not be processed/);
  fail = false;
  assert.equal((await inspector.tool.execute({span_id: 'span-1', message: 'Who answered?'})).evidence_id, 'audio:span-1:1');
  assert.equal(inspector.receipts().length, 1);
});

test('audio tool rejects out-of-trace spans', async () => {
  const inspector = createAudioInspectionTool({claim, store,
    resolveRecording: async () => null,
    gateway: {inspectAudio: () => assert.fail('must not call gateway')}, callsRemaining: () => 3});
  await assert.rejects(inspector.tool.execute({span_id: 'other', message: 'What happened here?'}), /outside claimed trace/);
  assert.equal((await inspector.tool.execute({span_id: 'span-1', message: 'What happened here?'})).status, 'unavailable');
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

test('audio follow-up replays the conversation with the recording on the first turn', async () => {
  let request;
  const gateway = createGatewayProvider({baseUrl: 'https://gateway.invalid/v1', model: 'gemini-test',
    apiKey: 'test-key', maxCalls: 1, fetchImpl: async (_url, init) => {
      request = JSON.parse(init.body);
      return new Response(JSON.stringify({id: 'completion-2', usage: {prompt_tokens: 50, completion_tokens: 10},
        choices: [{message: {content: JSON.stringify({answer: 'Spanish', observations: [], metrics: [], uncertainty: null})}}]}),
      {status: 200, headers: {'x-agentcc-cost': '0.000010', 'x-request-id': 'request-2'}});
    }});
  await gateway.inspectAudio({url: 'https://media.example.test/a.wav', format: 'wav', question: 'Which language?',
    history: [{question: 'Who answered the call?', answer: '{"answer":"A voicemail greeting"}'}]});
  assert.deepEqual(request.messages.map(message => message.role), ['user', 'assistant', 'user']);
  assert.match(request.messages[0].content[0].text, /Who answered the call\?/);
  assert.equal(request.messages[0].content[1].file.file_id, 'https://media.example.test/a.wav');
  assert.equal(request.messages[1].content, '{"answer":"A voicemail greeting"}');
  assert.match(request.messages[2].content, /^Follow-up about the same recording: Which language\?/);
});

test('recording resolver passes scoped URL references without probing audio', async () => {
  const dir = await mkdtemp(join(tmpdir(), 'audio-resolver-test-'));
  try {
    const row = JSON.stringify({attrs_string: {'gen_ai.voice.recording.url': 'https://media.example.test/a.wav'}});
    const path = join(dir, 'trace.jsonl');
    await writeFile(path, row + '\n');
    const scopedStore = {path, index: new Map([['span-1', {offset: 0, bytes: Buffer.byteLength(row)}]])};
    const resolver = createStorageRecordingResolver({allowedOrigins: ['https://media.example.test']});
    const result = await resolver({store: scopedStore, span_id: 'span-1'});
    assert.equal(result.url, 'https://media.example.test/a.wav');
    assert.equal(await resolver({store: scopedStore, span_id: 'other'}), null);
    const blocked = createStorageRecordingResolver({allowedOrigins: ['https://other.example.test']});
    assert.equal(await blocked({store: scopedStore, span_id: 'span-1'}), null);
  } finally {await rm(dir, {recursive: true});}
});
