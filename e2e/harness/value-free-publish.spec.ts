import { spawnSync } from 'node:child_process';
import { randomBytes, randomUUID } from 'node:crypto';
import { existsSync, mkdirSync, readdirSync, readFileSync, writeFileSync } from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { gzipSync } from 'node:zlib';
import { test, expect } from '@playwright/test';
import { parse as parseYaml } from 'yaml';
import { stripAnsi, TOKEN_SHAPE, ValueFree, ValueFreeRefusal } from '../lib/value-free';
import { NESTING_BOUND, sanitizeBytes, Scan, strictBase64 } from '../lib/value-free-codec';
import { assemblePublication, refusalLine, STDIO_POINTER } from '../lib/value-free-publish';
import { checkBeforePull, REFUSAL_FORMAT, refusalReasons, refusalRecordText, writeManifest } from '../lib/value-free-seal.mjs';
import { cleanReason, isCleanReason } from '../lib/value-free-vocabulary.mjs';
import { writeZip } from '../lib/value-free-zip';
import { privatePath, removePrivate } from './value-free/private-dir';
import { decode, killChildren, leaks, produced, published, resultOf, runChild, unzip, type ChildRun, type Forbidden } from './value-free/scan';

// Fixtures live in a private directory per test, outside the evidence tree (value-free/private-dir.ts).
test.afterEach(async ({}, testInfo) => removePrivate(testInfo));

// HR-JRN-01 round 4, offline (no stack, no browser): the publication boundary. Each test
// runs value-free/boundary.child.ts once in a value-free child run (value-free/boundary.config.ts:
// line, json, html and blob reporters) with one probe, and reads back only what the run
// publishes: its publish directory (every path, every file, zips opened) and its stdout,
// stderr and the two as one stream. A probe either publishes zero raw values, or is
// refused: exit 1, a publish directory that holds only REFUSED.json naming why, and the
// refusal line as the only output. The launcher's check (lib/value-free-seal.mjs) runs
// last on each, as main's runner and nightly run it before they pull.
// Round 5: the scan (value-free/scan.ts) decodes every layer itself, gzip included, past
// the harness's own bound; encoded payloads must publish tokenized through every layer,
// one too deep or in an encoding the harness does not decode refuses the run, and the
// refusal record is published content: a registered value in a publication file name,
// a diagnostic label, the record's own text or the publish directory's path never
// reaches what is published.
const CONFIG = fileURLToPath(new URL('./value-free/boundary.config.ts', import.meta.url));
const FILE = 'boundary.child.ts';
const ANY_TOKEN = new RegExp(`^${TOKEN_SHAPE.source}$`);

async function probe(name: string, out: string, mode: string, env: Record<string, string> = {}, extra: Forbidden = []) {
  const value = `Boundary probe ${randomBytes(8).toString('hex')}`;
  const id = randomUUID();
  const outside = path.join(out, 'outside', `${randomBytes(4).toString('hex')}.json`);
  mkdirSync(path.dirname(outside), { recursive: true });
  writeFileSync(outside, JSON.stringify({ identity: value, id }));
  const label = value.replace(/\W+/g, '-');
  const publish = mode === 'publish-path' ? path.join(out, label) : path.join(out, 'value-free-publish');
  const probes = { value, id, outside, publish, label, quoted: `Quoted "${randomBytes(6).toString('hex')}" probe`,
    diagnosticLabel: `Diagnostic label ${randomBytes(6).toString('hex')}` };
  const forbidden: Forbidden = [['probe value', value], ['probe id', id], ['probe label', label], ['quoted probe', probes.quoted],
    ['diagnostic label', probes.diagnosticLabel], ['token name', mode === 'token-name' ? 'vf-value-' : value], ...extra];
  // `out` is below the checkout and named from the test title: a registered string in it refuses the run (publishPathParts),
  // as it should. Only `publish-path` mode puts a value in the path on purpose, below `out`.
  for (const [what, text] of forbidden) {
    expect(out.split(path.sep).some((part) => part.includes(text)), `${name}: the probe's output path holds the ${what}`).toBe(false);
  }
  const run: ChildRun = await runChild(name, out, { file: FILE, valueFree: true, probes, config: CONFIG,
    env: { E2E_VALUE_FREE_BOUNDARY: mode, ...(mode === 'publish-path' ? { E2E_VALUE_FREE_PUBLISH: publish } : {}), ...env } })
    .finally(() => killChildren());
  run.publish = publish;
  const scan = await produced(run);
  return { run, value, id, outside, publish, probes, hits: leaks(scan.texts, forbidden), scan };
}

