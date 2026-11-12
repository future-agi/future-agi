// HR-JRN-01: where the value-free specs keep their fixtures. Playwright's output directory (test-results/) is evidence:
// The trace-hygiene global teardown (lib/trace-hygiene/global-teardown.ts) audits every byte of it, with no registry, and
// fails the run on any credential shape, link or file it cannot read. These specs write raw synthetic values, links,
// hard links, registries, withheld trees and whole child runs ON PURPOSE, to prove the publication refusals: fixtures,
// never evidence. So each test keeps them in its own private directory, OUTSIDE the output directory and beside it: in
// the gitignored scratch/ under the output directory's parent, so below the same checkout, on the same filesystem and
// where the walk rig transfers from exactly when the output directory is. Every path property a spec asserts reads as
// before. The directory is made create-only (mkdtemp, 0700) on first use and removed when the test ends, passed or
// failed: every spec that calls privatePath registers removePrivate as a top-level afterEach. What a product writes to
// the output directory by design (a walk receipt, the live run's masked findings) stays there, as evidence.
import { chmodSync, mkdirSync, mkdtempSync, rmSync } from 'node:fs';
import path from 'node:path';
import type { TestInfo } from '@playwright/test';

const made = new Map<string, string>();
const keyOf = (testInfo: TestInfo): string => `${testInfo.testId}\0${testInfo.repeatEachIndex}\0${testInfo.retry}`;

/** This test's private fixture directory joined with `parts`, as testInfo.outputPath joins the output directory. The
 * directory is made on first use; a path that would leave it is refused. */
export function privatePath(testInfo: TestInfo, ...parts: string[]): string {
  const key = keyOf(testInfo);
  let dir = made.get(key);
  if (!dir) {
    const parent = path.join(path.dirname(path.resolve(testInfo.project.outputDir)), 'scratch');
    mkdirSync(parent, { recursive: true });
    dir = mkdtempSync(path.join(parent, 'hr01-fixtures-'));
    chmodSync(dir, 0o700);
    made.set(key, dir);
  }
  const at = path.join(dir, ...parts);
  const rel = path.relative(dir, at);
  if (rel.startsWith('..') || path.isAbsolute(rel)) throw new Error('privatePath: the path leaves the private directory');
  return at;
}

/** Removes this test's private fixture directory, if it made one. Never throws, so a passed test is never failed by it;
 * a directory it could not remove stays outside the output directory, in the gitignored scratch/. */
export function removePrivate(testInfo: TestInfo): void {
  const key = keyOf(testInfo);
  const dir = made.get(key);
  if (!dir) return;
  made.delete(key);
  try {
    rmSync(dir, { recursive: true, force: true });
  } catch {
    // left in scratch/, never in the output directory
  }
}
