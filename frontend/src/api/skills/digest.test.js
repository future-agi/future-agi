import { createHash, webcrypto } from 'node:crypto';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { skillFilesDigest, skillManifestDigest } from './digest';

const hash = (text) => createHash('sha256').update(text).digest('hex');
beforeEach(() => vi.stubGlobal('crypto', webcrypto));
afterEach(() => vi.unstubAllGlobals());

it('binds file text and the complete comparison manifest to the same digest', async () => {
  const files = [{ path: 'reference.md', content: 'Policy' }, { path: 'SKILL.md', content: 'Instructions' }];
  const manifest = files.map(({ path, content }) => ({ path, sha256: hash(content) }));
  expect(await skillManifestDigest(manifest)).toBe(await skillFilesDigest(files));
  expect(await skillManifestDigest(manifest.slice(0, 1))).not.toBe(await skillFilesDigest(files));
});

it('preserves Python ensure_ascii escaping and code-point path order', async () => {
  const manifest = [{ path: '\u{1f600}.md', sha256: hash('first') }, { path: '.md', sha256: hash('second') }, { path: '\u007f.md', sha256: hash('third') }];
  const canonical = `[["\\u007f.md","${hash('third')}"],["\\ue000.md","${hash('second')}"],["\\ud83d\\ude00.md","${hash('first')}"]]`;
  expect(await skillManifestDigest(manifest)).toBe(`sha256:${hash(canonical)}`);
});

it.each([null, undefined, '', 'not-a-sha', 'A'.repeat(64)])('rejects an incomplete manifest hash %s', async (sha256) => {
  await expect(skillManifestDigest([{ path: 'SKILL.md', sha256 }])).rejects.toThrow('manifest is incomplete');
});
