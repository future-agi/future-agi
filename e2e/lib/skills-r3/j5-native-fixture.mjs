import { createServer } from 'node:http';
import { once } from 'node:events';
import { execFile } from 'node:child_process';
import { promisify } from 'node:util';
import { createHash } from 'node:crypto';
import { mkdtemp, mkdir, readFile, rm, realpath } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import assert from 'node:assert/strict';

const execute = promisify(execFile);
const hash = (bytes) => createHash('sha256').update(bytes).digest('hex');
// npm @openai/codex@0.167.1, Linux x64 musl vendor executable; npm's pinned
// package-lock retains the independently checked distribution integrity as well.
export const CODEX_BINARY_SHA256 = 'dd5bfdf19d794dcc6fb482d2d5a2d9fb31d11f6caa44b303bb72890d461e8a02';

export function assertCodexBoundary({ requests, events, expectedInstructions }) {
  assert.ok(events.some((event) => event.type === 'turn.completed'), 'native_turn_not_completed');
  assert.ok(!events.some((event) => event.type === 'turn.failed' || event.type === 'error'), 'native_turn_failed');
  assert.equal(requests.length, 1, 'unexpected_native_calls');
  const text = (requests[0].input ?? []).flatMap((item) => typeof item.content === 'string' ? [item.content]
    : (item.content ?? []).filter((part) => typeof part.text === 'string').map((part) => part.text)).join('\n');
  assert.ok(expectedInstructions.trim().length > 0, 'empty_native_instructions');
  assert.ok(text.includes(expectedInstructions.trimEnd()), 'native_instruction_delivery_missing');
}

/**
 * Actual pinned Codex process, synthetic model transport. The host discovers its
 * own project instructions; this fixture never supplies them in task messages.
 * It is bounded root-AGENTS discovery evidence, not a general compatibility badge,
 * behavioral model evaluation, real provider conformance or paid model result.
 */
export async function observeCodexLoad({ executable, executableSha256, project, expectedInstructions, signal }) {
  assert.ok(process.platform === 'linux', 'native_probe_requires_cloud_linux');
  assert.equal(executableSha256, CODEX_BINARY_SHA256, 'unqualified_native_binary');
  const binary = await realpath(executable);
  assert.equal(hash(await readFile(binary)), executableSha256, 'native_binary_changed');
  const installed = await readFile(join(project, 'AGENTS.md'), 'utf8');
  assert.equal(installed, expectedInstructions, 'native_preimage_changed');
  const isolated = await mkdtemp(join(tmpdir(), 'j5-native-home-'));
  const requests = []; const responses = [];
  const server = createServer(async (request, response) => {
    try {
      if (request.method !== 'POST' || request.url !== '/v1/responses') {
        response.writeHead(404); response.end(); return;
      }
      const chunks = []; let size = 0;
      for await (const chunk of request) {
        size += chunk.length;
        if (size > 2 * 1024 * 1024) throw new Error('native_request_too_large');
        chunks.push(chunk);
      }
      const body = JSON.parse(Buffer.concat(chunks).toString('utf8'));
      requests.push(body);
      const id = `resp_fixture_${requests.length}`; const itemId = `msg_fixture_${requests.length}`;
      const part = { type: 'output_text', text: 'Fixture acknowledged.', annotations: [], logprobs: [] };
      const item = { id: itemId, type: 'message', role: 'assistant', status: 'completed', content: [part] };
      const final = { id, object: 'response', created_at: 1, status: 'completed', model: body.model, output: [item],
        usage: { input_tokens: 1, output_tokens: 1, total_tokens: 2, input_tokens_details: { cached_tokens: 0 }, output_tokens_details: { reasoning_tokens: 0 } } };
      const events = [
        { type: 'response.created', response: { ...final, status: 'in_progress', output: [] } },
        { type: 'response.output_item.added', output_index: 0, item: { ...item, status: 'in_progress', content: [] } },
        { type: 'response.content_part.added', item_id: itemId, output_index: 0, content_index: 0, part: { ...part, text: '' } },
        { type: 'response.output_text.delta', item_id: itemId, output_index: 0, content_index: 0, delta: part.text },
        { type: 'response.output_text.done', item_id: itemId, output_index: 0, content_index: 0, text: part.text },
        { type: 'response.content_part.done', item_id: itemId, output_index: 0, content_index: 0, part },
        { type: 'response.output_item.done', output_index: 0, item },
        { type: 'response.completed', response: final },
      ].map((event, index) => ({ ...event, sequence_number: index + 1 }));
      responses.push(events);
      response.writeHead(200, { 'Content-Type': 'text/event-stream', 'Cache-Control': 'no-cache' });
      response.end(events.map((event) => `event: ${event.type}\ndata: ${JSON.stringify(event)}\n\n`).join(''));
    } catch {
      response.writeHead(400); response.end('Invalid synthetic request');
    }
  });
  try {
    await mkdir(join(isolated, 'codex'));
    server.listen(0, '127.0.0.1'); await once(server, 'listening');
    const origin = `http://127.0.0.1:${server.address().port}/v1`;
    const env = { PATH: process.env.PATH, HOME: isolated, CODEX_HOME: join(isolated, 'codex'),
      J5_FAKE_API_KEY: 'synthetic-fixture-only', OTEL_SDK_DISABLED: 'true', DO_NOT_TRACK: '1' };
    const { stdout: version } = await execute(binary, ['--version'], { env, signal, timeout: 15_000 });
    assert.equal(version.trim(), 'codex-cli 0.167.1', 'unqualified_native_version');
    const processResult = execute(binary, ['exec', '--skip-git-repo-check', '--ephemeral', '--sandbox', 'read-only', '--json', '-C', project,
      '-c', 'model="gpt-5.1-codex-mini"', '-c', 'model_provider="fixture"',
      '-c', 'model_providers.fixture.name="Synthetic fixture"', '-c', `model_providers.fixture.base_url="${origin}"`,
      '-c', 'model_providers.fixture.wire_api="responses"', '-c', 'model_providers.fixture.env_key="J5_FAKE_API_KEY"',
      '-c', 'model_providers.fixture.requires_openai_auth=false', '-c', 'analytics.enabled=false',
      'Reply with a short acknowledgement. Do not invoke tools or change files.'],
    { env, signal, timeout: 60_000, maxBuffer: 8 * 1024 * 1024 });
    // Codex appends piped stdin to its prompt; execFile opens that pipe but does
    // not close it automatically. An empty closed stream is not an extra input.
    processResult.child.stdin.end();
    const { stdout } = await processResult;
    const events = stdout.trim().split('\n').filter(Boolean).map((line) => JSON.parse(line));
    assertCodexBoundary({ requests, events, expectedInstructions });
    assert.equal(await readFile(join(project, 'AGENTS.md'), 'utf8'), installed, 'native_probe_modified_instructions');
    return { evidence_class: 'synthetic fixture', source: 'model_boundary', discovery: 'observed', delivery: 'observed',
      scope: 'codex-0.167.1-linux-root-AGENTS-only', runtime_sha256: executableSha256, version: version.trim(),
      installed_sha256: hash(installed), requests, responses, process_events: events };
  } finally {
    server.closeAllConnections(); await new Promise((resolve) => server.close(resolve));
    await rm(isolated, { recursive: true, force: true });
  }
}