/** A refused run: exit 1, only REFUSED.json published (naming `reason`), only the refusal line printed. */
function expectRefused(r: Awaited<ReturnType<typeof probe>>, reason: Record<string, unknown>) {
  expect(r.hits, `${r.run.name}: published raw values`).toEqual([]);
  expect(r.run.code, `${r.run.name}: the run fails`).toBe(1);
  expect(published(r.run), `${r.run.name}: only the refusal record`).toEqual(['REFUSED.json']);
  expect(refusalReasons(r.publish), `${r.run.name}: the refusal names why`).toContainEqual(expect.objectContaining(reason));
  expect(JSON.stringify(refusalReasons(r.publish))).not.toContain(r.publish);
  const printed = stripAnsi(r.run.stdout + r.run.stderr);
  expect(printed).toContain('value-free: REFUSED');
  expect(printed, `${r.run.name}: the held output is withheld`).not.toContain('vf-boundary');
  expect(checkBeforePull(r.publish).code, `${r.run.name}: the launcher pulls only the refusal record`).toBe(1);
  expect(published(r.run)).toEqual(['REFUSED.json']);
}

/** A published run: exit 0, the allowlisted sources only, sealed, zero raw values. */
function expectPublished(r: Awaited<ReturnType<typeof probe>>) {
  expect(r.hits, `${r.run.name}: published raw values`).toEqual([]);
  expect(r.run.code, `${r.run.name}: exit (stdout ${stripAnsi(r.run.stdout).slice(-400)})`).toBe(0);
  const top = [...new Set(published(r.run).map((e) => e.split(path.sep)[0]))];
  expect(top.every((t) => ['MANIFEST.json', 'attachments', 'html', 'reports', 'runner.log'].includes(t)), top.join(' ')).toBe(true);
  expect(checkBeforePull(r.publish), `${r.run.name}: the seal holds`).toEqual(expect.objectContaining({ code: 0 }));
  const log = readFileSync(path.join(r.publish, 'runner.log'), 'utf8');
  expect(log, `${r.run.name}: the log line, tokenized, in runner.log`).toMatch(new RegExp(`vf-boundary ${TOKEN_SHAPE.source} ${TOKEN_SHAPE.source}`));
  expect(resultOf(r.run, 'boundary child').stdout.map((s) => s.text ?? '').join(''), 'the report points to runner.log').toBe(STDIO_POINTER);
  return log;
}

test('value-free offline: the publication boundary, control: published, sealed, and a write after assembly breaks the seal',
  async ({}, testInfo) => {
    const r = await probe('control', privatePath(testInfo, 'control'), 'control');
    expectPublished(r);
    expect(published(r.run)).toEqual(expect.arrayContaining(['MANIFEST.json', 'runner.log', 'reports/json-1.json',
      'reports/blob-1.zip', 'html/index.html']));
    // The run left raw values elsewhere on disk (its own results.json, html, blob): never published, never pulled.
    expect(existsSync(path.join(r.run.out, 'results.json'))).toBe(true);
    // A late writer (any process, after the runner exits): the launcher's check publishes nothing (it holds no registry,
    // so it cannot scan a refusal record of its own): the directory leaves the pull path, never deleted.
    writeFileSync(path.join(r.publish, 'late.txt'), r.value);
    expect(checkBeforePull(r.publish).code).toBe(1);
    expect(existsSync(r.publish), 'nothing is left to pull').toBe(false);
    expect(published(r.run)).toEqual([]);
    const aside = readdirSync(r.run.out).filter((n) => n.startsWith('.value-free-publish-withheld-'));
    expect(aside, 'moved aside on the machine').toHaveLength(1);
    expect(readFileSync(path.join(r.run.out, aside[0], 'late.txt'), 'utf8')).toBe(r.value);
  });

test('value-free offline: the publication boundary, a test writing into the publish directory refuses the run', async ({}, testInfo) => {
  expectRefused(await probe('write-into-publish', privatePath(testInfo, 'write'), 'write-into-publish'),
    { reason: 'publish-directory-written-before-assembly' });
});

test('value-free offline: the publication boundary, a symlinked or hard-linked attachment refuses the run and is never followed',
  async ({}, testInfo) => {
    for (const mode of ['symlink', 'hardlink']) {
      const r = await probe(mode, privatePath(testInfo, mode), mode);
      expectRefused(r, { reason: 'unsafe-source', source: 'attachment', why: mode });
      expect(JSON.parse(readFileSync(r.outside, 'utf8')).identity, `${mode}: its target is never rewritten`).toBe(r.value);
    }
  });

test('value-free offline: the publication boundary, an env redirect of a report into the publish directory refuses the run',
  async ({}, testInfo) => {
    const out = privatePath(testInfo, 'env-redirect');
    expectRefused(await probe('env-redirect', out, 'env-redirect', { PLAYWRIGHT_HTML_OUTPUT_DIR: path.join(out, 'value-free-publish') }),
      { reason: 'unsafe-reporter-target', reporter: 'html', why: 'it writes into the publish directory' });
  });

