import { canonical, exactSet, requireValue, sha256, SHA256 } from './canonical.mjs';

/** Check IDs survive mutation runs, so a random exception is not a fallibility proof. */
export class OracleFailure extends Error {
  constructor(checkId, reason) { super(`${checkId}: ${reason}`); this.checkId = checkId; }
}

export function assertObservation(check, expected, observation) {
  try {
    requireValue(observation && check.sources.includes(observation.source), 'Independent observation source required');
    requireValue(SHA256.test(observation.artifact_sha256), 'Hashed raw observation artifact required');
    const actual = observation.value;
    if (check.kind === 'equal') {
      requireValue(canonical(actual) === canonical(expected), 'Exact value differs');
    } else if (check.kind === 'exact_set') {
      exactSet(actual, expected, check.id);
    } else if (check.kind === 'manifest') {
      // Actual is file bytes read from the filesystem/API, expected is a separately
      // preregistered manifest. Never compare a product-reported digest to itself.
      requireValue(Array.isArray(actual) && Array.isArray(expected), 'Manifest arrays required');
      exactSet(actual.map((f) => f.path), expected.map((f) => f.path), check.id);
      for (const want of expected) {
        const file = actual.find((f) => f.path === want.path);
        requireValue(typeof file.content === 'string', 'Observed UTF-8 content required');
        requireValue(sha256(Buffer.from(file.content, 'utf8')) === want.sha256 && Buffer.byteLength(file.content, 'utf8') === want.size_bytes, 'File bytes differ');
      }
    } else if (check.kind === 'matrix') {
      requireValue(Array.isArray(actual) && Array.isArray(expected), 'Matrix arrays required');
      const key = (row) => `${row.case_id}/${row.arm}/${row.repeat}`;
      exactSet(actual.map(key), expected.map(key), check.id);
      for (const want of expected) {
        const row = actual.find((candidate) => key(candidate) === key(want));
        requireValue(canonical(row) === canonical(want), 'Planned cell differs or is incomplete');
      }
    } else throw new Error('Unknown oracle kind');
    return true;
  } catch {
    // Do not echo expected/actual data; those can contain customer payloads.
    throw new OracleFailure(check.id, 'independent evidence assertion failed');
  }
}
