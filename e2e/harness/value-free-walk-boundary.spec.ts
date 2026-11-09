import { execFileSync, spawnSync } from 'node:child_process';
import { randomBytes, randomUUID } from 'node:crypto';
import { cpSync, existsSync, linkSync, mkdirSync, mkdtempSync, readdirSync, readFileSync, symlinkSync, writeFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import os from 'node:os';
import path from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { test, expect } from '@playwright/test';
import { VALUE_FREE_MIN_LENGTH } from '../lib/value-free';
import { exportForPull, verifySeal, writeManifest } from '../lib/value-free-seal.mjs';
import { privatePath, removePrivate } from './value-free/private-dir';
import { E2E_DIR, leaks, readTransferred, type Forbidden } from './value-free/scan';

// Fixtures live in a private directory per test, outside the evidence tree (value-free/private-dir.ts).
test.afterEach(async ({}, testInfo) => removePrivate(testInfo));

// HR-JRN-01 round 7, offline (no stack, no browser): the value-free walk job's publication boundary, end to end. The
// stopping rule it pins: every byte that crosses the boundary (every path component of every file a pull transfers,
// relative to the pull root, every such file's content, and the job's own printed output) is scanned against the run's
// registered values immediately before the transfer, and any hit refuses the whole export; the export directory's name
// is value-free by construction.
// Each case runs the launch's own job command (e2e/walks/launch/launch.sh print, value-free) from a scratch pull root (a
// fresh directory with a `.git` marker, standing for the checkout), with run-walks.sh's own value-free steps, read from
// the script (its walk environment block and its seal step), around the real value-free boundary child
// (value-free/boundary.child.ts) under the walk's reporter list, in place of the stack and the walks. The child
// registers one name. What crosses is read back independently (value-free/scan.ts, never lib/value-free-codec.ts): every
// file below the pull root that one of the launch's own pull patterns matches, its path and its contents through every
// layer, and the job's stdout and stderr.
// Round 8 (Sol's round-7 P2s 1 and 2): the final check is the publication choke point. It scans the EXACT bytes it emits,
// after the newline a held output that lacks one is given (no final newline, a bare CR: real adopted registries, a name
// ending in LF or CR LF); registries it cannot read whole refuse the export with nothing printed but a fixed constant (a
// registry line cut short after a run registered a word of the round-7 refusal line); and what it prints of its own is
// one STATUS constant shorter than any registered value (STATUS below; lib/value-free-boundary.mjs).
const repo = (rel: string) => fileURLToPath(new URL(`../../${rel}`, import.meta.url));
const read = (rel: string) => readFileSync(repo(rel), 'utf8');
const CLI = createRequire(import.meta.url).resolve('@playwright/test/cli');
const CHILD_CONFIG = fileURLToPath(new URL('./value-free/boundary.config.ts', import.meta.url));
const WALK_REPORTERS = '--reporter=./lib/value-free-reporter.ts,line,./walks/vm/counts-reporter.cjs';
const BOUNDARY = repo('e2e/lib/value-free-boundary.mjs');
const quote = (s: string) => `'${s.replace(/'/g, `'\\''`)}'`;
/** The choke point's own output, by its round-8 contract: one constant, shorter than any registered value. */
const STATUS = { clean: 'VFOK\n', record: 'VFRR\n', registry: 'VFE1\n', value: 'VFE2\n', entry: 'VFE3\n', seal: 'VFE4\n',
  failed: 'VFE5\n' };

/** A walks group whose first profile's name is long enough to be registered (the profile is a pulled path component). */
function walkGroup() {
  const groups = JSON.parse(read('e2e/walks/launch/groups.json'));
  const group = Object.keys(groups).find((g) => !g.startsWith('_') && groups[g].job === 'walks' &&
    (groups[g].profiles?.[0]?.length ?? 0) >= VALUE_FREE_MIN_LENGTH)!;
  return { group, profile: groups[group].profiles[0] as string };
}

/** The lines of run-walks.sh from the one that is exactly `first` to the one that is exactly `last` (inclusive or not). */
function block(lines: string[], first: string, last: string, inclusive: boolean): string {
  const from = lines.findIndex((l) => l.trim() === first);
  const to = lines.findIndex((l, i) => i > from && l.trim() === last);
  if (from < 0 || to < 0) throw new Error(`run-walks.sh: no block from "${first}" to "${last}"`);
  return lines.slice(from + 1, inclusive ? to + 1 : to).join('\n');
}

const childEnv = (extra: Record<string, string>): NodeJS.ProcessEnv => {
  const env: NodeJS.ProcessEnv = { ...process.env };
  // The job must not look like a worker of this run, nor inherit a value-free state of its own.
  for (const v of ['TEST_WORKER_INDEX', 'TEST_PARALLEL_INDEX', 'E2E_VALUE_FREE', 'E2E_VALUE_FREE_SALT', 'E2E_VALUE_FREE_REPORTER',
    'E2E_VALUE_FREE_REGISTRY', 'E2E_VALUE_FREE_REGISTRIES', 'E2E_VALUE_FREE_PUBLISH', 'E2E_VALUE_FREE_OUTPUTS', 'E2E_WALK_COUNTS',
    'E2E_VALUE_FREE_CHILD_LIST_REPORTER', 'E2E_VALUE_FREE_CHILD_JSON', 'PW_TEST_REPORTER', 'PLAYWRIGHT_HTML_REPORT',
    'PLAYWRIGHT_LAST_RUN_OUTPUT_FILE', 'WALK_LANE']) delete env[v];
  for (const kind of ['JSON', 'JUNIT', 'HTML', 'BLOB']) for (const what of ['FILE', 'DIR', 'NAME']) delete env[`PLAYWRIGHT_${kind}_OUTPUT_${what}`];
  return { ...env, ...extra };
};

/** Every file below `root` (links are never followed), relative, POSIX separators. */
function filesBelow(root: string, rel = ''): string[] {
  return readdirSync(path.join(root, rel), { withFileTypes: true }).flatMap((e) => {
    const at = rel ? `${rel}/${e.name}` : e.name;
    return e.isDirectory() ? filesBelow(root, at) : e.isFile() ? [at] : [];
  });
}
const matches = (rel: string, pattern: string) => pattern.endsWith('/**') ? rel.startsWith(pattern.slice(0, -2)) : rel === pattern;

/** One value-free walk job: the launch's job command around a stub of run-walks.sh for the group's first profile. `echo`
 * prints a careless line naming the value; `tail` ends the job's output with these bytes exactly (no newline added);
 * `cutRegistry` leaves a registry line cut short after the walk (a process killed mid-write). */
async function walkJob(name: string, value: string, o: { echo?: boolean; tail?: string; cutRegistry?: boolean } = {}) {
  const { group, profile } = walkGroup();
  const head = execFileSync('git', ['-C', repo(''), 'rev-parse', 'HEAD'], { encoding: 'utf8' }).trim();
  const launchEnv = childEnv({ WALK_SHA: head, E2E_VALUE_FREE: '1' });
  const argv = execFileSync('bash', [repo('e2e/walks/launch/launch.sh'), group, 'print'], { encoding: 'utf8', env: launchEnv })
    .trim().split('\n');
  const pulls = argv.flatMap((arg, i) => (arg === '--pull' ? [argv[i + 1]] : []));
  const command = argv.slice(argv.indexOf('--') + 1);
  // A scratch checkout: the pull root, with copies of the harness's lib and walk VM scripts beside the results (never
  // pulled). Copies, not links: a plain-Node CLI reached through a linked directory is a case of its own (below).
  const scratch = mkdtempSync(path.join(os.tmpdir(), 'vf-walk-'));
  const root = path.join(scratch, 'checkout');
  const tmp = path.join(scratch, 'tmp');
  for (const dir of [path.join(root, '.git'), path.join(root, 'e2e', 'walks'), tmp]) mkdirSync(dir, { recursive: true });
  cpSync(repo('e2e/lib'), path.join(root, 'e2e', 'lib'), { recursive: true });
  cpSync(repo('e2e/walks/vm'), path.join(root, 'e2e', 'walks', 'vm'), { recursive: true });
  const profileDir = path.join(root, 'e2e', 'walk-results', group, profile);
  // run-walks.sh's own value-free steps for one profile: its walk environment block, the walk (here the boundary child,
  // under the walk's reporter list), its seal step; `echo` adds a careless line of output naming the value.
  const lines = read('e2e/walks/vm/run-walks.sh').split('\n');
  const envBlock = block(lines, 'if [[ "${E2E_VALUE_FREE:-}" == 1 ]]; then', 'else', false);
  const sealBlock = ['seal_rc=0', block(lines, 'seal_rc=0', 'unset E2E_VALUE_FREE_PUBLISH', true)].join('\n');
  const stub = path.join(scratch, 'run-walks-stub.sh');
  const tailFile = path.join(scratch, 'tail.bin');
  if (o.tail !== undefined) writeFileSync(tailFile, o.tail);
  writeFileSync(stub, ['set -uo pipefail', 'note() { :; }', 'fail=0', `P=${quote(profileDir)}`, `profile=${quote(profile)}`,
    'GREP=offline', 'mkdir -p "$P"', 'export E2E_WALK_COUNTS="$P/counts.json"', envBlock,
    `(cd ${quote(E2E_DIR)} && node ${quote(CLI)} test --config ${quote(CHILD_CONFIG)} boundary.child.ts ${WALK_REPORTERS}) > "$P/walk.log" 2>&1 || fail=1`,
    ...(o.cutRegistry ? ['for f in "$E2E_VALUE_FREE_REGISTRY"/*.jsonl; do printf \'"partial\' >> "$f"; break; done'] : []),
    sealBlock, ...(o.echo ? [`echo ${quote(`profile=${profile} note ${value}`)}`] : []),
    ...(o.tail !== undefined ? [`cat ${quote(tailFile)}`] : []), 'exit "$fail"', ''].join('\n'));
  const at = command.indexOf('e2e/walks/vm/run-walks.sh');
  expect(command.slice(at - 1, at + 3), `${name}: the job command runs run-walks.sh`).toEqual(['bash', 'e2e/walks/vm/run-walks.sh', group, head]);
  command.splice(at, 3, stub);
  const probes = { value, id: randomUUID(), outside: path.join(scratch, 'outside.json'), publish: path.join(profileDir, 'unused'),
    quoted: `Quoted "${randomBytes(6).toString('hex')}" probe`, diagnosticLabel: `Diagnostic label ${randomBytes(6).toString('hex')}` };
  const job = spawnSync(command[0], command.slice(1), { cwd: root, encoding: 'utf8', timeout: 400_000,
    env: childEnv({ E2E_VALUE_FREE: '1', E2E_VALUE_FREE_CHILD_OUT: profileDir, E2E_VALUE_FREE_PROBES: JSON.stringify(probes),
      E2E_VALUE_FREE_BOUNDARY: 'control', TMPDIR: tmp }) });
  const transferred = filesBelow(root).filter((rel) => pulls.some((p) => matches(rel, p))).sort();
  const found = await readTransferred(root, transferred);
  found.texts.push({ where: 'job stdout', text: job.stdout ?? '' }, { where: 'job stderr', text: job.stderr ?? '' },
    { where: 'job output', text: `${job.stdout ?? ''}${job.stderr ?? ''}` });
  const exitFile = pulls[pulls.length - 1];
  const exportPull = pulls.find((p) => p.endsWith('/**') && p.includes(`/${profile}/`))!;
  const shape = transferred.map((rel) => rel === exitFile ? 'exit file' : rel.startsWith(exportPull.slice(0, -2))
    ? rel.slice(exportPull.length - 2) : 'another path').sort();
  return { job, root, tmp, profileDir, pulls, exitFile, exportName: exportPull.split('/').slice(-2)[0], transferred, shape,
    texts: found.texts };
}

type Outcome = 'published' | 'refusal record' | 'exit file only' | 'nothing';
const longName = () => `Synthetic walk name ${randomBytes(8).toString('hex')}`;
// Every name is a legal registered value (six characters or more). The labels never contain the name: a test's own output
// path is named from its title. `code`: what a refused job prints (round 8: one STATUS constant, the value refusal unless
// given).
const CASES: { label: string; value: () => string; outcome: Outcome; echo?: boolean; tail?: string; cutRegistry?: boolean;
  code?: string }[] = [
  { label: 'a long random name (control)', value: longName, outcome: 'published' },
  { label: 'the six-letter word the round-6 directory ends with', value: () => 'export', outcome: 'published' },
  { label: 'the round-6 directory name itself', value: () => 'value-free-export', outcome: 'published' },
  { label: 'the results directory name', value: () => 'walk-results', outcome: 'nothing' },
  { label: 'the first profile name', value: () => walkGroup().profile, outcome: 'exit file only' },
  { label: 'the exit file directory name', value: () => 'command', outcome: 'nothing' },
  { label: 'the exit file name', value: () => 'command-exit.txt', outcome: 'nothing' },
  { label: 'the seal file name', value: () => 'MANIFEST.json', outcome: 'refusal record' },
  { label: 'the runner log file name', value: () => 'runner.log', outcome: 'refusal record' },
  { label: 'the report directory name', value: () => 'reports', outcome: 'refusal record' },
  { label: 'the walk counts file name', value: () => 'counts-1.json', outcome: 'refusal record' },
  { label: 'the refusal record file name', value: () => 'REFUSED.json', outcome: 'published' },
  { label: 'a long random name the job prints', value: longName, outcome: 'nothing', echo: true },
  { label: "a word of the final check's own line", value: () => 'boundary', outcome: 'published' },
  // Round 8, Sol's round-7 P2 1 (I2) with real adopted registries: the job's output ends without the newline the name
  // ends with, or with a bare CR before it; the check gives the output its newline, so the bytes it emits hold the name.
  { label: 'a name ending in a line feed, the job output ending without one', value: () => 'SyntheticTail\n', outcome: 'nothing',
    tail: 'SyntheticTail' },
  { label: 'a name ending in CR LF, the job output ending in a bare CR', value: () => 'SyntheticTail\r\n', outcome: 'nothing',
    tail: 'SyntheticTail\r' },
  // Round 8, Sol's round-7 P2 2 (I1): a run registered a word of the round-7 refusal line, then a registry line was cut short.
  { label: 'a word of the round-7 refusal line, then a registry line cut short', value: () => 'registries', outcome: 'nothing',
    cutRegistry: true, code: STATUS.registry },
];
const SHAPES: Record<Outcome, string[]> = {
  published: ['MANIFEST.json', 'exit file', 'reports/counts-1.json', 'runner.log'],
  'refusal record': ['REFUSED.json', 'exit file'],
  'exit file only': ['exit file'],
  nothing: [],
};

test.describe('value-free offline: the walk job final boundary', () => {
  test.describe.configure({ mode: 'parallel' });
  for (const [i, c] of CASES.entries()) {
    test(`value-free offline: the walk job final boundary, case ${i + 1}: a registered name equal to ${c.label}`, async () => {
      test.setTimeout(480_000);
      const value = c.value();
      const forbidden: Forbidden = [['registered name', value]];
      const r = await walkJob(`case ${i + 1}`, value, { echo: c.echo, tail: c.tail, cutRegistry: c.cutRegistry });
      // The invariant: no transferred path (any component, or across components), no transferred file's content at any
      // layer, and no byte the job prints holds the registered name.
      expect(leaks(r.texts, forbidden), `case ${i + 1}: a registered value crosses the boundary`).toEqual([]);
      expect([0, 1], `case ${i + 1}: the job exits 0 or 1 (ruling 724)`).toContain(r.job.status);
      expect(r.shape, `case ${i + 1}: what the launch pulls`).toEqual(SHAPES[c.outcome]);
      expect(r.job.status, `case ${i + 1}: the job's exit`).toBe(c.outcome === 'published' ? 0 : 1);
      if (r.shape.includes('exit file')) {
        expect(readFileSync(path.join(r.root, r.exitFile), 'utf8'), 'the exit file holds the job command exit').toBe(`${r.job.status}\n`);
      }
      if (c.outcome === 'nothing') {
        // A refused export lands nothing, its exit file included; what it moved aside stays on the machine, never deleted.
        expect([r.job.stdout, r.job.stderr], 'only one fixed constant is printed').toEqual([c.code ?? STATUS.value, '']);
        const aside = (dir: string, name: string) => existsSync(dir) ? readdirSync(dir).filter((n) => n.startsWith(`.${name}-withheld-`)) : [];
        expect(aside(path.join(r.root, path.dirname(r.exitFile)), path.basename(r.exitFile)), 'the exit file, moved aside').toHaveLength(1);
        if (['command', 'command-exit.txt'].includes(value) || c.echo) {
          const moved = aside(r.profileDir, r.exportName);
          expect(moved, 'the export, moved aside').toHaveLength(1);
          expect(readdirSync(path.join(r.profileDir, moved[0])).sort()).toEqual(['MANIFEST.json', 'reports', 'runner.log']);
        }
      }
      expect(readdirSync(r.tmp).filter((n) => n.startsWith('e2e-vf')), 'no registry (raw values) is left behind').toEqual([]);
    });
  }
});

