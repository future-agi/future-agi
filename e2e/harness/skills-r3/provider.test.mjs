import test from 'node:test';
import assert from 'node:assert/strict';
import { ProgrammableProvider, completion } from '../../lib/skills-r3/programmable-provider.mjs';
import { providerScenarios } from '../../fixtures/skills-r3/provider-scenarios.mjs';

const request = (fixture, extra = {}) => fetch(`${fixture.baseURL()}/chat/completions`, {
  method: 'POST', headers: { 'content-type': 'application/json', authorization: 'Bearer synthetic-never-retain' },
  body: JSON.stringify({ model: 'fixture-model', messages: [{ role: 'user', content: 'synthetic-payload-never-retain' }], ...extra }),
});

for (const [name, script] of Object.entries(providerScenarios())) {
  test(`programmable provider ${name} preserves transport behavior`, async () => {
    const server = await new ProgrammableProvider().listen();
    try {
      const fixture = server.register(script);
      if (name === 'late_completion') {
        const pending = request(fixture);
        await server.barriers.reached('before-response');
        assert.equal(server.ledger(fixture.capability)[0].state, 'received');
        server.barriers.release('before-response');
        assert.equal((await pending).status, 200);
      } else if (name === 'lost_response') {
        await assert.rejects(request(fixture));
        assert.equal(server.ledger(fixture.capability)[0].state, 'disconnected_after_receipt');
      } else {
        for (const expected of script) {
          const response = await request(fixture);
          assert.equal(response.status, expected.status ?? 200);
          const text = await response.text();
          assert.equal(text, expected.kind === 'raw' ? expected.body : JSON.stringify(expected.body));
        }
      }
      assert.equal(server.remaining(fixture.capability), 0);
      assert.equal(server.ledger(fixture.capability).length, script.length);
      const serialized = JSON.stringify(server.ledger(fixture.capability));
      assert.ok(!serialized.includes('synthetic-payload-never-retain'));
      assert.ok(!serialized.includes('synthetic-never-retain'));
      assert.equal((await request(fixture)).status, 409, 'unexpected retry must be observable, not echo success');
    } finally { await server.close(); }
  });
}

test('per-test isolation, invalid bodies and body limits', async () => {
  const server = await new ProgrammableProvider().listen();
  try {
    const a = server.register([{ kind: 'json', body: completion({ content: 'A' }) }]);
    const b = server.register([{ kind: 'json', body: completion({ content: 'B' }) }]);
    assert.equal((await request(a, { stream: true })).status, 400);
    assert.equal(server.ledger(a.capability).length, 0);
    assert.equal((await request(a, { messages: [{ role: 'user', content: 'a'.repeat(1024 * 1024) }] })).status, 413);
    const [ra, rb] = await Promise.all([request(a), request(b)]);
    assert.equal((await ra.json()).choices[0].message.content, 'A');
    assert.equal((await rb.json()).choices[0].message.content, 'B');
    assert.equal(server.ledger(a.capability).length, 1);
    assert.equal(server.ledger(b.capability).length, 1);
    assert.throws(() => server.ledger('unknown'));
  } finally { await server.close(); }
});

test('missing usage, malformed tool IDs and partial delivery are not repaired by fixture', () => {
  const scenarios = providerScenarios();
  assert.ok(!('usage' in scenarios.missing_usage[0].body));
  const calls = scenarios.duplicate_ids[0].body.choices[0].message.tool_calls;
  assert.equal(calls[0].id, calls[1].id);
  assert.equal(scenarios.malformed_ids[0].body.choices[0].message.tool_calls[0].id, undefined);
  assert.equal(scenarios.utf8_overlap[1].body.choices[0].message.tool_calls.length, 2);
});
