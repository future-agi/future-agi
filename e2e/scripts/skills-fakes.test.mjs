import { test } from 'node:test';
import assert from 'node:assert/strict';
import { createServer } from 'node:http';
import { readFileSync } from 'node:fs';
import { createFixtureHandler } from '../stack/skills-fakes/server.mjs';

test('both test topologies freeze prices and production does not recognize the fixture switch', () => {
  for (const [file, processes] of [['docker-compose.e2e.yml', 2], ['docker-compose.standalone-e2e.yml', 1]]) {
    const text = readFileSync(new URL(`../stack/${file}`, import.meta.url), 'utf8');
    assert.equal((text.match(/E2E_SKILLS_FIXTURES: "1"/g) ?? []).length, processes);
    assert.equal((text.match(/LITELLM_LOCAL_MODEL_COST_MAP: "True"/g) ?? []).length, processes);
  }
  const settings = readFileSync(new URL('../../futureagi/tfc/settings/settings.py', import.meta.url), 'utf8');
  assert.equal(settings.includes('E2E_SKILLS_FIXTURES'), false);
  assert.equal(settings.includes('e2e_skills_transport'), false);
});

async function fixture(t) {
  const server = createServer(createFixtureHandler());
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  t.after(() => new Promise(resolve => server.close(resolve)));
  const origin = `http://127.0.0.1:${server.address().port}`;
  return async (path, body, method = 'POST', key) => {
    const response = await fetch(origin + path, { method,
      headers: { 'Content-Type': 'application/json', ...(key ? { Authorization: `Bearer ${key}` } : {}) },
      ...(body === undefined ? {} : { body: JSON.stringify(body) }) });
    return { status: response.status, body: await response.json() };
  };
}
const namespace = 'e2e-skills-qualification-0001';
const messages = [
  { role: 'system', content: 'Available skills:\n[{"id":"opaque-target","name":"synthetic","description":"Fixture"}]' },
  { role: 'user', content: 'User request and supplied input\n{"request":"eligible","fixtures":[]}' },
];
const completion = { model: 'gpt-4o-mini', messages };
const rule = { match: { request: 'eligible', turn: 0 }, response: { message: { role: 'assistant', content: null,
  tool_calls: [{ id: 'load-1', type: 'function', function: { name: 'load_skill', arguments: { skill_id: '$skill_id' } } }] },
  finish_reason: 'tool_calls' } };
const recording = { url: 'https://api.github.com/repos/synthetic/example', status: 200,
  headers: { ETag: 'synthetic-etag' }, body_base64: Buffer.from('{"private":false}').toString('base64') };

test('scenario namespace, duplicate and unknown routes fail closed', async t => {
  const send = await fixture(t);
  assert.equal((await send('/v1/chat/completions', completion, 'POST', namespace)).status, 401);
  assert.equal((await send('/scenarios', { namespace, rules: [rule], github: [] })).status, 201);
  assert.equal((await send('/scenarios', { namespace, rules: [], github: [] })).status, 409);
  assert.equal((await send('/v1/chat/completions', completion, 'POST', namespace + '-foreign')).status, 401);
  assert.equal((await send('/github', { url: recording.url })).status, 404);
  assert.equal((await send('/unknown', {})).status, 404);
  assert.equal((await send('/scenarios/' + namespace, undefined, 'DELETE')).status, 200);
  assert.equal((await send('/v1/chat/completions', completion, 'POST', namespace)).status, 401);
});

