import { readFile, writeFile } from 'node:fs/promises';
import { resolve } from 'node:path';
import { pathToFileURL } from 'node:url';
import { baselineReport, compareActionRuns, runJourney } from './runner.mjs';
import { requireValue } from '../../lib/skills-r3/canonical.mjs';

// Explicit driver path is executable trusted test code, never read from an audit/fixture/response.
// Example (cloud): node e2e/benchmarks/context-hub/cli.mjs baseline /tmp/l13-baseline.json
export async function main(args) {
  requireValue(process.platform === 'linux', 'Run benchmark code only on the approved cloud host');
  const [command, ...rest] = args;
  if (command === 'baseline') {
    requireValue(rest.length === 1, 'Usage: cli.mjs baseline OUTPUT.json');
    const audit = JSON.parse(await readFile(new URL('./documented-baselines.json', import.meta.url), 'utf8'));
    await writeFile(rest[0], `${JSON.stringify(baselineReport(audit), null, 2)}\n`, { flag: 'wx', mode: 0o600 });
    return 0;
  }
  if (command === 'run') {
    requireValue(rest.length === 5, 'Usage: cli.mjs run J1 RUN_ID REGISTRATION.json DRIVER.mjs OUTPUT_DIR');
    const [jobId, runId, registrationPath, driverPath, outputDir] = rest;
    const registration = JSON.parse(await readFile(registrationPath, 'utf8'));
    const { createDriver } = await import(pathToFileURL(resolve(driverPath)).href);
    requireValue(typeof createDriver === 'function', 'Driver must export createDriver');
    const driver = await createDriver({ jobId, runId, registration });
    const result = await runJourney({ jobId, runId, registration, driver, outputDir });
    return result.status === 'completed' ? 0 : 1;
  }
  if (command === 'compare') {
    requireValue(rest.length === 4, 'Usage: cli.mjs compare OURS.json COMPETITOR.json REVIEW.json OUTPUT.json');
    const [ours, competitor, review] = await Promise.all(rest.slice(0, 3).map(async (path) => JSON.parse(await readFile(path, 'utf8'))));
    const result = compareActionRuns(ours, competitor, review);
    await writeFile(rest[3], `${JSON.stringify({ schema: 1, job: ours.job, cohort: ours.cohort, ...result }, null, 2)}\n`, { flag: 'wx', mode: 0o600 });
    // Valid but below-threshold, incomplete, and unmeasured comparisons are not green gates.
    return result.ten_x_actions === true ? 0 : 1;
  }
  throw new Error('Choose baseline, run or compare; documentation counts never become measured comparisons');
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  try { process.exitCode = await main(process.argv.slice(2)); }
  catch { console.error('Benchmark failed. Check prerequisites and retained redacted run artifacts; no secret-bearing error is printed.'); process.exitCode = 1; }
}
