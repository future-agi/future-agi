// The CI job's steps around the held suite (.github/workflows/e2e-ci.yml, jobs.e2e.steps after a YAML parse), checked
// without running anything. No imports and only erasable TypeScript, so plain node can load this file as well as the
// harness specs. In value-free mode every step after the suite is the publication choke point, the upload of what it
// vouched for, a skipped step, or the one pinned Redis diagnostics stop step below; the diagnostics start step sits directly
// before the suite. Both are admitted for what makes them safe, not for their names: continue-on-error (they cannot change
// the job's outcome), no action (no uses or with: they upload nothing), and one --output-dir under $RUNNER_TEMP outside the
// publish folder (the diagnostics file stays on the runner; no upload takes $RUNNER_TEMP or redis-diagnostics). Each is
// pinned as its whole parsed object, so a renamed, altered, duplicated or moved step fails.

type CiStep = Record<string, unknown>;

export const SUITE_STEP_NAME = 'Run E2E suite';
export const SUITE_HELD_RUN = 'bin/e2e test > "$E2E_VALUE_FREE_HELD" 2>&1';
export const DIAGNOSTICS_SCRIPT = 'e2e/scripts/redis-diagnostics.mjs';

export const START_REDIS_DIAGNOSTICS: CiStep = {
  name: 'Start Redis diagnostics',
  'continue-on-error': true,
  run: 'node e2e/scripts/redis-diagnostics.mjs start --output-dir "$RUNNER_TEMP/redis-diagnostics"',
};

export const STOP_REDIS_DIAGNOSTICS: CiStep = {
  name: 'Stop Redis diagnostics',
  if: "${{ !cancelled() && steps.boot.outcome == 'success' }}",
  'continue-on-error': true,
  env: { SUITE_OUTCOME: '${{ steps.suite.outcome }}' },
  run: 'node e2e/scripts/redis-diagnostics.mjs stop --output-dir "$RUNNER_TEMP/redis-diagnostics" --suite-outcome "$SUITE_OUTCOME"',
};

const PINS: CiStep[] = [START_REDIS_DIAGNOSTICS, STOP_REDIS_DIAGNOSTICS];

const text = (v: unknown): string => (typeof v === 'string' ? v : '');
const record = (v: unknown): CiStep => (typeof v === 'object' && v !== null && !Array.isArray(v) ? (v as CiStep) : {});

/** Deep equality of parsed YAML values (plain objects, arrays, scalars), independent of key order. */
export function sameStep(a: unknown, b: unknown): boolean {
  if (a === b) return true;
  if (Array.isArray(a) || Array.isArray(b)) {
    if (!Array.isArray(a) || !Array.isArray(b) || a.length !== b.length) return false;
    const right: unknown[] = b;
    return a.every((v: unknown, i: number) => sameStep(v, right[i]));
  }
  if (typeof a !== 'object' || typeof b !== 'object' || a === null || b === null) return false;
  const left = a as CiStep;
  const right = b as CiStep;
  const ak = Object.keys(left).sort();
  const bk = Object.keys(right).sort();
  return ak.length === bk.length && ak.every((k: string, i: number) => k === bk[i] && sameStep(left[k], right[k]));
}

