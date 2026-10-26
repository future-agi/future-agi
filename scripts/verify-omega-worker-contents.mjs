import assert from 'node:assert/strict';
import {createHash} from 'node:crypto';
import {readFileSync} from 'node:fs';
import {spawnSync} from 'node:child_process';
import {workerFiles} from '../futureagi/ee/workers/error-feed-node/image-files.mjs';

// Prove that the source-mounted tests exercise exactly the shipped runtime.
const expected = Object.fromEntries(workerFiles.map(name => [name,
  createHash('sha256').update(readFileSync(new URL('../futureagi/ee/workers/error-feed-node/' + name, import.meta.url))).digest('hex')]));
const result = spawnSync('docker', ['run', '--rm', '-i', '--network', 'none', process.argv[2],
  '--input-type=module', '-e', `
    import assert from 'node:assert/strict';
    import {createHash} from 'node:crypto';
    import {readFileSync,readdirSync} from 'node:fs';
    const expected = JSON.parse(readFileSync(0,'utf8'));
    const files = readdirSync('/app/worker',{recursive:true,withFileTypes:true});
    const actual = files.filter(file=>file.isFile()).map(file=>
      file.parentPath.slice('/app/worker/'.length) + (file.parentPath==='/app/worker'?'':'/') + file.name);
    assert.deepEqual(actual.sort(),Object.keys(expected).sort());
    for(const [name,digest] of Object.entries(expected)) {
      assert.equal(createHash('sha256').update(readFileSync('/app/worker/'+name)).digest('hex'),digest,name);
    }
  `], {input: JSON.stringify(expected), encoding: 'utf8'});
if (result.error) throw result.error;
assert.equal(result.status, 0, result.stderr);
console.log('Shipped worker files exactly match the reviewed source allowlist and bytes');
