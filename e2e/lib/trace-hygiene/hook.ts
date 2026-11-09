// 708 add.78: the CAPTURE-TIME half of trace hygiene, inside the worker.
// Playwright 1.62's runner writes a test's trace in TestTracing.stopIfNeeded()
// (once, after every fixture teardown): it builds test.trace from the runner's
// step events and merges the per-context chunks (temporary zips in the
// worker's artifacts directory) into <outputDir>/trace.zip. The hook replaces
// that method on THIS test's tracing object:
//   1. before the merge: scrub the runner events and every context chunk as one
//      set, so no unscrubbed byte is ever written into the output directory;
//   2. after the merge: scrub the merged archive again (attachments and sources
//      join at the merge), then run the INDEPENDENT auditor over it with the
//      values this worker minted (lib/minted-values.ts);
//   3. any finding or failure: the trace leaves the output directory for a
//      quarantine under the OS temp directory and a value-free
//      evidence-refused.json takes its place; global-teardown.ts fails the run.
// Errors inside stopIfNeeded are swallowed by the runner, so every step here
// fails CLOSED (no trace) rather than open.
import { copyFileSync, existsSync, mkdirSync, readFileSync, renameSync, unlinkSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import path from 'node:path';
import type { TestInfo } from '@playwright/test';
import { auditBuffer } from '../../scripts/evidence-audit/audit.mjs';
import { mintedValues } from '../minted-values';
import { scrubTraceFile, scrubTraceSet } from './archive.mjs';

export const REFUSED_MARKER = 'evidence-refused.json';
const HOOKED = '__faTraceHygiene';

interface TracingInternals {
  stopIfNeeded(): Promise<void>;
  _options?: unknown;
  _shouldAbandonTrace?(): boolean;
  _temporaryTraceFiles?: string[];
  _traceEvents?: unknown[];
  __faTraceHygiene?: boolean;
}

/** Where refused evidence goes: outside every tree that is pulled or published. */
export const refusedRoot = () => path.join(tmpdir(), 'fa-e2e-evidence-refused');

function moveOut(file: string, label: string): void {
  const dir = path.join(refusedRoot(), `${label}-${process.pid}-${Date.now().toString(36)}`);
  mkdirSync(dir, { recursive: true, mode: 0o700 });
  const to = path.join(dir, path.basename(file));
  try {
    renameSync(file, to);
  } catch {
    copyFileSync(file, to);
    unlinkSync(file);
  }
}

function refuse(testInfo: TestInfo, reason: string, report: unknown = null): void {
  const trace = testInfo.outputPath('trace.zip');
  if (existsSync(trace)) {
    try {
      moveOut(trace, 'trace');
    } catch {
      unlinkSync(trace);
    }
  }
  const i = testInfo.attachments.findIndex((a) => a.name === 'trace');
  if (i >= 0) testInfo.attachments.splice(i, 1);
  const marker = testInfo.outputPath(REFUSED_MARKER);
  writeFileSync(marker, JSON.stringify({ rule: '708 add.78', reason, report }, null, 2) + '\n');
  testInfo.attachments.push({ name: 'evidence-refused', contentType: 'application/json', path: marker });
}

/** Install the hook on this test's tracing. False when this Playwright has no such internals. */
export function installTraceHygiene(testInfo: TestInfo): boolean {
  const tracing = (testInfo as unknown as { _tracing?: TracingInternals })._tracing;
  if (!tracing || typeof tracing.stopIfNeeded !== 'function' || typeof tracing._shouldAbandonTrace !== 'function')
    return false;
  if (tracing[HOOKED]) return true;
  tracing[HOOKED] = true;
  const original = tracing.stopIfNeeded.bind(tracing);
  tracing.stopIfNeeded = async () => {
    const retained = Boolean(tracing._options) && !tracing._shouldAbandonTrace!();
    let failed: string | null = null;
    if (retained) {
      try {
        const chunks = (tracing._temporaryTraceFiles ?? []).filter((f) => existsSync(f));
        const { buffers } = scrubTraceSet(chunks.map((f) => readFileSync(f)), tracing._traceEvents ?? []);
        chunks.forEach((f, n) => writeFileSync(f, buffers[n]));
      } catch (error) {
        for (const f of tracing._temporaryTraceFiles ?? []) {
          try {
            unlinkSync(f);
          } catch {
            // already gone
          }
        }
        tracing._options = undefined; // the runner then writes no trace at all
        failed = `capture-time scrub failed (${(error as Error).name})`;
      }
    }
    await original();
    if (failed) return refuse(testInfo, failed);
    const trace = testInfo.outputPath('trace.zip');
    if (!retained || !existsSync(trace)) return;
    try {
      scrubTraceFile(trace, { force: true });
      const report = auditBuffer(readFileSync(trace), 'trace.zip', { registry: mintedValues() });
      if (report.refused) refuse(testInfo, 'independent auditor findings in trace.zip', report);
    } catch (error) {
      refuse(testInfo, `trace post-process failed (${(error as Error).name})`);
    }
  };
  return true;
}