/** Every --output-dir argument of a run string, unquoted. */
function outputDirs(run: string): string[] {
  return Array.from(run.matchAll(/--output-dir\s+(?:"([^"]*)"|'([^']*)'|(\S+))/g), (m) => m[1] ?? m[2] ?? m[3] ?? '');
}

/** A folder under $RUNNER_TEMP that is not, and is not inside, the value-free publish folder. */
function runnerTempOnly(dir: string): boolean {
  return dir.startsWith('$RUNNER_TEMP/') && dir.length > '$RUNNER_TEMP/'.length && !dir.split('/').includes('..') &&
    !/value-free-publish|E2E_VALUE_FREE_PUBLISH/.test(dir);
}

/** Exact violation strings for the steps around the held suite; [] when the job is as admitted. */
export function ciStepViolations(input: readonly unknown[]): string[] {
  const steps: CiStep[] = input.map(record);
  const label = (st: CiStep): string => `.github/workflows/e2e-ci.yml: "${text(st.name) || text(st.uses) || text(st.id)}"`;
  const violations: string[] = [];
  const suites = steps.flatMap((st, i) => (st.name === SUITE_STEP_NAME ? [i] : []));
  if (suites.length !== 1) return [`.github/workflows/e2e-ci.yml: "${SUITE_STEP_NAME}" appears ${suites.length} times, not once`];
  const suite = suites[0];
  const suiteStep = steps[suite];
  if (suiteStep.id !== 'suite') violations.push(`${label(suiteStep)}: has no id: suite (the diagnostics stop reads its outcome)`);
  if (text(suiteStep.run).trim() !== SUITE_HELD_RUN) violations.push(`${label(suiteStep)}: its output is not held`);
  if (Object.hasOwn(suiteStep, 'continue-on-error')) violations.push(`${label(suiteStep)}: has continue-on-error`);
  const upload = steps.findIndex((st) => st.name === 'Upload report');
  const sdk = steps.findIndex((st) => st.id === 'sdk-ref');

  for (const pin of PINS) {
    const at = steps.flatMap((st, i) => (st.name === pin.name ? [i] : []));
    if (at.length !== 1) {
      violations.push(`${label(pin)}: appears ${at.length} times, not once`);
      continue;
    }
    const st = steps[at[0]];
    // The safety facts, each named; then the whole object against its pin.
    if (st['continue-on-error'] !== true) violations.push(`${label(st)}: is not continue-on-error`);
    if (Object.hasOwn(st, 'uses') || Object.hasOwn(st, 'with')) violations.push(`${label(st)}: runs an action (uses or with)`);
    const dirs = outputDirs(text(st.run));
    if (dirs.length !== 1 || !runnerTempOnly(dirs[0])) {
      violations.push(`${label(st)}: does not write only under $RUNNER_TEMP outside the publish folder`);
    }
    if (!sameStep(st, pin)) violations.push(`${label(st)}: differs from its exact pin`);
    if (pin === START_REDIS_DIAGNOSTICS && at[0] !== suite - 1) {
      violations.push(`${label(st)}: is not directly before "${SUITE_STEP_NAME}"`);
    }
    if (pin === STOP_REDIS_DIAGNOSTICS && (upload < 0 || at[0] < upload || (sdk >= 0 && at[0] > sdk))) {
      violations.push(`${label(st)}: is not after "Upload report" and before the SDK steps`);
    }
  }

  // Any other step that runs the diagnostics script is not admitted, wherever it sits.
  for (const st of steps) {
    if (text(st.run).includes(DIAGNOSTICS_SCRIPT) && !PINS.some((pin) => pin.name === st.name)) {
      violations.push(`${label(st)}: runs the Redis diagnostics under an unpinned name`);
    }
  }

  // After the held suite: the choke point, the upload of what it vouched for, the exact stop pin, or skipped.
  for (const st of steps.slice(suite + 1)) {
    const sealedUpload = st.name === 'Upload report' && text(st.uses).startsWith('actions/upload-artifact@');
    if (st.id === 'value_free_seal' || sealedUpload || sameStep(st, STOP_REDIS_DIAGNOSTICS)) continue;
    if (st.if !== '${{ false }}') violations.push(`${label(st)}: runs in value-free mode outside the choke point`);
  }

  // No upload takes the runner's temp folder or the diagnostics.
  for (const st of steps) {
    if (!text(st.uses).startsWith('actions/upload-artifact@')) continue;
    if (/redis-diagnostics|RUNNER_TEMP/.test(text(record(st.with).path))) {
      violations.push(`${label(st)}: uploads from $RUNNER_TEMP or the Redis diagnostics`);
    }
  }
  return violations;
}
