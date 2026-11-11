#!/usr/bin/env node
import { readFile } from 'node:fs/promises';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { execFile } from 'node:child_process';
import { promisify } from 'node:util';
import { randomUUID } from 'node:crypto';
import { CampaignExecutor } from './lib/context-campaign.mjs';
import { CampaignBatch } from './lib/context-campaign-batch.mjs';

const execute = promisify(execFile);

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
