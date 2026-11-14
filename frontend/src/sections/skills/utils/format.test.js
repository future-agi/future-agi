import { describe, expect, it } from 'vitest';
import { paths } from 'src/routes/paths';
import { allowanceInput, formatBytes, formatExpiry, formatTokens, formatUtcFull, formatWhen, shortDigest } from './format';

it.each([[0, '0 B'], [1023, '1023 B'], [1024, '1 KiB'], [1536, '1.5 KiB'], [2097152, '2 MiB']])('formats %s UTF-8 bytes', (bytes, expected) => expect(formatBytes(bytes)).toBe(expected));
it('marks token counts as estimates and abbreviates only the digest prefix', () => {
  expect(formatTokens(5000)).toBe(`≈ ${(5000).toLocaleString()} tokens`);
  expect(shortDigest('sha256:0123456789abcdef')).toBe('0123456789ab');
});
it('registers the exact skills routes and API keys settings helper', () => {
  expect(paths.dashboard.skills).toMatchObject({ root: '/dashboard/skills' });
  expect(paths.dashboard.skills.detail('id')).toBe('/dashboard/skills/id');
  expect(paths.dashboard.skills.edit('id')).toBe('/dashboard/skills/id/edit');
  expect(paths.dashboard.skills.compare('id')).toBe('/dashboard/skills/id/compare');
  expect(paths.dashboard.settings.apiKeys).toBe('/dashboard/settings/api_keys');
});

describe('dates', () => {
  const now = Date.parse('2026-11-14T00:48:30Z');
  const utc = { now, timeZone: 'UTC' };
  it.each([
    ['2026-11-14T00:48:10Z', 'Just now'],
    ['2026-11-14T00:36:00Z', '12 min ago'],
    ['2026-11-13T21:48:00Z', '3 hours ago'],
    ['2026-11-13T23:48:00Z', '1 hour ago'],
    ['2026-11-14T01:00:30Z', 'In 12 min'],
    ['2026-11-12T00:48:59Z', '12 Nov, 00:48'],
    ['2025-11-12T09:05:00Z', '12 Nov 2025, 09:05'],
  ])('formats %s relative to now as %s, never with seconds', (value, expected) => {
    expect(formatWhen(value, utc)).toBe(expected);
  });
  it('uses the requested time zone for absolute times', () => {
    expect(formatWhen('2026-11-10T23:30:00Z', { now, timeZone: 'Asia/Kolkata' })).toBe('11 Nov, 05:00');
  });
  it.each([
    ['2026-11-21T00:48:30Z', 'Expires in 7 days'],
    ['2026-11-15T00:48:30Z', 'Expires in 1 day'],
    ['2026-11-14T03:48:30Z', 'Expires in 3 hours'],
    ['2026-11-12T00:48:30Z', 'Expired 2 days ago'],
    ['2026-11-14T00:48:20Z', 'Expired just now'],
  ])('describes expiry %s as %s', (value, expected) => {
    expect(formatExpiry(value, { now })).toBe(expected);
  });
  it('gives the full UTC time for tooltips and returns nothing for missing values', () => {
    expect(formatUtcFull('2026-11-14T00:48:46.123Z')).toBe('14 Nov 2026, 00:48 UTC');
    expect(formatUtcFull(undefined)).toBe('');
    expect(formatWhen('not a date', utc)).toBe('');
    expect(formatExpiry(null, { now })).toBe('');
  });
});

it.each([[null, ''], ['', ''], ['20.00000000', '20.00'], ['20', '20.00'], ['20.5', '20.50'], ['12.00000001', '12.00000001'], ['0.125', '0.125']])('shows the allowance %s as %s without losing precision', (value, expected) => {
  expect(allowanceInput(value)).toBe(expected);
});
