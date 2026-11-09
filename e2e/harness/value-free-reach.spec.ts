import { execFileSync, spawnSync } from 'node:child_process';
import { randomBytes } from 'node:crypto';
import { chmodSync, cpSync, existsSync, lstatSync, mkdirSync, mkdtempSync, readdirSync, readFileSync, realpathSync, statSync, symlinkSync,
  writeFileSync } from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { test, expect, type TestInfo } from '@playwright/test';
import { privatePath, removePrivate } from './value-free/private-dir';
import { leaks, type Forbidden } from './value-free/scan';

// Fixtures live in a private directory per test, outside the evidence tree (value-free/private-dir.ts).
test.afterEach(async ({}, testInfo) => removePrivate(testInfo));

// HR-JRN-01 round 9, offline (no stack, no browser): Sol's two round-8 P2s on the publication choke point
// (lib/value-free-boundary.mjs).
//   1. (I2) The final check reads the held output as its OWN document as well as the exact bytes it emits. A held output
//      that is one pretty-printed JSON document is a document only without the status constant after it, so its base64
//      field's decoded layer was never read and the value crossed in stdout.
//   2. (I1) A pull pattern the check cannot enumerate exactly refuses the whole publication, and NOTHING any declared
//      pattern reaches is left on the pull path: not the files a glob matches (round 8 withheld only the patterns it
//      accepted, and the walk job's fallback tested a glob as a literal path), not a word of a pattern holding a blank,
//      not the whole root an empty pattern names. What crosses is read back independently, the way the walk rig's collect
//      reads a pull (cloud vm/exec/exec-collect.sh): the pattern prefixed with the pull root, split on blanks, each word
//      globbed by bash with globstar, nullglob and dotglob, read with `realpath -s`, a directory whole, regular files only.
//      Its /Users and /Volumes filter is dropped here (these roots live in the test output), so this reading takes more,
//      never less. What is withheld stays on the machine, never deleted.
// Round 10 (Sol's round-9 P2): a pull DECLARED outside the pull root (an absolute path, an escaping one, the root's parent)
// was refused (VFE5, exit 1) but left in place, and the walk rig transferred it. Now:
//   3. the walk launcher refuses such a declaration BEFORE dispatch (the choke point's `declare` check: every declared pull
//      one exact path below the pull root), so no job is created to collect it;
//   4. on the VM, if one is ever declared, the final check withholds what it reaches that the rig transfers (below /Users or
//      /Volumes, exec-collect.sh's add()), outside the pull root too, into the quarantine, never deleted; what the rig never
//      transfers is never moved; the quarantine itself lies outside every directory the rig transfers from;
//   5. so does the walk job wrapper's own fallback.
// Each check below runs with a private TMPDIR (outside the pull root and outside /Users and /Volumes on the walk VM), and
// "withheld" counts the scene and that directory together.
const repo = (rel: string) => fileURLToPath(new URL(`../../${rel}`, import.meta.url));
const BOUNDARY = repo('e2e/lib/value-free-boundary.mjs');
/** The choke point's own output (round 8): one constant, shorter than any registered value. */
const STATUS = { clean: 'VFOK\n', value: 'VFE2\n', entry: 'VFE3\n', failed: 'VFE5\n' };
const EXIT_REL = 'e2e/walk-results/command/command-exit.txt';
const EXPORT_REL = 'e2e/walk-results/g1/p1/vfout';
const PULLS = [`${EXPORT_REL}/**`, EXIT_REL];

const COLLECT = [
  'shopt -s globstar nullglob dotglob || exit 9',
  'root=$1; shift',
  'cd "$root" || exit 9',
  'for pattern in "$@"; do',
  '  [[ "$pattern" == /* ]] || pattern="$root/$pattern"',
  '  for match in $pattern; do',
  `    if [[ -d "$match" && ! -L "$match" ]]; then find "$match" -type f -print0; else printf '%s\\0' "$match"; fi`,
  '  done',
  'done',
  'exit 0',
].join('\n');
/** Every file the rig's collect would take for `pulls` from `root` (absolute paths, sorted). */
function rigCollect(root: string, pulls: string[]): string[] {
  const r = spawnSync('bash', ['--noprofile', '--norc', '-c', COLLECT, 'collect', root, ...pulls], { maxBuffer: 1 << 28 });
  expect(r.status, 'the rig reading runs').toBe(0);
  const found = new Set<string>();
  for (const f of r.stdout.toString('utf8').split('\0').slice(0, -1)) {
    const at = path.resolve(root, f); // realpath -s: lexical (a later word, and what find prints of it, is relative to the root)
    try {
      if (lstatSync(at).isFile()) found.add(at); // a regular file, never a link
    } catch { /* not there */ }
  }
  return [...found].sort();
}

/** Every regular file below `dir` (links never followed) holding `value`: what was withheld is still on the machine. */
function holding(dir: string, value: string): string[] {
  if (!existsSync(dir)) return [];
  return readdirSync(dir, { withFileTypes: true }).flatMap((e) => {
    const at = path.join(dir, e.name);
    if (e.isDirectory()) return holding(at, value);
    return e.isFile() && readFileSync(at, 'utf8').includes(value) ? [at] : [];
  });
}

/** A pull root standing for the checkout: a walk export and an exit file (PULLS), a registries root beside it holding
 * `value`, and a held output. */
