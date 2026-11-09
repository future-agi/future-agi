import { test, expect } from '@playwright/test';
import { settingsFixtureProfile } from '../flows/gateway/settings-fixture-profile';
import { gatewayProfile } from '../lib/profile';

test('W12 Settings has exactly five local fixtures without widening product profiles', () => {
  for (const profile of ['stock', 'r7alt', 'mcp', 'mcp-catalog', 'mcp-signin']) {
    expect(settingsFixtureProfile(profile)).toBe(profile);
  }
  for (const profile of ['mcp-catalog', 'mcp-signin']) {
    expect(() => gatewayProfile(profile)).toThrow('is not one of');
  }
  for (const profile of ['mcp-otel', 'pricefeed', 'onekey-cat', 'cloud-mode', 'unknown']) {
    expect(() => settingsFixtureProfile(profile)).toThrow();
  }
});
