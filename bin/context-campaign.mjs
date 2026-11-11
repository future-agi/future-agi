#!/usr/bin/env node
import { readFile } from 'node:fs/promises';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { execFile } from 'node:child_process';
import { promisify } from 'node:util';
import { CampaignExecutor } from './lib/context-campaign.mjs';

const execute = promisify(execFile);

export async function main(args, env = process.env) {
  if (args.length === 1 && args[0] === '--help') {
    console.log('Usage: node bin/context-campaign.mjs execute REVIEWED_INPUT.json');
    console.log('       node bin/context-campaign.mjs export SDK_PLAN.json REPOSITORY_ID BASE_COMMIT BINDING_REVISION CAMPAIGN_REVISION');
    console.log('Export uses CONTEXT_SDK_PYTHON (installed SDK) and prints {plan,contents}; explicitly review before uploading plan or executing contents.');
    console.log('Execute input: {targets:[{campaignId,bindingId,repository,idempotencyKey,contents:{path:base64}}]}');
    console.log('Requires pre-authorized server plans; does not authorize, merge, install or infer delivery.');
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
  if (args.length !== 2 || args[0] !== 'execute') throw new Error('invalid_arguments');
  const bytes = await readFile(args[1]);
  if (bytes.length > 16 * 1024 * 1024) throw new Error('input_too_large');
  const input = JSON.parse(bytes);
  if (!Array.isArray(input.targets) || input.targets.length === 0 || input.targets.length > 100) throw new Error('invalid_targets');
  const ids = input.targets.map((row) => `${row.campaignId}/${row.bindingId}`);
  if (new Set(ids).size !== ids.length || input.targets.some((row) => !row.idempotencyKey)) throw new Error('duplicate_or_unkeyed_target');
  const executor = new CampaignExecutor({ hubUrl: env.CONTEXT_HUB_URL,
    hubHeaders: JSON.parse(env.CONTEXT_HUB_HEADERS_JSON), githubToken: env.GITHUB_TOKEN });
  const results = [];
  for (const target of input.targets) {
    try {
      const receipt = await executor.execute({ ...target, signal: AbortSignal.timeout(300_000) });
      results.push({ campaignId: target.campaignId, bindingId: target.bindingId, ...receipt });
    } catch {
      results.push({ campaignId: target.campaignId, bindingId: target.bindingId, state: 'unknown',
        reason: 'Target did not finish. Retain its idempotency key and inspect server/Git observations before retry.' });
    }
  }
  console.log(JSON.stringify({ target_count: input.targets.length, targets: results, installed: false, delivered: false }));
  return results.every((row) => ['pr_open', 'merged'].includes(row.state)) ? 0 : 6;
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  try { process.exitCode = await main(process.argv.slice(2)); }
  catch { console.error('Campaign execution unavailable. Check the reviewed input and configured credentials; no secret-bearing errors are printed.'); process.exitCode = 2; }
}