function scene(base: string, value: string, held: string) {
  const root = path.join(base, 'checkout');
  for (const dir of [path.join(root, '.git'), path.join(root, EXPORT_REL, 'reports'), path.join(root, path.dirname(EXIT_REL))]) {
    mkdirSync(dir, { recursive: true });
  }
  writeFileSync(path.join(root, EXPORT_REL, 'runner.log'), 'clean log\n');
  writeFileSync(path.join(root, EXPORT_REL, 'reports', 'counts-1.json'), '{"planned":1}\n');
  writeFileSync(path.join(root, EXIT_REL), '0\n');
  const registries = path.join(base, 'registries');
  mkdirSync(path.join(registries, 'p1'), { recursive: true });
  writeFileSync(path.join(registries, 'p1', '101.jsonl'), `${JSON.stringify(value)}\n`);
  const heldFile = path.join(base, 'held.log');
  writeFileSync(heldFile, held);
  return { base, root, registries, held: heldFile, tmp: privateTmp() };
}
type Scene = ReturnType<typeof scene>;
const check = (s: Scene, pulls: string[], env?: NodeJS.ProcessEnv) => spawnSync(process.execPath,
  [BOUNDARY, 'publish', '--registries', s.registries, '--print', s.held, '--', ...pulls],
  { cwd: s.root, encoding: 'utf8', env: { ...(env ?? process.env), TMPDIR: s.tmp } });
/** A fresh private temporary directory (a check's TMPDIR): below the system one, outside every pull root here. */
function privateTmp(): string {
  const dir = mkdtempSync(path.join(os.tmpdir(), 'vf-reach-'));
  chmodSync(dir, 0o700);
  return dir;
}
/** How many files holding `value` are still on this machine, in the scene or the private TMPDIR: withheld, never deleted. */
const withheld = (s: { base: string; tmp: string }, value: string) => holding(s.base, value).length + holding(s.tmp, value).length;
/** A path the walk rig transfers from (exec-collect.sh add(): only /Users and /Volumes paths travel). */
const transported = (p: string) => /^\/(Users|Volumes)\//.test(path.resolve(p));
const synthetic = () => `Synthetic reach value ${randomBytes(8).toString('hex')}`;

// Sol round-8 P2 1. The value is base64'd inside a JSON document in a `buffer` (or report attachment `base64`) field.
const PAYLOADS: { label: string; held: (b64: string) => string; refused: boolean }[] = [
  { label: 'a pretty-printed JSON payload holding no value (control)', refused: false,
    held: () => `${JSON.stringify({ buffer: Buffer.from('{"plain":"text"}').toString('base64'), note: 'clean' }, null, 2)}\n` },
  { label: 'a compact JSON payload (control)', held: (b) => `${JSON.stringify({ buffer: b })}\n`, refused: true },
  { label: 'a pretty-printed JSON payload', held: (b) => `${JSON.stringify({ buffer: b }, null, 2)}\n`, refused: true },
  { label: 'a pretty-printed JSON payload with no final newline', held: (b) => JSON.stringify({ buffer: b }, null, 2), refused: true },
  { label: 'a pretty-printed json report, its attachment base64 field', refused: true,
    held: (b) => `${JSON.stringify({ config: {}, suites: [], attachments: [{ name: 'a', base64: b }] }, null, 2)}\n` },
];
/** An independent decode of every base64 report field in `text`. */
const decodedHit = (text: string, value: string) => [...text.matchAll(/"(?:buffer|base64|body|data)"\s*:\s*"([A-Za-z0-9+/=_-]+)"/g)]
  .some((m) => Buffer.from(m[1], 'base64').toString('utf8').includes(value));

test.describe('value-free offline: the final check reads the held output as its own document', () => {
  test.describe.configure({ mode: 'parallel' });
  for (const [i, c] of PAYLOADS.entries()) {
    test(`value-free offline: the final check reads the held output as its own document, case ${i + 1}: ${c.label}`,
      async ({}, testInfo) => {
        const value = synthetic();
        const b64 = Buffer.from(JSON.stringify({ deeper: value })).toString('base64');
        const held = c.held(b64);
        const s = scene(privatePath(testInfo, 'payload'), value, held);
        const r = check(s, PULLS);
        const label = `case ${i + 1}`;
        if (!c.refused) {
          expect([r.status, r.stdout, r.stderr], `${label}: exactly the bytes scanned`).toEqual([0, `${held}${STATUS.clean}`, '']);
          return;
        }
        expect([r.status, r.stdout, r.stderr], `${label}: refused, one constant printed`).toEqual([1, STATUS.value, '']);
        expect(decodedHit(r.stdout, value), `${label}: no decoded layer of stdout holds the value`).toBe(false);
        expect(leaks([{ where: `${label}: stdout`, text: r.stdout }], [['registered name', value]]), label).toEqual([]);
        expect(rigCollect(s.root, PULLS), `${label}: nothing is left to pull`).toEqual([]);
        expect(existsSync(s.registries), `${label}: the registries are removed`).toBe(false);
      });
  }
});

// Sol round-8 P2 2: each declared pattern below (with the walk export and the exit file) refuses the whole publication;
// the files named in `files` hold the registered value. `bash: false` runs the check with no bash on its PATH (the rig's
// reading of the pulls unavailable): a refusal of a check that failed, and every target it can bound below the root still
// leaves the pull path.
const REACH: { label: string; files: string[]; pattern: (root: string) => string; code?: string; bash?: false }[] = [
  { label: "Sol's round-8 glob, a file it matches holding the value", files: ['reports/p1/raw.log'], pattern: () => 'reports/*/raw.log' },
  { label: 'a glob in the first component', files: ['reports/p1/raw.log', 'deep/a/b/raw.log'], pattern: () => '**/raw.log' },
  { label: 'a pattern holding a blank, which the rig reads as two', files: ['reports/p1/raw.log'],
    pattern: () => 'reports/p1/raw.log extra/**' },
  { label: 'an absolute pattern inside the pull root', files: ['reports/p1/raw.log'], pattern: (root) => `${root}/reports/*/raw.log` },
  { label: 'an empty pattern, which the rig reads as the whole pull root', files: ['reports/p1/raw.log'], pattern: () => '' },
  { label: 'a glob whose literal part holds the other pulls', files: ['e2e/x/x.txt'], pattern: () => 'e2e/*/x.txt' },
  { label: "Sol's round-8 glob, the rig's reading unavailable", files: ['reports/p1/raw.log'], pattern: () => 'reports/*/raw.log',
    code: STATUS.failed, bash: false },
];

