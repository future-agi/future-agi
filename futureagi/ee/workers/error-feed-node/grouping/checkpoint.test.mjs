import test from 'node:test';
import assert from 'node:assert/strict';
import {gzipSync} from 'node:zlib';
import {MAX_CHECKPOINT_BYTES, encodeCheckpoint, decodeCheckpoint} from './checkpoint.mjs';

test('simulation checkpoint compression is lossless and legacy envelopes remain readable', () => {
  const small = {'checkpoint.json': {sequence: 1}};
  assert.deepEqual(decodeCheckpoint(encodeCheckpoint(small)), {files: small});
  const files = {...small, 'registry.json': {history: 'repeated history'.repeat(600000)}};
  const encoded = encodeCheckpoint(files, {compress: true});
  assert.equal(encoded.encoding, 'grouping-checkpoint-gzip/v1');
  assert.ok(Buffer.byteLength(JSON.stringify(encoded)) < MAX_CHECKPOINT_BYTES);
  assert.deepEqual(decodeCheckpoint(encoded), {files});
  assert.throws(() => encodeCheckpoint(files), /exceeds bound/);
});

test('checkpoint decompression rejects corrupt, unknown and oversized decoded data', () => {
  assert.throws(() => decodeCheckpoint({encoding: 'unknown', data: ''}), /encoding/);
  assert.throws(() => decodeCheckpoint({encoding: 'grouping-checkpoint-gzip/v1', data: 'corrupt'}));
  const data = gzipSync(Buffer.alloc(64 * 1024 * 1024 + 1, 65)).toString('base64');
  assert.throws(() => decodeCheckpoint({encoding: 'grouping-checkpoint-gzip/v1', data}));
});
