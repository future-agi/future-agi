// HR-JRN-01 child (child.config.ts; spawned by harness/value-free-live.spec.ts). The
// J03 two-tenant actor set exactly as flows/journeys/two-tenant-isolation.spec.ts asks
// for it (the default Owner A, Member and Viewer, Owner B, and a B Member), with raw
// values published on purpose through every path a journey has, plus the adversarial
// probes: ScopeActors evidence, the walk receipt, attachEvidence (body, text file,
// binary file), a plain attach of a text file by path, a plain testInfo.attach of a
// registered org name, the workspace name
// and the displayed key, a binary attachment, a file left in the output directory, a
// log line, a UUID split by colour codes, a UUID in a test title and a failing expect;
// the names of every workspace and org of both owners, read back from Postgres (the
// default workspaces and the signup domain's org name, which every seeded org shares);
// and an org key's prefix in a test title, which the line reporter prints before this
// run's worker registers it. The parent scans what this run publishes.
//
// The walk receipt runs walk-harness's guard (lib/walk/receipt-values.mjs) on every
// receipt in both modes. Value-free, the receipt is sanitized first and passes. Off,
// this control writes the raw email into it on purpose, so the guard refuses the
// write at fixture teardown: that test is an expected failure off, carrying the
// guard's message, and the raw values still reach off mode's report and log.
import { createHash } from 'node:crypto';
import { writeFileSync } from 'node:fs';
import { attachEvidence, displayedKeys, sharedValueFree } from '../../lib/value-free';
import { test, expect } from '../../lib/walk/fixtures';
import { namesOf } from './scan';

// The same string in two tests on two workers: one token per run (the parent reads it).
const PROBE = 'hr-jrn-01-probe';
// From the parent: an org name nothing but registration can recognise, the UUID in a title, and the 8-character
// prefix of an org key the parent seeded and read back from Postgres.
const probes = JSON.parse(process.env.E2E_VALUE_FREE_PROBES ?? 'null') as { orgName: string; titleId: string; keyPrefix: string };
if (!probes) throw new Error('E2E_VALUE_FREE_PROBES is set by harness/value-free-live.spec.ts');
const sha256 = (text: string) => createHash('sha256').update(text).digest('hex');

test.use({ scopeRoster: { orgB: [3] }, trace: 'off', screenshot: 'off', video: 'off' });

