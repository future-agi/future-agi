import test from 'node:test';
import assert from 'node:assert/strict';
import {createServer} from 'node:http';
import {mkdtemp, writeFile, readdir, rm, mkdir, symlink} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {runJob} from './worker.mjs';
import {createGatewayProvider, parseGatewayCost} from './gateway-provider.mjs';

async function gateway(t, handler) {
  const requests = [];
  const server = createServer(async (req, res) => {
    let raw = '';
    for await (const chunk of req) raw += chunk;
    const body = JSON.parse(raw);
    requests.push({url: req.url, authorization: req.headers.authorization, body});
    handler(req, res, body);
  });
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  t.after(() => { server.closeAllConnections(); return new Promise(resolve => server.close(resolve)); });
  return {url: 'http://127.0.0.1:' + server.address().port + '/v1', requests};
}

test('real Omega executes file tool through gateway and preserves reported pricing', async t => {
  const root = await mkdtemp(join(tmpdir(), 'omega-worker-test-'));
  t.after(() => rm(root, {recursive: true, force: true}));
  await mkdir(join(root, 'scratch'));
  await writeFile(join(root, 'trace.jsonl'), '{"refund_amount":10}\n');
  const mock = await gateway(t, (_req, res, body) => {
    const toolResult = body.messages.find(m => m.role === 'tool');
    res.writeHead(200, {'content-type': 'application/json', 'x-agentcc-cost': toolResult ? '0.000200' : '0.000100',
      'x-agentcc-model-used': 'routed-test-model', 'x-agentcc-cache': 'miss', 'x-request-id': 'mock-request'});
    res.end(JSON.stringify({id: 'mock-response', choices: [{message: toolResult
      ? {role: 'assistant', content: 'Recorded refund is 10.'}
      : {role: 'assistant', content: '', tool_calls: [{id: 'read1', type: 'function',
        function: {name: 'read_evidence', arguments: '{"offset":0,"length":100}'}}]}}],
      usage: {prompt_tokens: 100, completion_tokens: 20, total_tokens: 120,
        prompt_tokens_details: {cached_tokens: 10}, completion_tokens_details: {reasoning_tokens: 5}}}));
  });
  const result = await runJob({id: 'job_1', objective: 'Read the refund amount.', evidence_file: 'trace.jsonl'},
    {config: {baseUrl: mock.url, model: 'test-alias', apiKey: 'test-only-key'}, evidenceRoot: root, scratchRoot: join(root, 'scratch')});
  assert.equal(result.status, 'completed');
  assert.equal(result.output, 'Recorded refund is 10.');
  assert.equal(result.evidence.reads.length, 1);
  assert.equal(result.accounting.model_calls, 2);
  assert.equal(result.accounting.cost_usd, 0.0003);
  assert.equal(result.accounting.calls[0].routed_model, 'routed-test-model');
  assert.equal(result.accounting.calls[0].usage.completion_tokens_details.reasoning_tokens, 5);
  assert.equal(mock.requests[0].authorization, 'Bearer test-only-key');
  assert.equal(mock.requests[0].url, '/v1/chat/completions');
  assert.equal(mock.requests[0].body.model, 'test-alias');
  assert.deepEqual(await readdir(join(root, 'scratch')), []);
  assert.ok(!JSON.stringify(result).includes('test-only-key'));
});

test('missing pricing remains unknown even on cache hit; explicit zero remains zero', async t => {
  const mock = await gateway(t, (_req, res) => {
    res.writeHead(200, {'content-type': 'application/json', 'x-agentcc-cache': 'hit'});
    res.end(JSON.stringify({choices: [{message: {content: 'ok'}}], usage: {prompt_tokens: 10, completion_tokens: 2}}));
  });
  const g = createGatewayProvider({baseUrl: mock.url, model: 'test', apiKey: 'fake'});
  await g.provider.generate({agent: {id: 'a'}, messages: [{role: 'user', content: 'hello'}], tools: []});
  assert.equal(g.accounting().cost_usd, null);
  assert.equal(g.accounting().unknown_cost_calls, 1);
  assert.equal(g.accounting().calls[0].cache_status, 'hit');
  assert.equal(parseGatewayCost('0.000000'), 0);
  assert.equal(parseGatewayCost(''), null);
  assert.equal(parseGatewayCost('-1'), null);
  assert.equal(parseGatewayCost('NaN'), null);
});

test('gateway errors omit upstream bodies and are not retried', async t => {
  const mock = await gateway(t, (_req, res) => { res.writeHead(503); res.end('Bearer SECRET must never leak'); });
  const g = createGatewayProvider({baseUrl: mock.url, model: 'test', apiKey: 'fake'});
  await assert.rejects(g.provider.generate({agent: {id: 'a'}, messages: [], tools: []}),
    {message: 'Gateway request failed with HTTP 503'});
  assert.equal(mock.requests.length, 1);
  assert.equal(g.accounting().cost_usd, null);
  assert.ok(!JSON.stringify(g.accounting()).includes('SECRET'));
});

test('input budget counts serialized gateway bodies, not unsent runtime metadata', async t => {
  const mock = await gateway(t, (_req, res) => {
    res.writeHead(200, {'content-type': 'application/json', 'x-agentcc-cost': '0'});
    res.end(JSON.stringify({choices: [{message: {content: '{}'}}],
      usage: {prompt_tokens: 2, completion_tokens: 1}}));
  });
  const g = createGatewayProvider({baseUrl: mock.url, model: 'test', apiKey: 'fake', maxInputBytesTotal: 100});
  const request = {agent: {id: 'a', metadata: {host_only: 'x'.repeat(20000)}},
    messages: [{role: 'user', content: 'hello'}], tools: [], modalities: ['text']};

  await g.provider.generate(request);
  assert.equal(g.accounting().request_bytes, Buffer.byteLength(JSON.stringify(mock.requests[0].body)));
  assert.ok(g.accounting().request_bytes < 100);
  await assert.rejects(g.provider.generate(request), {message: 'Input context budget exhausted'});
  assert.equal(mock.requests.length, 1);
  assert.equal(g.accounting().model_calls, 1);
});

test('rejects traversal before any model request', async () => {
  const root = await mkdtemp(join(tmpdir(), 'omega-path-test-'));
  try {
    await mkdir(join(root, 'evidence'));
    await writeFile(join(root, 'outside'), 'private fixture');
    await symlink(join(root, 'outside'), join(root, 'evidence', 'escape'));
    for (const file of ['../outside', 'escape']) {
      await assert.rejects(runJob({id: 'bad', objective: 'read', evidence_file: file},
        {config: {}, evidenceRoot: join(root, 'evidence')}), /outside its root/);
    }
  } finally { await rm(root, {recursive: true, force: true}); }
});