test('value-free offline: the publication boundary, a late reporter is refused and its late writes never reach the pull',
  async ({}, testInfo) => {
    const r = await probe('late-reporter', privatePath(testInfo, 'late'), 'late-reporter');
    expect(r.run.code).toBe(1);
    // Its onExit write came after the assembly: the runner's exit check rewrote the refusal. Its exit-hook write came after
    // that: still there when the runner is gone, until the launcher's check.
    const reasons = refusalReasons(r.publish);
    expect(reasons).toContainEqual(expect.objectContaining({ reason: 'unresolvable-reporter', reporter: 'custom reporter' }));
    expect(reasons).toContainEqual(expect.objectContaining({ reason: 'changed-after-assembly' }));
    expect(published(r.run)).toEqual(['REFUSED.json', 'late-exit.txt']);
    expect(checkBeforePull(r.publish).code).toBe(1);
    expect(published(r.run), 'the launcher check publishes nothing').toEqual([]);
    const after = await produced(r.run);
    expect(leaks(after.texts, [['probe value', r.value], ['probe id', r.id]])).toEqual([]);
  });

test('value-free offline: the publication boundary, a json report with a source extension refuses the run (never a warning)',
  async ({}, testInfo) => {
    const r = await probe('source-ext', privatePath(testInfo, 'source-ext'), 'source-ext');
    expectRefused(r, { reason: 'unsafe-reporter-target', reporter: 'json', why: 'it has a source extension' });
    expect(stripAnsi(r.run.stdout + r.run.stderr)).not.toContain('not scrubbing');
  });

test('value-free offline: the publication boundary, an attachment source outside the output directory is published by content',
  async ({}, testInfo) => {
    const r = await probe('external-source', privatePath(testInfo, 'external'), 'external-source');
    expectPublished(r);
    const attachment = resultOf(r.run, 'boundary child').attachments.find((a) => a.name === 'external')!;
    expect(attachment.path, 'under a harness name').toMatch(/^attachments\/\d+\.json$/);
    expect(JSON.parse(readFileSync(path.join(r.publish, attachment.path!), 'utf8')).identity).toMatch(TOKEN_SHAPE);
    expect(JSON.parse(readFileSync(r.outside, 'utf8')).identity, 'the source stays on the machine, as it was').toBe(r.value);
  });

test('value-free offline: the publication boundary, a value split across stdout and stderr writes is published as one stream',
  async ({}, testInfo) => {
    const r = await probe('split', privatePath(testInfo, 'split'), 'split');
    const log = expectPublished(r);
    // Sol's round-3 probe: no stream (stdout, stderr, the two concatenated, runner.log) holds the value or the UUID, whole or
    // rejoined once colours are removed (the hits above), because the runner prints ONE stream, on stdout only.
    expect(r.run.stderr, 'nothing on stderr: the merged stream is the only output').toBe('');
    expect(stripAnsi(r.run.stdout), 'the runner printed what it published').toContain(log.trim().split('\n').slice(-1)[0]);
    expect(log).toContain('non-value interleave');
  });

// No registered string in the title: it names the output directory, and the token-name probe registers `vf-value-`.
test('value-free offline: the publication boundary, a value overlapping the token syntax refuses the run; the bare token prefix does not',
  async ({}, testInfo) => {
    expectRefused(await probe('token-overlap', privatePath(testInfo, 'token-overlap'), 'token-overlap'),
      { reason: 'token-syntax', kind: 'workspace name' });
    const named = await probe('token-name', privatePath(testInfo, 'token-name'), 'token-name');
    expectPublished(named);
    expect(named.hits, 'the name `vf-value-` appears nowhere it published').toEqual([]);
  });

/** A published attachment, decoded from the json report through every layer to its innermost text. */
async function innermost(r: Awaited<ReturnType<typeof probe>>, mode: string): Promise<Record<string, unknown>> {
  const a = resultOf(r.run, 'boundary child').attachments.find((x) => x.name === mode);
  expect(a?.body, `${mode}: the attachment is published with its body`).toBeDefined();
  const body = JSON.parse(decode(a!.body!));
  if (mode === 'encoded-json') return { identity: decode(body.buffer) };
  if (mode === 'encoded-zip') {
    const entries = await unzip(Buffer.from(body.buffer, 'base64'));
    return Object.fromEntries(entries.map((e) => [e.name, e.data.toString('utf8')]));
  }
  const note = JSON.parse(body.note);
  return { deeper: JSON.parse(decode(note.payload.base64)).deeper, quoted: note.quoted };
}

test('value-free offline: the publication boundary, encoded payloads are sanitized through every layer and published',
  async ({}, testInfo) => {
    for (const mode of ['encoded-json', 'encoded-zip', 'nested-json']) {
      const r = await probe(mode, privatePath(testInfo, mode), mode);
      expectPublished(r);
      // The end-to-end regression: the final published attachment, decoded layer by layer, carries the token where the
      // value was (sanitized, not dropped), in the json report and, read by the scan, in the html and blob reports.
      const inner = await innermost(r, mode);
      expect(Object.keys(inner).length, mode).toBeGreaterThan(0);
      for (const [field, text] of Object.entries(inner)) expect(text, `${mode}: ${field}`).toMatch(ANY_TOKEN);
      const isInnermost = (t: { where: string; text: string }) => mode === 'encoded-json' ? ANY_TOKEN.test(t.text)
        : mode === 'encoded-zip' ? t.where.endsWith('> identity.txt') && ANY_TOKEN.test(t.text)
        : new RegExp(`^\\{"deeper":"${TOKEN_SHAPE.source.replace(/^\\b|\\b$/g, '')}"\\}$`).test(t.text);
      for (const kind of ['html/index.html', 'reports/blob-1.zip']) {
        expect(r.scan.texts.some((t) => t.where.startsWith(kind) && t.where.includes('(base64 inside)') && isInnermost(t)),
          `${mode}: the ${kind} copy, decoded layer by layer, holds the token`).toBe(true);
      }
    }
  });

