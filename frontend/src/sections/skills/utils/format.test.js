import { expect, it } from 'vitest';
import { paths } from 'src/routes/paths';
import { formatBytes, formatTokens, shortDigest } from './format';

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
