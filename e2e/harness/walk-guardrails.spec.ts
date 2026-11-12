import { test, expect } from '@playwright/test';
import { checked, fingerprint, frames, frameKind, frameText, gatewaySection, policySection, type Policy } from '../lib/walk/guardrails';
import { declaredOverlay, HARNESS_PATHS, WalkReceipt } from '../lib/walk/receipt';
import { assertValueFreeReceipt } from '../lib/walk/receipt-values.mjs';

test.describe('W18 offline readers', () => {
  test('offline: false and missing evidence fail closed and never retain raw diffs', async ({}, info) => {
    const receipt = new WalkReceipt('W18', info);
    await expect(checked(receipt, 'control', 'storage', async () => false)).rejects.toThrow('W18 assertion failed');
    await expect(checked(receipt, 'missing', 'storage', async () => undefined)).rejects.toThrow('W18 assertion failed');
    const sensitive = 'synthetic-user@example.test';
    await expect(checked(receipt, 'failure', 'storage', async () => { throw new Error(sensitive); })).rejects.toThrow('W18 assertion failed');
    expect(await checked(receipt, 'pass', 'storage', async () => ({ count: 1 }))).toEqual({ count: 1 });
    expect(receipt.verdicts.map(r => r.verdict)).toEqual(['FAIL', 'FAIL', 'FAIL', 'PASS']);
    const json = JSON.stringify(receipt.verdicts);
    expect(json.includes(sensitive)).toBe(false);
    expect(() => assertValueFreeReceipt(json)).not.toThrow();
    expect(fingerprint(sensitive)).toMatch(/^sha256:[a-f0-9]{64}$/);
  });
  test('offline: full SSE parser distinguishes chunks, error and DONE', () => {
    const raw = 'data: {"choices":[{"delta":{"content":"hello"}}]}\r\n\r\ndata: [DONE]\r\n\r\n';
    expect(frames(raw).map(frameKind)).toEqual(['chunk', 'done']);
    expect(frameText(frames(raw))).toBe('hello');
    const refused = frames('data: {"error":{"type":"guardrail_error","code":"content_blocked"}}\n\ndata: [DONE]\n\n');
    expect(refused.map(frameKind)).toEqual(['error:guardrail_error/content_blocked', 'done']);
    expect(frameText(refused)).toBe('');
    expect(() => frameKind({ event: '', data: 'not JSON' })).toThrow();
  });
  test('offline: expected merge preserves last duplicate and null enabled independently', () => {
    const policy: Policy = { id: 'synthetic-id', name: 'minted-policy', scope: 'global', mode: 'enforce', priority: 100,
      is_active: true, checks: [
        { name: 'keyword-blocklist', enabled: false, action: 'block', threshold: 0.2, config: {} },
        { name: 'keyword-blocklist', enabled: false, action: 'log', threshold: 0.4, config: {} },
        { name: 'presidio-pii', enabled: null, action: 'block', threshold: 0.37, config: { language: 'en' } },
      ] };
    expect(policySection([policy])).toEqual({ enabled: true, checks: policy.checks.slice(1).map(c => ({ ...c,
      _policy: policy.id, _policy_name: policy.name, _mode: 'enforce', _scope: 'global' })) });
    expect(gatewaySection([policy])).toEqual({ pipeline_mode: 'parallel', checks: {
      'keyword-blocklist': { enabled: false, action: 'log', confidence_threshold: 0.4 },
      'presidio-pii': { enabled: false, action: 'block', confidence_threshold: 0.37, config: { language: 'en', provider: 'presidio' } },
    } });
  });
  test('offline: overlay is restricted to harness paths and hashes remain value-free', () => {
    const head = 'a'.repeat(40);
    expect(declaredOverlay(head, HARNESS_PATHS.join(' '))).toEqual({ from: head, paths: [...HARNESS_PATHS] });
    for (const path of ['futureagi', 'frontend', 'e2e/../futureagi', 'agentcc-gateway']) {
      expect(() => declaredOverlay(head, `e2e ${path}`)).toThrow('harness paths only');
    }
    expect(() => declaredOverlay('short', 'e2e')).toThrow('full commit id');
    expect(() => assertValueFreeReceipt(JSON.stringify({ overlay: { from: head, paths: ['e2e'] } }))).not.toThrow();
    expect(() => assertValueFreeReceipt(JSON.stringify({ overlay: { paths: [head] } }))).toThrow('key-shaped');
  });
});
