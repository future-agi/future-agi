// HR-JRN-01 round 4: the publication boundary of value-free mode (lib/value-free.ts).
//
// In value-free mode the ONLY files that leave the machine are those in ONE publish
// directory, which lib/value-free-reporter.ts assembles here after every reporter and
// worker has flushed (in its onExit: every reporter's onEnd has written, every worker
// has stopped). Its sources are allowlisted, and nothing is copied as it was
// (lib/value-free-builder.ts: reports/, html/, attachments/, runner.log), sealed by
// MANIFEST.json (lib/value-free-seal.mjs).
// By default the directory is `value-free-publish` next to the first project's output
// directory (e2e/value-free-publish for playwright.config.ts); E2E_VALUE_FREE_PUBLISH
// names another. Before it is put in place, a scan reads every file's contents through
// every layer the sanitizer wrote (lib/value-free-codec.ts, round 5: zips, embedded zips,
// base64 fields and JSON documents inside strings, to NESTING_BOUND layers), every path
// component and the merged stream for every registered value. Any hit; a payload nested
// past the bound or in an encoding the codec does not decode; any reporter whose output
// cannot be resolved or is unsafe (a custom reporter; one that writes into the publish
// directory, holds sources or has a source extension); any source that is a link, a hard
// link or a special file; any value refused at registration; and any write into the
// publish directory before assembly replace it with a fresh directory that holds only
// REFUSED.json, and the run fails.
// Round 5: the refusal record and its name are published content too. Each reason is cut
// to the fixed vocabulary (lib/value-free-vocabulary.mjs: codes, kinds, categories,
// lengths, file:line, never a location string), and the record, its name and the publish
// directory's own path are scanned like every published file. When the path holds a
// registered value, or no clean record can be written (the full one, then codes alone),
// NOTHING is published and the run fails.
// Every other file the run writes stays on the machine and is never pulled. A consumer
// transfers only on the verdict of `node e2e/lib/value-free-seal.mjs <publish-dir>` (it
// re-checks the seal against anything written after assembly): the intact publication, or
// the refusal record alone, never a directory it rejected; with `--export <dir>` (round 6)
// that verdict is copied to <dir>, the only path a walk launch pulls. Round 7: the walk job
// then runs the publication boundary's final check (lib/value-free-boundary.mjs) over
// exactly what its launch pulls, against the runs' registries, right before the pull.
// Round 8: that module is the publication choke point every consumer runs (publish, export,
// ci, seal); the seal CLI forwards to it. The path scans here read the publish directory's
// path below the checkout, and each entry's path below it, whole as well as by component.
// R2 (STRUCTURAL-REVIEW-R2.json S2): runner.log is a record (lib/value-free-record.mjs
// RUNNER_LOG: lines of text only), read so at assembly, by the scan and in the output the
// runner prints at its exit; a json or junit reporter with no output file, whose report
// would be the runner's output, is refused by the plan before any worker.
import { existsSync, lstatSync, readFileSync, renameSync, type Stats } from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import type { FullConfig } from '@playwright/test/reporter';
import { isInside, type ValueFree } from './value-free';
import { checkPath } from './value-free-boundary.mjs';
import { areaOf, Builder, NO_RECORD_SCHEMA, problemReason, type Area, type Reason, type Source } from './value-free-builder';
import { NESTING_BOUND, Scan, type Problem } from './value-free-codec';
import { reporterOutputs, SOURCE_FILE, type ReporterContext } from './value-free-scrub';
import { isPublishDir, listTree, MANIFEST, REFUSED, refusalRecordText, removeTree, stagingFor, verifySeal, writeManifest,
  writeRefusalDir } from './value-free-seal.mjs';
import { recordLayer, RUNNER_LOG, type RecordLayer } from './value-free-record.mjs';
import { cleanReason } from './value-free-vocabulary.mjs';