test('value-free offline: the walk export directory name is shorter than any registered value, and the export refuses a longer one',
  async ({}, testInfo) => {
    // Round 8: run-walks.sh exports through the choke point (node e2e/lib/value-free-boundary.mjs export <publish> <export>).
    const name = /value-free-boundary\.mjs export "\$E2E_VALUE_FREE_PUBLISH" "\$P\/([^"]+)"/.exec(read('e2e/walks/vm/run-walks.sh'))?.[1] ?? '';
    expect(name.length, 'run-walks.sh exports to a name no registered value fits in').toBeLessThan(VALUE_FREE_MIN_LENGTH);
    expect(name).toMatch(/^[a-z]+$/);
    const base = privatePath(testInfo, 'names');
    const pub = path.join(base, 'publish');
    mkdirSync(pub, { recursive: true });
    writeFileSync(path.join(pub, 'runner.log'), 'clean report\n');
    writeManifest(pub);
    // A name of VALUE_FREE_MIN_LENGTH characters or more could hold a registered value: nothing is exported, nothing moved.
    const long = exportForPull(pub, path.join(base, 'x'.repeat(VALUE_FREE_MIN_LENGTH)));
    expect([long.code, long.exported, existsSync(path.join(base, 'x'.repeat(VALUE_FREE_MIN_LENGTH)))]).toEqual([1, false, false]);
    expect(verifySeal(pub), 'the publication is untouched').toBeNull();
    const short = exportForPull(pub, path.join(base, 'x'.repeat(VALUE_FREE_MIN_LENGTH - 1)));
    expect([short.code, short.exported]).toEqual([0, true]);
  });