test('structured turns, UTF-8 text, typed JSON and exact digest ledger', async t => {
  const send = await fixture(t);
  const second = { match: { request: 'eligible', turn: 1, tool_text: 'café 漢字' }, response: {
    message: { role: 'assistant', content: '{"eligible":true,"amount":0,"missing":null}' }, finish_reason: 'stop' } };
  await send('/scenarios', { namespace, rules: [rule, second], github: [] });
  const first = await send('/v1/chat/completions', completion, 'POST', namespace);
  assert.equal(first.status, 200);
  assert.equal(first.body.choices[0].message.content, null);
  assert.deepEqual(JSON.parse(first.body.choices[0].message.tool_calls[0].function.arguments), { skill_id: 'opaque-target' });
  const conversation = [...messages, first.body.choices[0].message, { role: 'tool', tool_call_id: 'load-1', content: 'café 漢字' }];
  const final = await send('/v1/chat/completions', { ...completion, messages: conversation }, 'POST', namespace);
  assert.deepEqual(JSON.parse(final.body.choices[0].message.content), { eligible: true, amount: 0, missing: null });
  assert.deepEqual(final.body.usage, { prompt_tokens: 17, completion_tokens: 9, total_tokens: 26 });
  const { body: { ledger } } = await send('/scenarios/' + namespace, undefined, 'GET');
  assert.deepEqual(ledger.map(row => [row.kind, row.request, row.turn, row.status]), [
    ['model', 'eligible', 0, 200], ['model', 'eligible', 1, 200],
  ]);
  assert.deepEqual(ledger[1].messages, conversation);
  assert.match(ledger[1].request_sha256, /^[a-f0-9]{64}$/);
  assert.match(ledger[1].response_sha256, /^[a-f0-9]{64}$/);
  assert.notEqual(ledger[0].request_sha256, ledger[1].request_sha256);
  assert.equal(JSON.stringify(ledger).includes(namespace), false);
});

test('ambiguous rules fail rather than choose a response', async t => {
  const send = await fixture(t);
  await send('/scenarios', { namespace, rules: [rule, rule], github: [] });
  assert.equal((await send('/v1/chat/completions', completion, 'POST', namespace)).status, 409);
});

test('bounded delays, HTTP errors, missing usage and malformed tool protocol are programmable', async t => {
  const send = await fixture(t);
  for (const status of [401, 429, 500]) {
    const key = `${namespace}-${status}`;
    await send('/scenarios', { namespace: key, rules: [{ match: rule.match, status, delay_ms: 1 }], github: [] });
    assert.equal((await send('/v1/chat/completions', completion, 'POST', key)).status, status);
  }
  const response = structuredClone(rule.response);
  response.omit_usage = true;
  response.message.tool_calls.push(response.message.tool_calls[0]);
  await send('/scenarios', { namespace, rules: [{ match: rule.match, response }], github: [] });
  const malformed = await send('/v1/chat/completions', completion, 'POST', namespace);
  assert.equal(malformed.body.usage, undefined);
  assert.deepEqual(malformed.body.choices[0].message.tool_calls.map(call => call.id), ['load-1', 'load-1']);
  for (const delay_ms of [-1, 5001, '1']) {
    assert.equal((await send('/scenarios', { namespace: namespace + '-invalid', rules: [{ ...rule, delay_ms }], github: [] })).status, 400);
  }
});

test('recorded GitHub responses preserve bytes and isolate mutable refs from immutable archives', async t => {
  const send = await fixture(t);
  const archive = { ...recording, url: 'https://codeload.github.com/synthetic/example/zip/' + 'a'.repeat(40),
    body_base64: Buffer.from([80, 75, 0, 255]).toString('base64') };
  await send('/scenarios', { namespace, rules: [], github: [recording, archive] });
  assert.deepEqual((await send('/github', { url: archive.url })).body, archive);
  const moved = { ...recording, body_base64: Buffer.from('changed branch').toString('base64') };
  assert.equal((await send('/scenarios/' + namespace + '/github', moved, 'PUT')).status, 200);
  assert.deepEqual((await send('/github', { url: recording.url })).body, moved);
  assert.deepEqual((await send('/github', { url: archive.url })).body, archive);
  assert.equal((await send('/scenarios', { namespace: namespace + '-other', rules: [], github: [archive] })).status, 409);
  assert.equal((await send('/scenarios/' + namespace + '/github', { ...recording, url: recording.url + '-other' }, 'PUT')).status, 404);
  for (const url of ['http://api.github.com/repos/x/y', 'https://localhost/x', 'https://api.github.com.evil.invalid/x']) {
    assert.equal((await send('/scenarios', { namespace: namespace + '-bad', rules: [], github: [{ ...recording, url }] })).status, 400);
  }
});
