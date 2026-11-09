// HR-JRN-01: what goes into a value-free publish directory (lib/value-free-publish.ts
// assembles it). The Builder writes the allowlisted sources into a staging directory,
// re-serialized and sanitized through every layer (lib/value-free-codec.ts, round 5):
//   reports/<kind>-<n>.<ext>  each json, junit, blob and walk-counts report; per-test
//                             stdout and stderr replaced by a pointer to runner.log (a
//                             blob's stdio events dropped)
//   html/ (html-<n>/)         each HTML report: its embedded data re-serialized entry by
//                             entry, its text files sanitized, its binaries left out
//   attachments/<n>.<ext>     every file a json report attaches by path, copied by
//                             content (text sanitized, a binary withheld) under a name
//                             the harness chooses
//   runner.log                the runner's stdout and stderr in the order written,
//                             sanitized as one text: a record of lines of text (R2)
// A source nested past the codec's bound, in an encoding it does not decode, or a zip it
// cannot read, is a reason to refuse, named only by the category of what it would have
// been published as (areaOf) and the layer (Place).
// Round 12 (the structural review's S2): every report RECORD (reports/json-<n>.json,
// counts-<n>.json, blob-<n>.zip's report.jsonl, html's report.json and <fileId>.json) is
// published only as its record type's closed schema admits it (lib/value-free-record.mjs,
// through the codec): projected, then validated; an unknown field or kind refuses the run
// (`unknown-field`, `unknown-kind`), and nothing of that record is written. A junit report
// has no closed schema: it is refused, never published.
// R2 (STRUCTURAL-REVIEW-R2.json S2): runner.log is a record too (lib/value-free-record.mjs
// RUNNER_LOG, checkLog): lines of text only; a document in the runner's output refuses the
// run (`unknown-kind`, area runner-log), and runner.log is not written.
import { lstatSync, mkdirSync, readFileSync, writeFileSync, type Stats } from 'node:fs';
import path from 'node:path';
import { isTextContent, type ValueFree } from './value-free';
import { sanitizeBytes, sanitizeText, type Problem } from './value-free-codec';
import { recordLayer, RUNNER_LOG } from './value-free-record.mjs';
import { listTree, MANIFEST, REFUSED } from './value-free-seal.mjs';
import { asText, isZip, readZip, writeZip } from './value-free-zip';

/** Why a junit report is refused in value-free mode (lib/value-free-vocabulary.mjs WHY). */
export const NO_RECORD_SCHEMA = 'it has no closed record schema';
/** What a value-free report says instead of a test's stdout or stderr. */
export const STDIO_POINTER = 'value-free: test output is published only in runner.log of the publish directory, ' +
  'sanitized as one stream\n';
/** A refusal reason: fixed codes, kinds, categories, lengths and file:line only (lib/value-free-vocabulary.mjs). */
export type Reason = { reason: string } & Record<string, string | number>;
export interface Source { label: string; kind: 'json' | 'junit' | 'blob' | 'html' | 'counts'; path: string }
/** The top-level category of what is published: what a refusal says instead of a path. */
export type Area = 'runner-log' | 'reports' | 'html' | 'attachments' | 'manifest' | 'refusal-record' | 'runner-output' |
  'publish-path' | 'other';

export function areaOf(rel: string): Area {
  const top = rel.split('/')[0];
  return top === 'runner.log' ? 'runner-log' : top === MANIFEST ? 'manifest' : top === REFUSED ? 'refusal-record'
    : top === 'reports' ? 'reports' : /^html(-\d+)?$/.test(top) ? 'html' : top === 'attachments' ? 'attachments' : 'other';
}
export const problemReason = (p: Problem, area: Area): Reason => ({ reason: p.problem, area, place: p.place, depth: p.depth });