test('value-free child: J03 actors through evidence, receipt, attachments and a log line',
  async ({ scopeActors: scopes, walk }, testInfo) => {
    test.fail(scopes.valueFree === null, 'off: the walk receipt guard refuses the raw email this control writes on purpose');
    expect(scopes.valueFree === null, 'value-free exactly when the run switch is set').toBe(process.env.E2E_VALUE_FREE !== '1');
    scopes.valueFree?.register(PROBE, probes.orgName);
    const a = scopes.ownerA;
    const key = scopes.owners[0].apiKey;
    const [prefix, masked] = displayedKeys(key);
    // Every workspace and org name of both seeded orgs, as Postgres holds them: registered by the fixture's own read.
    const names = await namesOf([a.organizationId, scopes.ownerB.organizationId]);
    const shown = { org: probes.orgName, workspace: scopes.emptyWorkspace.name, prefix, masked, names };
    await attachEvidence(testInfo, 'vf-identities', { contentType: 'application/json', body: JSON.stringify({
      probe: PROBE, ...shown,
      actors: [a, scopes.member, scopes.viewer, scopes.ownerB, ...scopes.orgBMembers].map(
        ({ email, userId, organizationId, workspaceId }) => ({ email, userId, organizationId, workspaceId })),
      emptyWorkspace: scopes.emptyWorkspace,
      owners: scopes.owners.map(({ email, organizationId, apiKey, secretKey }) => ({ email, organizationId, apiKey, secretKey })),
      // J03 keys table rows by column id: object keys are published too.
      keyed: { [a.organizationId]: a.email },
      registry: process.env.E2E_VALUE_FREE_REGISTRY ?? null,
    }) });
    // A plain attach, as J03 does today: only the reporter stands between it and the report.
    const plain = JSON.stringify({ email: a.email, organizationId: a.organizationId, workspaceId: a.workspaceId, ...shown });
    await testInfo.attach('vf-plain', { contentType: 'application/json', body: plain });
    if (!scopes.valueFree) {
      // Off: evidence() is the base commit's expression, byte for byte, and the report keeps attachments as given.
      const legacy = JSON.stringify({ prefix: scopes.prefix, emptyWorkspace: scopes.emptyWorkspace, limitations: scopes.limitations,
        actors: [scopes.ownerA, scopes.ownerB, scopes.member, scopes.viewer, scopes.admin, scopes.member2, ...scopes.orgBMembers]
          .filter(Boolean).map((x) => ({ userId: x!.userId, email: x!.email, organizationId: x!.organizationId, workspaceId: x!.workspaceId })),
        requests: scopes.receipts });
      await testInfo.attach('vf-off-identity', { contentType: 'application/json', body: JSON.stringify({
        seededEqualsLegacy: JSON.stringify(scopes.evidence()) === legacy, plainSha256: sha256(plain) }) });
    }
    console.log(`vf-log-line ${a.email} ${a.organizationId} ${key}`);
    console.log(`vf-names ${names.join(' | ')}`);
    const id = a.organizationId;
    console.log(`vf-ansi ${id.slice(0, 9)}\u001b[7m${id.slice(9, 13)}\u001b[27m${id.slice(13)}`);
    const raw = JSON.stringify({ email: a.email, id, ...shown });
    const binary = testInfo.outputPath('evidence.bin');
    writeFileSync(binary, Buffer.concat([Buffer.from([0, 159, 146, 150]), Buffer.from(raw)]));
    await testInfo.attach('vf-binary', { path: binary, contentType: 'application/octet-stream' });
    // A plain attach of a text file by path: Playwright copies it; the publication copies that by content.
    const plainFile = testInfo.outputPath('evidence-plain.txt');
    writeFileSync(plainFile, raw);
    await testInfo.attach('vf-text-file', { path: plainFile, contentType: 'text/plain' });
    const source = testInfo.outputPath('evidence-source.txt');
    writeFileSync(source, raw);
    await attachEvidence(testInfo, 'vf-source', { path: source, contentType: 'text/plain' });
    const binarySource = testInfo.outputPath('evidence-source.bin');
    writeFileSync(binarySource, Buffer.concat([Buffer.from([0, 1]), Buffer.from(raw)]));
    await attachEvidence(testInfo, 'vf-binary-source', { path: binarySource, contentType: 'application/octet-stream' });
    // Never attached: only the end-of-run scrub of the output directory reaches it.
    writeFileSync(testInfo.outputPath('evidence-loose.txt'), raw);
    walk.receipt.verdict({ feature: 'vf-probe', lane: 'api', where: a.email, verdict: 'RECORDED',
      detail: { organizationId: a.organizationId, key } });
  });

test('value-free child: a failing expect reaches the report with tokens only', async ({ scopeActors: scopes }, testInfo) => {
  scopes.valueFree?.register(PROBE);
  await attachEvidence(testInfo, 'vf-probe', { contentType: 'application/json', body: JSON.stringify({ probe: PROBE }) });
  test.fail(true, 'two owners never share an email; the message carries both');
  expect(scopes.ownerA.email).toBe(scopes.ownerB.email);
});

// A UUID in a title. The title is identical in the runner and the worker (the parent's probe), so the
// test is found in both, and short enough that Playwright names the output directory with the whole
// UUID (it trims "<file>-<title>" to 60 characters).
test(`id ${probes.titleId}`, async ({}, testInfo) => {
  writeFileSync(testInfo.outputPath('title-probe.txt'), 'the output directory is named after this test\n');
  await testInfo.attach('vf-title', { contentType: 'text/plain', body: testInfo.title });
});

// An org key's prefix in a title: the line reporter prints it when the test begins, before this worker registers it
// (the verifier's probe). Short enough that the output directory is named after it too.
test(`key ${probes.keyPrefix}`, async ({}, testInfo) => {
  if (process.env.E2E_VALUE_FREE === '1') sharedValueFree().register(probes.keyPrefix);
  writeFileSync(testInfo.outputPath('key-probe.txt'), 'the output directory is named after this test\n');
  await testInfo.attach('vf-key-title', { contentType: 'text/plain', body: testInfo.title });
});