export { areaOf, NO_RECORD_SCHEMA, STDIO_POINTER, type Area, type Reason, type Source } from './value-free-builder';
export const PUBLISH_DIR_ENV = 'E2E_VALUE_FREE_PUBLISH';
export const PUBLISH_DIR_NAME = 'value-free-publish';
/** R2 (S2): why a json or junit reporter with no output file is refused (lib/value-free-vocabulary.mjs WHY). */
export const RUNNER_OUTPUT_REPORT = 'it writes its report to the runner output';

export interface Plan { publishDir: string; writable: boolean; sources: Source[]; reasons: Reason[] }
/** `seal`: the MANIFEST.json (or REFUSED.json) text as written, so the exit check also catches a rewritten seal.
 * `nothing`: no clean refusal record could be written, or the publish path holds a value: nothing is published. */
export interface Assembly { refused: boolean; reasons: Reason[]; seal?: string; nothing?: boolean }

const overlaps = (a: string, b: string): boolean => a === b || isInside(a, b) || isInside(b, a);
const lstatOrNull = (file: string): Stats | null => { try { return lstatSync(file); } catch { return null; } };
const exists = (file: string): boolean => lstatOrNull(file) !== null;
const textOrNull = (file: string): string | null => { try { return readFileSync(file, 'utf8'); } catch { return null; } };
const holdsSources = (dir: string, protectedDirs: string[]): boolean => path.dirname(dir) === dir ||
  protectedDirs.some((p) => p === dir || isInside(p, dir)) || ['.git', 'package.json'].some((m) => existsSync(path.join(dir, m)));
export const dedupe = (reasons: Reason[]): Reason[] =>
  reasons.filter((r, i) => reasons.findIndex((x) => JSON.stringify(x) === JSON.stringify(r)) === i);

/** The run's publish directory: E2E_VALUE_FREE_PUBLISH, else `value-free-publish` next to the first project's output
 * directory. */
export function publishDirFor(config: FullConfig): string {
  const declared = process.env[PUBLISH_DIR_ENV];
  if (declared) return path.resolve(process.cwd(), declared);
  const first = config.projects[0]?.outputDir ?? path.join(config.rootDir, 'test-results');
  return path.join(path.dirname(path.resolve(first)), PUBLISH_DIR_NAME);
}

/** Before any worker starts: every reporter resolved, each one a source, harmless (it only prints, and the runner holds
 * what it prints) or a reason to refuse. `sourceDirs`: the config's, the root and every project's test directory. */
export function planPublication(config: FullConfig, ctx: ReporterContext, publishDir: string, sourceDirs: string[]): Plan {
  const reasons: Reason[] = [];
  const sources: Source[] = [];
  const protectedDirs = [...sourceDirs, process.cwd(), os.homedir()].map((p) => path.resolve(p));
  let writable = true;
  if (holdsSources(publishDir, protectedDirs)) {
    reasons.push({ reason: 'unsafe-publish-directory', why: 'it is, or holds, a source tree' });
    writable = false;
  }
  for (const project of config.projects) {
    if (overlaps(path.resolve(project.outputDir), publishDir)) {
      reasons.push({ reason: 'unsafe-reporter-target', reporter: 'project outputDir', why: 'it overlaps the publish directory' });
    }
  }
  for (const { kind, label, file, dir } of reporterOutputs(config, ctx)) {
    if (kind === 'self' || kind === 'stdio') continue;
    if (kind === 'unknown') {
      reasons.push({ reason: 'unresolvable-reporter', reporter: label,
        why: 'value-free mode publishes only the built-in reporters and walks/vm/counts-reporter.cjs' });
      continue;
    }
    const where = kind === 'html' ? dir : file;
    if (!where) {
      // A json or junit report with neither a file nor a name goes to stdout, which the runner holds (runner.log). R2 (S2): the
      // runner log admits lines of text only, never a report record, so that reporter is refused here, before any worker.
      reasons.push({ reason: 'unresolvable-reporter', reporter: label,
        why: kind === 'json' || kind === 'junit' ? RUNNER_OUTPUT_REPORT : 'it names no output file' });
      continue;
    }
    // Round 12 (the structural review's S2): a report file is published only as a record with a closed schema
    // (lib/value-free-record.mjs); a junit report has none, so the plan refuses it here, before any worker.
    if (kind === 'junit') {
      reasons.push({ reason: 'unresolvable-reporter', reporter: label, why: NO_RECORD_SCHEMA });
      continue;
    }
    const unsafe = [file, dir].some((p) => p !== undefined && overlaps(p, publishDir)) ? 'it writes into the publish directory'
      : kind === 'html' ? (holdsSources(where, protectedDirs) ? 'it holds sources' : null)
      : SOURCE_FILE.test(path.basename(where)) || protectedDirs.includes(where) ? 'it has a source extension' : null;
    if (unsafe) reasons.push({ reason: 'unsafe-reporter-target', reporter: label, why: unsafe });
    else sources.push({ label, kind, path: where });
  }
  return { publishDir, writable, sources, reasons };
}

