import { test, expect } from '@playwright/test';
import { GATEWAY_PROFILES, PRODUCT_GATEWAY_PROFILES, gatewayProfile, walkGatewayProfile,
  type GatewayProfile, type WalkGatewayProfile } from './profile';

// Compile-time compatibility witness: the unchanged product Settings journey
// has five exhaustive Record<GatewayProfile, ...> tables with these exact keys.
const productExpectations: Record<GatewayProfile, string> = {
  stock: 'stock-node', r7alt: 'alternate-node', mcp: 'mcp-node',
};
const productIdentity: (value?: string) => GatewayProfile = gatewayProfile;
const walkIdentity: (value?: string) => WalkGatewayProfile = walkGatewayProfile;

// A future accidental widening must fail tsc, even before runtime tests run.
// @ts-expect-error Walk-only profiles must not become product table keys.
const walkIsNotProduct: GatewayProfile = 'mcp-otel';
void walkIsNotProduct;

// Cloud-only invocation with a config selecting lib/profile-compatibility.spec.ts.
// No browser, stack, provider request or fixture is used by these tests.
test('offline: product profile tables remain exhaustive and every value is indexable', () => {
  expect(Object.keys(productExpectations)).toEqual([...PRODUCT_GATEWAY_PROFILES]);
  for (const profile of PRODUCT_GATEWAY_PROFILES) {
    expect(productIdentity(profile)).toBe(profile);
    expect(productExpectations[productIdentity(profile)]).toBeTruthy();
    expect(walkIdentity(profile)).toBe(profile);
  }
  expect(productIdentity('')).toBe('stock');
});

test('offline: every walk-only declaration is preserved but refused by product-only expectations', () => {
  const extra = GATEWAY_PROFILES.filter(p => !(PRODUCT_GATEWAY_PROFILES as readonly string[]).includes(p));
  expect(extra).toEqual(['mcp-catalog', 'mcp-signin', 'mcp-otel', 'pricefeed', 'onekey-cat', 'cloud-mode']);
  for (const profile of extra) {
    expect(walkIdentity(profile)).toBe(profile);
    expect(() => productIdentity(profile)).toThrow('is not one of stock, r7alt, mcp');
  }
  for (const invalid of ['unknown', ' stock', 'STOCK']) {
    expect(() => productIdentity(invalid)).toThrow('is not one of');
    expect(() => walkIdentity(invalid)).toThrow('is not one of');
  }
});

test('offline: cloud-mode environment is not silently reinterpreted as a product profile', () => {
  const previous = process.env.E2E_PROFILE;
  process.env.E2E_PROFILE = 'cloud-mode';
  try {
    expect(walkGatewayProfile()).toBe('cloud-mode');
    expect(() => gatewayProfile()).toThrow('is not one of stock, r7alt, mcp');
  } finally {
    if (previous === undefined) delete process.env.E2E_PROFILE;
    else process.env.E2E_PROFILE = previous;
  }
});