test('value-free offline: the publication boundary, a payload past the nesting bound or in an unsupported encoding refuses the run',
  async ({}, testInfo) => {
    expectRefused(await probe('too-deep', privatePath(testInfo, 'too-deep'), 'too-deep'), { reason: 'nesting-bound' });
    expectRefused(await probe('unsupported', privatePath(testInfo, 'unsupported'), 'unsupported'), { reason: 'unsupported-encoding' });
  });

// Round 6 (Sol's round-5 P2 #1): an object key that is itself a JSON document is read like a string value. The probe's
// quoted value is the innermost of two such keys, escaped too often for the registry's two forms; the scan (value-free/
// scan.ts) reads keys as documents too.
test('value-free offline: the publication boundary, a JSON document inside an object key is sanitized through every layer and published',
  async ({}, testInfo) => {
    const r = await probe('nested-key', privatePath(testInfo, 'nested-key'), 'nested-key');
    expectPublished(r);
    // The final published attachment, decoded key by key: the token where the value was.
    const a = resultOf(r.run, 'boundary child').attachments.find((x) => x.name === 'nested-key');
    expect(a?.body, 'the attachment is published with its body').toBeDefined();
    const outer = JSON.parse(decode(a!.body!));
    expect(JSON.parse(Object.keys(JSON.parse(Object.keys(outer)[0]))[0]).identity, 'the json report copy').toMatch(ANY_TOKEN);
    const innermost = new RegExp(`^\\{"identity":"${TOKEN_SHAPE.source.replace(/^\\b|\\b$/g, '')}"\\}$`);
    for (const kind of ['reports/json-1.json', 'html/index.html', 'reports/blob-1.zip']) {
      expect(r.scan.texts.some((t) => t.where.startsWith(kind) && t.where.includes('(json inside a key)') && innermost.test(t.text)),
        `the ${kind} copy, read key by key, holds the token`).toBe(true);
    }
  });

test('value-free offline: the publication boundary, an object key nested past the bound refuses the run', async ({}, testInfo) => {
  expectRefused(await probe('deep-key', privatePath(testInfo, 'deep-key'), 'deep-key'), { reason: 'nesting-bound' });
});

test('value-free offline: the refusal record is published content: a value in a publication file name or a diagnostic label never reaches it',
  async ({}, testInfo) => {
    // `runner.log` names a file of every publication: the scan refuses it, and the record says where by category only.
    const named = await probe('filename-value', privatePath(testInfo, 'filename'), 'filename-value', {}, [['file name value', 'runner.log']]);
    expectRefused(named, { reason: 'scan-hit', area: 'runner-log', place: 'name' });
    // A registration label is diagnostic text the run chose: the record and the refusal line name a fixed kind instead.
    const labelled = await probe('label', privatePath(testInfo, 'label'), 'label');
    expectRefused(labelled, { reason: 'short-value', kind: 'value', length: 5 });
    for (const r of [named, labelled]) {
      const record = readFileSync(path.join(r.publish, 'REFUSED.json'), 'utf8');
      expect(Object.keys(JSON.parse(record)).sort(), `${r.run.name}: fixed fields only`).toEqual(['format', 'reasons', 'status']);
    }
  });

test('value-free offline: a refusal record that cannot be clean, or a publish path holding a value, publishes nothing',
  async ({}, testInfo) => {
    // The record's own format text registered: no clean record can be written, so nothing is.
    const text = await probe('refusal-text', privatePath(testInfo, 'refusal-text'), 'refusal-text', {}, [['refusal text', REFUSAL_FORMAT]]);
    // The publish directory's own name registered: nothing can be published under it, the record included.
    const where = await probe('publish-path', privatePath(testInfo, 'publish-path'), 'publish-path');
    for (const r of [text, where]) {
      expect(r.hits, `${r.run.name}: published raw values`).toEqual([]);
      expect(r.run.code, `${r.run.name}: the run fails`).toBe(1);
      expect(existsSync(r.publish), `${r.run.name}: nothing is published`).toBe(false);
      expect(stripAnsi(r.run.stdout + r.run.stderr)).toContain('value-free: REFUSED');
      expect(stripAnsi(r.run.stdout + r.run.stderr), `${r.run.name}: the held output is withheld`).not.toContain('vf-boundary');
      expect(checkBeforePull(r.publish), `${r.run.name}: the launcher pulls nothing`).toEqual(expect.objectContaining({ code: 1 }));
    }
  });

