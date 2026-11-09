// HR-JRN-01 round 4 child (value-free/boundary.config.ts; spawned by
// harness/value-free-publish.spec.ts with E2E_VALUE_FREE=1). It registers a synthetic
// value, publishes it raw through a plain attach and a log line as a careless test
// would, then tries the probe E2E_VALUE_FREE_BOUNDARY names against the publication
// boundary:
//   write-into-publish  writes a file into the publish directory during the run
//   symlink, hardlink   attaches (testInfo.attachments.push, no copy) a link to a file
//                       outside that holds the value
//   external-source     attaches a file outside the output directory by path
//   split               splits the value and a UUID across stdout and stderr writes
//   token-overlap       registers a name that overlaps this run's token syntax
//   token-name          registers and publishes the name `vf-value-`
// Round 5, encoded payloads (one JSON attachment each; the json report keeps its body as
// base64, the html report as text, the blob as base64):
//   encoded-json        the value base64'd in a `buffer` field (Sol's round-4 counterexample)
//   encoded-zip         a zip holding the value, base64'd in a `buffer` field
//   nested-json         a JSON document inside a string, whose `base64` field holds JSON with
//                       the value, and a second value with quotes (escaped twice outside)
//   too-deep            the value under more base64 layers than the harness decodes
//   unsupported         the value gzipped, base64'd in a `buffer` field (an encoding the
//                       harness does not decode)
// Round 6, a JSON document inside an object KEY (Sol's round-5 counterexample):
//   nested-key          the value with quotes as the innermost of two JSON documents, each the
//                       only key of the next (escaped too often for the registry's two forms)
//   deep-key            the value under more key layers than the harness decodes
// and the refusal record as published content:
//   filename-value      registers `runner.log`, a publication file name
//   label               registers a value too short to hide under a synthetic diagnostic label
//   refusal-text        registers the refusal record's own format text, then a short value
//   publish-path        registers a value the publish directory's own path holds
// and control, source-ext, env-redirect and late-reporter, whose probe is in the config
// or the environment. The parent reads what the run published.
import { linkSync, mkdirSync, symlinkSync, writeFileSync } from 'node:fs';
import path from 'node:path';
import { gzipSync } from 'node:zlib';
import { test, expect } from '@playwright/test';
import { sharedValueFree, ValueFreeRefusal } from '../../lib/value-free';
import { writeZip } from '../../lib/value-free-scrub';
import { REFUSAL_FORMAT } from '../../lib/value-free-seal.mjs';

const mode = process.env.E2E_VALUE_FREE_BOUNDARY ?? 'control';
const probes = JSON.parse(process.env.E2E_VALUE_FREE_PROBES ?? 'null') as { value: string; id: string; outside: string; publish: string;
  quoted: string; diagnosticLabel: string };
if (!probes) throw new Error('E2E_VALUE_FREE_PROBES is set by harness/value-free-publish.spec.ts');

test('value-free boundary child: a registered value against the publication boundary', async ({}, testInfo) => {
  const vf = sharedValueFree().register(probes.value);
  await testInfo.attach('registered', { contentType: 'text/plain', body: `${probes.value} ${probes.id}` });
  console.log(`vf-boundary ${probes.value} ${probes.id}`);
  if (mode === 'write-into-publish') {
    mkdirSync(probes.publish, { recursive: true });
    writeFileSync(path.join(probes.publish, 'leak.txt'), probes.value);
  } else if (mode === 'symlink' || mode === 'hardlink') {
    const link = testInfo.outputPath(`${mode}.txt`);
    mkdirSync(path.dirname(link), { recursive: true });
    if (mode === 'symlink') symlinkSync(probes.outside, link); else linkSync(probes.outside, link);
    testInfo.attachments.push({ name: mode, path: link, contentType: 'text/plain' });
  } else if (mode === 'external-source') {
    await testInfo.attach('external', { path: probes.outside, contentType: 'application/json' });
  } else if (mode === 'split') {
    // Sol's round-3 probe: pieces of one value across writes, interleaved with the other stream.
    process.stdout.write(probes.value.slice(0, 4));
    process.stdout.write(probes.value.slice(4));
    process.stdout.write('\n');
    process.stderr.write(probes.id.slice(0, 18));
    process.stderr.write(probes.id.slice(18));
    process.stderr.write('\n');
    process.stdout.write(probes.value.slice(0, 4));
    process.stderr.write('non-value interleave\n');
    process.stdout.write(`${probes.value.slice(4)}\n`);
  } else if (mode === 'token-overlap') {
    expect(() => vf.registerAs('workspace name', `vf-${vf.segment}-value-`)).toThrow(ValueFreeRefusal);
  } else if (mode === 'token-name') {
    const name = ['vf', 'value', ''].join('-');
    vf.registerAs('workspace name', name);
    await testInfo.attach('token-name', { contentType: 'text/plain', body: name });
    console.log(name);
  } else if (Object.hasOwn(ENCODED, mode)) {
    vf.register(probes.quoted);
    await testInfo.attach(mode, { contentType: 'application/json', body: ENCODED[mode]() });
  } else if (mode === 'filename-value') {
    vf.registerAs('workspace name', 'runner.log');
    console.log('runner.log');
  } else if (mode === 'label') {
    expect(() => vf.registerAs(probes.diagnosticLabel, 'Qabcd')).toThrow(ValueFreeRefusal);
  } else if (mode === 'refusal-text') {
    vf.registerAs('workspace name', REFUSAL_FORMAT);
    expect(() => vf.registerAs('workspace name', 'Qabcd')).toThrow(ValueFreeRefusal);
  } else if (mode === 'publish-path') {
    vf.registerAs('workspace name', path.basename(probes.publish));
  }
});

const b64 = (data: string | Buffer) => Buffer.from(data).toString('base64');
/** Base64 inside a `buffer` field, `layers` times over. */
const wrapped = (inner: string | Buffer, layers: number): string => {
  let body = JSON.stringify({ buffer: b64(inner) });
  for (let i = 1; i < layers; i++) body = JSON.stringify({ buffer: b64(body) });
  return body;
};
const ENCODED: Record<string, () => string> = {
  'encoded-json': () => wrapped(probes.value, 1),
  'encoded-zip': () => wrapped(writeZip([{ name: 'identity.txt', data: Buffer.from(probes.value) }]), 1),
  'nested-json': () => JSON.stringify({ note: JSON.stringify({ payload: { base64: b64(JSON.stringify({ deeper: probes.value })) },
    quoted: probes.quoted }) }),
  'too-deep': () => wrapped(probes.value, 12),
  unsupported: () => wrapped(gzipSync(Buffer.from(probes.value)), 1),
  'nested-key': () => keyed({ identity: probes.quoted }, 2),
  'deep-key': () => keyed({ identity: probes.value }, 12),
};
/** `doc` as the only key of a JSON document, `layers` times over. */
function keyed(doc: object, layers: number): string {
  let out = doc;
  for (let i = 0; i < layers; i++) out = { [JSON.stringify(out)]: true };
  return JSON.stringify(out);
}
