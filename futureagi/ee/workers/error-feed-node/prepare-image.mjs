import {copyFile, mkdir, readFile, readdir, unlink, writeFile} from 'node:fs/promises';
import {fileURLToPath} from 'node:url';
import {resolve, dirname, join} from 'node:path';
import {createHash} from 'node:crypto';
import {execFileSync} from 'node:child_process';

const worker = dirname(fileURLToPath(import.meta.url));
const context = resolve(worker, '../../.artifacts/node-worker');
const manifest = JSON.parse(await readFile(join(context, 'manifest.json'), 'utf8'));
if (manifest.packages?.length !== 1 || manifest.packages[0].name !== '@future-agi/omega-runtime') {
  throw new Error('Expected one @future-agi/omega-runtime tarball');
}
for (const item of manifest.packages) {
  const digest = createHash('sha256').update(await readFile(join(context, item.file))).digest('hex');
  if (digest !== item.sha256) throw new Error('Package checksum mismatch: ' + item.name);
}
await mkdir(join(context, 'worker'), {recursive: true});
const workerFiles = ['gateway-provider.mjs', 'worker.mjs', 'daemon.mjs', 'control-client.mjs',
  'coordinator.mjs', 'evidence-store.mjs', 'investigation.mjs', 'audio-inspection.mjs'];
for (const name of workerFiles) {
  await copyFile(join(worker, name), join(context, 'worker', name));
}
await copyFile(join(worker, 'Dockerfile'), join(context, 'Dockerfile'));
const installer = JSON.parse(await readFile(join(context, 'package.json'), 'utf8'));
// Pin the pure-JS decoder too: the collector's Kafka records use Snappy.
// Verified tarballs are reused on subsequent offline builds.
const externalPackages = [];
for (const [name, version] of [['kafkajs', '2.2.4'], ['kafkajs-snappy', '1.1.0'], ['snappyjs', '0.6.1']]) {
  const filename = `${name}-${version}.tgz`;
  const file = `packages/${filename}`;
  const previous = manifest.externalPackages?.find(item => item.name === name);
  if (previous) {
    if (previous.version !== version || previous.file !== file
        || createHash('sha256').update(await readFile(join(context, file))).digest('hex') !== previous.sha256) {
      throw new Error('External package checksum or version mismatch: ' + name);
    }
  } else {
    const packed = JSON.parse(execFileSync('npm', ['pack', `${name}@${version}`, '--ignore-scripts', '--json',
      '--pack-destination', join(context, 'packages')], {encoding: 'utf8'}));
    if (packed.length !== 1 || packed[0].filename !== filename) throw new Error('Unexpected external dependency');
  }
  installer.dependencies[name] = 'file:' + file;
  externalPackages.push({name, version, file,
    sha256: createHash('sha256').update(await readFile(join(context, file))).digest('hex')});
}
await writeFile(join(context, 'package.json'), JSON.stringify(installer, null, 2) + '\n');
// npm keeps the previous integrity when a file: tarball is replaced at the same version.
await unlink(join(context, 'package-lock.json')).catch(error => {
  if (error.code !== 'ENOENT') throw error;
});
execFileSync('npm', ['install', '--package-lock-only', '--offline', '--ignore-scripts', '--no-audit', '--no-fund'], {cwd: context});
manifest.externalPackages = externalPackages;
const expectedPackages = new Set([...manifest.packages, ...externalPackages].map(item => item.file.split('/').at(-1)));
for (const file of await readdir(join(context, 'packages'))) {
  if (!expectedPackages.has(file)) throw new Error('Unexpected package in image context: ' + file);
}
await writeFile(join(context, 'manifest.json'), JSON.stringify(manifest, null, 2) + '\n');
await writeFile(join(context, '.dockerignore'), [
  '*', '!Dockerfile', '!.dockerignore', '!package.json', '!package-lock.json',
  '!packages/', '!packages/*.tgz', '!worker/', ...workerFiles.map(name => '!worker/' + name), ''
].join('\n'));
console.log('Prepared local Docker context: ' + context);
