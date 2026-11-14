import { test, expect, request } from '@playwright/test';
import { E2E } from '../lib/env';

test('gateway routes chat completions to the deterministic mock', async () => {
  const req = await request.newContext();
  const res = await req.post(`${E2E.gatewayUrl}/v1/chat/completions`, {
    headers: { Authorization: 'Bearer local-dev-only-shared-secret-replace-me' },
    data: { model: 'gpt-4o-mini', messages: [{ role: 'user', content: 'ping' }] },
  });
  expect(res.status()).toBe(200);
  const body = await res.json();
  expect(body.choices[0].message.content).toBe('echo: ping');
  await req.dispose();
});
