import { test, expect } from '@playwright/test';
import { MockUpstream } from '../lib/upstream';

// Offline helper contracts: fetch is replaced, not the product or gateway.
test('reply base URLs preserve the default and validate bounded fixture names', () => {
  const mock = new MockUpstream();
  expect(mock.baseUrl('fixture')).toBe('http://llm.e2e-public.test:8080/s/fixture/-');
  expect(mock.baseUrl('fixture', { reply: 'Judge_1.v2-' }))
    .toBe('http://llm.e2e-public.test:8080/s/fixture/reply=Judge_1.v2-');
  expect(mock.baseUrl('fixture', { reply: 'a'.repeat(80), usage: 'zero' }))
    .toBe(`http://llm.e2e-public.test:8080/s/fixture/usage=zero,reply=${'a'.repeat(80)}`);
  for (const reply of ['', 'a'.repeat(81), 'a/b', 'a,b', 'a=b', '%20']) {
    expect(() => mock.baseUrl('fixture', { reply })).toThrow('reply is [A-Za-z0-9_.-]{1,80}');
  }
});

test('reply helpers send raw text, ordered JSON rules and scenario cleanup to the read URL', async () => {
  const mock = new MockUpstream('http://127.0.0.1:28082');
  const original = globalThis.fetch;
  const calls: { url: string; method: string | undefined; headers: HeadersInit | undefined; body: BodyInit | null | undefined }[] = [];
  globalThis.fetch = async (input, init) => {
    calls.push({ url: String(input), method: init?.method, headers: init?.headers, body: init?.body });
    expect(init?.signal).toBeInstanceOf(AbortSignal);
    expect(init?.signal?.aborted).toBe(false);
    return new Response(JSON.stringify(init?.method === 'DELETE' ? { cleared: true } : { stored: true }), { status: 200 });
  };
  const body = ' \n{"reason":"café fixture"}\r\n';
  const rules = [
    { user_contains: 'unique-user-marker', body: '{"improved_prompt":"fixture"}' },
    { system_contains: 'unique-system-marker', user_contains: 'unique-user-marker', body: '' },
  ];
  try {
    await mock.registerReply('fixture', 'judge', body);
    await mock.registerReplyRules('fixture', rules);
    await mock.clearReplies('fixture');
    expect(calls).toEqual([
      { url: 'http://127.0.0.1:28082/__replies/fixture/judge', method: 'POST',
        headers: { 'Content-Type': 'text/plain; charset=utf-8' }, body },
      { url: 'http://127.0.0.1:28082/__replies/fixture', method: 'POST',
        headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(rules) },
      { url: 'http://127.0.0.1:28082/__replies/fixture', method: 'DELETE', headers: undefined, body: undefined },
    ]);
  } finally {
    globalThis.fetch = original;
  }
});

test('reply helpers reject invalid path identifiers before sending requests', async () => {
  const mock = new MockUpstream('http://127.0.0.1:28082');
  const original = globalThis.fetch;
  let calls = 0;
  globalThis.fetch = async () => {
    calls++;
    throw new Error('unexpected request');
  };
  try {
    for (const scenario of ['', 'a/b', 'a'.repeat(81)]) {
      const error = `scenario ${JSON.stringify(scenario)} is not [A-Za-z0-9_.-]{1,80}`;
      await expect(mock.registerReply(scenario, 'judge', 'body')).rejects.toThrow(error);
      await expect(mock.registerReplyRules(scenario, [])).rejects.toThrow(error);
      await expect(mock.clearReplies(scenario)).rejects.toThrow(error);
    }
    for (const name of ['', 'a/b', 'a'.repeat(81)]) {
      await expect(mock.registerReply('fixture', name, 'body')).rejects.toThrow('reply is [A-Za-z0-9_.-]{1,80}');
    }
    expect(calls).toBe(0);
  } finally {
    globalThis.fetch = original;
  }
});

test('reply helpers report registration and cleanup HTTP failures', async () => {
  const mock = new MockUpstream('http://127.0.0.1:28082');
  const original = globalThis.fetch;
  globalThis.fetch = async () => new Response(null, { status: 400 });
  try {
    await expect(mock.registerReply('fixture', 'judge', 'body')).rejects.toThrow('mock-llm register reply 400');
    await expect(mock.registerReplyRules('fixture', [])).rejects.toThrow('mock-llm register reply rules 400');
    await expect(mock.clearReplies('fixture')).rejects.toThrow('mock-llm clear replies 400');
  } finally {
    globalThis.fetch = original;
  }
});