/** The run begins: an earlier run's publish directory (one this harness wrote) is removed, and so is a link at its path
 * (never its target). Anything else there is never touched, and refuses the run. */
export function clearPublishDir(plan: Plan): void {
  const st = plan.writable ? lstatOrNull(plan.publishDir) : null;
  if (!st) return;
  if (st.isSymbolicLink() || isPublishDir(plan.publishDir)) {
    removeTree(plan.publishDir);
    return;
  }
  plan.writable = false;
  plan.reasons.push({ reason: 'unsafe-publish-directory', why: 'it exists and is not a publish directory this harness wrote' });
}

/** The components of the publish directory's path a pull keeps: those below the nearest checkout (a `.git`), else all. */
export function publishPathParts(dir: string): string[] {
  const abs = path.resolve(dir);
  for (let at = path.dirname(abs); ; at = path.dirname(at)) {
    if (existsSync(path.join(at, '.git'))) return path.relative(at, abs).split(path.sep).filter(Boolean);
    if (path.dirname(at) === at) return abs.split(path.sep).filter(Boolean);
  }
}
/** Round 8: the publish directory's path as a pull transfers it (below the checkout), and an entry's path below it. The scan
 * reads each with the choke point's checkPath (lib/value-free-boundary.mjs): every component and the whole relative path,
 * so a value spanning the publish directory's own name and an entry name (Sol's round-7 `value-free-publish/MANIFEST.json`)
 * is found as main's pull of `e2e/value-free-publish/**` would transfer it. */
export const publishRel = (dir: string): string => publishPathParts(dir).join('/');
const below = (rel: string | undefined, entry: string): string => (rel ? `${rel}/${entry}` : entry);
const holds = (values: string[], check: (scan: Scan) => void): boolean => {
  const scan = new Scan(values);
  check(scan);
  return scan.findings.size > 0;
};

/** Replaces the publish directory with one that holds only REFUSED.json (when it may be written at all): every reason cut
 * to the vocabulary, the record, its name and the publish path scanned first. When any holds a value (the full record,
 * then one of codes alone), nothing is published. */
export function refusePublication(plan: Plan, reasons: Reason[], values: string[]): Assembly {
  const all = dedupe(reasons.map((r) => cleanReason(r) as Reason));
  if (!plan.writable) return { refused: true, reasons: all };
  const nothing = (extra: Reason[] = []): Assembly => {
    try { removeTree(plan.publishDir); } catch { /* fail closed: the launcher's check withholds what is left */ }
    return { refused: true, reasons: dedupe([...all, ...extra]), nothing: true };
  };
  const rel = publishRel(plan.publishDir);
  if (holds(values, (s) => checkPath(s, rel))) {
    return nothing([{ reason: 'value-in-publish-path', area: 'publish-path', place: 'name' }]);
  }
  if (holds(values, (s) => checkPath(s, below(rel, REFUSED)))) return nothing();
  for (const record of [refusalRecordText(all), refusalRecordText(dedupe(all.map((r) => ({ reason: r.reason }))))]) {
    if (holds(values, (s) => s.bytes(Buffer.from(record, 'utf8')))) continue;
    try { writeRefusalDir(plan.publishDir, record); } catch { return nothing(); }
    return { refused: true, reasons: all, seal: record };
  }
  return nothing();
}

