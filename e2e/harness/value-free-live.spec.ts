import { createHash, randomBytes, randomUUID } from 'node:crypto';
import { existsSync, readFileSync, writeFileSync } from 'node:fs';
import path from 'node:path';
import { request as pwRequest, test, expect } from '@playwright/test';
import { E2E } from '../lib/env';
import { provisionActor } from '../lib/provisioning';
import type { ScopeActor } from '../lib/scope-actors';
import { stripAnsi, TOKEN_SHAPE, ValueFree, VALUE_FREE_MIN_LENGTH } from '../lib/value-free';
import { STDIO_POINTER } from '../lib/value-free-publish';
import { checkBeforePull, refusalReasons } from '../lib/value-free-seal.mjs';
import { assertValueFreeReceipt } from '../lib/walk/receipt-values.mjs';
import { privatePath, removePrivate } from './value-free/private-dir';
import { attachmentOf, databaseNow, decode, E2E_DIR, filesUnder, forbiddenOf, keyPrefixOf, killChildren, leaks, overlapsPath, produced,
  published, rawIdentities, resultOf, runChild, seededUsersSince, testsOf, type ChildRun, type Forbidden } from './value-free/scan';

// Child runs live in a private directory per test, outside the evidence tree (value-free/private-dir.ts); the masked
// findings report (value-free-leaks.json) is evidence by design and stays in the test's output directory.
test.afterEach(async ({}, testInfo) => removePrivate(testInfo));

// HR-JRN-01, live (Standalone stack): value-free/j03-actors.child.ts runs twice with the
// switch (a salt it is given and its json reports selected by outputFile options, then
// the runner's own salt and the report PLAYWRIGHT_JSON_OUTPUT_NAME selects) and once
// without it (the off control); value-free/short-name.child.ts runs with the switch and
// must be refused; then, with no switch, value-free/refuse.child.ts with the explicit
// option and the J03 child with the reporter listed, both of which must be refused.
//
// Round 4: what a value-free run publishes is its publish directory and its stdout and
// stderr, nothing else, and that is what is scanned: every path (directories too) and
// file under value-free-publish, its json, html and blob reports with their zips
// opened, every report's per-test stdio joined, and the runner's stdout, stderr and the
// two as one stream, for every raw value Postgres holds for the seeded actors (no
// exception for a short or a shared name) and for the probes. What a value-free run
// leaves elsewhere on disk is read too, for information only. Off publishes no
// directory: everything it leaves is read, the positive control.
const PROBE = 'hr-jrn-01-probe';
const tok = (kind: string) => `vf-[a-z]{5}-${kind}-[0-9a-f]{16}`;
const token = (kind: string) => new RegExp(`^${tok(kind)}$`);
const sha256 = (data: string | Buffer) => createHash('sha256').update(data).digest('hex');
const J03 = 'value-free/j03-actors.child.ts';
const LOG = 'log line';
const FAIL = 'failing expect';
const titled = (run: ChildRun) => testsOf(run).find((t) => t.title.startsWith('id '))!;
const keyTitled = (run: ChildRun) => testsOf(run).find((t) => t.title.startsWith('key '))!;
const KEY_TITLE = new RegExp(`^key ${tok('value')}$`);
const runnerLog = (run: ChildRun) => readFileSync(path.join(run.publish, 'runner.log'), 'utf8');
const ALLOWED_TOP = ['MANIFEST.json', 'attachments', 'html', 'reports', 'runner.log'];