test('value-free offline: the final boundary check vouches only for what it read, and refuses the whole export otherwise',
  async ({}, testInfo) => {
    const value = `Synthetic boundary name ${randomBytes(8).toString('hex')}`;
    const exitRel = 'e2e/walk-results/command/command-exit.txt';
    const exportRel = 'e2e/walk-results/g1/p1/vfout';
    const pulls = [`${exportRel}/**`, exitRel];
    let n = 0;
    /** A pull root with an export and an exit file, a registry root holding the value, and a held output. */
    const scene = (registry: (dir: string) => void = (dir) => {
      mkdirSync(path.join(dir, 'p1'), { recursive: true });
      writeFileSync(path.join(dir, 'p1', '101.jsonl'), `${JSON.stringify(value)}\n${JSON.stringify({ refused: 5, what: 'value', at: 'x.ts:1' })}\n`);
    }) => {
      const root = privatePath(testInfo, `scene-${++n}`);
      mkdirSync(path.join(root, exportRel, 'reports'), { recursive: true });
      mkdirSync(path.join(root, path.dirname(exitRel)), { recursive: true });
      writeFileSync(path.join(root, exportRel, 'runner.log'), 'clean log\n');
      writeFileSync(path.join(root, exportRel, 'reports', 'counts-1.json'), '{"planned":1}\n');
      writeFileSync(path.join(root, exitRel), '0\n');
      const registries = path.join(root, '..', `registries-${n}`);
      mkdirSync(registries, { recursive: true });
      registry(registries);
      const held = path.join(root, '..', `held-${n}.log`);
      writeFileSync(held, 'profile=p1 walk rc=0\n');
      return { root, registries, held };
    };
    const check = (s: ReturnType<typeof scene>, patterns = pulls) => spawnSync(process.execPath,
      [BOUNDARY, '--registries', s.registries, '--print', s.held, '--', ...patterns], { cwd: s.root, encoding: 'utf8' });
    const left = (s: ReturnType<typeof scene>) => filesBelow(s.root).filter((rel) => pulls.some((p) => matches(rel, p))).sort();
    const forbidden: Forbidden = [['registered name', value]];

    // Clean: exit 0, exactly the bytes scanned printed (the held output, then the clean constant), everything left in place
    // for the pull, the registries removed.
    const clean = scene();
    const ok = check(clean);
    expect(ok.status, `clean: ${ok.stdout.slice(-300)}`).toBe(0);
    expect([ok.stdout, ok.stderr]).toEqual([`profile=p1 walk rc=0\n${STATUS.clean}`, '']);
    expect(left(clean)).toEqual([exitRel, `${exportRel}/reports/counts-1.json`, `${exportRel}/runner.log`]);
    expect(existsSync(clean.registries), 'the registries (raw values) are removed once read').toBe(false);

    /** A refusal: exit 1, one fixed constant and nothing else, nothing left on the pull path, every pull moved aside (never
     * deleted). */
    const refused = (label: string, s: ReturnType<typeof scene>, code: string, patterns = pulls) => {
      const r = check(s, patterns);
      expect(r.status, label).toBe(1);
      expect([r.stdout, r.stderr], label).toEqual([code, '']);
      expect(leaks([{ where: `${label}: stdout`, text: r.stdout }, { where: `${label}: stderr`, text: r.stderr }], forbidden), label).toEqual([]);
      expect(left(s), `${label}: nothing is left to pull`).toEqual([]);
      expect(readdirSync(path.join(s.root, exportRel, '..')).filter((x) => x.startsWith('.vfout-withheld-')), label).toHaveLength(1);
      expect(readdirSync(path.join(s.root, path.dirname(exitRel))).filter((x) => x.startsWith('.command-exit.txt-withheld-')), label)
        .toHaveLength(1);
      expect(existsSync(s.registries), `${label}: the registries are removed`).toBe(false);
    };
    // The value in a file, one layer down (a JSON document holding it base64'd in a `buffer` field).
    const layered = scene();
    writeFileSync(path.join(layered.root, exportRel, 'reports', 'deep.json'),
      JSON.stringify({ buffer: Buffer.from(JSON.stringify({ deeper: value })).toString('base64') }));
    refused('a value one layer down', layered, STATUS.value);
    // The value as a directory name, and the value across two path components (no single component holds it).
    const named = scene();
    mkdirSync(path.join(named.root, exportRel, value));
    writeFileSync(path.join(named.root, exportRel, value, 'a.txt'), 'x\n');
    refused('a value as a path component', named, STATUS.value);
    const across = scene((dir) => writeFileSync(path.join(dir, '7.jsonl'), `${JSON.stringify('g1/p1/vfout')}\n`));
    refused('a value across path components', across, STATUS.value);
    // The value in the held output: refused, and the output is never printed.
    const printed = scene();
    writeFileSync(printed.held, `profile=p1 note ${value}\n`);
    refused('a value in the job output', printed, STATUS.value);
    // Round 8 (I2): the bytes emitted, not the held output alone. A value ending in the newline the held output lacks (Sol's
    // round-7 probes: `ordinary` and `SyntheticTail`), one ending in CR LF after a bare CR, and one spanning the held
    // output's end and the status constant after it.
    for (const [label, registered, held] of [['a newline the output lacks', 'ordinary\n', 'ordinary'],
      ['a long name and the newline the output lacks', 'SyntheticTail\n', 'SyntheticTail'],
      ['CR LF after a bare CR', 'SyntheticTail\r\n', 'SyntheticTail\r'],
      ['the output end and the status after it', 'walk rc=0\nVFOK', 'profile=p1 walk rc=0']]) {
      const tail = scene((dir) => writeFileSync(path.join(dir, 'tail.jsonl'), `${JSON.stringify(registered)}\n`));
      writeFileSync(tail.held, held);
      const r = check(tail);
      expect(leaks([{ where: `${label}: stdout`, text: r.stdout }], [['registered name', registered]]), label).toEqual([]);
      expect([r.status, r.stdout, r.stderr], label).toEqual([1, STATUS.value, '']);
      expect(left(tail), `${label}: nothing is left to pull`).toEqual([]);
    }
    // A link (never followed) or a hard link in a pulled tree.
    const linked = scene();
    symlinkSync(path.join(linked.root, exitRel), path.join(linked.root, exportRel, 'link.txt'));
    refused('a symlink', linked, STATUS.entry);
    const hard = scene();
    linkSync(path.join(hard.root, exportRel, 'runner.log'), path.join(hard.root, exportRel, 'reports', 'twin.log'));
    refused('a hard link', hard, STATUS.entry);
    // A pattern it cannot enumerate exactly, or one outside the pull root.
    refused('a glob inside a pattern', scene(), STATUS.entry, [...pulls, 'e2e/*/x.txt']);
    refused('a pattern outside the pull root', scene(), STATUS.entry, [...pulls, '../outside/**']);
    // Round 8 (I1): registries it cannot read whole fail closed (nothing transferred, nothing printed but the registry
    // constant, never a scan against an empty list): missing, a line cut short, a line that is not JSON, a string shorter
    // than any registered value can be (register() never writes one), and Sol's round-7 case, a run that registered a word
    // of the round-7 refusal line, then a line cut short.
    const missing = scene();
    const missingRoot = missing.registries;
    refused('no registries', { ...missing, registries: `${missingRoot}-absent` }, STATUS.registry);
    refused('a registry line cut short', scene((dir) => writeFileSync(path.join(dir, '8.jsonl'), `${JSON.stringify(value)}\n"Synth`)),
      STATUS.registry);
    refused('a registry line that is not JSON', scene((dir) => writeFileSync(path.join(dir, '9.jsonl'), 'not json\n')), STATUS.registry);
    refused('a registered string shorter than any value', scene((dir) => writeFileSync(path.join(dir, '12.jsonl'), `${JSON.stringify('VFOK')}\n`)),
      STATUS.registry);
    const vocabulary = scene((dir) => writeFileSync(path.join(dir, '13.jsonl'), `${JSON.stringify('registries')}\n"partial`));
    const partial = check(vocabulary);
    expect(leaks([{ where: 'partial registry: stdout', text: partial.stdout }, { where: 'partial registry: stderr', text: partial.stderr }],
      [['registered word', 'registries']]), 'a registry cut short').toEqual([]);
    expect([partial.status, partial.stdout, partial.stderr], 'a registry cut short').toEqual([1, STATUS.registry, '']);
    expect(left(vocabulary), 'a registry cut short: nothing is left to pull').toEqual([]);
    // What it prints of its own is a constant no registered value fits in: a registered word of the round-7 lines changes
    // nothing, clean or refused.
    const quietClean = scene((dir) => writeFileSync(path.join(dir, '10.jsonl'), `${JSON.stringify('value-free boundary')}\n`));
    const quiet = check(quietClean);
    expect([quiet.status, quiet.stdout], 'clean').toEqual([0, `profile=p1 walk rc=0\n${STATUS.clean}`]);
    expect(left(quietClean)).toEqual([exitRel, `${exportRel}/reports/counts-1.json`, `${exportRel}/runner.log`]);
    const quietRefused = scene((dir) => writeFileSync(path.join(dir, '11.jsonl'), `${JSON.stringify('nothing is transferred')}\n`));
    symlinkSync(path.join(quietRefused.root, exitRel), path.join(quietRefused.root, exportRel, 'link.txt'));
    const silent = check(quietRefused);
    expect([silent.status, silent.stdout], 'refused').toEqual([1, STATUS.entry]);
    expect(left(quietRefused), 'refused: nothing is left to pull').toEqual([]);
  });

