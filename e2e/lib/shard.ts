import { readdirSync, readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import path from 'node:path';

// Sharding by measured duration. Playwright's own --shard splits by test COUNT
// in file order. Here the harness specs are most of the tests and almost none
// of the time, so --shard puts nearly every flow into the first shard.
//
// E2E_SHARD=k/N runs the spec files this plan gives shard k. The plan is a pure
// function of the checkout (the spec files on disk and .test-durations.json),
// so every shard of a run computes the same split and a re-run repeats it.

/** Where specs live. playwright.config.ts builds its testMatch from this list. */
export const SPEC_ROOTS = ['flows', 'harness'];
export const SPEC_GLOBS = SPEC_ROOTS.map(root => `${root}/**/*.spec.ts`);

const E2E_DIR = path.join(path.dirname(fileURLToPath(import.meta.url)), '..');
const DURATIONS_FILE = path.join(E2E_DIR, '.test-durations.json');

/** Every spec file under the roots, as sorted paths relative to e2e/. */
export function listSpecFiles(dir: string = E2E_DIR): string[] {
  const files: string[] = [];
  const walk = (relative: string) => {
    for (const entry of readdirSync(path.join(dir, relative), { withFileTypes: true })) {
      const child = `${relative}/${entry.name}`;
      if (entry.isDirectory()) walk(child);
      else if (entry.name.endsWith('.spec.ts')) files.push(child);
    }
  };
  for (const root of SPEC_ROOTS) walk(root);
  return files.sort();
}

/** Seconds of test time per spec file, as scripts/shard-report.mjs recorded them. */
export function readDurations(file: string = DURATIONS_FILE): Record<string, number> {
  const parsed: unknown = JSON.parse(readFileSync(file, 'utf8'));
  if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) {
    throw new Error(`shard: ${file} must be an object of spec file to seconds`);
  }
  return parsed as Record<string, number>;
}

/**
 * Splits `files` into `total` groups of similar duration. Every file lands in
 * exactly one group. A file without a recorded duration (a new spec) counts as
 * the median file, so it runs without waiting for the durations to be refreshed.
 */
export function planShards(files: string[], durations: Record<string, number>, total: number): string[][] {
  if (!Number.isInteger(total) || total < 1) throw new Error(`shard: invalid shard count ${total}`);
  if (new Set(files).size !== files.length) throw new Error('shard: duplicate spec file');
  const known = files.map(file => durations[file]).filter(value => Number.isFinite(value) && value > 0)
    .sort((a, b) => a - b);
  const median = known.length ? known[Math.floor(known.length / 2)] : 1;
  const weight = (file: string) => {
    const value = durations[file];
    return Number.isFinite(value) && value > 0 ? value : median;
  };
  // Longest first, each to the least loaded group. Ties break by path and by
  // group index, so the result does not depend on the input order.
  const ordered = [...files].sort((a, b) => weight(b) - weight(a) || (a < b ? -1 : a > b ? 1 : 0));
  const groups: string[][] = Array.from({ length: total }, () => []);
  const load: number[] = new Array(total).fill(0);
  for (const file of ordered) {
    const target = load.indexOf(Math.min(...load));
    groups[target].push(file);
    load[target] += weight(file);
  }
  return groups.map(group => group.sort());
}

export interface Shard { index: number; total: number }

/** Parses E2E_SHARD ("2/4"). Unset or empty means the suite is not sharded. */
export function parseShard(value: string | undefined): Shard | undefined {
  if (!value) return undefined;
  const match = /^([1-9]\d*)\/([1-9]\d*)$/.exec(value);
  const index = Number(match?.[1]);
  const total = Number(match?.[2]);
  if (!match || index > total) throw new Error(`shard: E2E_SHARD must be k/N with 1 <= k <= N, got "${value}"`);
  return { index, total };
}

/**
 * The testMatch for this process: undefined when the suite is not sharded
 * (Playwright then uses SPEC_GLOBS), else this shard's spec files. A shard
 * with no files is an error, never an empty green run.
 */
export function shardTestMatch(value: string | undefined = process.env.E2E_SHARD, dir: string = E2E_DIR,
  durationsFile: string = DURATIONS_FILE): string[] | undefined {
  const shard = parseShard(value);
  if (!shard || shard.total === 1) return undefined;
  const files = listSpecFiles(dir);
  // testMatch entries are globs; a spec path must match itself and nothing else.
  const special = files.find(file => /[*?[\]{}()!\\]/.test(file));
  if (special) throw new Error(`shard: spec path "${special}" contains a glob character`);
  const mine = planShards(files, readDurations(durationsFile), shard.total)[shard.index - 1];
  if (!mine.length) throw new Error(`shard: ${value} has no spec files (${files.length} files in total)`);
  return mine;
}