/** Every registered value (and its JSON-escaped form), colours removed or not, in every file's contents through every
 * layer, every path (each component and, round 8, the whole path below `under`, the publish directory's own path as a pull
 * transfers it), and the given texts. A link, a hard link, a special file, a binary, a zip it cannot read, a payload past
 * the bound is a finding too. Findings name categories (Area, Place), kinds and lengths only. */
export function scanPublication(dir: string | null, values: string[],
  texts: { area: Area; text: string; layer?: RecordLayer }[], under?: string): Reason[] {
  const found = new Map<string, Reason>();
  const add = (r: Reason) => found.set(JSON.stringify(r), r);
  const read = (area: Area, check: (scan: Scan) => void) => {
    const scan = new Scan(values);
    check(scan);
    for (const f of scan.findings.values()) add({ ...f, area } as Reason);
  };
  if (dir) {
    for (const e of listTree(dir)) {
      const area = areaOf(e.path);
      read(area, (scan) => checkPath(scan, below(under, e.path)));
      if (e.type === 'symlink' || e.type === 'special' || (e.type === 'file' && (e.nlink ?? 1) > 1)) {
        add({ reason: 'unsafe-entry', kind: e.type === 'file' ? 'hardlink' : e.type, area });
      } else if (e.type === 'file') {
        // Round 12 (S2): a report record is read against its closed schema (lib/value-free-record.mjs recordLayer).
        read(area, (scan) => scan.bytes(readFileSync(path.join(dir, e.path)), 0, 'content', recordLayer(e.path)));
      }
    }
  }
  for (const t of texts) read(t.area, (scan) => scan.bytes(Buffer.from(t.text, 'utf8'), 0, 'content', t.layer ?? null));
  return [...found.values()];
}

/** Assembles the publish directory (the reporter's onExit), or its refusal. `reasons`: what the run already knows refuses
 * it (values refused at registration, unsafe attachment sources, payloads past the bound). */
export function assemblePublication(input: { plan: Plan; vf: ValueFree; runnerLog: string; reasons: Reason[] }): Assembly {
  const { plan, vf } = input;
  const values = vf.values();
  const reasons = dedupe([...plan.reasons, ...input.reasons]);
  if (!plan.writable) return { refused: true, reasons: dedupe(reasons.map((r) => cleanReason(r) as Reason)) };
  if (exists(plan.publishDir)) reasons.push({ reason: 'publish-directory-written-before-assembly' });
  if (reasons.length) return refusePublication(plan, reasons, values);
  let staging: string | undefined;
  try {
    staging = stagingFor(plan.publishDir, 'assembly');
    const builder = new Builder(staging, vf);
    builder.runnerLog(input.runnerLog);
    for (const source of plan.sources) builder.source(source);
    if (builder.reasons.length) { removeTree(staging); return refusePublication(plan, builder.reasons, values); }
    writeManifest(staging, { scanned: { values: values.length } });
    const hits = scanPublication(staging, values, [], publishRel(plan.publishDir));
    if (hits.length) { removeTree(staging); return refusePublication(plan, hits, values); }
    // Last: whatever wrote into the publish path while this assembled refuses the run, never merged; a publish path that
    // holds a value publishes nothing (refusePublication).
    if (exists(plan.publishDir)) { removeTree(staging); return refusePublication(plan, [{ reason: 'publish-directory-written-before-assembly' }], values); }
    if (holds(values, (s) => checkPath(s, publishRel(plan.publishDir)))) {
      removeTree(staging);
      return refusePublication(plan, [], values);
    }
    const seal = readFileSync(path.join(staging, MANIFEST), 'utf8');
    renameSync(staging, plan.publishDir);
    staging = undefined;
    return { refused: false, reasons: [], seal };
  } catch (error) {
    if (staging) { try { removeTree(staging); } catch { /* best effort */ } }
    return refusePublication(plan, [{ reason: 'assembly-failed', why: (error as NodeJS.ErrnoException).code ?? 'error' }], values);
  }
}

