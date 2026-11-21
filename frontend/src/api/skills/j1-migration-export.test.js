import { expect, it } from 'vitest';
import { migrationExportFiles, storeZip } from './j1-migration-export';

const value = { resource_id: 'leaf', version_id: 'immutable-version', digest: `sha256:${'a'.repeat(64)}`,
  mapping_receipts: [{ source_resource: 'source-one', relation: 'derived' }],
  archive_base64: btoa('PK\x03\x04synthetic'), exit: { source_modified: false, credentials_revoked: false } };
const text = (blob) => new Promise((resolve, reject) => { const reader = new FileReader(); reader.onload = () => resolve(reader.result); reader.onerror = reject; reader.readAsText(blob); });

it('keeps exact ZIP bytes and a separate mapping receipt without claiming cleanup', async () => {
  const files = migrationExportFiles(value, 'leaf');
  expect(await text(files.archive)).toBe(atob(value.archive_base64));
  const { archive_base64: _zip, ...receipt } = value;
  expect(JSON.parse(await text(files.receipt))).toEqual(receipt);
  expect(files).toMatchObject({ versionId: value.version_id, digest: value.digest, mappingCount: 1 });
});
it('refuses missing mapping data, wrong identity and malformed ZIP before download', () => {
  expect(() => migrationExportFiles(value, 'other')).toThrow();
  expect(() => migrationExportFiles({ ...value, mapping_receipts: undefined }, 'leaf')).toThrow();
  expect(() => migrationExportFiles({ ...value, archive_base64: btoa('not a zip') }, 'leaf')).toThrow();
});

const bytes = (blob) => new Promise((resolve, reject) => { const reader = new FileReader(); reader.onload = () => resolve(new Uint8Array(reader.result)); reader.onerror = reject; reader.readAsArrayBuffer(blob); });

it('stores nested archives byte-for-byte with standard CRC-32 and a complete central directory', async () => {
  const inner = new Uint8Array([0x50, 0x4b, 3, 4, 0, 255, 7]);
  const zip = await bytes(storeZip([{ path: 'skills/a/v1.zip', bytes: inner }, { path: 'check.txt', bytes: new TextEncoder().encode('123456789') }]));
  const view = new DataView(zip.buffer);
  expect(view.getUint32(0, true)).toBe(0x04034b50);
  expect(view.getUint16(8, true)).toBe(0); // stored, never recompressed
  const nameLength = view.getUint16(26, true);
  expect(new TextDecoder().decode(zip.slice(30, 30 + nameLength))).toBe('skills/a/v1.zip');
  expect([...zip.slice(30 + nameLength, 30 + nameLength + inner.length)]).toEqual([...inner]);
  const second = 30 + nameLength + inner.length;
  expect(view.getUint32(second + 14, true)).toBe(0xcbf43926); // CRC-32("123456789")
  const end = zip.length - 22;
  expect(view.getUint32(end, true)).toBe(0x06054b50);
  expect(view.getUint16(end + 10, true)).toBe(2);
  expect(view.getUint32(view.getUint32(end + 16, true), true)).toBe(0x02014b50);
});

it('refuses unsafe export paths', () => {
  expect(() => storeZip([{ path: '../escape.zip', bytes: new Uint8Array() }])).toThrow('Unsafe export path.');
  expect(() => storeZip([{ path: '/abs.zip', bytes: new Uint8Array() }])).toThrow('Unsafe export path.');
});
