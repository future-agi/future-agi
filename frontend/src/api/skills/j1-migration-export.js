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
