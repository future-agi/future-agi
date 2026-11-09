// 708 add.78: the end-of-run half of trace hygiene, wired as globalTeardown in
// playwright.config.ts and playwright.walk.config.ts (compat inherits it). The
// runner calls it after every worker has finished and BEFORE any reporter's
// onEnd copies attachments into an HTML or blob report, and a --reporter
// override on the command line cannot drop it. For each project output
// directory it:
//   1. scrubs every trace archive not yet scrubbed (specs that bypass the
//      shared fixtures, traces from a worker that died);
//   2. runs the INDEPENDENT auditor over every byte of the directory;
//   3. moves every refused file to a quarantine under the OS temp directory,
//      writes a value-free evidence-audit.json and fails the run on any finding,
//      unreadable file or worker refusal (evidence-refused.json).
// Reports and results.json written later by reporters are gated by
// e2e/scripts/evidence-audit.mjs where evidence lands (walks/launch/launch.sh).
import { existsSync, lstatSync, readdirSync, writeFileSync } from 'node:fs';
import path from 'node:path';
import type { FullConfig } from '@playwright/test';
import { auditPaths, quarantineAll } from '../../scripts/evidence-audit/audit.mjs';
import { sweepTraceArchives } from './archive.mjs';
import { REFUSED_MARKER, refusedRoot } from './hook';

function markers(dir: string): string[] {
  const found: string[] = [];
  const walk = (p: string) => {
    const st = lstatSync(p);
    if (st.isDirectory()) for (const child of readdirSync(p)) walk(path.join(p, child));
    else if (st.isFile() && path.basename(p) === REFUSED_MARKER) found.push(path.relative(dir, p));
  };
  walk(dir);
  return found;
}

export default async function traceHygieneTeardown(config: FullConfig): Promise<void> {
  const dirs = [...new Set(config.projects.map((p) => p.outputDir))].filter((d) => existsSync(d));
  const failures: string[] = [];
  for (const dir of dirs) {
    const sweep = sweepTraceArchives([dir]);
    const refusedByWorkers = markers(dir);
    const report = auditPaths([dir], { root: dir });
    const { moved: quarantined, failed: quarantineFailed } = report.refused
      ? quarantineAll(report, dir, path.join(refusedRoot(), `teardown-${process.pid}-${Date.now().toString(36)}`))
      : { moved: [], failed: [] };
    writeFileSync(path.join(dir, 'evidence-audit.json'), JSON.stringify({ rule: '708 add.78', ...report,
      quarantined, quarantineFailed, sweep: { scrubbed: sweep.scrubbed.length, failed: sweep.failed.length },
      refusedByWorkers }, null, 2) + '\n');
    if (report.refused || refusedByWorkers.length)
      failures.push(`${path.basename(dir)}: ${report.hits.length} finding(s), ${report.problems.length} unreadable, ` +
        `${refusedByWorkers.length} worker refusal(s), ${quarantined.length} file(s) quarantined` +
        (quarantineFailed.length ? `, ${quarantineFailed.length} could NOT be quarantined (still in place)` : ''));
  }
  if (failures.length)
    throw new Error(`708 add.78 evidence REFUSED (value-free report: evidence-audit.json in the output directory): ` +
      failures.join('; '));
}