test('value-free live: J03 actors and the adversarial probes publish no raw value in the publish directory or the output',
  async ({}, testInfo) => {
    test.setTimeout(1_500_000); // three J03 child runs, each provisioning the J03 set twice (one per worker), and four refusals
    const since = await databaseNow();
    const saltA = randomBytes(32).toString('hex');
    // An org key seeded here and read back from Postgres: a child puts its prefix in a test title and a report's name.
    const req = await pwRequest.newContext({ baseURL: E2E.apiUrl });
    const keyPrefix = await provisionActor(req, 'h1-keyprobe').then((owner) => keyPrefixOf(owner.organizationId))
      .finally(() => req.dispose());
    const letter = (from: string) => from[randomBytes(1)[0] % from.length];
    // One character short of the safe-substitution length.
    const shortName = `${letter('QXZ')}${letter('qxz')} ${letter('QXZ')}${letter('qxz')}`;
    expect(shortName.length).toBe(VALUE_FREE_MIN_LENGTH - 1);
    const probes = { orgName: `Probe organization ${randomBytes(6).toString('hex')}`, titleId: randomUUID(), keyPrefix, shortName };
    const runs: ChildRun[] = [];
    let refuseFrom = since;
    // Round 10 (708 add.68): each value-free run's own window (A and B, then the short run), apart from the off run's and
    // the key probe's.
    const valueFreeFrom = await databaseNow();
    let offFrom = since;
    let shortFrom = since;
    try {
      runs.push(await runChild('value-free A (given salt)', privatePath(testInfo, 'run-a'), { file: J03, valueFree: true, salt: saltA, probes }));
      runs.push(await runChild('value-free B (own salt)', privatePath(testInfo, 'run-b'),
        { file: J03, valueFree: true, jsonByName: true, probes }));
      offFrom = await databaseNow();
      runs.push(await runChild('off', privatePath(testInfo, 'run-off'), { file: J03, valueFree: false, probes }));
      shortFrom = await databaseNow();
      runs.push(await runChild('value-free, a name too short to hide', privatePath(testInfo, 'run-short'),
        { file: 'value-free/short-name.child.ts', valueFree: true, probes }));
      refuseFrom = await databaseNow();
      runs.push(await runChild('explicit option, no switch', privatePath(testInfo, 'run-refuse'),
        { file: 'value-free/refuse.child.ts', valueFree: false, probes }));
      runs.push(await runChild('reporter listed, no switch', privatePath(testInfo, 'run-refuse-reporter'),
        { file: J03, valueFree: false, listReporter: true, probes }));
    } finally { killChildren(); }
    const [runA, runB, off, short, refuse, refuseReporter] = runs;
    for (const run of [runA, runB, off]) {
      expect(run.code, `${run.name}: child exit (stats ${JSON.stringify(run.results?.stats ?? null)})`).toBe(0);
      expect(run.results!.stats, `${run.name}: four expected outcomes`)
        .toMatchObject({ expected: 4, unexpected: 0, flaky: 0, skipped: 0 });
      expect(testsOf(run)).toHaveLength(4);
      expect(resultOf(run, LOG).workerIndex, `${run.name}: the two J03 tests ran on two workers`).not.toBe(resultOf(run, FAIL).workerIndex);
    }

    // The explicit option alone is refused before anything is provisioned.
    expect(refuse.code, 'the refused child fails').toBe(1);
    expect(refuse.results!.stats).toMatchObject({ expected: 0, unexpected: 1 });
    expect(stripAnsi(resultOf(refuse, 'explicit option').errors.map((e) => e.message ?? '').join('\n')))
      .toContain('value-free mode is one switch: set E2E_VALUE_FREE=1 for the run');
    // The reporter listed without the switch refuses the whole run before any test starts.
    expect(refuseReporter.code, 'the run with the reporter alone fails').toBe(1);
    expect(refuseReporter.results, 'no report: no test ran').toBeNull();
    expect(stripAnsi(refuseReporter.stdout + refuseReporter.stderr)).toContain('lib/value-free-reporter.ts is listed but E2E_VALUE_FREE=1 is not set');
    expect(existsSync(refuseReporter.out) ? filesUnder(refuseReporter.out) : [], 'and no output').toEqual([]);
    expect(await seededUsersSince(refuseFrom), 'neither refusal provisioned anybody').toBe(0);

    // A value too short to hide refuses the run: it fails, publishes only its refusal record, and prints only why.
    expect(short.code, 'the run with a name too short to hide fails').toBe(1);
    expect(short.results, 'and publishes no report').toBeNull();
    expect(published(short), 'its publish directory holds only the refusal record').toEqual(['REFUSED.json']);
    expect(refusalReasons(short.publish)).toContainEqual(expect.objectContaining({ reason: 'short-value', kind: 'workspace name',
      length: VALUE_FREE_MIN_LENGTH - 1 }));
    const refusal = stripAnsi(short.stdout + short.stderr);
    expect(refusal).toContain('value-free: REFUSED');
    expect(refusal).toContain(`a workspace name of ${VALUE_FREE_MIN_LENGTH - 1} characters`);
    expect(refusal, 'its held output (the log line, the failure) is withheld').not.toContain('vf-short');

    const raw = await rawIdentities(since);
    expect(raw.users.length, 'three runs x two tests x five actors, the refused run and the key probe').toBeGreaterThanOrEqual(30);
    expect(raw.keys.length, 'an org key per owner').toBeGreaterThanOrEqual(12);
    // Every raw value, the names every seeded org shares included (the signup domain's org name, the default
    // workspace), and the probes'; no length filter: the short name is in it.
    const probed: Forbidden = [['probe org name', probes.orgName], ['probe title id', probes.titleId],
      ['probe key prefix', keyPrefix], ['probe short name', shortName]];
    const forbidden: Forbidden = [...forbiddenOf(raw), ...probed]
      .filter(([, value], i, all) => all.findIndex(([, v]) => v === value) === i).sort((a, b) => b[1].length - a[1].length);
    const shared = (names: string[]) => names.filter((n, i) => names.indexOf(n) !== i);
    expect(shared(raw.workspaces.map((w) => w.name)), 'a workspace name every seeded org shares is in the oracle').not.toEqual([]);
    expect(shared(raw.orgNames.map((o) => o.name)), 'so is an org name they share').not.toEqual([]);
    // 708 add.68 (round 10): the checkout's own path is not a registered value. Every value the value-free runs seeded,
    // read back for their own windows, is disjoint from it and each of its components, both ways (value-free ScopeActors
    // sign up at their own prefix's domain); the off run and the key probe sign up at futureagi.com, whose org name is a
    // component of it. So in a value-free run's output an occurrence of a forbidden value wholly inside an occurrence of
    // the checkout path is that path, not a leak; every other occurrence still is.
    const checkout = path.resolve(E2E_DIR, '..');
    const own = [...forbiddenOf(await rawIdentities(valueFreeFrom, offFrom)), ...forbiddenOf(await rawIdentities(shortFrom, refuseFrom))];
    expect(own.length, 'the value-free runs seeded values').toBeGreaterThan(0);
    expect(own.filter(([, v]) => overlapsPath(v, checkout)).map(([label, v]) => ({ label, length: v.length })),
      'no value a value-free run seeded overlaps the checkout path (708 add.68)').toEqual([]);
    const [scanA, scanB, scanOff, scanShort] = [await produced(runA), await produced(runB), await produced(off), await produced(short)];
    // For information only (never pulled): what the value-free runs leave on disk outside their publish directory.
    const onDisk = async (run: ChildRun) => leaks((await produced(run, run.out)).texts
      .filter((t) => !t.where.startsWith('value-free-publish')), forbidden, checkout).length;
    const findings = {
      [runA.name]: leaks(scanA.texts, [...forbidden, ['salt', saltA]], checkout),
      [runB.name]: leaks(scanB.texts, forbidden, checkout),
      [short.name]: leaks(scanShort.texts, forbidden, checkout),
      [off.name]: leaks(scanOff.texts, forbidden).length,
      zipsOpened: Object.fromEntries([[runA.name, Object.keys(scanA.zips).length], [runB.name, Object.keys(scanB.zips).length]]),
      informationalOutsidePublish: { [runA.name]: await onDisk(runA), [runB.name]: await onDisk(runB), [short.name]: await onDisk(short) },
    };
    // Masked: categories, places and field context only, so the gate may publish it.
    writeFileSync(testInfo.outputPath('value-free-leaks.json'), `${JSON.stringify(findings, null, 2)}\n`);

    expect(findings[short.name], `${short.name}: published raw values`).toEqual([]);
    expect(checkBeforePull(short.publish).code, 'a refused run: the launcher pulls only its refusal record').toBe(1);
    expect(published(short)).toEqual(['REFUSED.json']);
    for (const [run, scan] of [[runA, scanA], [runB, scanB]] as const) {
      expect(findings[run.name], `${run.name}: published raw values`).toEqual([]);
      // Only the allowlisted sources, under harness names.
      const entries = published(run);
      expect([...new Set(entries.map((e) => e.split(path.sep)[0]))].sort(), `${run.name}: allowlisted sources only, all of them`)
        .toEqual(ALLOWED_TOP);
      expect(entries.filter((e) => /\.(bin|png|webm|jpe?g|zip)$/.test(e) && !/^reports\/blob-\d+\.zip$/.test(e)),
        `${run.name}: no binary published`).toEqual([]);
      expect(entries.filter((e) => e.startsWith('attachments/')).every((e) => /^attachments\/\d+\.[a-z]+$/.test(e))).toBe(true);
      expect(entries.filter((e) => /evidence-|title-probe|key-probe/.test(e)), `${run.name}: loose files stay on the machine`).toEqual([]);
      // The scan read the reports, not just their wrappers: html's embedded data and the blob, both opened.
      const opened = Object.entries(scan.zips);
      expect(opened.some(([where, names]) => where.startsWith('html/index.html') && names.includes('report.json'))).toBe(true);
      expect(opened.some(([where, names]) => /^reports\/blob-1\.zip/.test(where) && names.some((n) => n.endsWith('.jsonl')))).toBe(true);
      const blob = scan.texts.find((t) => /^reports\/blob-1\.zip > .*\.jsonl$/.test(t.where))!.text;
      expect(blob, `${run.name}: the blob report carries tokens`).toMatch(new RegExp(tok('email')));
      expect(blob, `${run.name}: and no stdio event (runner.log is their publication)`).not.toContain('"onStdIO"');
      expect(checkBeforePull(run.publish), `${run.name}: the seal holds`).toEqual(expect.objectContaining({ code: 0 }));

      const failing = resultOf(run, FAIL);
      expect(failing.status).toBe('failed');
      expect(stripAnsi(failing.errors.map((e) => e.message ?? '').join('\n')), `${run.name}: the failure reached the report, tokenized`)
        .toMatch(new RegExp(`Expected: "${tok('email')}"`));
      // The log line and the colour-split UUID: in runner.log and the printed output, as one sanitized stream; every
      // report says where instead of carrying the chunks.
      const merged = runnerLog(run);
      expect(merged, `${run.name}: the log line, tokenized`).toMatch(new RegExp(`vf-log-line ${tok('email')} ${tok('id')} ${tok('key')}`));
      expect(merged, `${run.name}: the colour-split UUID, colours removed and tokenized`).toMatch(new RegExp(`vf-ansi ${tok('id')}\\n`));
      expect(stripAnsi(run.stdout), `${run.name}: printed as it was published`).toContain(merged.split('\n').find((l) => l.startsWith('vf-log-line'))!);
      expect(resultOf(run, LOG).stdout.map((s) => s.text ?? '').join(''), `${run.name}: the report points to runner.log`).toBe(STDIO_POINTER);

      // One token per value across workers and across the worker's evidence and the reporter's plain attachment.
      const identities = attachmentOf(run, LOG, 'vf-identities');
      expect(attachmentOf(run, FAIL, 'vf-probe').probe, `${run.name}: one token across workers`).toBe(identities.probe);
      const plain = attachmentOf(run, LOG, 'vf-plain');
      for (const field of ['org', 'workspace', 'prefix', 'masked', 'email', 'organizationId'] as const) {
        expect(plain[field], `${run.name}: ${field} is tokenized by the reporter as the worker tokenized it`)
          .toBe(identities[field] ?? identities.actors[0][field]);
        expect(plain[field]).toMatch(TOKEN_SHAPE);
      }
      expect(identities.registry && existsSync(identities.registry), `${run.name}: the run's registry is gone`).toBe(false);
      expect(typeof identities.registry).toBe('string');
      // Every workspace and org name of both orgs, read from Postgres (the shared defaults too): tokens, the same in the
      // worker's evidence and in the reporter's plain attach.
      expect(identities.names.length, `${run.name}: the names probe`).toBe(attachmentOf(off, LOG, 'vf-identities').names.length);
      for (const name of identities.names) expect(name, `${run.name}: a workspace or org name`).toMatch(TOKEN_SHAPE);
      expect(plain.names, `${run.name}: names tokenized by the reporter as the worker tokenized them`).toEqual(identities.names);

      // An org key's prefix in a title, printed by the line reporter before the worker registered it: tokenized in the
      // report and in the runner's stdout (held until the run ended).
      const keyed = keyTitled(run);
      expect(keyed.title).toMatch(KEY_TITLE);
      expect(decode(keyed.result.attachments.find((a) => a.name === 'vf-key-title')!.body!)).toBe(keyed.title);
      expect(stripAnsi(run.stdout), `${run.name}: the line reporter's title, scrubbed`).toContain(` ${keyed.title}`);

      // A UUID in a title: tokenized in the report.
      const { title, result } = titled(run);
      expect(title).toMatch(new RegExp(`^id ${tok('id')}$`));
      expect(decode(result.attachments.find((a) => a.name === 'vf-title')!.body!)).toBe(title);
      // Binary: withheld in the report and never published.
      for (const name of ['vf-binary', 'vf-binary-source']) {
        const a = resultOf(run, LOG).attachments.find((x) => x.name === name)!;
        expect(a.path, `${run.name}: ${name} keeps no file`).toBeUndefined();
        expect(decode(a.body!)).toBe('withheld in value-free mode: application/octet-stream');
      }
      expect(attachmentOf(run, LOG, 'vf-source').email, `${run.name}: attachEvidence's text file, inline`).toMatch(token('email'));
      // A text file attached by path (Playwright's own copy): published by content under a harness name.
      const byPath = resultOf(run, LOG).attachments.find((a) => a.name === 'vf-text-file')!;
      expect(byPath.path, `${run.name}: a harness name in the publish directory`).toMatch(/^attachments\/\d+\.txt$/);
      expect(JSON.parse(readFileSync(path.join(run.publish, byPath.path!), 'utf8')).email).toMatch(token('email'));
      // The walk receipt: sanitized, then the walk-harness guard passed it.
      const receipt = decode(resultOf(run, LOG).attachments.find((a) => a.name === 'walk-receipt')!.body!);
      expect(() => assertValueFreeReceipt(receipt), `${run.name}: the published receipt passes the receipt guard`).not.toThrow();
    }
    // A: both json reports published under harness names; neither name carries the key prefix.
    const reportsA = published(runA).filter((e) => e.startsWith('reports/json-'));
    expect(reportsA, 'A: the two json reports, renamed by the harness').toEqual(['reports/json-1.json', 'reports/json-2.json']);
    for (const rel of reportsA) {
      expect(JSON.parse(readFileSync(path.join(runA.publish, rel), 'utf8')).stats).toMatchObject({ expected: 4, unexpected: 0 });
    }
    // B: the report PLAYWRIGHT_JSON_OUTPUT_NAME selected is the one published.
    expect(published(runB).filter((e) => e.startsWith('reports/json-'))).toEqual(['reports/json-1.json']);

    const vfA = new ValueFree(saltA);
    const probeA = attachmentOf(runA, LOG, 'vf-identities').probe;
    expect(probeA, 'run A tokens are HMAC-SHA256 under the salt it was given').toBe(vfA.token(PROBE));
    expect(attachmentOf(runB, LOG, 'vf-identities').probe).toMatch(token('value'));
    expect(attachmentOf(runB, LOG, 'vf-identities').probe, 'the next run, the next salt').not.toBe(probeA);
    expect(attachmentOf(runA, LOG, 'vf-plain').org, 'the registered org name, under the given salt').toBe(vfA.token(probes.orgName));
    expect(keyTitled(runA).title, 'the key prefix in a title, under the given salt').toBe(`key ${vfA.token(keyPrefix)}`);

    // Within run A every path names Owner A with the same tokens, and they are tokens of real rows.
    const seeded = attachmentOf(runA, LOG, 'seeded-scopes');
    expect(attachmentOf(runA, LOG, 'scope-api-receipt').actors).toEqual(seeded.actors);
    const ownerA = seeded.actors[0];
    const identities = attachmentOf(runA, LOG, 'vf-identities');
    expect(identities.actors[0]).toEqual({ email: ownerA.email, userId: ownerA.userId,
      organizationId: ownerA.organizationId, workspaceId: ownerA.workspaceId });
    expect(identities.keyed).toEqual({ [ownerA.organizationId]: ownerA.email });
    expect(seeded.emptyWorkspace.name, 'the workspace name, registered').toBe(identities.workspace);
    expect(attachmentOf(runA, LOG, 'walk-receipt').verdicts).toEqual([{ feature: 'vf-probe', lane: 'api',
      where: ownerA.email, verdict: 'RECORDED', detail: { organizationId: ownerA.organizationId, key: identities.owners[0].apiKey } }]);
    const known = new Set([...raw.users.flatMap((u) => [u.id, u.email]), ...raw.orgs, ...raw.workspaces.map((w) => w.id)]
      .map((v) => vfA.token(v)));
    for (const title of [LOG, FAIL]) {
      for (const a of attachmentOf(runA, title, 'seeded-scopes').actors) {
        for (const t of [a.email, a.userId, a.organizationId, a.workspaceId]) expect(known.has(t), `${title}: ${t}`).toBe(true);
      }
    }

    // Off: today's evidence, raw and byte for byte, no publish directory, and the scan finds it (its positive control).
    expect(existsSync(off.publish), 'off makes no publish directory').toBe(false);
    expect(scanOff.texts.some((t) => TOKEN_SHAPE.test(t.text)), 'off publishes no token anywhere').toBe(false);
    expect(findings[off.name], 'the scan finds what off mode publishes').toBeGreaterThan(0);
    const identity = attachmentOf(off, LOG, 'vf-off-identity');
    expect(identity.seededEqualsLegacy, 'off evidence() is the base expression').toBe(true);
    expect(sha256(Buffer.from(resultOf(off, LOG).attachments.find((a) => a.name === 'vf-plain')!.body!, 'base64')),
      'off attachment bytes reach the report unchanged').toBe(identity.plainSha256);
    const offSeeded = attachmentOf(off, LOG, 'seeded-scopes');
    expect(Object.keys(offSeeded)).toEqual(['prefix', 'emptyWorkspace', 'limitations', 'actors', 'requests']);
    expect(offSeeded.prefix).toMatch(/^e2e-h1-\d+-[0-9a-z]+$/);
    const emails = new Set(raw.users.map((u) => u.email));
    expect(offSeeded.actors.every((a: ScopeActor) => emails.has(a.email)), 'off evidence carries the real emails').toBe(true);
    // Off, the receipt guard (walk-harness, every receipt) refuses the raw email this control writes: an expected failure.
    expect(resultOf(off, LOG).status, 'off: the receipt write is refused').toBe('failed');
    expect(stripAnsi(resultOf(off, LOG).errors.map((e) => e.message ?? '').join('\n'))).toContain('walk receipt refused: email-shaped value');
    expect(filesUnder(off.out).filter((f) => f.endsWith('walk-receipt.json')), 'and no receipt is written').toEqual([]);
    expect(filesUnder(off.out).some((f) => f.endsWith('.bin')), 'off leaves binary attachments as they were').toBe(true);
    expect(titled(off).title, 'off leaves titles as they were').toBe(`id ${probes.titleId}`);
    expect(keyTitled(off).title).toBe(`key ${keyPrefix}`);
    expect(stripAnsi(off.stdout), 'off prints the key-titled test as it is').toContain(`key ${keyPrefix}`);
    expect(existsSync(path.join(off.out, `report-${keyPrefix}.json`)), 'off keeps the report name as given').toBe(true);
    const offNames: string[] = attachmentOf(off, LOG, 'vf-identities').names;
    expect(offNames.filter((n) => shared(raw.workspaces.map((w) => w.name)).includes(n)), 'off: the shared default workspace name')
      .not.toEqual([]);
    expect(offNames.filter((n) => shared(raw.orgNames.map((o) => o.name)).includes(n)), 'off: the shared org name').not.toEqual([]);
  });
