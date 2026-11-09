// HR-JRN-01: value-free mode's on-disk pass (lib/value-free.ts). Since round 4 only
// the publish directory lib/value-free-publish.ts assembles leaves the machine; this
// pass is defence in depth for the files that stay on it, and the codec and report
// resolution the publication reuses. When a run ends,
// lib/value-free-reporter.ts walks every directory and file the run writes (the
// projects' output directories, every reporter's report, E2E_WALK_COUNTS and the
// paths E2E_VALUE_FREE_OUTPUTS names) and leaves only what the sanitizer passed:
//   - a text file is rewritten through it, every layer inside it too (lib/value-free-codec.ts:
//     zips entry by entry, a zip embedded as a data: URL, base64 report fields and JSON
//     documents inside strings, to its nesting bound);
//   - any other file (a screenshot, a video, a binary attachment), and one nested past the
//     bound or in an encoding the codec does not decode, is deleted;
//   - a file or directory whose NAME carries a value is renamed to its tokenized name,
//     which is exactly how the reports, rewritten the same way, now refer to it: every
//     entry inside a directory target, the target itself, and every directory on its
//     path that did not exist when the run began.
// A refused run (lib/value-free.ts) deletes every target instead (withhold()).
// Everything is synchronous, so the same pass also runs in the runner's exit hook.
// A directory that holds sources (the config's, a project's tests, the working
// directory, home, a checkout or a package) is never walked: one stray
// `outputDir: '.'` must not rewrite the e2e tree.
import { existsSync, lstatSync, readdirSync, readFileSync, realpathSync, renameSync, rmSync, statSync, unlinkSync, writeFileSync,
  type Stats } from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import type { FullConfig } from '@playwright/test/reporter';
import { isInside, VALUE_FREE_REPORTER, type ValueFree } from './value-free';
import { sanitizeBytes } from './value-free-codec';

// The zip codec moved to lib/value-free-zip.ts (round 5; round 7: lib/value-free-reading.mjs, re-exported there); its
// names stay importable here.
export { asText, isZip, readZip, writeZip, type ZipEntry } from './value-free-zip';

/** The sanitized bytes of one file, every layer inside it too (lib/value-free-codec.ts), or null when it cannot be
 * sanitized: a binary, a zip it cannot read, a payload nested past the bound or in an encoding it does not decode. The
 * on-disk pass then removes it. */
export function scrubBytes(buf: Buffer, valueFree: ValueFree): Buffer | null {
  const r = sanitizeBytes(buf, valueFree);
  return 'bytes' in r ? r.bytes : null;
}

function scrubFile(file: string, valueFree: ValueFree): void {
  const buf = readFileSync(file);
  const clean = scrubBytes(buf, valueFree);
  if (clean === null) unlinkSync(file);
  else if (!clean.equals(buf)) writeFileSync(file, clean);
}

/** A name with every value tokenized; a name that would not be one name any more is tokenized whole. */
function cleanName(name: string, valueFree: ValueFree): string {
  const clean = valueFree.text(name);
  return clean && !clean.includes(path.sep) && clean !== '..' && clean !== '.' ? clean : valueFree.token(name);
}

/** `rename: false` while later reporters may still read the run's files by path (onEnd). */
export function scrubTree(target: string, valueFree: ValueFree, rename: boolean): void {
  let stat: Stats;
  try { stat = lstatSync(target); } catch { return; }
  // A link or a hard link is removed, never rewritten: writing through it would change a file outside the run's outputs.
  if (stat.isSymbolicLink() || (stat.isFile() && stat.nlink > 1)) { unlinkSync(target); return; }
  if (stat.isFile()) { scrubFile(target, valueFree); return; }
  if (!stat.isDirectory()) return;
  for (const name of readdirSync(target)) {
    const child = path.join(target, name);
    scrubTree(child, valueFree, rename);
    if (!rename || valueFree.text(name) === name || !existsSync(child)) continue;
    const dest = path.join(target, cleanName(name, valueFree));
    if (existsSync(dest)) rmSync(child, { recursive: true, force: true });
    else renameSync(child, dest);
  }
}

/** The deepest directory on `target`'s path that exists now (when the run begins): the run made every one below it. */
export function existingParent(target: string): string {
  let at = path.dirname(path.resolve(target));
  while (!existsSync(at) && path.dirname(at) !== at) at = path.dirname(at);
  return at;
}

/** Renames, top down from `root` (existingParent() at the start of the run), every directory the run made on the
 * way to `target` and `target` itself, wherever the name carries a value. */
export function renameTarget(target: string, root: string, valueFree: ValueFree): void {
  let at = root;
  for (const name of path.relative(root, path.resolve(target)).split(path.sep).filter(Boolean)) {
    const clean = valueFree.text(name) === name ? name : cleanName(name, valueFree);
    const [from, to] = [path.join(at, name), path.join(at, clean)];
    if (clean !== name && existsSync(from)) {
      if (existsSync(to)) rmSync(from, { recursive: true, force: true });
      else renameSync(from, to);
    }
    at = to;
  }
}