test('value-free offline: the seal and final boundary CLIs run, and refuse, when reached through a linked directory',
  async ({}, testInfo) => {
    const base = privatePath(testInfo, 'linked');
    mkdirSync(base, { recursive: true });
    const linked = path.join(base, 'lib');
    symlinkSync(repo('e2e/lib'), linked);
    // The seal check on a missing publish directory: exit 1 and the choke point's constant, never a silent 0.
    const seal = spawnSync(process.execPath, [path.join(linked, 'value-free-seal.mjs'), path.join(base, 'missing')],
      { cwd: base, encoding: 'utf8' });
    expect([seal.status, seal.stdout, seal.stderr], 'the seal check').toEqual([1, STATUS.seal, '']);
    // The final check with no registries: refused, never a silent 0.
    const root = path.join(base, 'root');
    mkdirSync(path.join(root, 'e2e'), { recursive: true });
    writeFileSync(path.join(root, 'e2e', 'exit.txt'), '0\n');
    const final = spawnSync(process.execPath, [path.join(linked, 'value-free-boundary.mjs'), '--registries', path.join(base, 'absent'),
      '--', 'e2e/exit.txt'], { cwd: root, encoding: 'utf8' });
    expect([final.status, final.stdout, final.stderr], 'the final check').toEqual([1, STATUS.registry, '']);
    expect(existsSync(path.join(root, 'e2e', 'exit.txt')), 'nothing is left to pull').toBe(false);
  });

// Round 8: the choke point's own output, as the module defines it, is the contract above, and every constant is shorter
// than VALUE_FREE_MIN_LENGTH bytes (no registered value fits in one, registry or not). Read at run time, so a module
// without it fails here alone.
test('value-free offline: the choke point prints only constants shorter than any registered value', async () => {
  const mod = (await import(pathToFileURL(BOUNDARY).href)) as { STATUS?: Record<string, string> };
  expect(mod.STATUS, 'the module exports its constants').toEqual(STATUS);
  for (const [name, text] of Object.entries(STATUS)) {
    expect(Buffer.byteLength(text), name).toBeLessThan(VALUE_FREE_MIN_LENGTH);
  }
  expect(new Set(Object.values(STATUS)).size, 'each constant names one verdict').toBe(Object.keys(STATUS).length);
});