test('value-free offline: the seal check publishes nothing it cannot vouch for and never touches a directory that holds sources',
  async ({}, testInfo) => {
    const base = privatePath(testInfo, 'seal');
    const at = (name: string, files: Record<string, string>) => {
      const dir = path.join(base, name);
      mkdirSync(dir, { recursive: true });
      for (const [file, text] of Object.entries(files)) writeFileSync(path.join(dir, file), text);
      return dir;
    };
    const record = (reasons: object[]) => JSON.stringify({ format: REFUSAL_FORMAT, status: 'refused', reasons });
    // A refusal record of fixed codes, lengths, kinds and file:line: kept, and the only thing pulled.
    const clean = at('clean', { 'REFUSED.json': record([{ reason: 'short-value', kind: 'workspace name', length: 5, at: 'x.spec.ts:1' }]) });
    expect(checkBeforePull(clean).code).toBe(1);
    expect(readdirSync(clean)).toEqual(['REFUSED.json']);
    // One carrying a location string (a value can hide in it), or any field outside the fixed vocabulary: nothing.
    for (const [name, reasons] of [['where', [{ reason: 'scan-hit', kind: 'value', length: 9, where: 'runner.log (path)' }]],
      ['why', [{ reason: 'unsafe-source', source: 'attachment', why: 'Boundary probe 0011223344556677' }]],
      ['at', [{ reason: 'short-value', kind: 'value', length: 5, at: 'Boundary probe 00112233' }]]] as const) {
      const dir = at(`tampered-${name}`, { 'REFUSED.json': record([...reasons]) });
      expect(checkBeforePull(dir).code, name).toBe(1);
      expect(existsSync(dir), `${name}: nothing is left to pull`).toBe(false);
    }
    // A foreign directory at the publish path: moved off the pull path on the machine, never deleted.
    const foreign = at('foreign', { 'a.txt': 'Boundary probe 0011223344556677' });
    expect(checkBeforePull(foreign).code).toBe(1);
    expect(existsSync(foreign)).toBe(false);
    const aside = readdirSync(base).filter((n) => n.startsWith('.foreign-withheld-'));
    expect(aside).toHaveLength(1);
    expect(readdirSync(path.join(base, aside[0]))).toEqual(['a.txt']);
    // A directory that holds sources (a checkout or a package): never touched.
    const sources = at('sources', { 'package.json': '{}', 'x.ts': 'export {};' });
    expect(checkBeforePull(sources).code).toBe(1);
    expect(readdirSync(sources).sort()).toEqual(['package.json', 'x.ts']);
  });

test('value-free offline: the CI report upload takes the sealed html/ (or the refusal record alone) in value-free mode, off unchanged',
  async ({}, testInfo) => {
    const repo = fileURLToPath(new URL('../..', import.meta.url));
    type Step = { name?: string; id?: string; if?: string; run?: string; uses?: string; with?: Record<string, unknown>;
      'working-directory'?: string };
    const steps = (parseYaml(readFileSync(path.join(repo, '.github/workflows/e2e-ci.yml'), 'utf8')).jobs.e2e.steps) as Step[];
    const upload = steps.findIndex((st) => st.name === 'Upload report');
    const seal = steps.findIndex((st) => st.id === 'value_free_seal');
    expect(seal, 'a seal check runs first, right before the upload').toBe(upload - 1);
    expect(steps[seal].if).toBe("${{ !cancelled() && env.E2E_VALUE_FREE == '1' }}");
    // Off (E2E_VALUE_FREE unset): the same step, the same report directory, the same condition as before.
    expect(steps[upload].uses).toBe('actions/upload-artifact@v4');
    expect(steps[upload].if).toBe("${{ !cancelled() && (env.E2E_VALUE_FREE != '1' || steps.value_free_seal.outputs.path != '') }}");
    expect(steps[upload].with!.path).toBe("${{ env.E2E_VALUE_FREE == '1' && (steps.value_free_seal.outputs.path || " +
      "'e2e/value-free-publish/.nothing-to-upload') || 'e2e/playwright-report' }}");
    // The seal step's own script, as the runner runs it (bash -eo pipefail, in its working directory), against each kind of
    // publish directory. Round 8: it is the publication choke point; it reads the run's registries (one adopted run that
    // registered nothing here) and the held suite output, and prints exactly what it scanned, or one STATUS constant.
    const base = privatePath(testInfo, 'ci');
    const check = (name: string, make?: (dir: string) => void) => {
      const pub = path.join(base, name);
      if (make) { mkdirSync(pub, { recursive: true }); make(pub); }
      const output = path.join(base, `${name}.out`);
      writeFileSync(output, '');
      const registries = path.join(base, `${name}-registries`);
      mkdirSync(path.join(registries, 'run'), { recursive: true });
      writeFileSync(path.join(registries, 'run', '1.jsonl'), '');
      const held = path.join(base, `${name}-held.log`);
      writeFileSync(held, 'suite output\n');
      const r = spawnSync('bash', ['--noprofile', '--norc', '-eo', 'pipefail', '-c', steps[seal].run!],
        { cwd: path.join(repo, steps[seal]['working-directory'] ?? ''), encoding: 'utf8',
          env: { ...process.env, E2E_VALUE_FREE: '1', E2E_VALUE_FREE_PUBLISH: pub, GITHUB_OUTPUT: output,
            E2E_VALUE_FREE_REGISTRIES: registries, E2E_VALUE_FREE_HELD: held } });
      return { pub, code: r.status, output: readFileSync(output, 'utf8'), printed: [r.stdout, r.stderr] };
    };
    const publication = (dir: string) => {
      mkdirSync(path.join(dir, 'html'));
      writeFileSync(path.join(dir, 'html', 'index.html'), '<p>report</p>');
      writeFileSync(path.join(dir, 'runner.log'), 'log\n');
      writeManifest(dir);
    };
    const intact = check('intact', publication);
    expect(intact.code, 'an intact publication').toBe(0);
    expect(intact.printed, 'the held output, then the clean constant').toEqual(['suite output\nVFOK\n', '']);
    expect(intact.output).toBe(`path=${path.join(intact.pub, 'html')}\n`);
    const refused = check('refused', (dir) => writeFileSync(path.join(dir, 'REFUSED.json'),
      JSON.stringify({ format: REFUSAL_FORMAT, status: 'refused', reasons: [{ reason: 'short-value', kind: 'value', length: 5 }] })));
    expect(refused.code, 'a refused run fails the job').toBe(1);
    expect(refused.printed).toEqual(['suite output\nVFRR\n', '']);
    expect(refused.output, 'and uploads its refusal record alone').toBe(`path=${path.join(refused.pub, 'REFUSED.json')}\n`);
    const broken = check('broken', (dir) => { publication(dir); writeFileSync(path.join(dir, 'html', 'late.html'), 'late'); });
    expect(broken.code, 'a broken seal fails closed').toBe(1);
    expect(broken.printed, 'and prints only its constant').toEqual(['VFE4\n', '']);
    expect(broken.output, 'and uploads nothing').toBe('');
    expect(existsSync(broken.pub)).toBe(false);
    const missing = check('missing');
    expect([missing.code, missing.output, missing.printed]).toEqual([1, '', ['VFE4\n', '']]);
    const noHtml = check('no-html', (dir) => { writeFileSync(path.join(dir, 'runner.log'), 'log\n'); writeManifest(dir); });
    expect([noHtml.code, noHtml.output, noHtml.printed], 'a publication without its html report uploads nothing')
      .toEqual([1, '', ['VFE4\n', '']]);
  });