test.describe('value-free offline: a pull pattern the final check cannot enumerate exactly', () => {
  test.describe.configure({ mode: 'parallel' });
  for (const [i, c] of REACH.entries()) {
    test(`value-free offline: a pull pattern the final check cannot enumerate exactly, case ${i + 1}: ${c.label}`,
      async ({}, testInfo) => {
        const value = synthetic();
        const s = scene(privatePath(testInfo, 'reach'), value, 'profile=p1 walk rc=0\n');
        for (const f of c.files) {
          mkdirSync(path.dirname(path.join(s.root, f)), { recursive: true });
          writeFileSync(path.join(s.root, f), `${value}\n`);
        }
        const pulls = [...PULLS, c.pattern(s.root)];
        // No bash on PATH: an empty directory stands for it.
        const nobin = privatePath(testInfo, 'nobin');
        mkdirSync(nobin, { recursive: true });
        const r = check(s, pulls, c.bash === false ? { PATH: nobin } : undefined);
        const label = `case ${i + 1}`;
        expect([r.status, r.stdout, r.stderr], `${label}: refused, one constant printed`).toEqual([1, c.code ?? STATUS.entry, '']);
        expect(rigCollect(s.root, pulls), `${label}: what the rig would take`).toEqual([]);
        expect(withheld(s, value), `${label}: withheld on this machine, never deleted`).toBe(c.files.length);
        expect(existsSync(s.registries), `${label}: the registries are removed`).toBe(false);
      });
  }
});

// Round 10, Sol's round-9 P2: a pull declared OUTSIDE the pull root (`files` are relative to the scene, beside the root,
// each holding the registered value). Whatever the rig would take of it leaves the pull path into the quarantine, and the
// verdict is the refusal (VFE3) unless the rig's reading is unavailable (VFE5).
const OUTSIDE: { label: string; files: string[]; pattern: (s: Scene) => string; code?: string; bash?: false }[] = [
  { label: "Sol's round-9 exact receipt, an absolute path outside the pull root", files: ['outside/exact/receipt.txt'],
    pattern: (s) => path.join(s.base, 'outside', 'exact', 'receipt.txt') },
  { label: 'an escaping exact path', files: ['outside/exact/receipt.txt'], pattern: () => '../outside/exact/receipt.txt' },
  { label: 'an escaping directory pull', files: ['outside/dir/a.txt', 'outside/dir/deep/b.txt'], pattern: () => '../outside/dir/**' },
  { label: 'an absolute glob outside the pull root', files: ['outside/p1/receipt.txt', 'outside/p2/receipt.txt'],
    pattern: (s) => `${s.base}/outside/*/receipt.txt` },
  { label: "the pull root's parent, which reaches the root's siblings and the root", files: ['outside/exact/receipt.txt',
    'checkout/reports/p1/raw.log'], pattern: () => '..' },
  { label: "Sol's round-9 exact receipt, the rig's reading unavailable", files: ['outside/exact/receipt.txt'],
    pattern: (s) => path.join(s.base, 'outside', 'exact', 'receipt.txt'), code: STATUS.failed, bash: false },
];

test.describe('value-free offline: a pull declared outside the pull root', () => {
  test.describe.configure({ mode: 'parallel' });
  for (const [i, c] of OUTSIDE.entries()) {
    test(`value-free offline: a pull declared outside the pull root, case ${i + 1}: ${c.label}`, async ({}, testInfo) => {
      const value = synthetic();
      const s = scene(privatePath(testInfo, 'outside-root'), value, 'profile=p1 walk rc=0\n');
      expect(transported(s.base), 'the scene lies where the walk rig transfers from').toBe(true);
      for (const f of c.files) {
        mkdirSync(path.dirname(path.join(s.base, f)), { recursive: true });
        writeFileSync(path.join(s.base, f), `${value}\n`);
      }
      const pulls = [...PULLS, c.pattern(s)];
      const nobin = privatePath(testInfo, 'nobin');
      mkdirSync(nobin, { recursive: true });
      const r = check(s, pulls, c.bash === false ? { PATH: nobin } : undefined);
      const label = `case ${i + 1}`;
      expect(rigCollect(s.root, pulls), `${label}: what the rig would take`).toEqual([]);
      expect(withheld(s, value), `${label}: withheld on this machine, never deleted`).toBe(c.files.length);
      expect([r.status, r.stdout, r.stderr], `${label}: refused, one constant printed`).toEqual([1, c.code ?? STATUS.entry, '']);
      expect(existsSync(s.registries), `${label}: the registries are removed`).toBe(false);
    });
  }
});

test('value-free offline: a pull declared outside the pull root and outside what the rig transfers is never moved',
  async ({}, testInfo) => {
    const value = synthetic();
    const s = scene(privatePath(testInfo, 'untransported'), value, 'profile=p1 walk rc=0\n');
    // A file below the private TMPDIR: on the walk VM, below /tmp, which the rig never collects.
    const elsewhere = path.join(s.tmp, 'elsewhere', 'receipt.txt');
    expect(transported(elsewhere), 'a path the walk rig never transfers').toBe(false);
    mkdirSync(path.dirname(elsewhere), { recursive: true });
    writeFileSync(elsewhere, 'not a registered value\n');
    const r = check(s, [...PULLS, elsewhere]);
    expect([r.status, r.stdout, r.stderr], 'refused, one constant printed').toEqual([1, STATUS.entry, '']);
    expect(readFileSync(elsewhere, 'utf8'), 'left where it is').toBe('not a registered value\n');
    expect(rigCollect(s.root, PULLS), 'and the pulls below the root are withheld').toEqual([]);
  });

