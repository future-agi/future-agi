import { expect, it } from 'vitest';
import { migrationExportFiles } from './j1-migration-export';

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
