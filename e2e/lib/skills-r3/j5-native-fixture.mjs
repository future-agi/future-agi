import { readFile, realpath } from 'node:fs/promises';
import { createHash } from 'node:crypto';
import assert from 'node:assert/strict';
import { probeCodexLoad } from '../../../bin/lib/context-load-probe.mjs';

const hash = (bytes) => createHash('sha256').update(bytes).digest('hex');
// npm @openai/codex@0.167.1, Linux x64 musl vendor executable; npm's pinned
// package-lock retains the independently checked distribution integrity as well.
export const CODEX_BINARY_SHA256 = 'dd5bfdf19d794dcc6fb482d2d5a2d9fb31d11f6caa44b303bb72890d461e8a02';
export const CODEX_VERSION = 'codex-cli 0.167.1';
export const NATIVE_SCOPE = 'codex-0.167.1-linux-root-AGENTS-only';

/** Independent oracle over the raw captured requests; never trusts the probe's own verdict. */
export function assertCodexBoundary({ requests, events, expectedInstructions }) {
  assert.ok(events.some((event) => event.type === 'turn.completed'), 'native_turn_not_completed');
  assert.ok(!events.some((event) => event.type === 'turn.failed' || event.type === 'error'), 'native_turn_failed');
  assert.equal(requests.length, 1, 'unexpected_native_calls');
  const text = (requests[0].input ?? []).flatMap((item) => typeof item.content === 'string' ? [item.content]
    : (item.content ?? []).filter((part) => typeof part.text === 'string').map((part) => part.text)).join('\n');
  assert.ok(expectedInstructions.trim().length > 0, 'empty_native_instructions');
  assert.ok(text.includes(expectedInstructions.trimEnd()), 'native_instruction_delivery_missing');
}

/** Qualification gate for the pinned host the harness accepts: cloud Linux and the exact Codex build. */
export async function assertQualifiedCodex({ executable, executableSha256 }) {
  assert.ok(process.platform === 'linux', 'native_probe_requires_cloud_linux');
  assert.equal(executableSha256, CODEX_BINARY_SHA256, 'unqualified_native_binary');
  assert.equal(hash(await readFile(await realpath(executable))), executableSha256, 'native_binary_changed');
}

/**
 * Actual pinned Codex process, synthetic model transport, through the product
 * load probe (bin/lib/context-load-probe.mjs). The probe's verdict is not
 * trusted: the raw captured request is re-checked here. Bounded root-AGENTS
 * discovery evidence, not a general compatibility badge, behavioral model
 * evaluation, real provider conformance or paid model result.
 */
export async function observeCodexLoad({ executable, executableSha256, project, expectedInstructions, signal }) {
  await assertQualifiedCodex({ executable, executableSha256 });
  const observed = await probeCodexLoad({ executable, executableSha256, expectedVersion: CODEX_VERSION, project, expectedInstructions, signal });
  assertCodexBoundary({ requests: observed.requests, events: observed.process_events, expectedInstructions });
  return { evidence_class: 'synthetic fixture', ...observed, scope: NATIVE_SCOPE };
}
