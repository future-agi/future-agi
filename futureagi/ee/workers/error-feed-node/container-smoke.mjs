// Pipe into the built image's node entrypoint; never copied into the runtime image.
import assert from 'node:assert/strict';
import {createServer} from 'node:http';
import {mkdtemp, writeFile, readFile, readdir, access, rm} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {execFile} from 'node:child_process';
import {promisify} from 'node:util';

assert.equal(process.getuid(), 1000);
for (const name of ['core', 'model-provider-adapter', 'model-chat-provider', 'model-response-provider',
  'workflow-runtime-adapter', 'workflow-local-provider', 'workflow-graph-provider']) {
  await import('@omega/' + name);
}
for (const path of ['/app/.git', '/app/examples', '/app/packages']) {
  await assert.rejects(access(path));
}
const directory = await mkdtemp(join(tmpdir(), 'omega-container-smoke-'));
await writeFile(join(directory, 'trace.jsonl'), '{"requested":100,"refunded":10}\n');
await writeFile(join(directory, 'job.json'), JSON.stringify({
  id: 'container_smoke', objective: 'Read the recorded refund.', evidence_file: 'trace.jsonl'
}));
let calls = 0;
const server = createServer(async (req, res) => {
  let data = '';
  for await (const chunk of req) data += chunk;
  const body = JSON.parse(data);
  assert.equal(req.url, '/v1/chat/completions');
  assert.equal(req.headers.authorization, 'Bearer fixture-only');
  assert.equal(body.model, 'gateway-alias');
  calls += 1;
  const read = body.messages.some(m => m.role === 'tool');
  res.writeHead(200, {'content-type': 'application/json', 'x-agentcc-cost': '0.000100',
    'x-agentcc-provider': 'fixture', 'x-agentcc-model-used': 'fixture-model'});
  res.end(JSON.stringify({choices: [{message: read ? {content: 'Recorded refund: 10.'} : {
    content: '', tool_calls: [{id: 'r1', type: 'function', function: {
      name: 'read_evidence', arguments: '{"offset":0,"length":128}'
    }}]
  }}], usage: {prompt_tokens: 40, completion_tokens: 10, total_tokens: 50}}));
});
await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
try {
  const {stdout} = await promisify(execFile)(process.execPath,
    ['worker/worker.mjs', '--job', join(directory, 'job.json'), '--output', join(directory, 'result.json')],
    {env: {...process.env, OMEGA_EVIDENCE_DIR: directory, AGENTCC_BASE_URL: 'http://127.0.0.1:' + server.address().port + '/v1',
      AGENTCC_API_KEY: 'fixture-only', OMEGA_MODEL_ID: 'gateway-alias'}});
  const result = JSON.parse(await readFile(join(directory, 'result.json'), 'utf8'));
  assert.equal(result.status, 'completed');
  assert.equal(result.output, 'Recorded refund: 10.');
  assert.equal(result.accounting.cost_usd, 0.0002);
  assert.equal(calls, 2);
  assert.equal(result.evidence.reads.length, 1);
  assert.equal((await readdir('/tmp')).filter(name => name.startsWith('omega-attempt-')).length, 0);
  assert.ok(!stdout.includes('fixture-only'));
  console.log(JSON.stringify({status: 'passed', uid: process.getuid(), installed_packages: 7,
    real_omega_tool_roundtrip: true, gateway_protocol_calls: calls, reported_mock_cost_usd: result.accounting.cost_usd,
    readonly_root: true, scratch_cleaned: true, live_inference: false}));
} finally {
  server.closeAllConnections();
  await new Promise(resolve => server.close(resolve));
  await rm(directory, {recursive: true, force: true});
}