// Round 10, before dispatch: the choke point's `declare` check, which the walk launcher runs on its own pulls. Clean, it
// prints nothing; anything it cannot enumerate exactly below the pull root refuses the launch with one constant.
test('value-free offline: the declaration check refuses every pull that is not one exact path below the pull root',
  async () => {
    const declare = (pulls: string[]) => {
      const r = spawnSync(process.execPath, [BOUNDARY, 'declare', '--', ...pulls], { encoding: 'utf8' });
      return [r.status, r.stdout, r.stderr];
    };
    expect(declare(PULLS), 'the walk launcher\'s own pulls').toEqual([0, '', '']);
    for (const p of ['/Volumes/x/hr01-outside/receipt.txt', '../outside/**', '../outside/exact/receipt.txt', '..', '.', '',
      'e2e/../..', 'e2e/walk-results/../../../x/**', '/', '**', 'reports/*/raw.log', 'reports/p1/raw.log extra/**']) {
      expect(declare([...PULLS, p]), `declared ${JSON.stringify(p)}`).toEqual([1, STATUS.entry, '']);
    }
    expect(declare([]), 'no pull declared').toEqual([1, STATUS.entry, '']);
  });

test('value-free offline: the walk launcher refuses an escaping pull declaration before dispatch and creates no job',
  async ({}, testInfo) => {
    // A copy of the launcher beside a groups.json whose group name escapes the pull root (`e2e/walk-results/<group>/...`);
    // host-guard and omega-cloud are stubs that log any call, so a dispatch would show.
    const dir = privatePath(testInfo, 'launch');
    mkdirSync(dir, { recursive: true });
    cpSync(repo('e2e/walks/launch/launch.sh'), path.join(dir, 'launch.sh'));
    const group = '../../../../vf-declared-outside';
    writeFileSync(path.join(dir, 'groups.json'), JSON.stringify({ [group]: { job: 'walks', lane: 'gw-vf-declare', profiles: ['p1'] } }));
    const calls = path.join(dir, 'calls.log');
    const stub = (name: string) => {
      const file = path.join(dir, name);
      writeFileSync(file, `#!/usr/bin/env bash\necho "${name} $*" >> '${calls}'\necho 'EXEC-JOB lane=gw-vf-declare job=j-20261029t0000-000000'\n`);
      chmodSync(file, 0o755);
      return file;
    };
    const head = execFileSync('git', ['-C', repo(''), 'rev-parse', 'HEAD'], { encoding: 'utf8' }).trim();
    const env: NodeJS.ProcessEnv = { ...process.env, E2E_VALUE_FREE: '1', WALK_WT: repo(''), WALK_SHA: head,
      OMEGA_CLOUD: stub('omega-cloud'), HOST_GUARD: stub('host-guard'), WALK_PULL_ROOT: path.join(dir, 'pulls') };
    delete env.WALK_LANE;
    for (const mode of ['print', 'plan', 'run']) {
      const r = spawnSync('bash', [path.join(dir, 'launch.sh'), group, mode], { encoding: 'utf8', env });
      const got = [r.status, r.stdout.split('\n')[0], `${r.stdout}${r.stderr}`.includes('vf-declared-outside')];
      expect(got, `${mode}: refused before dispatch, the declaration never printed`).toEqual([64, STATUS.entry.trim(), false]);
    }
    expect(existsSync(calls) ? readFileSync(calls, 'utf8') : '', 'no host-guard, no omega-cloud: no job').toBe('');
  });

const childEnv = (extra: Record<string, string>): NodeJS.ProcessEnv => {
  const env: NodeJS.ProcessEnv = { ...process.env };
  for (const v of ['TEST_WORKER_INDEX', 'TEST_PARALLEL_INDEX', 'E2E_VALUE_FREE', 'E2E_VALUE_FREE_SALT', 'E2E_VALUE_FREE_REPORTER',
    'E2E_VALUE_FREE_REGISTRY', 'E2E_VALUE_FREE_REGISTRIES', 'E2E_VALUE_FREE_PUBLISH', 'E2E_VALUE_FREE_OUTPUTS', 'E2E_WALK_COUNTS']) {
    delete env[v];
  }
  return { ...env, ...extra };
};

/** The walk job wrapper itself (e2e/walks/vm/value-free-job.sh), from a scratch checkout holding copies of the harness lib
 * and walk VM scripts: its command registers `value` through the registries root the wrapper gives it and writes it to
 * reports/p1/raw.log, which the glob pull matches (Sol's round-8 wrapperUnsupportedPull). `withoutCheck`: the copy lacks the
 * choke point, so the check never reaches its verdict and the wrapper's own fallback is all that withholds. */
function wrapperJob(base: string, value: string, withoutCheck = false) {
  const root = path.join(base, 'checkout');
  const tmp = privateTmp(); // the wrapper's TMPDIR: its registries root and quarantine go there
  for (const dir of [path.join(root, '.git'), path.join(root, 'e2e', 'walks')]) mkdirSync(dir, { recursive: true });
  cpSync(repo('e2e/lib'), path.join(root, 'e2e', 'lib'), { recursive: true,
    filter: (src) => !(withoutCheck && path.basename(src) === 'value-free-boundary.mjs') });
  cpSync(repo('e2e/walks/vm'), path.join(root, 'e2e', 'walks', 'vm'), { recursive: true });
  const command = [
    "const fs = require('node:fs'), p = require('node:path');",
    "const r = process.env.E2E_VALUE_FREE_REGISTRIES; fs.mkdirSync(p.join(r, 'run'));",
    `fs.writeFileSync(p.join(r, 'run', '1.jsonl'), JSON.stringify(${JSON.stringify(value)}) + '\\n');`,
    `fs.mkdirSync('reports/p1', { recursive: true }); fs.writeFileSync('reports/p1/raw.log', ${JSON.stringify(value)} + '\\n');`,
  ].join(' ');
  const pulls = ['reports/*/raw.log', 'command/command-exit.txt'];
  const job = spawnSync('bash', ['e2e/walks/vm/value-free-job.sh', 'command/command-exit.txt', ...pulls, '--', process.execPath,
    '-e', command], { cwd: root, encoding: 'utf8', timeout: 120_000, env: childEnv({ TMPDIR: tmp }) });
  return { job, root, tmp, pulls };
}
/** The walk job wrapper whose command registers `value` and writes it to two targets OUTSIDE the pull root, declared as
 * an absolute exact path and an escaping directory pull (Sol's round-9 reproducer and its escaping spelling). */
