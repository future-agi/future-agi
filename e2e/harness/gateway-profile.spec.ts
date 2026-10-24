import { test, expect } from '@playwright/test';
import { GatewayAdmin } from '../lib/gateway-admin';
import { gatewayProfile, nodeFingerprint, PROFILE_FINGERPRINTS } from '../lib/profile';

// The gateway profile's own tests. Offline: the fingerprint read from GET /-/config bodies
// captured from futureagi/agentcc-gateway:e2esa started on each profile's file
// (2026-10-22). Live: the running gateway is the declared profile, so a flow that
// reads its expectations from gatewayProfile() never passes on the wrong stack.
// (grepInvertPattern's profile rule: scripts/quarantine.test.mjs; the inspector's
// profile rule: harness/managed-mock.spec.ts P3, P4, N8-N12.)

const captured = {
  stock: { server: { host: '0.0.0.0', idle_timeout_ms: 120000, max_request_timeout_ms: 300000, port: 8080,
    read_timeout_ms: 5000, write_timeout_ms: 300000 }, logging: { content_mode: 'full', include_bodies: false,
    level: 'info', request_logging: true }, org_sections_enforced: ['alerting', 'budgets', 'rate_limiting', 'tool_policy'] },
  alt: { server: { host: '0.0.0.0', idle_timeout_ms: 90000, max_request_timeout_ms: 300000, port: 8080,
    read_timeout_ms: 7000, write_timeout_ms: 300000 }, logging: { content_mode: 'full', include_bodies: true,
    level: 'warn', request_logging: true }, org_sections_enforced: ['alerting', 'budgets', 'cache', 'rate_limiting', 'tool_policy'] },
};

test('gateway profile offline: each captured node config reads as its own profile, and the two differ', () => {
  expect(nodeFingerprint(captured.stock)).toEqual(PROFILE_FINGERPRINTS.stock);
  expect(nodeFingerprint(captured.alt)).toEqual(PROFILE_FINGERPRINTS.alt);
  const { stock, alt } = PROFILE_FINGERPRINTS;
  // request_logging is on in both (the gateway's default); every other field tells them apart.
  expect(Object.keys(stock).filter((k) => stock[k as keyof typeof stock] === alt[k as keyof typeof alt]))
    .toEqual(['requestLogging']);
});

test('gateway profile live: the running gateway is the declared profile', async () => {
  const admin = await GatewayAdmin.open();
  try {
    expect(nodeFingerprint(await admin.config())).toEqual(PROFILE_FINGERPRINTS[gatewayProfile()]);
  } finally {
    await admin.dispose();
  }
});
