import assert from 'node:assert/strict';
import { EventEmitter } from 'node:events';
import { readFileSync } from 'node:fs';
import test from 'node:test';

const serverURL = new URL('../stack/mock-llm/server.mjs', import.meta.url);
// Red-first safety: never import the old module that listens at evaluation.
assert.match(readFileSync(serverURL, 'utf8'), /export function handleRequest\(/);
const { handleRequest } = await import(serverURL.href);

function call(method, url, body = '', chunks, headers = {}) {
  const req = Object.assign(new EventEmitter(), { method, url, headers });
  const res = { status: 0, headers: {}, data: '', ended: false,
    writeHead(status, headers) { this.status = status; this.headers = headers; },
    write(data) { this.data += data; },
    end(data = '') { this.data += data; this.ended = true; },
    destroy() { this.ended = true; } };
  handleRequest(req, res);
  for (const part of chunks ?? [typeof body === 'string' ? body : JSON.stringify(body)]) req.emit('data', Buffer.from(part));
  req.emit('end');
  assert.equal(res.ended, true);
  return { status: res.status, headers: res.headers, raw: res.data,
    body: res.headers['Content-Type'] === 'application/json' ? JSON.parse(res.data) : null };
}

test('decision fixtures preserve native envelopes, identifiers, probabilities and usage', () => {
  for (const [url, model, key] of [
    ['/v1/systemone', 'jev-1.14.0', 'typesafe'],
    ['/v1/decisions', 'pplx-decider-v1.2', 'perplexity'],
    ['/api/alpha/decisions', 'openai/gpt-6-luna-decisions', 'openrouter'],
  ]) {
    const body = { model, state: 'e2e-decision:pass unique-fixture',
      questions: { check: { type: 'noul', instructions: 'synthetic only' } },
      ...(key === 'openrouter' ? { provider: { allow_fallbacks: true, require_parameters: true } } : {}) };
    const headers = { authorization: `Bearer e2e-decision-${key}-key` };
    assert.deepEqual(call('POST', url, body, undefined, headers).body, {
      model, answers: { check: { type: 'noul', noul: 0.9 } }, usage: { input_tokens: 37, output_tokens: 0 },
    });
    const failed = call('POST', url, { ...body, state: 'e2e-decision:fail' }, undefined, headers);
    assert.deepEqual(failed.body.answers, { check: { type: 'noul', noul: 0.1 } });
    const refusal = call('POST', url, { ...body, state: 'e2e-decision:refuse' }, undefined, headers);
    assert.deepEqual(refusal.body.answers, { check: { type: 'refusal' } });
  }
  const native = call('POST', '/v1/decisions', {
    model: 'gpt-6-luna', input: 'e2e-decision:pass',
    questions: [{ name: 'check', type: 'predicate', instructions: 'synthetic only' }],
  }, undefined, { authorization: 'Bearer e2e-decision-openai-key' });
  assert.deepEqual(native.body, { model: 'gpt-6-luna', answers: [{ name: 'check', type: 'predicate', probability: 0.9 }],
    usage: { input_tokens: 37, output_tokens: 0 } });
});

test('decision fixtures refuse wrong keys, fallback policy, invalid input and oversized bodies', () => {
  const body = { model: 'openai/gpt-6-luna-decisions', state: 'e2e-decision:pass',
    questions: { q: { type: 'noul', instructions: 'synthetic only' } } };
  const auth = { authorization: 'Bearer e2e-decision-openrouter-key' };
  assert.equal(call('POST', '/api/alpha/decisions', body).status, 401);
  assert.equal(call('POST', '/api/alpha/decisions', body, undefined, { authorization: 'Bearer e2e-decision-openai-key' }).status, 401);
  assert.equal(call('POST', '/api/alpha/decisions', body, undefined, auth).status, 400);
  const routed = { ...body, provider: { allow_fallbacks: true, require_parameters: true } };
  assert.equal(call('POST', '/api/alpha/decisions', routed, undefined, auth).status, 200);
  // The old single-host policy is refused, so the gateway must send the current one.
  assert.equal(call('POST', '/api/alpha/decisions', { ...body, provider: { allow_fallbacks: false, require_parameters: true } }, undefined, auth).status, 400);
  for (const override of [{ questions: {} }, { questions: [] }, { model: 'not-a-model' }, { models: ['other'] }, { questions: { q: { type: 'unknown' } } }]) {
    assert.equal(call('POST', '/api/alpha/decisions', { ...routed, ...override }, undefined, auth).status, 400);
  }
  assert.equal(call('GET', '/v1/decisions', {}, undefined, auth).status, 405);
  assert.equal(call('POST', '/v1/decisions', '', ['x'.repeat(262145)], auth).status, 413);
});

test('decision choice and score fixtures keep labels and ordinal positions', () => {
  const response = call('POST', '/v1/decisions', { model: 'gpt-6-luna', input: 'e2e-decision:pass', questions: [
    { name: 'label', type: 'choice', choices: [{ value: 'bad' }, { value: 'good' }] },
    { name: 'grade', type: 'score', levels: [{ label: '0', description: 'bad' }, { label: '1', description: 'good' }] },
  ] }, undefined, { authorization: 'Bearer e2e-decision-openai-key' });
  assert.deepEqual(response.body.answers, [
    { name: 'label', type: 'choice', confidence: 1, choice: 'good', probabilities: [{ value: 'bad', probability: 0 }, { value: 'good', probability: 1 }] },
    { name: 'grade', type: 'score', confidence: 1, score: 1, probabilities: [{ value: 0, label: '0', probability: 0 }, { value: 1, label: '1', probability: 1 }] },
  ]);
});

test('serving health and scalar/batch embeddings are finite nonzero eight-vectors', () => {
  assert.deepEqual(call('GET', '/model/v1/models').body, { models: ['text_embedding'] });
  for (const text of ['one', ['first', 'second'], Array(64).fill('bounded')]) {
    const response = call('POST', '/model/v1/embed', { input_type: 'text', text });
    assert.equal(response.status, 200);
    assert.deepEqual(response.body, { embeddings: Array.from({ length: Array.isArray(text) ? text.length : 1 },
      () => [0.125, 0.125, 0.125, 0.125, 0.125, 0.125, 0.125, 0.125]) });
  }
});

test('serving rejects invalid shapes, unsupported methods/routes and oversized bodies', () => {
  for (const body of ['{', 'null', '42', '"x"', '[]', {}, { text: 'x' },
    { text: 'x', input_type: 'image' }, { text: '', input_type: 'text' },
    { text: '  ', input_type: 'text' }, { text: [], input_type: 'text' },
    { text: ['yes', 1], input_type: 'text' }, { text: [null], input_type: 'text' },
    { text: Array(65).fill('x'), input_type: 'text' },
    { text: 'x', input_type: 'text', url: 'https://provider.invalid' }]) {
    assert.equal(call('POST', '/model/v1/embed', body).status, 400);
  }
  assert.equal(call('GET', '/model/v1/embed').status, 405);
  assert.equal(call('POST', '/model/v1/models').status, 405);
  for (const path of ['/model/v1/embed/image', '/model/v1/embed/audio', '/model/v1/other']) {
    assert.equal(call('POST', path, {}).status, 404);
  }
  assert.equal(call('POST', '/model/v1/embed', '', ['x'.repeat(131072), 'x'.repeat(131073)]).status, 413);
});

test('existing OpenAI model list, chat usage, streaming echo and embedding wire stay exact', () => {
  assert.deepEqual(call('GET', '/v1/models').body, { object: 'list', data:
    ['gpt-4o-mini', 'gpt-4o', 'text-embedding-3-small'].map(id => ({ id, object: 'model', owned_by: 'e2e' })) });
  const messages = [{ role: 'user', content: 'old' }, { role: 'assistant', content: 'ignored' },
    { role: 'user', content: 'hello  world\nagain' }];
  const chat = call('POST', '/v1/chat/completions', { model: 'turing_flash', messages });
  assert.deepEqual(chat.body, { id: 'chatcmpl-e2e', object: 'chat.completion', model: 'turing_flash',
    choices: [{ index: 0, message: { role: 'assistant', content: 'echo: hello  world\nagain' }, finish_reason: 'stop' }],
    usage: { prompt_tokens: 7, completion_tokens: 7, total_tokens: 14 } });
  const stream = call('POST', '/v1/chat/completions', { messages, stream: true });
  const chunks = stream.raw.split('\n\n').filter(line => line && line !== 'data: [DONE]')
    .map(line => JSON.parse(line.slice(6)));
  assert.equal(chunks.map(chunk => chunk.choices[0].delta.content ?? '').join(''), chat.body.choices[0].message.content);
  assert.equal(chunks.at(-1).choices[0].finish_reason, 'stop');
  assert.ok(stream.raw.endsWith('data: [DONE]\n\n'));
  assert.deepEqual(call('POST', '/v1/embeddings', { input: ['a', 'b'] }).body, {
    object: 'list', model: 'text-embedding-3-small', data: [0, 1].map(index => ({ object: 'embedding', index,
      embedding: [0.125, 0.125, 0.125, 0.125, 0.125, 0.125, 0.125, 0.125] })),
    usage: { prompt_tokens: 1, total_tokens: 1 } });
});