function wrapperOutside(base: string, value: string, withoutCheck = false) {
  const root = path.join(base, 'checkout');
  const tmp = privateTmp();
  for (const dir of [path.join(root, '.git'), path.join(root, 'e2e', 'walks')]) mkdirSync(dir, { recursive: true });
  cpSync(repo('e2e/lib'), path.join(root, 'e2e', 'lib'), { recursive: true,
    filter: (src) => !(withoutCheck && path.basename(src) === 'value-free-boundary.mjs') });
  cpSync(repo('e2e/walks/vm'), path.join(root, 'e2e', 'walks', 'vm'), { recursive: true });
  const receipt = path.join(base, 'outside', 'exact', 'receipt.txt');
  const escaping = path.join(base, 'outside', 'esc', 'receipt.txt');
  const command = [
    "const fs = require('node:fs'), p = require('node:path');",
    "const r = process.env.E2E_VALUE_FREE_REGISTRIES; fs.mkdirSync(p.join(r, 'run'));",
    `fs.writeFileSync(p.join(r, 'run', '1.jsonl'), JSON.stringify(${JSON.stringify(value)}) + '\\n');`,
    `for (const f of ${JSON.stringify([receipt, escaping])}) { fs.mkdirSync(p.dirname(f), { recursive: true });`,
    `fs.writeFileSync(f, ${JSON.stringify(value)} + '\\n'); }`,
  ].join(' ');
  const pulls = [receipt, '../outside/esc/**', 'command/command-exit.txt'];
  const job = spawnSync('bash', ['e2e/walks/vm/value-free-job.sh', 'command/command-exit.txt', ...pulls, '--', process.execPath,
    '-e', command], { cwd: root, encoding: 'utf8', timeout: 120_000, env: childEnv({ TMPDIR: tmp }) });
  return { job, root, tmp, pulls };
}
const forbiddenFor = (value: string): Forbidden => [['registered name', value]];

test('value-free offline: the walk job wrapper leaves no declared pull target in place when the check refuses a glob pull',
  async ({}, testInfo) => {
    const value = synthetic();
    const base = privatePath(testInfo, 'wrapper');
    const w = wrapperJob(base, value);
    expect([w.job.status, w.job.stdout, w.job.stderr], 'refused, one constant printed').toEqual([1, STATUS.entry, '']);
    expect(rigCollect(w.root, w.pulls), 'what the rig would take').toEqual([]);
    expect(leaks([{ where: 'job stdout', text: w.job.stdout }], forbiddenFor(value))).toEqual([]);
    expect(readdirSync(path.join(w.root, 'command')).filter((n) => n.startsWith('.command-exit.txt-withheld-')), 'the exit file')
      .toHaveLength(1);
    expect(withheld({ base, tmp: w.tmp }, value), 'withheld on this machine, never deleted').toBe(1);
  });

test('value-free offline: the walk job wrapper leaves no declared pull target in place when the check never reaches its verdict',
  async ({}, testInfo) => {
    const value = synthetic();
    const base = privatePath(testInfo, 'fallback');
    const w = wrapperJob(base, value, true);
    expect([w.job.status, w.job.stdout, w.job.stderr], 'nothing printed').toEqual([1, '', '']);
    expect(rigCollect(w.root, w.pulls), 'what the rig would take').toEqual([]);
    expect(readdirSync(path.join(w.root, 'command')).filter((n) => n.startsWith('.command-exit.txt-withheld-')), 'the exit file')
      .toHaveLength(1);
    // What the fallback withheld is in one quarantine outside the pull root (the run's registries, which no check read, are
    // beside it: never pulled).
    const quarantine = readdirSync(w.tmp).filter((n) => n.startsWith('value-free-withheld-'));
    expect(quarantine, 'one quarantine outside the pull root').toHaveLength(1);
    expect(holding(path.join(w.tmp, quarantine[0]), value).length + holding(w.root, value).length, 'never deleted').toBe(1);
  });

test('value-free offline: the walk job wrapper leaves no target declared outside the pull root in place when the check refuses',
  async ({}, testInfo) => {
    const value = synthetic();
    const base = privatePath(testInfo, 'wrapper-outside');
    expect(transported(base), 'the scene lies where the walk rig transfers from').toBe(true);
    const w = wrapperOutside(base, value);
    expect(rigCollect(w.root, w.pulls), 'what the rig would take').toEqual([]);
    expect(withheld({ base, tmp: w.tmp }, value), 'withheld on this machine, never deleted').toBe(2);
    expect([w.job.status, w.job.stdout, w.job.stderr], 'refused, one constant printed').toEqual([1, STATUS.entry, '']);
    expect(readdirSync(path.join(w.root, 'command')).filter((n) => n.startsWith('.command-exit.txt-withheld-')), 'the exit file')
      .toHaveLength(1);
  });

test('value-free offline: the walk job wrapper\'s fallback leaves no target declared outside the pull root in place',
  async ({}, testInfo) => {
    const value = synthetic();
    const base = privatePath(testInfo, 'fallback-outside');
    expect(transported(base), 'the scene lies where the walk rig transfers from').toBe(true);
    const w = wrapperOutside(base, value, true);
    expect(rigCollect(w.root, w.pulls), 'what the rig would take').toEqual([]);
    const quarantine = readdirSync(w.tmp).filter((n) => n.startsWith('value-free-withheld-'));
    expect(quarantine, 'one quarantine outside the pull root').toHaveLength(1);
    expect(holding(path.join(w.tmp, quarantine[0]), value), 'never deleted').toHaveLength(2);
    expect([w.job.status, w.job.stdout, w.job.stderr], 'nothing printed').toEqual([1, '', '']);
  });

