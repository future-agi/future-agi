import axios from 'src/utils/axios';
import { apiPath } from 'src/api/contracts/api-surface';
import { skillV11Result, useSkillV11Mutation } from './testing';

// Exact published-version export; never a moving label or an unpublished draft.
export function migrationExportFiles(value, resourceId) {
  if (value?.resource_id !== resourceId || typeof value.version_id !== 'string'
    || !/^sha256:[a-f0-9]{64}$/.test(value.digest || '')
    || !Array.isArray(value.mapping_receipts) || typeof value.archive_base64 !== 'string') {
    throw new Error('The exact export identity is unavailable. Nothing was downloaded.');
  }
  const bytes = Uint8Array.from(atob(value.archive_base64), (char) => char.charCodeAt(0));
  if (bytes[0] !== 0x50 || bytes[1] !== 0x4b) throw new Error('The exported ZIP is invalid.');
  const { archive_base64: _archive, ...receipt } = value;
  return { archive: new Blob([bytes], { type: 'application/zip' }),
    receipt: new Blob([JSON.stringify(receipt, null, 2)], { type: 'application/json' }),
    versionId: value.version_id, digest: value.digest, mappingCount: value.mapping_receipts.length };
}

export function useMigrationExport(resourceId, number) {
  return useSkillV11Mutation({ send: async (_, config) => {
    if (!Number.isSafeInteger(number) || number < 1) throw new Error('Choose a published version.');
    const value = skillV11Result(await axios.get(apiPath('/model-hub/skills/{resource_id}/versions/{number}/migration-export/', { resource_id: resourceId, number }), config));
    return migrationExportFiles(value, resourceId);
  } });
}

const CRC_TABLE = (() => {
  const table = new Uint32Array(256);
  for (let n = 0; n < 256; n += 1) {
    let c = n;
    for (let k = 0; k < 8; k += 1) c = c & 1 ? 0xedb88320 ^ (c >>> 1) : c >>> 1;
    table[n] = c >>> 0;
  }
  return table;
})();
function crc32(bytes) {
  let c = 0xffffffff;
  for (let i = 0; i < bytes.length; i += 1) c = CRC_TABLE[(c ^ bytes[i]) & 0xff] ^ (c >>> 8);
  return (c ^ 0xffffffff) >>> 0;
}

/** Uncompressed (stored) ZIP. Entries keep their exact bytes, so each nested
 * per-version export archive stays byte-identical to its single download. */
export function storeZip(entries) {
  const encoder = new TextEncoder();
  const locals = [];
  const central = [];
  let offset = 0;
  for (const entry of entries) {
    const name = encoder.encode(entry.path);
    const data = entry.bytes;
    if (!/^[A-Za-z0-9._/-]+$/.test(entry.path) || entry.path.startsWith('/') || entry.path.split('/').includes('..')) throw new Error('Unsafe export path.');
    const crc = crc32(data);
    const local = new DataView(new ArrayBuffer(30));
    local.setUint32(0, 0x04034b50, true); local.setUint16(4, 20, true); local.setUint16(6, 0x0800, true);
    local.setUint16(8, 0, true); local.setUint16(10, 0, true); local.setUint16(12, 0x21, true);
    local.setUint32(14, crc, true); local.setUint32(18, data.length, true); local.setUint32(22, data.length, true);
    local.setUint16(26, name.length, true); local.setUint16(28, 0, true);
    const record = new DataView(new ArrayBuffer(46));
    record.setUint32(0, 0x02014b50, true); record.setUint16(4, 20, true); record.setUint16(6, 20, true); record.setUint16(8, 0x0800, true);
    record.setUint16(10, 0, true); record.setUint16(12, 0, true); record.setUint16(14, 0x21, true);
    record.setUint32(16, crc, true); record.setUint32(20, data.length, true); record.setUint32(24, data.length, true);
    record.setUint16(28, name.length, true); record.setUint32(42, offset, true);
    locals.push(new Uint8Array(local.buffer), name, data);
    central.push(new Uint8Array(record.buffer), name);
    offset += 30 + name.length + data.length;
  }
  const size = central.reduce((total, part) => total + part.length, 0);
  const end = new DataView(new ArrayBuffer(22));
  end.setUint32(0, 0x06054b50, true); end.setUint16(8, entries.length, true); end.setUint16(10, entries.length, true);
  end.setUint32(12, size, true); end.setUint32(16, offset, true);
  return new Blob([...locals, ...central, new Uint8Array(end.buffer)], { type: 'application/zip' });
}

/** Whole-pilot export: every linked skill's current published version, each as
 * its exact single-version archive plus mapping receipt, in one download. */
export function useMigrationCollectionExport() {
  return useSkillV11Mutation({ send: async ({ skillIds }, config) => {
    if (!Array.isArray(skillIds) || !skillIds.length || new Set(skillIds).size !== skillIds.length) throw new Error('Choose the linked skills to export.');
    const encoder = new TextEncoder();
    const entries = [];
    const items = [];
    for (const resourceId of skillIds) {
      const skill = skillV11Result(await axios.get(apiPath('/model-hub/skills/{skill_id}/', { skill_id: resourceId }), config));
      const number = skill.latest_version;
      if (!Number.isSafeInteger(number) || number < 1) throw new Error('A linked skill has no published version. Nothing was downloaded.');
      const value = skillV11Result(await axios.get(apiPath('/model-hub/skills/{resource_id}/versions/{number}/migration-export/', { resource_id: resourceId, number }), config));
      const exported = migrationExportFiles(value, resourceId);
      const { archive_base64: _archive, ...receipt } = value;
      const base = `skills/${resourceId}/v${number}`;
      entries.push({ path: `${base}.zip`, bytes: new Uint8Array(await exported.archive.arrayBuffer()) });
      entries.push({ path: `${base}.mapping.json`, bytes: encoder.encode(JSON.stringify(receipt, null, 2)) });
      items.push({ resource_id: resourceId, name: skill.name, version: number, version_id: value.version_id, digest: value.digest, archive: `${base}.zip`, mapping: `${base}.mapping.json`, mapping_receipts: value.mapping_receipts.length });
    }
    entries.push({ path: 'migration-export.json', bytes: encoder.encode(JSON.stringify({ format: 'futureagi-migration-export-v1', items }, null, 2)) });
    return { archive: storeZip(entries), items };
  } });
}
