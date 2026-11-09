// HR-JRN-01: what harness/value-free-live.spec.ts needs to run a child and read back
// what it published: in value-free mode its publish directory (value-free-publish next
// to its output directory) and its stdout and stderr, nothing else; off, everything it
// left (the positive control). Every layer is decoded (readAll), round 5: zips (the blob
// report, the HTML report's embedded data, a zip inside a base64 field) are opened with
// Playwright's own yauzl, not with lib/value-free-zip.ts's reader, gzip too, base64 report
// fields and JSON documents inside strings, to a depth past the harness's own bound; and
// colour codes are removed by this file's own ECMA-48 reading, not lib/value-free.ts's,
// so the scan does not trust the code it checks. Every finding is masked: categories,
// places and field context only, so the gate may publish the list.
import { spawn, type ChildProcess } from 'node:child_process';
import { existsSync, readdirSync, readFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import os from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { gunzipSync } from 'node:zlib';
import pg from 'pg';
import { E2E } from '../../lib/env';
import { displayedKeys, VALUE_FREE_ENV, VALUE_FREE_REGISTRY_ENV, VALUE_FREE_REPORTER_ENV, VALUE_FREE_SALT_ENV } from '../../lib/value-free';

const requireHere = createRequire(import.meta.url);
export const E2E_DIR = fileURLToPath(new URL('../..', import.meta.url));
const CHILD_CONFIG = fileURLToPath(new URL('./child.config.ts', import.meta.url));
const CLI = requireHere.resolve('@playwright/test/cli');

export type JsonAttachment = { name: string; contentType: string; path?: string; body?: string };
export type JsonResult = { workerIndex: number; status: string; errors: { message?: string }[];
  stdout: { text?: string; buffer?: string }[]; stderr: { text?: string; buffer?: string }[]; attachments: JsonAttachment[] };
type JsonSuite = { specs?: { title: string; tests: { results: JsonResult[] }[] }[]; suites?: JsonSuite[] };
/** `publish`: the run's publish directory (value-free mode); `results`: its first json report as published there, or
 * off, the results.json it wrote. */
export interface ChildRun { name: string; out: string; publish: string; valueFree: boolean; code: number | null; stdout: string;
  stderr: string; results: { stats: Record<string, number>; suites: JsonSuite[] } | null }
/** `jsonByName`: the json report is the one PLAYWRIGHT_JSON_OUTPUT_NAME selects (results.json, absolute), not an outputFile.
 * `config` and `env`: another child config and extra environment (the publication boundary's probes). */
export interface ChildOptions { file: string; valueFree: boolean; salt?: string; listReporter?: boolean; jsonByName?: boolean;
  probes: object; config?: string; env?: Record<string, string> }
export const PUBLISH = 'value-free-publish';

const running = new Set<ChildProcess>();
export const killChildren = () => { for (const child of running) child.kill('SIGKILL'); };

export async function runChild(name: string, out: string, o: ChildOptions): Promise<ChildRun> {
  const env: NodeJS.ProcessEnv = { ...process.env, E2E_VALUE_FREE_CHILD_OUT: out, E2E_VALUE_FREE_PROBES: JSON.stringify(o.probes) };
  // The nested runner must not look like a worker of this one or inherit a value-free state.
  for (const v of ['TEST_WORKER_INDEX', 'TEST_PARALLEL_INDEX', VALUE_FREE_ENV, VALUE_FREE_SALT_ENV, VALUE_FREE_REPORTER_ENV,
    VALUE_FREE_REGISTRY_ENV, 'E2E_VALUE_FREE_CHILD_LIST_REPORTER', 'E2E_VALUE_FREE_CHILD_JSON', 'E2E_VALUE_FREE_OUTPUTS',
    'E2E_WALK_COUNTS', 'PW_TEST_REPORTER', 'PLAYWRIGHT_HTML_REPORT', 'PLAYWRIGHT_LAST_RUN_OUTPUT_FILE']) delete env[v];
  for (const kind of ['JSON', 'JUNIT', 'HTML', 'BLOB']) for (const what of ['FILE', 'DIR', 'NAME']) delete env[`PLAYWRIGHT_${kind}_OUTPUT_${what}`];
  if (o.valueFree) env[VALUE_FREE_ENV] = '1';
  if (o.salt) env[VALUE_FREE_SALT_ENV] = o.salt;
  if (o.listReporter) env.E2E_VALUE_FREE_CHILD_LIST_REPORTER = '1';
  if (o.jsonByName) Object.assign(env, { E2E_VALUE_FREE_CHILD_JSON: 'name', PLAYWRIGHT_JSON_OUTPUT_NAME: path.join(out, 'results.json') });
  delete env.E2E_VALUE_FREE_PUBLISH;
  Object.assign(env, o.env ?? {});
  const child = spawn(process.execPath, [CLI, 'test', '--config', o.config ?? CHILD_CONFIG, o.file], { cwd: E2E_DIR, env,
    stdio: ['ignore', 'pipe', 'pipe'] });
  running.add(child);
  let stdout = '';
  let stderr = '';
  child.stdout!.on('data', (d) => { stdout += d; });
  child.stderr!.on('data', (d) => { stderr += d; });
  const guard = setTimeout(() => child.kill('SIGKILL'), 420_000); // a hung child never outlives this test
  const code = await new Promise<number | null>((resolve) => child.on('close', resolve));
  clearTimeout(guard);
  running.delete(child);
  const publish = path.join(out, PUBLISH);
  const file = o.valueFree ? path.join(publish, 'reports', 'json-1.json') : path.join(out, 'results.json');
  return { name, out, publish, valueFree: o.valueFree, code, stdout, stderr,
    results: existsSync(file) ? JSON.parse(readFileSync(file, 'utf8')) : null };
}

export function testsOf(run: ChildRun): { title: string; result: JsonResult }[] {
  const found: { title: string; result: JsonResult }[] = [];
  const visit = (suite: JsonSuite) => {
    for (const spec of suite.specs ?? []) for (const t of spec.tests) for (const result of t.results) found.push({ title: spec.title, result });
    for (const inner of suite.suites ?? []) visit(inner);
  };
  for (const suite of run.results?.suites ?? []) visit(suite);
  return found;
}
export const resultOf = (run: ChildRun, title: string): JsonResult => {
  const found = testsOf(run).find((t) => t.title.includes(title))?.result;
  if (!found) throw new Error(`${run.name}: no test "${title}"`);
  return found;
};
export const decode = (value: string) => Buffer.from(value, 'base64').toString('utf8');
export function bodyOf(run: ChildRun, title: string, name: string): string {
  const body = resultOf(run, title).attachments.find((a) => a.name === name)?.body;
  if (body === undefined) throw new Error(`${run.name}: no ${name} attachment body on "${title}"`);
  return decode(body);
}
export const attachmentOf = (run: ChildRun, title: string, name: string): any => JSON.parse(bodyOf(run, title, name));
export const filesUnder = (dir: string) => (existsSync(dir) ? readdirSync(dir, { recursive: true, withFileTypes: true }) : [])
  .filter((e) => e.isFile()).map((e) => path.join(e.parentPath, e.name));
/** Every entry under a directory, directories, links and special files included (their paths are published too). */
export const entriesUnder = (dir: string) => (existsSync(dir) ? readdirSync(dir, { recursive: true, withFileTypes: true }) : [])
  .map((e) => ({ file: path.join(e.parentPath, e.name), isFile: e.isFile(), isDir: e.isDirectory() }));
/** The publish directory's entries, relative and sorted. */
export const published = (run: ChildRun): string[] => entriesUnder(run.publish).map((e) => path.relative(run.publish, e.file)).sort();

type Entry = { name: string; data: Buffer };
const { yauzl } = requireHere('playwright-core/lib/utilsBundle') as { yauzl: any };
export function unzip(buf: Buffer): Promise<Entry[]> {
  return new Promise((resolve, reject) => yauzl.fromBuffer(buf, { lazyEntries: true }, (error: Error | null, zip: any) => {
    if (error) return reject(error);
    const entries: Entry[] = [];
    zip.on('error', reject);
    zip.on('end', () => resolve(entries));
    zip.on('entry', (entry: { fileName: string }) => zip.openReadStream(entry, (e: Error | null, stream: NodeJS.ReadableStream) => {
      if (e) return reject(e);
      const chunks: Buffer[] = [];
      stream.on('data', (c: Buffer) => chunks.push(c));
      stream.on('error', reject);
      stream.on('end', () => { entries.push({ name: entry.fileName, data: Buffer.concat(chunks) }); zip.readEntry(); });
    }));
    zip.readEntry();
  }));
}
const EMBEDDED_ZIP = /data:application\/zip;base64,([A-Za-z0-9+/=]+)/g;
/** How deep the scan decodes: well past lib/value-free-codec.ts's bound, so a payload the harness let through deeper than
 * its own bound is still read here. Deeper than this fails the test. */
export const SCAN_DEPTH = 32;
const isPk = (b: Buffer) => b.length >= 4 && b[0] === 0x50 && b[1] === 0x4b && (b[2] === 3 || b[2] === 5);
const isGzip = (b: Buffer) => b.length >= 2 && b[0] === 0x1f && b[1] === 0x8b;
/** Base64 by its alphabet (standard or url-safe, whitespace allowed); anything else is plain text, read as it is. */
const base64 = (s: string): Buffer | null => {
  const t = s.replace(/\s+/g, '');
  return t.length >= 4 && t.length % 4 !== 1 && /^[A-Za-z0-9+/_-]+={0,2}$/.test(t) ? Buffer.from(t, 'base64') : null;
};
const parse = (s: string): unknown => { try { return JSON.parse(s); } catch { return undefined; } };
/** `strict`: reading a publication, where a layer the scan cannot open fails the test; elsewhere (off, the informational
 * read of what stays on disk) it is read as raw text only. */
type Found = { texts: Text[]; zips: Record<string, string[]>; strict: boolean };

/** Everything a published byte sequence holds, decoded through every layer, independently of lib/value-free-codec.ts:
 * zips (yauzl) and gzip (zlib, which the harness never decodes: it refuses it), embedded zip data: URLs, and in JSON or
 * JSONL the base64 report fields (blob `base64` and `data`, json `buffer` and the json report's `body`), every string
 * and every object key that is itself a JSON document, and every string whose JSON form is escaped (a value with a quote
 * inside a nested document is escaped twice in the outer text). */
async function readAll(buf: Buffer, where: string, depth: number, found: Found): Promise<void> {
  if (depth > SCAN_DEPTH) {
    if (found.strict) throw new Error(`${where}: nested deeper than the scan reads (${SCAN_DEPTH} layers)`);
    return;
  }
  const text = buf.toString('utf8');
  found.texts.push({ where, text });
  if (isPk(buf)) { await readZipAt(buf, where, `${where}#0`, depth, found); return; }
  if (isGzip(buf)) {
    let inflated: Buffer | null = null;
    try { inflated = gunzipSync(buf); } catch (error) { if (found.strict) throw error; }
    if (inflated) await readAll(inflated, `${where} (gzip)`, depth + 1, found);
    return;
  }
  const latin = buf.toString('latin1');
  let i = 0;
  for (const m of latin.matchAll(EMBEDDED_ZIP)) await readZipAt(Buffer.from(m[1], 'base64'), where, `${where}#${i++}`, depth, found);
  if (!/^\s*[[{]/.test(text)) return;
  const whole = parse(text);
  const docs = whole !== undefined ? [whole] : text.split('\n').filter((l) => /^\s*[[{]/.test(l)).map(parse).filter((d) => d !== undefined);
  for (const doc of docs) {
    await readJson(doc, where, depth, Boolean(doc && typeof doc === 'object' && 'suites' in doc && 'config' in doc), found);
  }
}

/** A zip (a file, an entry or an embedded data: URL): every entry's name and contents. One the scan cannot open fails a
 * strict read. */
async function readZipAt(buf: Buffer, where: string, key: string, depth: number, found: Found): Promise<void> {
  let entries: Entry[];
  try { entries = await unzip(buf); } catch (error) { if (found.strict) throw error; return; }
  found.zips[key] = entries.map((e) => e.name);
  for (const e of entries) {
    found.texts.push({ where: `${where} > ${e.name} (its name)`, text: e.name });
    await readAll(e.data, `${where} > ${e.name}`, depth + 1, found);
  }
}

async function readJson(v: unknown, where: string, depth: number, reportBodies: boolean, found: Found): Promise<void> {
  if (Array.isArray(v)) { for (const x of v) await readJson(x, where, depth, reportBodies, found); return; }
  if (!v || typeof v !== 'object') return;
  const o = v as Record<string, unknown>;
  for (const [key, x] of Object.entries(o)) {
    if (JSON.stringify(key).slice(1, -1) !== key) found.texts.push({ where: `${where} (json key)`, text: key });
    // Round 6: an object key that is itself a JSON document is read like a string value, a layer down.
    const keyDoc = /^\s*[[{]/.test(key) ? parse(key) : undefined;
    if (keyDoc !== undefined && keyDoc !== null && typeof keyDoc === 'object') {
      if (depth + 1 > SCAN_DEPTH) {
        if (found.strict) throw new Error(`${where}: a key nested deeper than the scan reads (${SCAN_DEPTH} layers)`);
      } else await readJson(keyDoc, `${where} (json inside a key)`, depth + 1, false, found);
    }
    if (typeof x !== 'string') { await readJson(x, where, depth, reportBodies, found); continue; }
    const encoded = key === 'base64' || key === 'buffer' || (key === 'data' && o.isBase64 === true) || (key === 'body' && reportBodies);
    const bytes = encoded ? base64(x) : null;
    if (bytes) { await readAll(bytes, `${where} (base64 inside)`, depth + 1, found); continue; }
    const doc = /^\s*[[{]/.test(x) ? parse(x) : undefined;
    if (doc !== undefined && typeof doc === 'object') {
      found.texts.push({ where: `${where} (json inside)`, text: x });
      await readJson(doc, `${where} (json inside)`, depth + 1, false, found);
    } else if (JSON.stringify(x).slice(1, -1) !== x) found.texts.push({ where: `${where} (json string)`, text: x });
  }
}

export type Text = { where: string; text: string };
/** What the run published: in value-free mode every path (directories too) and file under its publish directory, every
 * layer decoded (readAll), off every one under its output (the positive control, the publish directory never made); the
 * report's attachments and stdio decoded; the runner's stdout, its stderr, and the two read as one stream. `root`
 * overrides what is read from disk (the informational read of everything a value-free run left). */
export async function produced(run: ChildRun, root = run.valueFree ? run.publish : run.out): Promise<Found> {
  const found: Found = { texts: [{ where: 'runner stdout', text: run.stdout }, { where: 'runner stderr', text: run.stderr },
    { where: 'runner stdout and stderr', text: run.stdout + run.stderr }], zips: {}, strict: run.valueFree && root === run.publish };
  for (const { file, isFile } of entriesUnder(root)) {
    const where = path.relative(root, file);
    found.texts.push({ where: `${where} (its path)`, text: where });
    if (isFile) await readAll(readFileSync(file), where, 0, found);
  }
  for (const { title, result } of testsOf(run)) {
    for (const a of result.attachments) if (a.body !== undefined) found.texts.push({ where: `${title}: ${a.name}`, text: decode(a.body) });
    for (const s of [...result.stdout, ...result.stderr]) if (s.buffer) found.texts.push({ where: `${title}: stdio`, text: decode(s.buffer) });
    // The report's stdout and stderr chunks read back as streams, joined (a value split across chunks is whole again).
    for (const kind of ['stdout', 'stderr'] as const) {
      found.texts.push({ where: `${title}: joined ${kind}`, text: result[kind].map((s) => s.text ?? (s.buffer ? decode(s.buffer) : '')).join('') });
    }
  }
  return found;
}

/** Round 7: what a pull transfers from below `root` (`files`, relative to it), read like a publication: each file's path
 * relative to the pull root, then its contents through every layer (readAll, strict). */
export async function readTransferred(root: string, files: string[]): Promise<Found> {
  const found: Found = { texts: [], zips: {}, strict: true };
  for (const rel of files) {
    found.texts.push({ where: `${rel} (its path)`, text: rel });
    await readAll(readFileSync(path.join(root, rel)), rel, 0, found);
  }
  return found;
}

const GENERIC: [string, RegExp][] = [['email', /[\w.%+-]+@[\w-]+(?:\.[\w-]+)*\.[A-Za-z]{2,}/],
  ['UUID', /\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b/],
  ['32-hex key', /\b[0-9a-fA-F]{32}\b/], ['JWT', /\beyJ[\w-]+\.[\w-]+\.[\w-]+/]];
export type Forbidden = [label: string, value: string][];
/** Any text with every value masked: what a reader needs (the field), never the value. */
export function mask(text: string, forbidden: Forbidden): string {
  let out = text;
  for (const [label, value] of forbidden) out = out.split(value).join(`<${label}>`);
  for (const [label, re] of GENERIC) out = out.replace(new RegExp(re.source, 'g'), `<${label}>`);
  return out.replace(/[0-9a-fA-F]{8,}/g, '<hex>').replace(/e2e-[\w-]+/g, '<e2e-name>').replace(/[^\x20-\x7e]/g, '?');
}
const around = (text: string, at: number, length: number, forbidden: Forbidden) =>
  mask(`${text.slice(Math.max(0, at - 48), at)}<HIT>${text.slice(at + length, at + length + 48)}`, forbidden);

/** Text with every escape sequence removed (ECMA-48: CSI with any parameter bytes, colon-form colours included; OSC
 * and the other string controls up to their terminator; any other escape), the JSON-escaped forms decoded first. */
export function normalize(text: string): string {
  return text.replace(/\\u001[bB]/g, '\u001b').replace(/\\u009([bBcCdD])/g, (_m, c: string) => String.fromCharCode(0x90 + parseInt(c, 16)))
    .replace(/\\u0007/g, '\u0007')
    .replace(/(?:\u001b[\]PX^_]|[\u0090\u0098\u009d-\u009f])[\s\S]*?(?:\u0007|\u001b\\|\u009c)/g, '')
    .replace(/(?:\u001b\[|\u009b)[0-?]*[ -/]*[@-~]/g, '')
    .replace(/\u001b[ -/]*[0-~]/g, '');
}

/** Where a raw value (by category) or a generic email/UUID/key/JWT shape appears, colours removed or not. */
/** Whether `value` and the checkout path `checkout` overlap, case folded: the value inside the path (one component, or
 * across them), or a component inside the value (708 add.68: no substring overlap either way). */
export function overlapsPath(value: string, checkout: string): boolean {
  const v = value.toLowerCase();
  return checkout.toLowerCase().includes(v) || checkout.split(path.sep).filter(Boolean).some((c) => v.includes(c.toLowerCase()));
}

/** The first occurrence of `value` in `view` that does not lie wholly inside an occurrence of `exempt`; -1 when none. */
function firstOutside(view: string, value: string, exempt?: string): number {
  const spans: [number, number][] = [];
  if (exempt) for (let i = view.indexOf(exempt); i >= 0; i = view.indexOf(exempt, i + 1)) spans.push([i, i + exempt.length]);
  for (let at = view.indexOf(value); at >= 0; at = view.indexOf(value, at + 1)) {
    if (!spans.some(([start, end]) => at >= start && at + value.length <= end)) return at;
  }
  return -1;
}

/** `exempt` (round 10, 708 add.68): the checkout's own path. An occurrence of a forbidden value wholly inside an
 * occurrence of it is that path, not a published value; any other occurrence is a leak as before. */
export function leaks(texts: Text[], forbidden: Forbidden, exempt?: string): string[] {
  // Machine paths (the rig's job directory may hold UUID-shaped segments) are not published values.
  const paths = [path.resolve(E2E_DIR, '..'), os.homedir(), os.tmpdir()].sort((a, b) => b.length - a.length);
  const found: string[] = [];
  for (const { where, text } of texts) {
    const place = mask(where, forbidden);
    for (const view of new Set([text, normalize(text)])) {
      for (const [label, value] of forbidden) {
        const at = firstOutside(view, value, exempt);
        if (at >= 0) found.push(`${place}: ${label} near ${around(view, at, value.length, forbidden)}`);
      }
      const swept = paths.reduce((acc, p) => acc.split(p).join('<path>'), view);
      for (const [label, re] of GENERIC) {
        const hit = re.exec(swept);
        if (hit) found.push(`${place}: ${label} shape near ${around(swept, hit.index, hit[0].length, forbidden)}`);
      }
    }
  }
  return [...new Set(found)];
}

async function query<T extends pg.QueryResultRow>(sql: string, args: unknown[] = []): Promise<T[]> {
  const client = new pg.Client({ connectionString: E2E.pgUrl });
  await client.connect();
  try { return (await client.query<T>(sql, args)).rows; } finally { await client.end(); }
}
export const databaseNow = async (): Promise<Date> => (await query<{ now: Date }>('SELECT now() AS now'))[0].now;
export const seededUsersSince = async (since: Date): Promise<number> => Number((await query<{ n: string }>(
  `SELECT count(*) AS n FROM accounts_user WHERE created_at >= $1 AND email LIKE '%e2e-h1-%'`, [since]))[0].n);

/** Every raw value Postgres holds for the actors seeded since `since`: users (and their names), orgs, every workspace
 * of those orgs (the owners' default ones too), every name and display name, every key. */
export async function rawIdentities(since: Date, until?: Date) {
  // `until` (round 10): only the actors seeded before it, so one run's window can be read apart from the next.
  const users = await query<{ id: string; email: string; name: string; org: string | null }>(`SELECT id::text, email, name,
    organization_id::text AS org FROM accounts_user WHERE created_at >= $1 AND ($2::timestamptz IS NULL OR created_at < $2)
    AND email LIKE '%e2e-h1-%'`, [since, until ?? null]);
  const members = await query<{ org: string }>(`SELECT DISTINCT organization_id::text AS org
    FROM accounts_organization_membership WHERE user_id = ANY($1::uuid[])`, [users.map((u) => u.id)]);
  const orgs = [...new Set([...users.map((u) => u.org), ...members.map((m) => m.org)].filter((o): o is string => Boolean(o)))];
  const workspaces = await query<{ id: string; name: string; display_name: string }>(
    'SELECT id::text, name, display_name FROM accounts_workspace WHERE organization_id = ANY($1::uuid[])', [orgs]);
  const orgNames = await query<{ name: string; display_name: string }>(
    'SELECT name, display_name FROM accounts_organization WHERE id = ANY($1::uuid[])', [orgs]);
  const keys = await query<{ a: string; s: string }>(
    'SELECT api_key AS a, secret_key AS s FROM accounts_orgapikey WHERE organization_id = ANY($1::uuid[])', [orgs]);
  return { users, orgs, workspaces, orgNames, keys };
}

/** The scan's oracle: every raw value above, by category, with no exception for a short or a shared one (the signup
 * domain's org name, "Default Workspace"). */
export function forbiddenOf(raw: Awaited<ReturnType<typeof rawIdentities>>): Forbidden {
  const all: Forbidden = [
    ...raw.users.flatMap((u): Forbidden => [['email', u.email], ['user id', u.id], ['email local part', u.email.split('@')[0]],
      ['user name', u.name], ...(u.email.match(/e2e-h1-\d+-[0-9a-z]+/g) ?? []).map((p): [string, string] => ['run prefix', p])]),
    ...raw.orgs.map((o): [string, string] => ['org id', o]),
    ...raw.workspaces.flatMap((w): Forbidden => [['workspace id', w.id], ['workspace name', w.name], ['workspace name', w.display_name]]),
    ...raw.orgNames.flatMap((o): Forbidden => [['org name', o.name], ['org name', o.display_name]]),
    ...raw.keys.flatMap((k): Forbidden => [['full key', k.a], ['full key', k.s],
      ...displayedKeys(k.a, k.s).map((d): [string, string] => ['displayed key', d])]),
  ];
  return all.filter(([, value], i) => value && all.findIndex(([, v]) => v === value) === i);
}

/** An org key's 8-character prefix, read back from Postgres. */
export const keyPrefixOf = async (organizationId: string): Promise<string> => (await query<{ a: string }>(
  'SELECT api_key AS a FROM accounts_orgapikey WHERE organization_id = $1 LIMIT 1', [organizationId]))[0].a.slice(0, 8);

/** The names of every workspace and of the orgs themselves, read back from Postgres (the default workspaces too). */
export const namesOf = async (organizationIds: string[]): Promise<string[]> => [...new Set([
  ...(await query<{ n: string }>('SELECT name AS n FROM accounts_workspace WHERE organization_id = ANY($1::uuid[])', [organizationIds])),
  ...(await query<{ n: string }>('SELECT name AS n FROM accounts_organization WHERE id = ANY($1::uuid[])', [organizationIds])),
].map((r) => r.n))].sort();