/** A refused run: every target deleted, then the names on the way to them cleaned. */
export function withhold(targets: ScrubTargets, roots: Map<string, string>, valueFree: ValueFree): void {
  for (const target of [...targets.dirs, ...targets.files]) rmSync(target, { recursive: true, force: true });
  for (const target of [...targets.dirs, ...targets.files]) renameTarget(target, roots.get(target) ?? path.dirname(target), valueFree);
}

export interface ScrubTargets { dirs: string[]; files: string[] }
/** What Playwright passes every reporter (createReporters: reporterCommandOptions). */
export interface ReporterContext { configDir: string; commandHash?: string; shard?: { current: number; total: number } | null }

const fromEnv = (name: string): string | undefined => (process.env[name] ? path.resolve(process.cwd(), process.env[name]!) : undefined);
const str = (v: unknown): string | undefined => (typeof v === 'string' && v ? v : undefined);
/** Playwright's resolveReporterOutputPath: the option against the config directory, else the default next to the
 * nearest package.json (else the working directory). */
function reporterPath(defaultValue: string, configDir: string, option: string | undefined): string {
  if (option) return path.resolve(configDir, option);
  let pkg = configDir;
  while (!existsSync(path.join(pkg, 'package.json')) && path.dirname(pkg) !== pkg) pkg = path.dirname(pkg);
  return path.resolve(existsSync(path.join(pkg, 'package.json')) ? pkg : process.cwd(), defaultValue);
}
/** Playwright's resolveOutputFile, for json, junit and blob: the file a reporter writes and the directory it owns. */
function outputFile(reporter: string, o: Record<string, unknown>, ctx: ReporterContext,
  fallback?: { fileName: string; outputDir: string }): { file?: string; dir?: string } {
  const name = reporter.toUpperCase();
  const file = fromEnv(`PLAYWRIGHT_${name}_OUTPUT_FILE`) ?? (str(o.outputFile) && path.resolve(ctx.configDir, str(o.outputFile)!));
  if (file) return { file };
  const dir = fromEnv(`PLAYWRIGHT_${name}_OUTPUT_DIR`) ?? (str(o.outputDir) && path.resolve(ctx.configDir, str(o.outputDir)!))
    ?? (fallback && reporterPath(fallback.outputDir, ctx.configDir, undefined)) ?? ctx.configDir;
  const reportName = process.env[`PLAYWRIGHT_${name}_OUTPUT_NAME`] ?? str(o.fileName) ?? fallback?.fileName;
  // Neither: the report goes to stdout, which the runner holds and scrubs.
  return reportName ? { file: path.resolve(dir, reportName), dir: fallback ? dir : undefined } : {};
}
/** The blob reporter's default file name (blob.ts _defaultReportName). */
function blobName(ctx: ReporterContext): string {
  let name = 'report';
  if (ctx.commandHash) name += `-${ctx.commandHash.replace(/[\x00-\x2C\x2E-\x2F\x3A-\x40\x5B-\x60\x7B-\x7F]+/g, '-')}`;
  if (ctx.shard) name += `-${`${ctx.shard.current}`.padStart(`${ctx.shard.total}`.length, '0')}`;
  return `${name}.zip`;
}

/** The walk counts reporter (walks/vm/counts-reporter.cjs): numbers and source frames only, written in onEnd. */
export const COUNTS_REPORTER = fileURLToPath(new URL('../walks/vm/counts-reporter.cjs', import.meta.url));
const STDIO_REPORTERS = ['line', 'list', 'dot', 'github', 'null'];
const real = (file: string): string => { try { return realpathSync(file); } catch { return path.resolve(file); } };
/** One configured reporter, what value-free mode knows of it and where it writes. `label` names it in a refusal record:
 * a built-in name, `value-free`, `counts`, or `custom reporter` (never its path). */
export interface ReporterOutput {
  label: string;
  kind: 'json' | 'junit' | 'blob' | 'html' | 'counts' | 'stdio' | 'self' | 'unknown';
  file?: string;
  dir?: string;
}
/** Every reporter of the run, PW_TEST_REPORTER's extra one included, with its report resolved exactly as Playwright
 * resolves it (its options and their PLAYWRIGHT_* overrides). Non-built-in reporters are absolute paths by then
 * (Playwright resolves them), and only this harness's two are known by their real path. */