const lstatOrNull = (file: string): Stats | null => { try { return lstatSync(file); } catch { return null; } };
/** Why a source cannot be read safely: a link (never followed), a hard link, or anything but a regular file. */
export const unsafeKind = (st: Stats): string | null =>
  st.isSymbolicLink() ? 'symlink' : !st.isFile() ? 'special file' : st.nlink > 1 ? 'hardlink' : null;
const EXTENSIONS: [RegExp, string][] = [[/json/, '.json'], [/markdown/, '.md'], [/csv/, '.csv'], [/xml/, '.xml'], [/yaml/, '.yaml']];
const b64 = (text: string): string => Buffer.from(text).toString('base64');
const withheld = (contentType: string): string => `withheld in value-free mode: ${contentType}`;
type JsonAttachment = { name?: string; contentType?: string; path?: string; body?: string };
type JsonResult = { stdout?: unknown[]; stderr?: unknown[]; attachments?: JsonAttachment[] };
type JsonSuite = { specs?: { tests?: { results?: JsonResult[] }[] }[]; suites?: JsonSuite[] };

/** Writes the allowlisted sources into a staging directory, re-serialized and sanitized. */
export class Builder {
  readonly reasons: Reason[] = [];
  private readonly counts = new Map<string, number>();
  private readonly copied = new Map<string, string>();
  private attachments = 0;

  constructor(private readonly root: string, private readonly vf: ValueFree) {}

  write(rel: string, data: Buffer | string): void {
    const file = path.join(this.root, rel);
    mkdirSync(path.dirname(file), { recursive: true });
    writeFileSync(file, data, { flag: 'wx' });
  }

  /** The runner's stdout and stderr, merged, every layer inside it sanitized: published only as the runner log's closed
   * schema admits it (R2: lines of text, never a document). */
  runnerLog(text: string): void {
    const clean = sanitizeText(text, this.vf, RUNNER_LOG);
    if (typeof clean === 'string') this.write('runner.log', clean);
    else this.reasons.push(problemReason(clean, 'runner-log'));
  }

  /** A source's bytes: a link, a hard link or a special file is refused, never read or followed. */
  private read(file: string, label: string, missing: 'refuse' | 'skip'): Buffer | null {
    const st = lstatOrNull(file);
    if (!st) {
      if (missing === 'refuse') this.reasons.push({ reason: 'missing-report', reporter: label });
      return null;
    }
    const why = unsafeKind(st);
    if (why) { this.reasons.push({ reason: 'unsafe-source', source: label, why }); return null; }
    return readFileSync(file);
  }

  /** A report's bytes sanitized through every layer, as the record `rel` is published as (round 12: projected to its closed
   * schema and validated); null (and the reason) when they cannot be. */
  private clean(buf: Buffer, label: string, rel: string): Buffer | null {
    const r = sanitizeBytes(buf, this.vf, recordLayer(rel));
    if ('bytes' in r) return r.bytes;
    this.reasons.push('binary' in r ? { reason: 'unreadable-report', reporter: label } : problemReason(r, 'reports'));
    return null;
  }

  source(src: Source): void {
    const n = (this.counts.get(src.kind) ?? 0) + 1;
    this.counts.set(src.kind, n);
    if (src.kind === 'html') { this.html(src, n); return; }
    const buf = this.read(src.path, src.label, 'refuse');
    if (!buf) return;
    if (src.kind === 'blob') {
      const entries = readZip(buf);
      if (!entries) { this.reasons.push({ reason: 'unreadable-report', reporter: src.label }); return; }
      // A blob's stdio events carry each chunk as written: the merged runner.log is their only publication.
      const rel = `reports/blob-${n}.zip`;
      const clean = this.clean(writeZip(entries.map((e) => (e.name.endsWith('.jsonl') ? { name: e.name, data: Buffer.from(
        e.data.toString('utf8').split('\n').filter((line) => { try { return JSON.parse(line).method !== 'onStdIO'; } catch { return true; } })
          .join('\n')) } : e))), src.label, rel);
      if (clean) this.write(rel, clean);
      return;
    }
    if (src.kind === 'junit') {
      // Round 12 (S2): no closed record schema, so never published (the plan already refuses it, before any worker).
      this.reasons.push({ reason: 'unresolvable-reporter', reporter: src.label, why: NO_RECORD_SCHEMA });
      return;
    }
    let parsed: unknown;
    try { parsed = JSON.parse(buf.toString('utf8')); } catch { parsed = undefined; }
    if (parsed === undefined) { this.reasons.push({ reason: 'unreadable-report', reporter: src.label }); return; }
    if (src.kind === 'json') this.jsonReport(parsed as { suites?: JsonSuite[] });
    const rel = `reports/${src.kind}-${n}.json`;
    const clean = this.clean(Buffer.from(`${JSON.stringify(parsed, null, 2)}\n`), src.label, rel);
    if (clean) this.write(rel, clean);
  }

