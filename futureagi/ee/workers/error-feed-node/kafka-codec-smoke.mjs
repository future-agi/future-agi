// Run from the built image with stdin, not a second dependency installation:
// docker run --rm -i --network none IMAGE --input-type=module < this-file
import './worker/daemon.mjs';
import kafkaModule from 'kafkajs';
import assert from 'node:assert/strict';

const {CompressionCodecs, CompressionTypes} = kafkaModule;
const codec = CompressionCodecs[CompressionTypes.Snappy]();
const input = Buffer.from(JSON.stringify({trace_id: 'synthetic', event_kind: 'root_ended'}));
const compressed = await codec.compress({buffer: input});
assert.deepEqual(await codec.decompress(compressed), input);
// The Go producer can use Xerial framing as well as raw Snappy blocks.
const header = Buffer.from('82534e41505059000000000100000001', 'hex');
const length = Buffer.alloc(4);
length.writeUInt32BE(compressed.length);
assert.deepEqual(await codec.decompress(Buffer.concat([header, length, compressed])), input);
console.log('Kafka daemon imports; raw and Xerial Snappy decoding passed');
