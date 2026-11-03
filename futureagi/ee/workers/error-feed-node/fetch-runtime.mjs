import {createHash} from 'node:crypto';
import {spawnSync} from 'node:child_process';
import {copyFile, mkdir, mkdtemp, readFile, rm, writeFile} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {dirname, join, resolve} from 'node:path';
import {fileURLToPath} from 'node:url';

const worker = dirname(fileURLToPath(import.meta.url));
const context = resolve(worker, '../../.artifacts/node-worker');
const name = '@future-agi/omega-runtime';
const version = '0.0.1';
const sha256 = '2a864d6a3f50ffe6f5edbc433c58a9b2381ee1d095bc881e5d91322e4da839fc';
const sourceCommit = 'f542aa2bdf59dc4f14d8bf8cf2edaeb29ce17c1d';
const token = process.env.NODE_AUTH_TOKEN ?? process.env.GITHUB_TOKEN;

if (!token) throw new Error('NODE_AUTH_TOKEN or GITHUB_TOKEN is required to fetch private Omega');
const staging = await mkdtemp(join(tmpdir(), 'omega-registry-fetch-'));
try {
  const npmrc = join(staging, '.npmrc');
  await writeFile(npmrc,
    '@future-agi:registry=https://npm.pkg.github.com\n' +
    '//npm.pkg.github.com/:_authToken=${NODE_AUTH_TOKEN}\n');
  const result = spawnSync('npm', ['pack', `${name}@${version}`, '--json', '--ignore-scripts',
    '--registry=https://npm.pkg.github.com', '--userconfig', npmrc,
    '--cache', join(staging, 'cache'), '--pack-destination', staging], {
    encoding: 'utf8',
    env: {...process.env, NODE_AUTH_TOKEN: token},
  });
  if (result.error) throw result.error;
  if (result.status !== 0) throw new Error(`Private Omega fetch failed (npm exit ${result.status})`);
  const packed = JSON.parse(result.stdout);
  if (packed.length !== 1 || packed[0].name !== name || packed[0].version !== version) {
    throw new Error('GitHub Packages returned an unexpected Omega package');
  }
  const bytes = await readFile(join(staging, packed[0].filename));
  if (createHash('sha256').update(bytes).digest('hex') !== sha256) {
    throw new Error('Private Omega tarball digest differs from the reviewed release');
  }
  const file = `packages/${packed[0].filename}`;
  await mkdir(join(context, 'packages'), {recursive: true});
  await copyFile(join(staging, packed[0].filename), join(context, file));
  let externalPackages = [];
  try {
    externalPackages = JSON.parse(await readFile(join(context, 'manifest.json'), 'utf8')).externalPackages ?? [];
  } catch (error) {
    if (error.code !== 'ENOENT') throw error;
  }
  await writeFile(join(context, 'manifest.json'), JSON.stringify({schemaVersion: 1, sourceCommit,
    packages: [{name, version, file, sha256}], externalPackages}, null, 2) + '\n');
  await writeFile(join(context, 'package.json'), JSON.stringify({name: 'omega-error-feed-installer',
    version: '0.0.0', private: true, dependencies: {[name]: `file:${file}`}}, null, 2) + '\n');
  console.log(`Fetched and verified ${name}@${version} from GitHub Packages`);
} finally {
  await rm(staging, {recursive: true, force: true});
}
