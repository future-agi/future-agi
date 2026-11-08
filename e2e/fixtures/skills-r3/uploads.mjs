import { mkdir, writeFile } from 'node:fs/promises';
import { join } from 'node:path';
import { requireValue } from '../../lib/skills-r3/canonical.mjs';
import { uploadCases } from './library.mjs';

// Deliberately small ZIP_STORED fixture writer. Unsafe names exist only inside archive
// bytes; this module never extracts them or writes them as filesystem paths.
function crc32(bytes) {
  let crc = 0xffffffff;
  for (const byte of bytes) {
    crc ^= byte;
    for (let bit = 0; bit < 8; bit++) crc = (crc >>> 1) ^ (crc & 1 ? 0xedb88320 : 0);
  }
  return (crc ^ 0xffffffff) >>> 0;
}
export function zipFixture(entries) {
  requireValue(Array.isArray(entries) && entries.length <= 1001, 'Bounded fixture entries required');
  const local = []; const directory = []; let offset = 0;
  for (const { path, content, mode = 0o100644 } of entries) {
    const name = Buffer.from(path, 'utf8'); const bytes = Buffer.from(content, 'utf8');
    requireValue(name.length <= 1024 && bytes.length <= 1024 * 1024, 'Bounded fixture member required');
    const checksum = crc32(bytes);
    const header = Buffer.alloc(30);
    header.writeUInt32LE(0x04034b50); header.writeUInt16LE(20, 4); header.writeUInt16LE(0x800, 6);
    header.writeUInt16LE(33, 12); header.writeUInt32LE(checksum, 14);
    header.writeUInt32LE(bytes.length, 18); header.writeUInt32LE(bytes.length, 22); header.writeUInt16LE(name.length, 26);
    local.push(header, name, bytes);
    const central = Buffer.alloc(46);
    central.writeUInt32LE(0x02014b50); central.writeUInt16LE(0x314, 4); central.writeUInt16LE(20, 6);
    central.writeUInt16LE(0x800, 8); central.writeUInt16LE(33, 14); central.writeUInt32LE(checksum, 16);
    central.writeUInt32LE(bytes.length, 20); central.writeUInt32LE(bytes.length, 24); central.writeUInt16LE(name.length, 28);
    central.writeUInt32LE((mode * 65536) >>> 0, 38); central.writeUInt32LE(offset, 42);
    directory.push(central, name); offset += header.length + name.length + bytes.length;
  }
  const central = Buffer.concat(directory); const end = Buffer.alloc(22);
  end.writeUInt32LE(0x06054b50); end.writeUInt16LE(entries.length, 8); end.writeUInt16LE(entries.length, 10);
  end.writeUInt32LE(central.length, 12); end.writeUInt32LE(offset, 16);
  return Buffer.concat([...local, central, end]);
}

export function archiveFixtures() {
  const cases = uploadCases();
  return Object.fromEntries(Object.entries({ ...cases,
    symlink: [{ path: 'AGENTS.md', content: 'root' }, { path: 'references/link', content: '../../outside', mode: 0o120777 }],
    unsupported: [{ path: 'AGENTS.md', content: 'root' }, { path: 'assets/example.bin', content: '\0\u0001\u0002' }],
    notices: [{ path: 'repo/skill/SKILL.md', content: 'Synthetic skill' }, { path: 'repo/LICENSE', content: 'Synthetic license evidence, not a rights assertion.' }, { path: 'repo/NOTICE', content: 'Retain this synthetic notice.' }],
  }).map(([name, entries]) => [name, zipFixture(entries)]));
}

/** Writes only predetermined safe fixture names and the valid folder input. */
export async function writeUploadFixtures(destination) {
  await mkdir(destination, { recursive: false });
  await mkdir(join(destination, 'folder'));
  await writeFile(join(destination, 'folder', 'AGENTS.md'), uploadCases().valid[0].content, { flag: 'wx' });
  for (const [name, bytes] of Object.entries(archiveFixtures())) await writeFile(join(destination, `${name}.zip`), bytes, { flag: 'wx' });
}
