#!/usr/bin/env node
import { readFile, writeFile } from 'node:fs/promises';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { execFile } from 'node:child_process';
import { promisify } from 'node:util';
import { randomUUID } from 'node:crypto';
import { CampaignExecutor } from './lib/context-campaign.mjs';
import { CampaignBatch } from './lib/context-campaign-batch.mjs';
import { ContextRollout, ownershipPacket } from './lib/context-rollout.mjs';

const execute = promisify(execFile);

/** Read the planning credential a previous start/replace stored (CONTEXT_CREDENTIAL_FILE) into the SDK environment. */
export async function credentialEnv(env) {
  if (!env.CONTEXT_CREDENTIAL_FILE) return env;
  const secret = (await readFile(env.CONTEXT_CREDENTIAL_FILE, 'utf8')).trim();
  if (!secret) throw new Error('empty_credential_file');
  return { ...env, FI_CONTEXT_CREDENTIAL: secret };
}

export async function main(args, env = process.env) {
  if (args.length === 1 && args[0] === '--help') {
    console.log('Usage: node bin/context-campaign.mjs execute REVIEWED_INPUT.json');
    console.log('       node bin/context-campaign.mjs export SDK_PLAN.json REPOSITORY_ID BASE_COMMIT BINDING_REVISION CAMPAIGN_REVISION');
    console.log('Export uses CONTEXT_SDK_PYTHON (installed SDK) and prints {plan,contents}; explicitly review before uploading plan or executing contents.');
    console.log('       node bin/context-campaign.mjs bind CONTEXT_ID ROWS.json');
    console.log('       node bin/context-campaign.mjs plan-all PLAN_REQUEST.json');
    console.log('       node bin/context-campaign.mjs apply-all PLANS.json');
    console.log('       node bin/context-campaign.mjs verify-prs|merge-reviewed UNIT.json');
    console.log('verify-prs/merge-reviewed input: {targets:[{repository_id,repository,pull_request_number,commit,plan,contents}]} for one reviewed unit;');
    console.log('merge-reviewed re-verifies every PR head, parent and tree, then merges each guarded by its verified head SHA;');
    console.log('it prints one receipt per target (merged, reconciled already-merged at the verified head, refused or unknown) and exits 6 unless all merged.');
    console.log('Execute input: {targets:[{campaignId,bindingId,repository,idempotencyKey,contents:{path:base64}}]}');
    console.log('plan-all input: {campaignId,selected:[{repository_id,repository,project}],exclusions,mode,name,version,client,restoreLocks,outDir}');
    console.log('plan-all prints review groups (identical managed delta, local bytes proven preserved) and individual exceptions.');
    console.log('Requires pre-authorized server plans; does not authorize, install or infer delivery. Only merge-reviewed merges.');
    console.log('Rollout path (one command per step over the whole fixed campaign; review stays a separate decision):');
    console.log('       node bin/context-campaign.mjs ownership-review INVENTORY.json      grouped ownership packet from `fi.context inventory` output');
    console.log('       node bin/context-campaign.mjs context-create CONTEXT.json | context-edit EDIT.json');
    console.log('       node bin/context-campaign.mjs release-review CONTEXT_ID REVISION   prints the candidate to review');
    console.log('       node bin/context-campaign.mjs release-publish REVIEW.json BASE_VERSION|null   publishes exactly the reviewed candidate');
    console.log('       node bin/context-campaign.mjs start|replace REQUEST.json   freeze every binding at one release, issue the implied planning credential, plan all;');
    console.log('                                                                  replace first cancels {campaignId} with {reason}; both print a grouped review packet');
    console.log('       node bin/context-campaign.mjs authorize-reviewed PACKET.json ACKNOWLEDGED.json   one approval; refused if anything changed or the');
    console.log('                                                                  acknowledged exception repository ids differ from the packet');
    console.log('                                                                  {credentialFile} stores the planning credential in a new 0600 file for later');
    console.log('                                                                  subcommands (CONTEXT_CREDENTIAL_FILE); the secret is never printed');
    console.log('One-step paths (same reviews, approvals and separate receipts; fewer commands):');
    console.log('       node bin/context-campaign.mjs adopt-review SELECTED.json     inventory without hooks and print the grouped ownership packet');
    console.log('       context-create/context-edit also print the candidate review packet (no separate release-review)');
    console.log('       node bin/context-campaign.mjs release-publish REVIEW.json BASE_VERSION|null START.json   publish, then freeze and plan the rollout');
    console.log('       node bin/context-campaign.mjs authorize-reviewed PACKET.json ACKNOWLEDGED.json TARGETS.json   one approval, then open each authorized PR');
    console.log('       node bin/context-campaign.mjs merge-install UNIT.json       one merge decision: merge, install merged, load-check installed (pinned host)');
    console.log('       node bin/context-campaign.mjs reconcile-resume CAMPAIGN_ID TARGET.json REASON   pause, reconcile the unknown write, resume only if exact');
    console.log('       node bin/context-campaign.mjs replan-resolved STARTED.json REQUEST.json REPOSITORY_IDS.json   after resolving surfaced local_conflict');
    console.log('                                                                  exceptions, re-plan exactly those targets and print the complete new packet');
    console.log('       node bin/context-campaign.mjs status CAMPAIGN_ID pause|resume|cancel REASON');
    console.log('       node bin/context-campaign.mjs install TARGETS.json        after merge-reviewed: record merges, apply and verify each lock (no delivery claim)');
    console.log('       node bin/context-campaign.mjs load-check TARGETS.json     pinned native host per checkout (CONTEXT_NATIVE_HOST, CONTEXT_NATIVE_HOST_SHA256)');
    console.log('Environment: CONTEXT_HUB_URL, CONTEXT_HUB_HEADERS_JSON (human-bound API headers), GITHUB_TOKEN.');
    console.log('Keep each idempotencyKey for transport retries. A new key reconciles an uncertain prior attempt without repeating Git writes.');
    return 0;
  }
  if (args[0] === 'export') {
    if (args.length !== 6 || !env.CONTEXT_SDK_PYTHON) throw new Error('installed_sdk_and_exact_plan_inputs_required');
    const { stdout } = await execute(env.CONTEXT_SDK_PYTHON,
      ['-I', fileURLToPath(new URL('./lib/context-campaign-export.py', import.meta.url)), ...args.slice(1)],
      { env, timeout: 60_000, maxBuffer: 16 * 1024 * 1024 });
    // Parsing rejects stray process output; exceptions never expose the private
    // plan, lease or subprocess stderr. Nothing is sent to the hub or Git here.
    const value = JSON.parse(stdout);
    if (!value.plan?.plan_digest || !value.contents) throw new Error('invalid_sdk_export');
    console.log(JSON.stringify(value));
    return 0;
  }
  const hubHeaders = () => JSON.parse(env.CONTEXT_HUB_HEADERS_JSON);
  const batch = () => {
    const executor = new CampaignExecutor({ hubUrl: env.CONTEXT_HUB_URL, hubHeaders: hubHeaders(), githubToken: env.GITHUB_TOKEN });
    const hub = async (method, path, data) => {
      const response = await fetch(`${executor.hub}${path}`, { method, redirect: 'error',
        headers: { ...hubHeaders(), 'Content-Type': 'application/json', 'Idempotency-Key': randomUUID() },
        ...(data === undefined ? {} : { body: JSON.stringify(data) }) });
      if (!response.ok) throw new Error(`hub_http_${response.status}`);
      return response.status === 204 ? null : (await response.json()).result;
    };
    return new CampaignBatch({ hub, executor, python: env.CONTEXT_SDK_PYTHON, env });
  };
  const rollout = async () => {
    const runner = batch();
    return new ContextRollout({ hub: runner.hub, executor: runner.executor, python: env.CONTEXT_SDK_PYTHON, env: await credentialEnv(env) });
  };
  const json = async (path) => JSON.parse(await readFile(path, 'utf8'));
  const print = (value) => { console.log(JSON.stringify(value)); return 0; };
  if (args[0] === 'ownership-review' && args.length === 2) {
    const packet = ownershipPacket(await json(args[1]));
    console.log(JSON.stringify(packet));
    return packet.individual.length ? 6 : 0;
  }
  // ---- BEGIN one-step rollout commands (round 6) ----
  if (args[0] === 'adopt-review' && args.length === 2) {
    // Inventory (no hooks) and grouped ownership packet in one command; needs only the installed SDK.
    const runner = new ContextRollout({ hub: async () => { throw new Error('hub_not_used'); }, python: env.CONTEXT_SDK_PYTHON });
    const result = await runner.inventoryOwnership({ manifestPath: args[1] });
    console.log(JSON.stringify(result));
    return result.exitCode || result.packet.individual.length ? 6 : 0;
  }
  if (args[0] === 'release-publish' && args.length === 4) {
    // Publish exactly the reviewed candidate and freeze/plan the rollout of it; prints the rollout packet to review.
    const request = await json(args[3]);
    const runner = await rollout();
    const { version, started } = await runner.publishAndStart({ review: await json(args[1]), baseVersion: args[2] === 'null' ? null : Number(args[2]), start: request });
    if (request.credentialFile) await writeFile(request.credentialFile, runner.env.FI_CONTEXT_CREDENTIAL, { flag: 'wx', mode: 0o600 });
    console.log(JSON.stringify({ version, started: { ...started, review: { ...started.review, patches: Object.fromEntries(started.review.patches) } } }));
    return started.packet.individual.length ? 6 : 0;
  }
  if (args[0] === 'authorize-reviewed' && args.length === 4) {
    const { campaign, receipts } = await (await rollout()).authorizeAndOpen({ packet: await json(args[1]), acknowledgedExceptions: await json(args[2]),
      targets: (await json(args[3])).targets });
    console.log(JSON.stringify({ campaign, receipts, installed: false, delivered: false }));
    return receipts.every((row) => row.state === 'pr_open') ? 0 : 6;
  }
  if (args[0] === 'merge-install' && args.length === 2) {
    const { campaignId, targets } = await json(args[1]);
    const host = env.CONTEXT_NATIVE_HOST ? { executable: env.CONTEXT_NATIVE_HOST, sha256: env.CONTEXT_NATIVE_HOST_SHA256,
      version: env.CONTEXT_NATIVE_HOST_VERSION ?? null } : null;
    const result = await (await rollout()).mergeReviewed({ campaignId, targets, host });
    console.log(JSON.stringify(result));
    const all = (rows, ok) => rows.length === targets.length && rows.every(ok);
    return all(result.merges, (row) => row.state === 'merged') && all(result.installed, (row) => row.state === 'installed' && row.verified === true)
      && (!host || all(result.loads, (row) => row.delivery === 'observed')) ? 0 : 6;
  }
  if (args[0] === 'reconcile-resume' && args.length === 4) {
    const result = await (await rollout()).reconcileAndResume({ campaignId: args[1], target: await json(args[2]), reason: args[3] });
    console.log(JSON.stringify(result));
    return result.observed.state === 'pr_open' ? 0 : 6;
  }
  if (args[0] === 'replan-resolved' && args.length === 4) {
    // After the operator resolved the local conflicts a start/replace printed: re-plan exactly those targets; prints the new complete packet.
    const prior = await json(args[1]);
    const review = { ...prior.review, patches: new Map(Object.entries(prior.review?.patches ?? {})) };
    const result = await (await rollout()).replanResolved({ campaignId: prior.campaign_id, review, request: await json(args[2]), repositoryIds: await json(args[3]) });
    console.log(JSON.stringify({ campaign_id: prior.campaign_id, review: { ...result.review, patches: Object.fromEntries(result.review.patches) }, packet: result.packet }));
    return result.packet.individual.length ? 6 : 0;
  }
  // ---- END one-step rollout commands ----
  if (args[0] === 'context-create' && args.length === 2) return print(await (await rollout()).createContext(await json(args[1])));
  if (args[0] === 'context-edit' && args.length === 2) return print(await (await rollout()).editDraft(await json(args[1])));
  if (args[0] === 'release-review' && args.length === 3) return print(await (await rollout()).reviewRelease({ contextId: args[1], revision: Number(args[2]) }));
  if (args[0] === 'release-publish' && args.length === 3) {
    return print(await (await rollout()).publishReviewed({ review: await json(args[1]), baseVersion: args[2] === 'null' ? null : Number(args[2]) }));
  }
  if ((args[0] === 'start' || args[0] === 'replace') && args.length === 2) {
    const request = await json(args[1]);
    const runner = await rollout();
    const started = args[0] === 'start' ? await runner.start(request) : await runner.replace(request);
    // Later subcommands run in fresh processes: keep the short-lived planning
    // credential in an operator-chosen new private file, never on stdout.
    if (request.credentialFile) await writeFile(request.credentialFile, runner.env.FI_CONTEXT_CREDENTIAL, { flag: 'wx', mode: 0o600 });
    // The planning credential's secret stays in this process; only its id is printed.
    console.log(JSON.stringify({ ...started, review: { ...started.review, patches: Object.fromEntries(started.review.patches) } }));
    return started.packet.individual.length ? 6 : 0;
  }
  if (args[0] === 'authorize-reviewed' && args.length === 3) {
    return print(await (await rollout()).authorizeReviewed({ packet: await json(args[1]), acknowledgedExceptions: await json(args[2]) }));
  }
  if (args[0] === 'status' && args.length === 4) return print(await (await rollout()).setStatus({ campaignId: args[1], action: args[2], reason: args[3] }));
  if (args[0] === 'install' && args.length === 2) {
    const { campaignId, targets } = await json(args[1]);
    const result = await (await rollout()).install({ campaignId, targets });
    console.log(JSON.stringify({ ...result, delivered: false }));
    return result.installed.length === targets.length && result.installed.every((row) => row.state === 'installed' && row.verified === true) ? 0 : 6;
  }
  if (args[0] === 'load-check' && args.length === 2) {
    const { targets } = await json(args[1]);
    const receipts = await (await rollout()).loadCheck({ host: { executable: env.CONTEXT_NATIVE_HOST, sha256: env.CONTEXT_NATIVE_HOST_SHA256,
      version: env.CONTEXT_NATIVE_HOST_VERSION ?? null }, targets });
    console.log(JSON.stringify({ receipts }));
    return receipts.every((row) => row.delivery === 'observed') ? 0 : 6;
  }
  if (args[0] === 'bind') {
    if (args.length !== 3) throw new Error('invalid_arguments');
    const bindings = await batch().bindAll({ contextId: args[1], rows: JSON.parse(await readFile(args[2], 'utf8')) });
    console.log(JSON.stringify({ binding_count: bindings.length, bindings }));
    return 0;
  }
  if (args[0] === 'plan-all') {
    if (args.length !== 2) throw new Error('invalid_arguments');
    const result = await batch().planAll(JSON.parse(await readFile(args[1], 'utf8')));
    console.log(JSON.stringify({ ...result, patches: Object.fromEntries(result.patches) }));
    return result.exceptions.some((row) => row.kind === 'ungrouped') ? 6 : 0;
  }
  if (args[0] === 'apply-all') {
    if (args.length !== 2) throw new Error('invalid_arguments');
    const results = await batch().applyAll({ plans: JSON.parse(await readFile(args[1], 'utf8')) });
    console.log(JSON.stringify({ results, delivered: false }));
    return results.every((row) => row.state === 'installed' && row.verified === true) ? 0 : 6;
  }
  if (args[0] === 'verify-prs' || args[0] === 'merge-reviewed') {
    if (args.length !== 2) throw new Error('invalid_arguments');
    const { targets } = JSON.parse(await readFile(args[1], 'utf8'));
    const results = args[0] === 'verify-prs' ? await batch().verifyPulls({ targets }) : await batch().mergeAll({ targets });
    console.log(JSON.stringify({ results, installed: false, delivered: false }));
    // Per-target merge receipts: anything not merged (refused or unknown) is a non-zero, retained result.
    return args[0] === 'verify-prs' || results.every((row) => row.state === 'merged') ? 0 : 6;
  }
  if (args.length !== 2 || args[0] !== 'execute') throw new Error('invalid_arguments');
  const bytes = await readFile(args[1]);
  if (bytes.length > 16 * 1024 * 1024) throw new Error('input_too_large');
  const input = JSON.parse(bytes);
  if (!Array.isArray(input.targets) || input.targets.length === 0 || input.targets.length > 100) throw new Error('invalid_targets');
  const ids = input.targets.map((row) => `${row.campaignId}/${row.bindingId}`);
  if (new Set(ids).size !== ids.length || input.targets.some((row) => !row.idempotencyKey)) throw new Error('duplicate_or_unkeyed_target');
  if (new Set(input.targets.map((row) => row.campaignId)).size !== 1) throw new Error('one_campaign_per_execution');
  const runner = batch();
  runner.signal = AbortSignal.timeout(300_000 * Math.min(input.targets.length, 10));
  const results = (await runner.executeAll({ campaignId: input.targets[0].campaignId, targets: input.targets }))
    .map((row) => ({ campaignId: input.targets[0].campaignId, ...row }));
  console.log(JSON.stringify({ target_count: input.targets.length, targets: results, installed: false, delivered: false }));
  return results.every((row) => ['pr_open', 'merged'].includes(row.state)) ? 0 : 6;
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  try { process.exitCode = await main(process.argv.slice(2)); }
  catch { console.error('Campaign execution unavailable. Check the reviewed input and configured credentials; no secret-bearing errors are printed.'); process.exitCode = 2; }
}
