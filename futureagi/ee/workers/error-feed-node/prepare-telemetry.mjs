import {createHash} from 'node:crypto';
import {readFile} from 'node:fs/promises';
import {join} from 'node:path';
import {execFileSync} from 'node:child_process';

// Bundle the locked dependency tree so the Docker install remains network-free.
export async function prepareTelemetry(worker, context, manifest) {
  const source = join(worker, 'telemetry');
  const hash = createHash('sha256');
  for (const file of ['package.json', 'package-lock.json', 'index.mjs']) hash.update(await readFile(join(source, file)));
  const sourceDigest = hash.digest('hex');
  const name = '@future-agi/error-feed-telemetry', version = '0.0.1';
  const filename = 'future-agi-error-feed-telemetry-0.0.1.tgz', file = 'packages/' + filename;
  const previous = manifest.externalPackages?.find(item => item.name === name);
  if (previous?.sourceDigest === sourceDigest) {
    const sha256 = createHash('sha256').update(await readFile(join(context, file))).digest('hex');
    if (previous.file !== file || previous.version !== version || previous.sha256 !== sha256) {
      throw new Error('Telemetry package checksum or version mismatch');
    }
    return previous;
  }
  execFileSync('npm', ['ci', '--ignore-scripts', '--no-audit', '--no-fund'], {cwd: source, stdio: 'pipe'});
  const packed = JSON.parse(execFileSync('npm', ['pack', '--ignore-scripts', '--json',
    '--pack-destination', join(context, 'packages')], {cwd: source, encoding: 'utf8', maxBuffer: 8 * 1024 * 1024}));
  if (packed.length !== 1 || packed[0].filename !== filename) throw new Error('Unexpected telemetry package');
  return {name, version, file, sourceDigest,
    sha256: createHash('sha256').update(await readFile(join(context, file))).digest('hex')};
}
