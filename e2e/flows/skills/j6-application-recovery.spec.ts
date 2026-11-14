import { createHash, randomUUID } from 'node:crypto';
import { spawn } from 'node:child_process';
import { mkdir, readFile, unlink, writeFile } from 'node:fs/promises';
import { resolve } from 'node:path';
import { test, expect } from '../../lib/fixtures';
import { E2E } from '../../lib/env';
import { flowAnnotation } from '../../lib/flow-meta';
import { libraryFixture } from '../../fixtures/skills-r3/library.mjs';
import { ProgrammableProvider, completion } from '../../lib/skills-r3/programmable-provider.mjs';

const API = '/model-hub/skills/';
const UI_READY = 60_000;

// Synthetic GCP subprocess integration, deliberately not *.native.spec.ts and
// never reported as Simulation/E2B qualification or a complete J6 measurement.
test('SKILLS-E2E-061: recover a persisted refund checkpoint through the real registry without a second business request', {
  tag: ['@flow'],
  annotation: flowAnnotation({ id: 'SKILLS-E2E-061', area: 'skills',
    userGoal: 'Resume an eligible original application thread after a registry outage without replaying its completed refund, and keep an unobserved consumer unknown',
    steps: [
      'prepare a published fifteen-skill synthetic context and two declared application bindings through public APIs',
      'run the installed application CLI against real session and file APIs and interrupt registry access after a business result',
      'reopen the durable application database and resume the same session with no duplicate business request',
      'explicitly prepare and run a separately reviewed clean task with no inherited messages or automatic refund replay',
      'inspect original session identity, independent model-boundary digests and persisted receipts',
      'report observed root delivery, revoke the original credential and inspect the consumer UI without a false target match',
    ],
    backendChecks: [
      'independent business SQLite has one applied refund and one request before and after recovery',
      'application SQLite contains the refund receipt before checkpoint advancement; resume retains the complete closure and original session',
      'four original-thread provider requests and one explicitly reconstructed clean-thread request match independent model-boundary hashes; the complete catalog has fifteen members',
      'Postgres retains the original session and real immutable version identity; a never-observed binding stays unknown',
      'public access rejects the revoked credential and the UI no longer describes its historical delivery as matching the current target',
    ],
  }),
}, async ({ page, actor, request, probe }, testInfo) => {
  test.setTimeout(600_000); // setup 120s + three CLI runs × 120s + two UI waits × 60s.
  expect(process.platform, 'cloud Linux only').toBe('linux');
  const python = process.env.J6_APP_PYTHON;
  expect(python, 'Install the pinned SDK and application wheel on the cloud VM').toBeTruthy();
  const dir = testInfo.outputPath('j6-application');
  await mkdir(dir, { recursive: true });
  const prefix = `e2e-j6-${testInfo.workerIndex}-${Date.now().toString(36)}`;
  const headers = { Authorization: `Bearer ${actor.tokens.access}`, 'X-Organization-Id': actor.organizationId, 'X-Workspace-Id': actor.workspaceId };
  const human = async (method: string, path: string, data?: unknown): Promise<any> => {
    const response = await request.fetch(`${E2E.apiUrl}${API}${path}`, { method, data,
      headers: { ...headers, 'Idempotency-Key': randomUUID() } });
    expect(response.ok(), `${method} ${path} returned ${response.status()}`).toBeTruthy();
    return response.status() === 204 ? null : (await response.json()).result;
  };
  const provider = await new ProgrammableProvider().listen();
  const inputFile = resolve(dir, 'private-input.json');
  const authorityFault = process.env.J6_AUTHORITY_FAULT || 'revoked';
  expect(['revoked', 'expired']).toContain(authorityFault);
  let credential: any;
  let reporter: any;
  let revoked = false;
  try {
    const catalog = libraryFixture({ unsupportedMember: false });
    const leaves = [];
    for (const [index, member] of catalog.skills.entries()) {
      const name = `${prefix}-${index}`;
      leaves.push(await human('POST', 'publish/', {
        files: member.files.map((file: any) => ({ ...file, content: file.content.replaceAll(member.name, name) })),
        labels: [], expected_release_revision: 0, acknowledge_warnings: true,
      }));
    }
    const text = 'Follow the reviewed policy and select only relevant skills. Do not repeat completed refunds.\n';
    const created = await human('POST', 'contexts/', { name: prefix, description: 'Synthetic registry recovery integration', tags: [], files: [{ path: 'AGENTS.md', content: text }] });
    const root = `contexts/${created.context_id}/`;
    const links = leaves.map((leaf: any) => ({ skill_id: leaf.skill.id, selector: { version: 1 } }));
    const input = { revision: created.revision, links, refresh_skill_ids: [] };
    const preview = await human('POST', `${root}dependencies/preview/`, input);
    const draft = await human('POST', `${root}dependencies/apply/`, { ...input, expected_fingerprint: preview.fingerprint });
    const head = await human('POST', `${root}draft/head-review/`, { revision: draft.revision });
    const ackInput = { revision: head.draft_revision, expected_digest: head.candidate_digest, expected_head_version: head.expected_head_version, review_fingerprint: head.review_fingerprint };
    const acknowledged = await human('POST', `${root}draft/acknowledge-head/`, ackInput);
    const version = (await human('POST', `${root}draft/publish/`, { ...ackInput, revision: acknowledged.revision,
      review_fingerprint: acknowledged.review_fingerprint, base_version: null, note: '', acknowledge_warnings: true })).version;
    const bindings: { id: string; name: string }[] = [];
    for (const suffix of ['application', 'unobserved']) bindings.push(await human('POST', `${root}bindings/`, {
      name: `${prefix}-${suffix}`, kind: 'runtime', selector: { version: version.number }, update_mode: 'new_thread',
      profile: 'ctxhub-support', profile_version: '0.1.0', repository: null, expected_report_interval_seconds: 3600,
    }));
    credential = await human('POST', 'contexts/credentials/', { purpose: 'consume',
      grants: [{ kind: 'agent_context', resource_id: created.context_id, labels: [], version_ids: [version.id] }],
      binding_ids: [], expires_at: new Date(Date.now() + (authorityFault === 'expired' ? 90_000 : 3_600_000)).toISOString() });
    const skillId = leaves[0].skill.id;
    const tool = (id: string, name: string, args: object) => ({ id, type: 'function', function: { name, arguments: JSON.stringify(args) } });
    const scenario = provider.register([
      { kind: 'json', body: completion({ content: null, tool_calls: [tool('select-refund', 'load_skill', { skill_id: skillId })] }, { finishReason: 'tool_calls' }) },
      { kind: 'json', body: completion({ content: null, tool_calls: [tool('read-policy', 'read_skill_file', { skill_id: skillId, path: 'references/policy.md' })] }, { finishReason: 'tool_calls' }) },
      { kind: 'json', body: completion({ content: null, tool_calls: [tool('refund-operation', 'issue_refund', { amount: 25 })] }, { finishReason: 'tool_calls' }) },
      { kind: 'json', body: completion({ content: 'The synthetic refund of 25 units was applied.' }) },
      { kind: 'json', body: completion({ content: 'Verify the independent business receipt; no new action was requested.' }) },
    ]);
    await writeFile(inputFile, JSON.stringify({ api_url: E2E.apiUrl, credential: credential.secret, binding_id: bindings[0].id,
      runtime_digest: `sha256:${createHash('sha256').update('j6-synthetic-runtime-v1').digest('hex')}`, version_id: version.id,
      provider_url: scenario.baseURL(), request: { schema: 1, thread: randomUUID(),
        case: { case_id: 'refund-boundary', order_id: `synthetic-${randomUUID()}`, request: 'Refund 25 units for an order purchased 30 days ago.' } } }), { mode: 0o600 });
    const receiptDir = resolve(dir, 'runtime');
    await new Promise<void>((resolveChild, reject) => {
      const child = spawn(python!, [resolve('fixtures/skills-r3/j6-application-recovery.py'), inputFile, receiptDir], { stdio: ['ignore', 'pipe', 'pipe'], timeout: 360_000 });
      let diagnostics = '';
      child.stderr.on('data', (chunk) => { diagnostics = (diagnostics + String(chunk)).slice(-16_384); });
      child.once('error', reject);
      child.once('exit', (code, signal) => code === 0 ? resolveChild() : reject(new Error(`Public application recovery failed (${code}, ${signal}): ${diagnostics}`)));
    });
    const receipt = JSON.parse(await readFile(resolve(receiptDir, 'recovery-receipt.json'), 'utf8'));
    const providerLedger = provider.ledger(scenario.capability);
    expect(providerLedger).toHaveLength(5);
    expect(provider.remaining(scenario.capability)).toBe(0);
    expect(providerLedger.every((row: any) => row.state === 'responded')).toBeTruthy();
    const modelRequests = receipt.resumed.events.filter((event: any) => event.kind === 'model_request');
    const cleanRequests = receipt.cold_thread_reconstruction.result.events.filter((event: any) => event.kind === 'model_request');
    expect(cleanRequests).toHaveLength(1);
    expect([...modelRequests, ...cleanRequests].map((event: any) => event.body.request_digest)).toEqual(providerLedger.map((row: any) => `sha256:${row.request_sha256}`));
    expect(receipt.cold_thread_reconstruction.copied_messages).toBe(0);
    expect(receipt.cold_thread_reconstruction.additional_business_requests).toBe(0);
    expect(receipt.cold_thread_reconstruction.preparation_elapsed_ns).toBeGreaterThan(0);
    expect(receipt.resumed.events.find((event: any) => event.kind === 'catalog').body.members).toHaveLength(15);
    // One-sided fallibility proof: only the expected independent business count
    // changes. The provider, app, database and receipts stay unchanged.
    expect(receipt.business_after.request_count, 'independent business request oracle').toBe(process.env.J6_ORACLE_FAULT === 'duplicate' ? 2 : 1);
    expect(receipt.business_after.operations).toHaveLength(1);
    const sessions = await probe.pg<any>('SELECT id, version_id, digest, incarnation_id FROM model_hub_hubskillcontextsession WHERE organization_id=$1 AND workspace_id=$2 AND binding_id=$3', [actor.organizationId, actor.workspaceId, bindings[0].id]);
    expect(sessions).toHaveLength(2);
    expect(sessions.map((row: any) => row.id).sort()).toEqual([receipt.paused.session_id, receipt.cold_thread_reconstruction.result.session_id].sort());
    expect(sessions.every((row: any) => row.version_id === version.id && row.digest === version.digest)).toBe(true);
    const rootDelivery = receipt.resumed.events.find((event: any) => event.kind === 'root_delivery');
    expect(rootDelivery).toBeTruthy();
    reporter = await human('POST', 'contexts/credentials/', { purpose: 'report', grants: [],
      binding_ids: [bindings[0].id], expires_at: new Date(Date.now() + 3_600_000).toISOString() });
    for (const session of sessions) {
      const reported = await request.post(`${E2E.apiUrl}${API}contexts/bindings/${bindings[0].id}/receipts/`, {
        headers: { Authorization: `Context ${reporter.secret}` }, data: {
        event_id: randomUUID(), session_id: session.id, instance_id: session.incarnation_id, sequence: 0,
        stage: 'instructions_delivered', version_id: version.id, digest: version.digest, materialization_digest: null,
        observed_at: new Date().toISOString(), details: { path: 'AGENTS.md', sha256: createHash('sha256').update(text).digest('hex'),
          ranges: [[0, text.length]], runtime_version: 'python/3.11', adapter_version: 'ctxhub-support/0.1.0', diagnostic_codes: [], trace_ref: null },
      } });
      expect(reported.ok(), 'purpose-bound reporter receipt must be accepted').toBeTruthy();
    }
    const before = await human('GET', `${root}consumers/`);
    expect(before.results.find((row: any) => row.binding.id === bindings[0].id).states).toContain('matches_target');
    expect(before.results.find((row: any) => row.binding.id === bindings[1].id).states).toEqual(['unknown']);
    if (authorityFault === 'revoked') {
      await human('DELETE', `contexts/credentials/${credential.id}/`);
      revoked = true;
    }
    // Real credential expiry through its public expires_at contract; no database
    // mutation, forged access receipt or test-only product clock.
    await expect.poll(async () => {
      const denied = await request.post(`${E2E.apiUrl}${API}contexts/bindings/${bindings[0].id}/sessions/${receipt.paused.session_id}/access-check/`, { data: {}, headers: { Authorization: `Context ${credential.secret}` } });
      return [401, 403].includes(denied.status());
    }, { timeout: authorityFault === 'expired' ? 100_000 : 10_000, intervals: [1000] }).toBe(true);
    const after = await human('GET', `${root}consumers/`);
    expect(after.results.every((row: any) => row.states.includes('unknown') && !row.states.includes('matches_target'))).toBeTruthy();
    await testInfo.attach('j6-runtime-observations', { body: JSON.stringify({ receipt, providerLedger, sessions, before, after, authorityFault, credentialExpiresAt: credential.expires_at }), contentType: 'application/json' });
    await page.setViewportSize({ width: 1440, height: 900 });
    await page.goto(`/dashboard/skills/contexts/${created.context_id}?version=1`);
    await page.getByRole('tab', { name: 'Consumers', exact: true }).click({ timeout: UI_READY });
    const table = page.getByRole('table', { name: 'Context delivery observations' });
    for (const binding of bindings) {
      const row = table.getByRole('row').filter({ hasText: binding.name });
      await expect(row.getByText('No report from this app yet', { exact: true })).toBeVisible({ timeout: UI_READY });
      await expect(row.getByText('Matches target', { exact: true })).toHaveCount(0);
    }
    await page.screenshot({ path: testInfo.outputPath('j6-recovered-then-revoked.png') });
    if (process.env.J6_DRIVER_SEED) {
      // Private cloud-local initial conditions for a separate registered-driver
      // attempt. Never attach these human tokens to a test report.
      await writeFile(process.env.J6_DRIVER_SEED, JSON.stringify({
        appUrl: E2E.appUrl, apiUrl: E2E.apiUrl, contextId: created.context_id,
        targetVersion: version.number, bindingIds: bindings.map(binding => binding.id),
        author: { access: actor.tokens.access, refresh: actor.tokens.refresh,
          organizationId: actor.organizationId, workspaceId: actor.workspaceId },
        fixture_hash: catalog.fixture_sha256,
        initial: { version, bindings, incident: `original consume credential ${authorityFault} after persisted refund recovery`,
          expected_session: receipt.paused.session_id, business_effects: 1, complete_job_qualified: false },
      }), { mode: 0o600, flag: 'wx' });
    }
  } finally {
    await unlink(inputFile).catch(() => {});
    try {
      if (credential && !revoked) await human('DELETE', `contexts/credentials/${credential.id}/`);
      if (reporter) await human('DELETE', `contexts/credentials/${reporter.id}/`);
    } finally { await provider.close(); }
  }
});
