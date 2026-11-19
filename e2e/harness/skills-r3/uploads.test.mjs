import test from 'node:test';
import assert from 'node:assert/strict';
import { execFileSync } from 'node:child_process';
import { mkdtemp, readFile, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { archiveFixtures, writeUploadFixtures } from '../../fixtures/skills-r3/uploads.mjs';

// Independent stdlib ZIP reader checks real ZIP bytes/CRC without extracting hostile members.
const probe = `import json,sys,zipfile,stat
with zipfile.ZipFile(sys.argv[1]) as z:
 assert z.testzip() is None
 print(json.dumps([{'path':i.filename,'mode':i.external_attr>>16,'bytes':len(z.read(i))} for i in z.infolist()]))
`;
test('real upload archives retain unsafe metadata, notices, UTF-8 and complete manifest members', async () => {
  const root = await mkdtemp(join(tmpdir(), 'l13-uploads-'));
  try {
    const destination = join(root, 'inputs'); await writeUploadFixtures(destination);
    const read = (name) => JSON.parse(execFileSync('python3', ['-I', '-c', probe, join(destination, `${name}.zip`)], { encoding: 'utf8' }));
    assert.equal(read('traversal')[0].path, '../AGENTS.md');
    assert.equal(read('absolute')[0].path, '/AGENTS.md');
    assert.equal(read('symlink')[1].mode & 0o170000, 0o120000);
    assert.equal(read('pagedManifest').length, 28);
    assert.ok(read('longUtf8')[0].bytes > 100 * 1024);
    assert.deepEqual(read('notices').map((entry) => entry.path), ['repo/skill/SKILL.md', 'repo/LICENSE', 'repo/NOTICE']);
    assert.equal(await readFile(join(destination, 'folder/AGENTS.md'), 'utf8'), '# Synthetic standing instructions\n');
    await assert.rejects(writeUploadFixtures(destination), /EEXIST/);
    for (const [name, bytes] of Object.entries(archiveFixtures())) assert.deepEqual(bytes, archiveFixtures()[name], `${name} must be deterministic`);
  } finally { await rm(root, { recursive: true }); }
});