/** At the runner's exit, after every other reporter's onExit: the publication is still exactly what was sealed, and the
 * output about to be printed (sanitized through every layer, or the problem that stopped it) holds no registered value and,
 * R2 (S2), only what the runner log admits (lines of text, never a document). Otherwise it is refused now. */
export function confirmAtExit(plan: Plan, assembly: Assembly, vf: ValueFree, output: string | Problem): Assembly {
  if (!plan.writable) return assembly;
  const values = vf.values();
  if (assembly.nothing) {
    // Nothing was published: whatever is at the path now was written after assembly, and is not published either.
    try { removeTree(plan.publishDir); } catch { /* the launcher's check withholds it */ }
    return assembly;
  }
  const late: Reason[] = [];
  // The seal as it is on disk, and as it was written: a writer that also rewrote MANIFEST.json is caught here.
  const sealFile = path.join(plan.publishDir, assembly.refused ? REFUSED : MANIFEST);
  const broken = verifySeal(plan.publishDir) ??
    (assembly.seal !== undefined && textOrNull(sealFile) !== assembly.seal ? 'the seal was rewritten after assembly' : null);
  if (broken) late.push({ reason: 'changed-after-assembly', why: broken });
  if (typeof output !== 'string') late.push(problemReason(output, 'runner-output'));
  else if (!assembly.refused) late.push(...scanPublication(null, values, [{ area: 'runner-output', text: output, layer: RUNNER_LOG }]));
  return late.length ? refusePublication(plan, [...assembly.reasons, ...late], values) : assembly;
}

/** The one line a refused run prints: fixed words, kinds, categories, lengths and file:line, never a value or a path. */
export function refusalLine(reasons: Reason[], nothing = false): string {
  const said = dedupe(reasons).map((r) => {
    switch (r.reason) {
      case 'short-value': case 'token-syntax': return `a ${r.kind ?? 'value'} of ${r.length} characters (${r.at ?? 'unknown'}) ` +
        (r.reason === 'token-syntax' ? 'overlaps the token syntax' : 'is too short to hide');
      case 'unsafe-reporter-target': return `the ${r.reporter} reporter's target is unsafe (${r.why})`;
      case 'unresolvable-reporter': return `a ${r.reporter} cannot be published (${r.why})`;
      case 'unsafe-publish-directory': return `the publish directory is unsafe (${r.why})`;
      case 'publish-directory-written-before-assembly': return 'something wrote into the publish directory before assembly';
      case 'unsafe-source': return `a source (${r.source}) is a ${r.why}`;
      case 'missing-report': case 'unreadable-report': return `the ${r.reporter} report is ${r.reason === 'missing-report' ? 'missing' : 'unreadable'}`;
      case 'scan-hit': return `the scan found a ${r.kind} of ${r.length} characters in ${r.area} (${r.place})`;
      case 'nesting-bound': return `a payload in ${r.area} is nested deeper than ${NESTING_BOUND} layers (${r.place})`;
      case 'unsupported-encoding': return `a payload in ${r.area} is in an encoding value-free mode does not decode (${r.place})`;
      case 'unreadable-zip': return `a zip in ${r.area} cannot be read (${r.place})`;
      case 'unsafe-entry': return `the publication holds a ${r.kind} (${r.area})`;
      case 'value-in-publish-path': return "the publish directory's own path holds a registered value";
      case 'unknown-field': return `a record in ${r.area} holds a field outside its closed schema (${r.place})`;
      case 'unknown-kind': return `a record in ${r.area} is of a kind its closed schema does not admit (${r.place})`;
      case 'changed-after-assembly': return `the publish directory changed after assembly (${r.why})`;
      default: return `${r.reason}${r.why ? ` (${r.why})` : ''}`;
    }
  });
  return `value-free: REFUSED. ${said.join('; ')}. ${nothing ? 'This run publishes nothing' : 'This run publishes only its refusal ' +
    'record (REFUSED.json in its publish directory)'} and its output is withheld.\n`;
}