  /** Per-test stdio becomes the pointer; every attachment file is copied by content under a harness name. */
  private jsonReport(report: { suites?: JsonSuite[] }): void {
    const visit = (suite: JsonSuite): void => {
      for (const spec of suite.specs ?? []) for (const test of spec.tests ?? []) for (const result of test.results ?? []) {
        if (result.stdout?.length) result.stdout = [{ text: STDIO_POINTER }];
        if (result.stderr?.length) result.stderr = [{ text: STDIO_POINTER }];
        for (const attachment of result.attachments ?? []) this.attachment(attachment);
      }
      for (const inner of suite.suites ?? []) visit(inner);
    };
    for (const suite of report.suites ?? []) visit(suite);
  }

  private attachment(a: JsonAttachment): void {
    if (typeof a.path !== 'string') return;
    const source = path.resolve(a.path);
    const contentType = a.contentType ?? 'application/octet-stream';
    let published = this.copied.get(source);
    if (published === undefined) {
      published = '';
      const buf = this.read(source, 'attachment', 'skip');
      const r = buf && isTextContent(contentType) ? sanitizeBytes(buf, this.vf) : null;
      if (r && 'problem' in r) this.reasons.push(problemReason(r, 'attachments'));
      else if (r && 'bytes' in r && !isZip(r.bytes) && asText(r.bytes) !== null) {
        published = `attachments/${++this.attachments}${EXTENSIONS.find(([re]) => re.test(contentType))?.[1] ?? '.txt'}`;
        this.write(published, r.bytes);
      }
      this.copied.set(source, published);
    }
    if (published) { a.path = published; return; }
    delete a.path;
    a.body = b64(withheld(contentType));
    a.contentType = 'text/plain';
  }

  /** An HTML report: index.html (its embedded zip re-serialized entry by entry) and its text files, sanitized; binaries
   * left out. */
  private html(src: Source, n: number): void {
    const top = n === 1 ? 'html' : `html-${n}`;
    if (!lstatOrNull(src.path)?.isDirectory()) { this.reasons.push({ reason: 'missing-report', reporter: src.label }); return; }
    for (const e of listTree(src.path)) {
      if (e.type === 'dir') continue;
      if (e.type !== 'file' || (e.nlink ?? 1) > 1) {
        this.reasons.push({ reason: 'unsafe-source', source: 'html report', why: e.type === 'file' ? 'hardlink' : e.type === 'symlink' ? 'symlink' : 'special file' });
        continue;
      }
      // Round 12 (S2): index.html's embedded report data are records (report.json, <fileId>.json); the rest is payload.
      const r = sanitizeBytes(readFileSync(path.join(src.path, e.path)), this.vf, recordLayer(`${top}/${e.path}`));
      if ('problem' in r) { this.reasons.push(problemReason(r, 'html')); continue; }
      const rel = e.path.split('/').map((part) => (this.vf.text(part) === part ? part : this.vf.token(part))).join('/');
      if ('bytes' in r) this.write(`${top}/${rel}`, r.bytes);
    }
  }
}