test('value-free offline: the codec sanitizes and scans the same layers, to its stated bound, and refuses what it cannot decode', () => {
  const value = `Codec probe ${randomBytes(8).toString('hex')}`;
  const vf = new ValueFree(randomBytes(32).toString('hex')).register(value);
  const scan = (buf: Buffer) => { const s = new Scan(vf.values()); s.bytes(buf); return [...s.findings.values()]; };
  const b64 = (data: string | Buffer) => Buffer.from(data).toString('base64');
  const layers = (inner: string | Buffer, n: number) => {
    let text = JSON.stringify({ buffer: b64(inner) });
    for (let i = 1; i < n; i++) text = JSON.stringify({ buffer: b64(text) });
    return Buffer.from(text);
  };
  // Exactly NESTING_BOUND layers: sanitized through all of them, and the scan of the result is clean; the raw input is not.
  const deep = layers(value, NESTING_BOUND);
  expect(scan(deep), 'the scan reads the value through every layer').toContainEqual(expect.objectContaining({ reason: 'scan-hit' }));
  const clean = sanitizeBytes(deep, vf);
  expect('bytes' in clean && scan(clean.bytes), 'sanitized, nothing left').toEqual([]);
  // One more layer: refused by both, never silently stopped.
  expect(sanitizeBytes(layers(value, NESTING_BOUND + 1), vf)).toEqual(expect.objectContaining({ problem: 'nesting-bound' }));
  expect(scan(layers('plain', NESTING_BOUND + 1))).toContainEqual(expect.objectContaining({ reason: 'nesting-bound' }));
  // A zip inside a field, from its bytes; gzip, an encoding the codec does not decode, is refused; so is a zip it cannot read.
  const zipped = layers(writeZip([{ name: 'identity.txt', data: Buffer.from(value) }]), 1);
  const unzipped = sanitizeBytes(zipped, vf);
  expect('bytes' in unzipped && scan(unzipped.bytes)).toEqual([]);
  expect(sanitizeBytes(layers(gzipSync(Buffer.from(value)), 1), vf)).toEqual(expect.objectContaining({ problem: 'unsupported-encoding' }));
  expect(scan(layers(gzipSync(Buffer.from(value)), 1))).toContainEqual(expect.objectContaining({ reason: 'unsupported-encoding' }));
  const broken = writeZip([{ name: 'a.txt', data: Buffer.from(value) }]).subarray(0, 40);
  expect(sanitizeBytes(layers(broken, 1), vf)).toEqual(expect.objectContaining({ problem: 'unreadable-zip' }));
  // A nested JSON document in a string, with a value escaped twice in the outer text.
  const quoted = `Quoted "${randomBytes(4).toString('hex')}" probe`;
  vf.register(quoted);
  const nested = Buffer.from(JSON.stringify({ note: JSON.stringify({ name: quoted }) }));
  expect(nested.toString('utf8')).not.toContain(quoted);
  expect(nested.toString('utf8')).not.toContain(JSON.stringify(quoted).slice(1, -1));
  expect(scan(nested), 'found unescaped').toContainEqual(expect.objectContaining({ reason: 'scan-hit', place: 'nested-json' }));
  const nestedClean = sanitizeBytes(nested, vf);
  expect('bytes' in nestedClean && JSON.parse(JSON.parse(nestedClean.bytes.toString('utf8')).note).name).toMatch(ANY_TOKEN);
  // A json report's binary-typed body is withheld (never decoded); a text one through every layer. A second pass changes nothing.
  const report = Buffer.from(JSON.stringify({ config: {}, suites: [{ specs: [{ tests: [{ results: [{ attachments: [
    { name: 'png', contentType: 'image/png', body: b64(Buffer.from([0x89, 0x50, 0, 1])) },
    { name: 'json', contentType: 'application/json', body: b64(layers(value, 2)) }] }] }] }] }] }));
  const once = sanitizeBytes(report, vf);
  if (!('bytes' in once)) throw new Error('the report is sanitized');
  const [png, json] = JSON.parse(once.bytes.toString('utf8')).suites[0].specs[0].tests[0].results[0].attachments;
  expect(png).toEqual({ name: 'png', contentType: 'text/plain', body: b64('withheld in value-free mode: image/png') });
  expect(json.contentType).toBe('application/json');
  expect(scan(once.bytes)).toEqual([]);
  const twice = sanitizeBytes(once.bytes, vf);
  expect('bytes' in twice && twice.bytes.equals(once.bytes), 'idempotent').toBe(true);
  // Base64 by its alphabet only; anything else is plain text, read as it is.
  expect(strictBase64('not base64!')).toBeNull();
  expect(strictBase64('abcde')).toBeNull();
  expect(strictBase64(b64('x y'))?.toString()).toBe('x y');
});

