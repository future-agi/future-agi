import assert from 'node:assert/strict';
import { EventEmitter } from 'node:events';
import { readFileSync } from 'node:fs';
import test from 'node:test';

const serverURL = new URL('../stack/mock-llm/server.mjs', import.meta.url);
// Red-first safety: never import the old module that listens at evaluation.
assert.match(readFileSync(serverURL, 'utf8'), /export function handleRequest\(/);
const { handleRequest } = await import(serverURL.href);

function call(method, url, body = '', chunks) {
  const req = Object.assign(new EventEmitter(), { method, url });
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
