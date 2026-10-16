import {gzipSync, gunzipSync} from 'node:zlib';

// The control client permits 8 MiB. Leave room for its request envelope.
export const MAX_CHECKPOINT_BYTES = 7 * 1024 * 1024;
const MAX_EXPANDED_BYTES = 64 * 1024 * 1024;
const ENCODING = 'grouping-checkpoint-gzip/v1';

// Registry events retain full before/after states for validation and replay.
// Compress only the durable envelope; never truncate those states or receipts.
export function encodeCheckpoint(files, {compress = false} = {}) {
  const json = JSON.stringify({files});
  const bytes = Buffer.byteLength(json);
  if (bytes <= MAX_CHECKPOINT_BYTES) return {files};
  if (!compress || bytes > MAX_EXPANDED_BYTES) throw new Error('Grouping checkpoint exceeds bound');
  const checkpoint = {encoding: ENCODING, data: gzipSync(json).toString('base64')};
  if (Buffer.byteLength(JSON.stringify(checkpoint)) > MAX_CHECKPOINT_BYTES) {
    throw new Error('Grouping checkpoint exceeds bound');
  }
  return checkpoint;
}

export function decodeCheckpoint(checkpoint = {}) {
  if (!Object.hasOwn(checkpoint, 'encoding')) return checkpoint;
  if (checkpoint.encoding !== ENCODING || typeof checkpoint.data !== 'string'
      || Buffer.byteLength(JSON.stringify(checkpoint)) > MAX_CHECKPOINT_BYTES) {
    throw new Error('Invalid grouping checkpoint encoding');
  }
  return JSON.parse(gunzipSync(Buffer.from(checkpoint.data, 'base64'),
    {maxOutputLength: MAX_EXPANDED_BYTES}).toString('utf8'));
}
