import test from 'node:test';
import assert from 'node:assert/strict';
import { readCompleteClosure } from '../../benchmarks/context-hub/drivers/j6.mjs';
import { sha256 } from '../../lib/skills-r3/canonical.mjs';
import { assertObservation } from '../../lib/skills-r3/oracles.mjs';

const ID = '00000000-0000-4000-8000-000000000001';
const VERSION = '00000000-0000-4000-8000-000000000002';
const HASH = `sha256:${'a'.repeat(64)}`;

function fixture(change = () => {}) {
  const contents = { 'AGENTS.md': 'Policy 😀\n', 'skills/refund/SKILL.md': 'Refund 25 units.\n' };
  const files = Object.entries(contents).map(([path, text]) => ({ path, sha256: sha256(Buffer.from(text)), size_bytes: Buffer.byteLength(text) }));
  const calls = [];
  const read = async (path) => {
    calls.push(path);
    const url = new URL(path, 'http://localhost/');
    let response;
    if (!url.pathname.endsWith('/files/')) {
      const offset = Number(url.searchParams.get('file_offset'));
      response = { distribution_blocked: false, descriptor: { links: [] }, manifest: {
        resource_id: ID, version_id: VERSION, version: 1, digest: HASH,
        file_count: 2, file_offset: offset, files: [files[offset]], next_file_offset: offset === 0 ? 1 : null,
      } };
    } else {
      const file = files.find(row => row.path === url.searchParams.get('path'));
      const chars = [...contents[file.path]];
      const offset = Number(url.searchParams.get('offset'));
      const end = Math.min(offset + 8, chars.length);
      response = { ...file, version_id: VERSION, digest: HASH, offset,
        text: chars.slice(offset, end).join(''), total_chars: chars.length,
        next_offset: end === chars.length ? null : end, eof: end === chars.length };
    }
    change(response, calls.length);
    return response;
  };
  return { read, calls, contents, files };
}

test('J6 observes every closure page and UTF-8 file chunk independently', async () => {
  const source = fixture();
  const result = await readCompleteClosure(source.read, ID, 1, new AbortController().signal);
  assert.deepEqual(result.files, Object.entries(source.contents).map(([path, content]) => ({ path, content })));
  assert(source.calls.some(path => path.includes('file_offset=1')));
  assert(source.calls.some(path => path.includes('offset=8&')));
  const check = { id: 'eligible-complete-closure', kind: 'manifest', sources: ['api'] };
  assertObservation(check, source.files, { source: 'api', value: result.files, artifact_sha256: sha256('raw-observation') });
  const wrong = structuredClone(source.files);
  wrong[0].sha256 = '0'.repeat(64);
  assert.throws(() => assertObservation(check, wrong, { source: 'api', value: result.files, artifact_sha256: sha256('raw-observation') }), /eligible-complete-closure/);
});

for (const [name, change] of [
  ['quarantined alternative', row => { if (row.manifest) row.distribution_blocked = true; }],
  ['changed closure page', (row, call) => { if (row.manifest && call === 2) row.manifest.digest = 'changed'; }],
  ['missing closure member', row => { if (row.manifest) row.manifest.file_count = 3; }],
  ['wrong file bytes', row => { if ('text' in row) row.text = row.text.replace('Policy', 'Broken'); }],
  ['incomplete file', row => { if ('text' in row) row.total_chars += 1; }],
  ['nonadvancing page', row => { if (row.manifest) row.manifest.next_file_offset = 0; }],
]) {
  test(`J6 rejects ${name} rather than substituting another release`, async () => {
    await assert.rejects(readCompleteClosure(fixture(change).read, ID, 1, new AbortController().signal));
  });
}