// Round 11 (the round-10 verify's two P2s, each reproduced by an actual rig transfer):
//   6. The walk job wrapper classified TMPDIR before resolving it: a relative TMPDIR (`../outside/tmp`), a noncanonical
//      absolute one (`/tmp/..` before a directory where the rig transfers from) or a link to one passed its private-side
//      check, so its registries root and its fallback quarantine went there; a declared `../outside/**` reached both and the
//      rig transferred the quarantined receipt and the raw registry. The choke point's own quarantine had the link spelling
//      too. TMPDIR, the registries, the quarantine and every path a pull reaches are now resolved before anything is
//      classified: the private side canonically lies outside the pull root and outside /Users and /Volumes, else the next
//      candidate (/tmp), else nothing runs (one constant, no exit file).
//   7. This machine's home was never read whenever it lay outside the checkout, so a receipt declared below a home where the
//      rig transfers from stayed for it, in the choke point and in the wrapper's fallback. A home is now left unread only
//      when its canonical path lies where the rig never transfers from.
//   8. A pull spelled THROUGH a link: the rig follows a link named in a pattern, so a link below /Volumes to the private side
//      would carry the quarantine (or a fallback's registries) out under its own lexical path, which resolving alone reads
//      as private. The link itself is withheld.
/** Shell-quoted. */
const sq = (s: string) => `'${s.replace(/'/g, `'\\''`)}'`;
/** A link at `at` to the directory `to`; returns `at`. */
function link(to: string, at: string): string {
  mkdirSync(path.dirname(at), { recursive: true });
  symlinkSync(to, at);
  return at;
}
/** How many files holding `value` lie in `dirs` or in a quarantine made since `since` below the system temporary
 * directory (/tmp, and os.tmpdir() where it differs), where withheld entries go when TMPDIR names no private side: what
 * was withheld is still on this machine. Values are unique per test, so another test's quarantine never counts. */
function keptOnMachine(dirs: string[], value: string, since: number): number {
  const system = [...new Set(['/tmp', os.tmpdir()])].flatMap((t) => {
    try {
      return readdirSync(t).filter((n) => n.startsWith('value-free-withheld-')).map((n) => path.join(t, n))
        .filter((q) => statSync(q).mtimeMs >= since - 2000);
    } catch { return []; }
  });
  return [...dirs, ...system].reduce((n, d) => {
    try { return n + holding(d, value).length; } catch { return n; }
  }, 0);
}
/** How many files holding `value` lie in the quarantines on the private side `tmp`. */
const inQuarantine = (tmp: string, value: string) => readdirSync(tmp).filter((n) => n.startsWith('value-free-withheld-'))
  .reduce((n, q) => n + holding(path.join(tmp, q), value).length, 0);
/** A fresh directory below `top` (/Users or /Volumes, where the walk rig transfers from): below this test's output when that
 * lies there, else directly below `top`; null when this machine has none this test may write (the case is then skipped,
 * and the gate requires none skipped). */
function freshBelow(top: string, testInfo: TestInfo): string | null {
  const out = privatePath(testInfo, `below-${path.basename(top).toLowerCase()}`);
  for (const parent of [...(path.resolve(out).startsWith(`${top}/`) ? [out] : []), top]) {
    try {
      mkdirSync(parent, { recursive: true });
      const dir = mkdtempSync(path.join(parent, 'vf-home-'));
      if (realpathSync(dir).startsWith(`${top}/`)) return dir;
    } catch { /* the next */ }
  }
  return null;
}
/** The walk job wrapper's scene: a scratch checkout holding copies of the harness lib and walk VM scripts (`withoutCheck`:
 * without the choke point, so the wrapper's own fallback is all that withholds), a directory beside it where the rig
 * transfers from (`outside`, holding an empty `tmp`), and a private temporary directory. */
function wrapperScene(base: string, withoutCheck: boolean) {
  const root = path.join(base, 'checkout');
  const outside = path.join(base, 'outside');
  for (const dir of [path.join(root, '.git'), path.join(root, 'e2e', 'walks'), path.join(outside, 'tmp')]) mkdirSync(dir, { recursive: true });
  cpSync(repo('e2e/lib'), path.join(root, 'e2e', 'lib'), { recursive: true,
    filter: (src) => !(withoutCheck && path.basename(src) === 'value-free-boundary.mjs') });
  cpSync(repo('e2e/walks/vm'), path.join(root, 'e2e', 'walks', 'vm'), { recursive: true });
  return { base, root, outside, tmp: privateTmp() };
}
type WrapperScene = ReturnType<typeof wrapperScene>;
/** Runs the wrapper in `w` with `pulls` and its exit file declared. Its command registers `value` through the registries
 * root the wrapper gives it, writes it to each of `targets`, runs `also` (Node, the checkout its working directory) and
 * leaves a marker beside the scene that says it ran. `env` goes over the child environment (TMPDIR the scene's private
 * directory unless given). */
function runWrapper(w: WrapperScene, value: string, targets: string[], pulls: string[], env: Record<string, string> = {}, also = '') {
  const command = [
    "const fs = require('node:fs'), p = require('node:path');",
    "const r = process.env.E2E_VALUE_FREE_REGISTRIES; fs.mkdirSync(p.join(r, 'run'));",
    `fs.writeFileSync(p.join(r, 'run', '1.jsonl'), JSON.stringify(${JSON.stringify(value)}) + '\\n');`,
    `for (const f of ${JSON.stringify(targets)}) { fs.mkdirSync(p.dirname(f), { recursive: true });`,
    `fs.writeFileSync(f, ${JSON.stringify(value)} + '\\n'); }`,
    also,
    `fs.writeFileSync(${JSON.stringify(path.join(w.base, 'ran'))}, 'ran\\n');`,
  ].join(' ');
  const all = [...pulls, 'command/command-exit.txt'];
  const job = spawnSync('bash', ['e2e/walks/vm/value-free-job.sh', 'command/command-exit.txt', ...all, '--', process.execPath,
    '-e', command], { cwd: w.root, encoding: 'utf8', timeout: 120_000, env: childEnv({ TMPDIR: w.tmp, ...env }) });
  return { job, pulls: all };
}
/** TMPDIR spelled so that it names a directory where the rig transfers from (`outside`'s `tmp`), from a pull root beside
 * `outside`; `tmp` is a private directory the link may live in. */
const TMPDIRS: { label: string; tmpdir: (outside: string, tmp: string) => string }[] = [
  { label: 'a relative TMPDIR, ../outside/tmp', tmpdir: () => '../outside/tmp' },
  { label: 'an absolute TMPDIR spelled through /tmp/..', tmpdir: (outside) => `/tmp/..${path.join(outside, 'tmp')}` },
  { label: 'a TMPDIR that is a link to it', tmpdir: (outside, tmp) => link(path.join(outside, 'tmp'), path.join(tmp, 'tmp-link')) },
];

test.describe('value-free offline: a private side spelled where the rig transfers from', () => {
  test.describe.configure({ mode: 'parallel' });
  for (const [i, c] of TMPDIRS.entries()) {
    test(`value-free offline: a private side spelled where the rig transfers from (the choke point), case ${i + 1}: ${c.label}`,
      async ({}, testInfo) => {
        const since = Date.now();
        const value = synthetic();
        const s = scene(privatePath(testInfo, 'tmpdir'), value, 'profile=p1 walk rc=0\n');
        const outside = path.join(s.base, 'outside');
        mkdirSync(path.join(outside, 'tmp'), { recursive: true });
        writeFileSync(path.join(outside, 'receipt.txt'), `${value}\n`);
        expect(transported(outside), 'the scene lies where the walk rig transfers from').toBe(true);
        const pulls = [...PULLS, '../outside/**'];
        const r = spawnSync(process.execPath, [BOUNDARY, 'publish', '--registries', s.registries, '--print', s.held, '--', ...pulls],
          { cwd: s.root, encoding: 'utf8', env: { ...process.env, TMPDIR: c.tmpdir(outside, s.tmp) } });
        const label = `case ${i + 1}`;
        expect(rigCollect(s.root, pulls), `${label}: what the rig would take`).toEqual([]);
        expect(keptOnMachine([s.base, s.tmp], value, since), `${label}: withheld on this machine, never deleted`).toBe(1);
        expect([r.status, r.stdout, r.stderr], `${label}: refused, one constant printed`).toEqual([1, STATUS.entry, '']);
        expect(existsSync(s.registries), `${label}: the registries are removed`).toBe(false);
      });
  }
  for (const withoutCheck of [false, true]) {
    const how = withoutCheck ? "the wrapper's fallback" : 'the wrapper';
    for (const [i, c] of TMPDIRS.entries()) {
      test(`value-free offline: a private side spelled where the rig transfers from (${how}), case ${i + 1}: ${c.label}`,
        async ({}, testInfo) => {
          const since = Date.now();
          const value = synthetic();
          const w = wrapperScene(privatePath(testInfo, 'wrapper-tmpdir'), withoutCheck);
          const { job, pulls } = runWrapper(w, value, [path.join(w.outside, 'receipt.txt')], ['../outside/**'],
            { TMPDIR: c.tmpdir(w.outside, w.tmp) });
          const label = `case ${i + 1}`;
          expect(rigCollect(w.root, pulls), `${label}: what the rig would take (the receipt, the registry, the quarantine)`).toEqual([]);
          expect(keptOnMachine([w.base, w.tmp], value, since), `${label}: the receipt withheld on this machine, never deleted`).toBe(1);
          expect([job.status, job.stdout, job.stderr], `${label}: refused`).toEqual([1, withoutCheck ? '' : STATUS.entry, '']);
          expect(existsSync(path.join(w.base, 'ran')), `${label}: the command ran`).toBe(true);
        });
    }
  }
  test('value-free offline: a private side spelled where the rig transfers from, none left: the walk job wrapper runs nothing',
    async ({}, testInfo) => {
      const value = synthetic();
      const w = wrapperScene(privatePath(testInfo, 'no-private-side'), false);
      // A stub mktemp before the real one: every directory it makes lands below `outside`, where the rig transfers from,
      // whatever template it is given, so no candidate (TMPDIR, then /tmp) is a private side.
      const real = execFileSync('bash', ['-c', 'command -v mktemp'], { encoding: 'utf8' }).trim();
      const bin = path.join(w.base, 'stub-bin');
      mkdirSync(bin, { recursive: true });
      writeFileSync(path.join(bin, 'mktemp'), ['#!/usr/bin/env bash', 't=${!#}',
        `exec ${sq(real)} "\${@:1:$#-1}" ${sq(w.outside)}/"\${t##*/}"`, ''].join('\n'));
      chmodSync(path.join(bin, 'mktemp'), 0o755);
      const { job } = runWrapper(w, value, [path.join(w.outside, 'receipt.txt')], ['../outside/**'],
        { PATH: `${bin}:${process.env.PATH ?? ''}` });
      expect([job.status, job.stdout, job.stderr], 'refused before the command, one constant printed').toEqual([1, STATUS.failed, '']);
      expect(existsSync(path.join(w.base, 'ran')), 'the command never ran').toBe(false);
      expect(existsSync(path.join(w.root, 'command', 'command-exit.txt')), 'no exit file').toBe(false);
      expect(readdirSync(w.outside), 'nothing left below outside: each directory made there is removed').toEqual(['tmp']);
    });
});

// A home where the rig transfers from (below /Volumes, below /Users, or a link to one), a receipt declared below it by its
// absolute path: withheld like any other.
const HOMES: { label: string; home: (testInfo: TestInfo, tmp: string) => string | null }[] = [
  { label: 'a home below /Volumes', home: (testInfo) => freshBelow('/Volumes', testInfo) },
  { label: 'a home below /Users', home: (testInfo) => freshBelow('/Users', testInfo) },
  { label: 'a home that is a link to a directory below /Volumes', home: (testInfo, tmp) => {
    const to = freshBelow('/Volumes', testInfo);
    return to && link(to, path.join(tmp, 'home-link'));
  } },
];

test.describe("value-free offline: a receipt declared below this machine's home where the rig transfers from", () => {
  test.describe.configure({ mode: 'parallel' });
  for (const [i, c] of HOMES.entries()) {
    test(`value-free offline: a receipt declared below this machine's home where the rig transfers from (the choke point), case ${
      i + 1}: ${c.label}`, async ({}, testInfo) => {
      const value = synthetic();
      const s = scene(privatePath(testInfo, 'home'), value, 'profile=p1 walk rc=0\n');
      const home = c.home(testInfo, s.tmp);
      test.skip(home === null, 'no directory there this test may write');
      const receipt = path.join(realpathSync(home!), 'receipt.txt');
      writeFileSync(receipt, `${value}\n`);
      const pulls = [...PULLS, receipt];
      const r = spawnSync(process.execPath, [BOUNDARY, 'publish', '--registries', s.registries, '--print', s.held, '--', ...pulls],
        { cwd: s.root, encoding: 'utf8', env: { ...process.env, TMPDIR: s.tmp, HOME: home! } });
      const label = `case ${i + 1}`;
      expect(rigCollect(s.root, pulls), `${label}: what the rig would take`).toEqual([]);
      expect(withheld(s, value), `${label}: withheld on this machine, never deleted`).toBe(1);
      expect([r.status, r.stdout, r.stderr], `${label}: refused, one constant printed`).toEqual([1, STATUS.entry, '']);
      expect(existsSync(s.registries), `${label}: the registries are removed`).toBe(false);
    });
  }
  for (const withoutCheck of [false, true]) {
    const how = withoutCheck ? "the wrapper's fallback" : 'the wrapper';
    for (const [i, c] of HOMES.entries()) {
      if (i === 2 && !withoutCheck) continue; // the link: the choke point's own case above and the fallback's below
      test(`value-free offline: a receipt declared below this machine's home where the rig transfers from (${how}), case ${
        i + 1}: ${c.label}`, async ({}, testInfo) => {
        const value = synthetic();
        const w = wrapperScene(privatePath(testInfo, 'wrapper-home'), withoutCheck);
        const home = c.home(testInfo, w.tmp);
        test.skip(home === null, 'no directory there this test may write');
        const receipt = path.join(realpathSync(home!), 'receipt.txt');
        const { job, pulls } = runWrapper(w, value, [receipt], [receipt], { HOME: home! });
        const label = `case ${i + 1}`;
        expect(rigCollect(w.root, pulls), `${label}: what the rig would take`).toEqual([]);
        expect(inQuarantine(w.tmp, value), `${label}: the receipt withheld on this machine, never deleted`).toBe(1);
        expect([job.status, job.stdout, job.stderr], `${label}: refused`).toEqual([1, withoutCheck ? '' : STATUS.entry, '']);
      });
    }
  }
});

// A pull spelled through a link below `outside` to the private side, which holds the quarantine (and, in the fallback, the
// raw registries): the link itself is withheld.
test.describe('value-free offline: a pull spelled through a link to the private side', () => {
  test.describe.configure({ mode: 'parallel' });
  for (const withoutCheck of [false, true]) {
    const how = withoutCheck ? "the wrapper's fallback" : 'the wrapper';
    test(`value-free offline: a pull spelled through a link to the private side (${how}), case 1: to the quarantine`,
      async ({}, testInfo) => {
        const value = synthetic();
        const w = wrapperScene(privatePath(testInfo, 'door'), withoutCheck);
        const door = path.join(w.outside, 'door');
        const { job, pulls } = runWrapper(w, value, [path.join(w.outside, 'receipt.txt')],
          ['../outside/receipt.txt', '../outside/door/value-free-withheld-*/**'], {},
          `fs.symlinkSync(${JSON.stringify(w.tmp)}, ${JSON.stringify(door)});`);
        expect(rigCollect(w.root, pulls), 'what the rig would take').toEqual([]);
        expect(inQuarantine(w.tmp, value), 'the receipt withheld on this machine, never deleted').toBe(1);
        expect(existsSync(door) || readdirSync(w.outside).includes('door'), 'the link withheld').toBe(false);
        expect([job.status, job.stdout, job.stderr], 'refused').toEqual([1, withoutCheck ? '' : STATUS.entry, '']);
      });
  }
  test("value-free offline: a pull spelled through a link to the private side (the wrapper's fallback), case 2: to the registries",
    async ({}, testInfo) => {
      const value = synthetic();
      const w = wrapperScene(privatePath(testInfo, 'door-registries'), true);
      const door = path.join(w.outside, 'door');
      const { job, pulls } = runWrapper(w, value, [], ['../outside/door/e2e-vf-registries-*/**'], {},
        `fs.symlinkSync(${JSON.stringify(w.tmp)}, ${JSON.stringify(door)});`);
      expect(rigCollect(w.root, pulls), 'what the rig would take').toEqual([]);
      expect(holding(w.tmp, value), 'the raw registry stays on the private side, never deleted').toHaveLength(1);
      expect(readdirSync(w.outside).includes('door'), 'the link withheld').toBe(false);
      expect([job.status, job.stdout, job.stderr], 'nothing printed').toEqual([1, '', '']);
    });
});
