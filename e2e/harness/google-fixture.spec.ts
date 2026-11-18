import { test, expect, request } from '@playwright/test';
import { E2E } from '../lib/env';
import { googleFixture } from '../lib/google-fixture';
import { validateGoogleFixtureMounts } from '../lib/managed-mock';

test('Google fixture changes visibility only for its synthetic browser', async () => {
  const ordinary = await request.newContext({ baseURL: E2E.apiUrl });
  const synthetic = await request.newContext({ baseURL: E2E.apiUrl,
    userAgent: 'FutureAGI-E2E-Google-Fixture' });
  try {
    const normal = await ordinary.get('/api/deployment-info/');
    const fixture = await synthetic.get('/api/deployment-info/');
    expect(normal.status()).toBe(200);
    expect(fixture.status()).toBe(200);
    expect((await normal.json()).result.mode).toBe('oss');
    expect((await fixture.json()).result.mode).toBe('cloud');
    const provider = await googleFixture();
    try { expect(Array.isArray(await provider.events())).toBe(true); }
    finally { await provider.dispose(); }
  } finally { await ordinary.dispose(); await synthetic.dispose(); }
});

test('Google certificate mount validation refuses write access and unrelated volumes', () => {
  const mount = { Type: 'volume', Source: '/owned/google-tls', Destination: '/run/google-fixture-tls', RW: false };
  // Deliberately partial: the validator consumes only Config/Mounts.
  const app = { Mounts: [mount], Config: { Env: ['REQUESTS_CA_BUNDLE=/run/google-fixture-tls/ca.pem'] } } as Parameters<typeof validateGoogleFixtureMounts>[0];
  const mock = { Mounts: [{ ...mount, Destination: '/tls', RW: true },
    { Type: 'bind', Source: '/owned/server.py', Destination: '/srv/server.py', RW: false }],
    Config: { Entrypoint: ['python'] } } as Parameters<typeof validateGoogleFixtureMounts>[1];
  expect(() => validateGoogleFixtureMounts(app, mock)).not.toThrow();
  expect(() => validateGoogleFixtureMounts({ ...app, Mounts: [{ ...mount, RW: true }] }, mock)).toThrow('certificate volume mismatch');
  expect(() => validateGoogleFixtureMounts({ ...app, Mounts: [{ ...mount, Source: '/other/tls' }] }, mock)).toThrow('certificate volume mismatch');
});
