import test from 'node:test';
import assert from 'node:assert/strict';
import {createHash} from 'node:crypto';
import {copyFile, mkdir, mkdtemp, readFile, rm, writeFile} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {execFileSync, spawnSync} from 'node:child_process';

test('image preparation reuses verified Kafka and Snappy artifacts offline and rejects tampering', async () => {
  const root = await mkdtemp(join(tmpdir(), 'omega-image-test-'));
  try {
    const worker = join(root, 'workers/error-feed-node');
    const context = join(root, '.artifacts/node-worker');
    const source = join(root, 'fixture/package');
    for (const path of [worker, join(context, 'packages'), source]) await mkdir(path, {recursive: true});
    await copyFile(new URL('./prepare-image.mjs', import.meta.url), join(worker, 'prepare-image.mjs'));
    for (const name of ['gateway-provider.mjs', 'worker.mjs', 'daemon.mjs', 'control-client.mjs',
      'coordinator.mjs', 'evidence-store.mjs', 'investigation.mjs', 'audio-inspection.mjs', 'Dockerfile']) {
      await writeFile(join(worker, name), '// fixture\n');
    }
    const externalPackages = [];
    await writeFile(join(source, 'package.json'), JSON.stringify({name: '@future-agi/omega-runtime', version: '0.0.1'}));
    const omegaFile = 'packages/future-agi-omega-runtime-0.0.1.tgz';
    execFileSync('tar', ['-czf', join(context, omegaFile), '-C', join(root, 'fixture'), 'package']);
    const omegaPackage = {name: '@future-agi/omega-runtime', version: '0.0.1', file: omegaFile,
      sha256: createHash('sha256').update(await readFile(join(context, omegaFile))).digest('hex')};
    for (const [name, version] of [['kafkajs', '2.2.4'], ['kafkajs-snappy', '1.1.0'], ['snappyjs', '0.6.1']]) {
      await writeFile(join(source, 'package.json'), JSON.stringify({name, version,
        ...(name === 'kafkajs-snappy' ? {dependencies: {snappyjs: '^0.6.0'}} : {})}));
      const file = `packages/${name}-${version}.tgz`;
      execFileSync('tar', ['-czf', join(context, file), '-C', join(root, 'fixture'), 'package']);
      const sha256 = createHash('sha256').update(await readFile(join(context, file))).digest('hex');
      externalPackages.push({name, version, file, sha256});
    }
    await writeFile(join(context, 'manifest.json'), JSON.stringify({packages: [omegaPackage], externalPackages}));
    await writeFile(join(context, 'package.json'), JSON.stringify({name: 'fixture', version: '1.0.0',
      private: true, dependencies: {'@future-agi/omega-runtime': 'file:' + omegaFile}}));
    const options = {env: {...process.env, npm_config_offline: 'true',
      npm_config_cache: join(root, 'empty-cache')}, encoding: 'utf8', timeout: 30000};
    const result = spawnSync(process.execPath, [join(worker, 'prepare-image.mjs')], options);
    assert.equal(result.status, 0, result.stderr);
    const lock = JSON.parse(await readFile(join(context, 'package-lock.json'), 'utf8'));
    assert.equal(lock.packages['node_modules/@future-agi/omega-runtime'].version, '0.0.1');
    const omegaArtifact = join(context, omegaFile);
    const originalOmega = await readFile(omegaArtifact);
    await writeFile(omegaArtifact, 'tampered Omega fixture');
    const invalidOmega = spawnSync(process.execPath, [join(worker, 'prepare-image.mjs')], options);
    assert.notEqual(invalidOmega.status, 0);
    assert.match(invalidOmega.stderr, /Package checksum mismatch/);
    await writeFile(omegaArtifact, originalOmega);
    const oldIntegrity = lock.packages['node_modules/@future-agi/omega-runtime'].integrity;
    await writeFile(join(source, 'runtime.js'), 'export const revision = 2;\n');
    execFileSync('tar', ['-czf', omegaArtifact, '-C', join(root, 'fixture'), 'package']);
    omegaPackage.sha256 = createHash('sha256').update(await readFile(omegaArtifact)).digest('hex');
    await writeFile(join(context, 'manifest.json'), JSON.stringify({packages: [omegaPackage], externalPackages}));
    const repacked = spawnSync(process.execPath, [join(worker, 'prepare-image.mjs')], options);
    assert.equal(repacked.status, 0, repacked.stderr);
    const refreshed = JSON.parse(await readFile(join(context, 'package-lock.json'), 'utf8'));
    assert.notEqual(refreshed.packages['node_modules/@future-agi/omega-runtime'].integrity, oldIntegrity);
    for (const item of externalPackages) {
      assert.equal(lock.packages['node_modules/' + item.name].version, item.version);
      const artifact = join(context, item.file);
      const original = await readFile(artifact);
      await writeFile(artifact, 'tampered fixture');
      const invalid = spawnSync(process.execPath, [join(worker, 'prepare-image.mjs')], options);
      assert.notEqual(invalid.status, 0);
      assert.match(invalid.stderr, /External package checksum or version mismatch/);
      await writeFile(artifact, original);
    }
  } finally { await rm(root, {recursive: true, force: true}); }
});
