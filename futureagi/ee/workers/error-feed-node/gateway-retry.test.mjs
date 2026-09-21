import test from 'node:test';
import assert from 'node:assert/strict';
import {createGatewayProvider} from './gateway-provider.mjs';

const request = {agent: {id: 'test'}, messages: [{role: 'user', content: 'Check refund'}], tools: []};
const success = () => new Response(JSON.stringify({id: 'response', choices: [{message: {content: 'ok'}}],
  usage: {prompt_tokens: 12, completion_tokens: 3}}),
{headers: {'content-type': 'application/json', 'x-agentcc-cost': '0.000200'}});

function fixture(statuses, options = {}, retryAfter = null) {
  const sent = [], waits = [];
  const gateway = createGatewayProvider({baseUrl: 'http://fixture/v1', model: 'fixture', apiKey: 'fake',
    retryRandom: () => 0.5, retrySleep: async ms => { waits.push(ms); },
    fetchImpl: async (_url, init) => {
      sent.push(init.body);
      const status = statuses[Math.min(sent.length - 1, statuses.length - 1)];
      return status === 200 ? success() : new Response('SECRET upstream body', {status,
        headers: retryAfter === null ? {} : {'retry-after': retryAfter}});
    }, ...options});
  return {gateway, sent, waits};
}

test('429 recovery retains every attempt and unknown-cost receipt', async () => {
  const {gateway, sent, waits} = fixture([429, 429, 200]);
  assert.equal((await gateway.provider.generate(request)).content, 'ok');
  assert.deepEqual(waits, [1500, 2500]);
  assert.equal(new Set(sent).size, 1);
  const accounting = gateway.accounting();
  assert.equal(accounting.model_calls, 3);
  assert.equal(accounting.cost_usd, null);
  assert.equal(accounting.unknown_cost_calls, 2);
  assert.deepEqual(accounting.calls.map(call => call.http_status), [429, 429, 200]);
  assert.deepEqual(accounting.calls.map(call => call.retry_of), [undefined, 1, 1]);
  assert.ok(!JSON.stringify(accounting).includes('SECRET'));
});

test('only bounded explicit 429 responses retry', async () => {
  for (const status of [400, 401, 403, 429, 500, 503]) {
    const {gateway, sent} = fixture([status]);
    await assert.rejects(gateway.provider.generate(request), {message: `Gateway request failed with HTTP ${status}`});
    assert.equal(sent.length, status === 429 ? 3 : 1);
  }
  const {gateway, sent, waits} = fixture([429, 200], {maxCalls: 1});
  await assert.rejects(gateway.provider.generate(request), /HTTP 429/);
  assert.equal(sent.length, 1);
  assert.deepEqual(waits, []);
});

test('retry delay honors Retry-After but stays within 30 seconds', async () => {
  const {gateway, waits} = fixture([429, 200], {}, '5');
  await gateway.provider.generate(request);
  assert.deepEqual(waits, [5500]);
  const capped = fixture([429], {}, '60');
  await assert.rejects(capped.gateway.provider.generate(request), /HTTP 429/);
  assert.equal(capped.sent.length, 1);
});