/** Every string a JSON text holds, object keys included, each JSON document inside a string or a key and each `body`
 * (base64) read again: the round-6 regressions' own reading, not lib/value-free-codec.ts's. */
function stringsIn(text: string, depth = 0): string[] {
  if (depth > 32) throw new Error('nested deeper than this reading reads');
  const out = [text];
  if (!/^\s*[[{]/.test(text)) return out;
  let doc: unknown;
  try { doc = JSON.parse(text); } catch { return out; }
  const visit = (v: unknown): void => {
    if (typeof v === 'string') out.push(...stringsIn(v, depth + 1));
    else if (Array.isArray(v)) v.forEach(visit);
    else if (v && typeof v === 'object') {
      for (const [k, x] of Object.entries(v)) {
        out.push(...stringsIn(k, depth + 1));
        if (k === 'body' && typeof x === 'string') out.push(...stringsIn(Buffer.from(x, 'base64').toString('utf8'), depth + 1));
        visit(x);
      }
    }
  };
  visit(doc);
  return out;
}

test('value-free offline: the codec reads a JSON document inside an object key like a string value, to the same bound', () => {
  const value = `Key probe "${randomBytes(6).toString('hex')}" name`;
  const vf = new ValueFree(randomBytes(32).toString('hex')).register(value);
  const scan = (buf: Buffer) => { const s = new Scan(vf.values()); s.bytes(buf); return [...s.findings.values()]; };
  const recoverable = (text: string) => stringsIn(text).some((s) => s.includes(value));
  // `layers` JSON documents, each the only key of the next, the value innermost: escaped once more per layer outside.
  const keyed = (layers: number) => {
    let doc: object = { identity: value };
    for (let i = 0; i < layers; i++) doc = { [JSON.stringify(doc)]: true };
    return Buffer.from(JSON.stringify(doc));
  };
  // Two layers (Sol's round-5 shape): neither of the registry's two forms is in the text; only reading the keys finds it.
  const two = keyed(2).toString('utf8');
  expect(two).not.toContain(value);
  expect(two).not.toContain(JSON.stringify(value).slice(1, -1));
  expect(recoverable(two), "this test's own reading finds the value").toBe(true);
  expect(scan(keyed(2)), 'the scan reads the key as a document').toContainEqual(expect.objectContaining({ reason: 'scan-hit', place: 'nested-json' }));
  // Sanitized through every key layer up to the bound: nothing to recover, a token where the value was, a clean scan, and
  // a second pass changes nothing.
  for (const layers of [1, 2, NESTING_BOUND]) {
    const r = sanitizeBytes(keyed(layers), vf);
    if (!('bytes' in r)) throw new Error(`${layers} key layers: refused, not sanitized`);
    const text = r.bytes.toString('utf8');
    expect(recoverable(text), `${layers} key layers: nothing left to recover`).toBe(false);
    expect(stringsIn(text).some((s) => ANY_TOKEN.test(s)), `${layers} key layers: the token where the value was`).toBe(true);
    expect(scan(r.bytes), `${layers} key layers: the scan of the result is clean`).toEqual([]);
    const again = sanitizeBytes(r.bytes, vf);
    expect('bytes' in again && again.bytes.equals(r.bytes), `${layers} key layers: idempotent`).toBe(true);
  }
  // One key layer past the bound: refused by both, never read as an opaque name.
  expect(sanitizeBytes(keyed(NESTING_BOUND + 1), vf)).toEqual(expect.objectContaining({ problem: 'nesting-bound' }));
  expect(scan(keyed(NESTING_BOUND + 1))).toContainEqual(expect.objectContaining({ reason: 'nesting-bound' }));
});

test('value-free offline: a json report whose attachment holds a JSON document in an object key is published tokenized and sealed',
  async ({}, testInfo) => {
    const value = `Key probe "${randomBytes(6).toString('hex')}" name`;
    const vf = new ValueFree(randomBytes(32).toString('hex')).register(value);
    // The round-5 counterexample's report: an attachment body (base64) whose only key is a JSON document holding another.
    const payload = { [JSON.stringify({ note: JSON.stringify({ identity: value }) })]: true };
    const report = { config: {}, suites: [{ specs: [{ tests: [{ results: [{ attachments: [{ name: 'nested-key',
      contentType: 'application/json', body: Buffer.from(JSON.stringify(payload)).toString('base64') }] }] }] }] }] };
    const source = privatePath(testInfo, 'source.json');
    mkdirSync(path.dirname(source), { recursive: true });
    writeFileSync(source, JSON.stringify(report));
    expect(stringsIn(readFileSync(source, 'utf8')).some((s) => s.includes(value)), "this test's own reading finds it").toBe(true);
    const publishDir = privatePath(testInfo, 'value-free-publish');
    const assembly = assemblePublication({ plan: { publishDir, writable: true, sources: [{ kind: 'json', label: 'json', path: source }],
      reasons: [] }, vf, runnerLog: 'clean\n', reasons: [] });
    expect(assembly.refused, 'published').toBe(false);
    expect(checkBeforePull(publishDir).code, 'sealed').toBe(0);
    // The sealed publication, read independently through the body and every key: no value, the token in its place.
    const published = readFileSync(path.join(publishDir, 'reports', 'json-1.json'), 'utf8');
    expect(stringsIn(published).some((s) => s.includes(value)), 'the sealed publication holds no registered value').toBe(false);
    const body = JSON.parse(published).suites[0].specs[0].tests[0].results[0].attachments[0].body as string;
    const key = Object.keys(JSON.parse(Buffer.from(body, 'base64').toString('utf8')))[0];
    expect(JSON.parse(JSON.parse(key).note).identity, 'the token where the value was').toMatch(ANY_TOKEN);
  });

test('value-free offline: a refusal record carries only the fixed vocabulary, and a registration label is never in it', () => {
  // Every field cut to the vocabulary: a location string, a free `why`, a path-like `at` and an unknown code never survive.
  expect(cleanReason({ reason: 'scan-hit', kind: 'value', length: 10, where: 'runner.log (path)', area: 'runner-log', place: 'name' }))
    .toEqual({ reason: 'scan-hit', kind: 'value', length: 10, area: 'runner-log', place: 'name' });
  expect(cleanReason({ reason: 'unsafe-source', source: 'attachment', why: 'Boundary probe 00112233' }))
    .toEqual({ reason: 'unsafe-source', source: 'attachment' });
  expect(cleanReason({ reason: 'short-value', kind: 'Diagnostic label 1', length: 5, at: '/home/x/a.ts:1' }))
    .toEqual({ reason: 'short-value', length: 5 });
  expect(cleanReason({ reason: 'anything else' })).toEqual({ reason: 'unlisted-reason' });
  expect(isCleanReason({ reason: 'short-value', kind: 'workspace name', length: 5, at: 'scope-actors.ts:217' })).toBe(true);
  expect(isCleanReason({ reason: 'scan-hit', where: 'html' })).toBe(false);
  expect(Object.keys(JSON.parse(refusalRecordText([{ reason: 'scan-hit', where: 'x' }])))).toEqual(['format', 'status', 'reasons']);
  // A label the caller chose is diagnostic text: the refusal names the fixed kind `value`, and so does its message.
  const label = `Diagnostic label ${randomBytes(6).toString('hex')}`;
  let thrown: unknown;
  try { new ValueFree(randomBytes(32).toString('hex')).registerAs(label, 'Qabcd'); } catch (error) { thrown = error; }
  expect(thrown).toBeInstanceOf(ValueFreeRefusal);
  expect((thrown as ValueFreeRefusal).refusals).toEqual([expect.objectContaining({ what: 'value', refused: 5 })]);
  expect((thrown as Error).message).not.toContain(label);
  expect(refusalLine([{ reason: 'value-in-publish-path', area: 'publish-path', place: 'name' }], true))
    .toMatch(/^value-free: REFUSED\. .*This run publishes nothing and its output is withheld\.\n$/);
});