export function reporterOutputs(config: FullConfig, ctx: ReporterContext): ReporterOutput[] {
  const extra = process.env.PW_TEST_REPORTER;
  const reporters: ReadonlyArray<readonly [string, unknown?]> = [...config.reporter, ...(extra ? [[extra] as const] : [])];
  const self = real(VALUE_FREE_REPORTER);
  const counts = real(COUNTS_REPORTER);
  return reporters.map(([name, options]): ReporterOutput => {
    const o = (options && typeof options === 'object' ? options : {}) as Record<string, unknown>;
    if (name === 'json' || name === 'junit') return { label: name, kind: name, ...outputFile(name, o, ctx) };
    if (name === 'blob') return { label: name, kind: 'blob', ...outputFile(name, o, ctx, { fileName: blobName(ctx), outputDir: 'blob-report' }) };
    if (name === 'html') {
      return { label: name, kind: 'html', dir: fromEnv('PLAYWRIGHT_HTML_OUTPUT_DIR') ?? fromEnv('PLAYWRIGHT_HTML_REPORT')
        ?? reporterPath('playwright-report', ctx.configDir, str(o.outputFolder)) };
    }
    if (STDIO_REPORTERS.includes(name)) return { label: name, kind: 'stdio' };
    const resolved = real(path.resolve(ctx.configDir, name));
    if (resolved === self) return { label: 'value-free', kind: 'self' };
    // counts-reporter.cjs writes options.outputFile, else E2E_WALK_COUNTS, as given (against the working directory).
    if (resolved === counts) {
      const file = str(o.outputFile) ?? process.env.E2E_WALK_COUNTS;
      return { label: 'counts', kind: 'counts', file: file ? path.resolve(process.cwd(), file) : undefined };
    }
    // A custom reporter: the options it is given for where it writes, which is all that can be known of it.
    return { label: 'custom reporter', kind: 'unknown', file: str(o.outputFile) && path.resolve(ctx.configDir, str(o.outputFile)!),
      dir: str(o.outputDir ?? o.outputFolder) && path.resolve(ctx.configDir, str(o.outputDir ?? o.outputFolder)!) };
  });
}

/** Every place a run writes: the projects' output directories, each reporter's report resolved exactly as Playwright
 * resolves it (its options and their PLAYWRIGHT_* overrides, PW_TEST_REPORTER's extra reporter included),
 * E2E_WALK_COUNTS and the paths E2E_VALUE_FREE_OUTPUTS names. */
export function outputTargets(config: FullConfig, ctx: ReporterContext): ScrubTargets {
  const dirs = new Set(config.projects.map((p) => path.resolve(p.outputDir)));
  const files = new Set<string>();
  const extra = process.env.PW_TEST_REPORTER;
  const reporters: ReadonlyArray<readonly [string, unknown?]> = [...config.reporter, ...(extra ? [[extra] as const] : [])];
  for (const [name, options] of reporters) {
    const o = (options && typeof options === 'object' ? options : {}) as Record<string, unknown>;
    let out: { file?: string; dir?: string };
    if (name === 'json' || name === 'junit') out = outputFile(name, o, ctx);
    else if (name === 'blob') out = outputFile(name, o, ctx, { fileName: blobName(ctx), outputDir: 'blob-report' });
    else if (name === 'html') {
      out = { dir: fromEnv('PLAYWRIGHT_HTML_OUTPUT_DIR') ?? fromEnv('PLAYWRIGHT_HTML_REPORT')
        ?? reporterPath('playwright-report', ctx.configDir, str(o.outputFolder)) };
    } else if (STDIO_REPORTERS.includes(name)) out = {};
    else {
      // A custom reporter: the options it is given for where it writes.
      if (str(o.outputFile)) files.add(path.resolve(ctx.configDir, str(o.outputFile)!));
      for (const key of ['outputDir', 'outputFolder']) if (str(o[key])) dirs.add(path.resolve(ctx.configDir, str(o[key])!));
      out = {};
    }
    if (out.file) files.add(out.file);
    if (out.dir) dirs.add(out.dir);
  }
  // Playwright's own last-run record (runner/lastRun.ts: the first project's outputDir, or this override), and the walk counts.
  for (const file of [fromEnv('PLAYWRIGHT_LAST_RUN_OUTPUT_FILE'), fromEnv('E2E_WALK_COUNTS')]) if (file) files.add(file);
  for (const declared of (process.env.E2E_VALUE_FREE_OUTPUTS ?? '').split(path.delimiter).filter(Boolean)) {
    const resolved = path.resolve(declared);
    let isDir = false;
    try { isDir = statSync(resolved).isDirectory(); } catch { /* not written (yet) */ }
    (isDir ? dirs : files).add(resolved);
  }
  return { dirs: [...dirs], files: [...files] };
}

export const SOURCE_FILE = /(^package\.json|\.(c|m)?[jt]sx?|\.py)$/;
/** Targets that are safe to rewrite: never a source tree or an ancestor of one. */
export function guardTargets(targets: ScrubTargets, sources: string[]): ScrubTargets & { refused: string[] } {
  const protectedDirs = [...sources, process.cwd(), os.homedir()].map((p) => path.resolve(p));
  const refused: string[] = [];
  const dirs = targets.dirs.map((d) => path.resolve(d)).filter((dir) => {
    const bad = path.dirname(dir) === dir || protectedDirs.some((p) => p === dir || isInside(p, dir)) ||
      ['.git', 'package.json'].some((marker) => existsSync(path.join(dir, marker)));
    if (bad) refused.push(dir);
    return !bad;
  });
  const files = targets.files.map((f) => path.resolve(f)).filter((file) => {
    const bad = SOURCE_FILE.test(path.basename(file)) || protectedDirs.includes(file);
    if (bad) refused.push(file);
    return !bad;
  });
  return { dirs, files, refused };
}
