import { createServer } from 'node:http';
import { once } from 'node:events';
import { execFile } from 'node:child_process';
import { promisify } from 'node:util';
import { createHash } from 'node:crypto';
import { mkdtemp, mkdir, readFile, rm, realpath } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';

const execute = promisify(execFile);
const hash = (bytes) => createHash('sha256').update(bytes).digest('hex');
const need = (condition, code) => { if (!condition) throw new Error(code); };
const SHA256 = /^[a-f0-9]{64}$/;

/** Text the host actually sent to the model, from captured Responses API requests. */
export function boundaryText(requests) {
  return requests.flatMap((request) => (request.input ?? []).flatMap((item) => (typeof item.content === 'string' ? [item.content]
    : (item.content ?? []).filter((part) => typeof part.text === 'string').map((part) => part.text)))).join('\n');
}

function syntheticReply(body, sequence) {
  const id = `resp_probe_${sequence}`; const itemId = `msg_probe_${sequence}`;
  const part = { type: 'output_text', text: 'Probe acknowledged.', annotations: [], logprobs: [] };
  const item = { id: itemId, type: 'message', role: 'assistant', status: 'completed', content: [part] };
  const final = { id, object: 'response', created_at: 1, status: 'completed', model: body.model, output: [item],
    usage: { input_tokens: 1, output_tokens: 1, total_tokens: 2, input_tokens_details: { cached_tokens: 0 }, output_tokens_details: { reasoning_tokens: 0 } } };
  return [
    { type: 'response.created', response: { ...final, status: 'in_progress', output: [] } },
    { type: 'response.output_item.added', output_index: 0, item: { ...item, status: 'in_progress', content: [] } },
    { type: 'response.content_part.added', item_id: itemId, output_index: 0, content_index: 0, part: { ...part, text: '' } },
    { type: 'response.output_text.delta', item_id: itemId, output_index: 0, content_index: 0, delta: part.text },
    { type: 'response.output_text.done', item_id: itemId, output_index: 0, content_index: 0, text: part.text },
    { type: 'response.content_part.done', item_id: itemId, output_index: 0, content_index: 0, part },
    { type: 'response.output_item.done', output_index: 0, item },
    { type: 'response.completed', response: final },
  ].map((event, index) => ({ ...event, sequence_number: index + 1 }));
}

/**
 * Load check for one installed checkout: run the operator's pinned Codex
 * executable in the checkout against a loopback capture endpoint (no provider,
 * no paid call) and report whether the installed reviewed instructions reached
 * the model boundary. The host discovers its own project instructions; the
 * probe never supplies them. This is bounded root-AGENTS discovery evidence,
 * not behavioural evaluation. The raw captured requests and process events are
 * returned so an independent oracle can re-check them.
 */
export async function probeCodexLoad({ executable, executableSha256, expectedVersion = null, project, expectedInstructions, signal }) {
  need(typeof executable === 'string' && SHA256.test(executableSha256 ?? ''), 'native_host_pin_required');
  need(typeof expectedInstructions === 'string' && expectedInstructions.trim().length > 0, 'expected_instructions_required');
  const binary = await realpath(executable);
  need(hash(await readFile(binary)) === executableSha256, 'native_binary_changed');
  const installed = await readFile(join(project, 'AGENTS.md'), 'utf8');
  need(installed === expectedInstructions, 'native_preimage_changed');
  const isolated = await mkdtemp(join(tmpdir(), 'context-load-probe-'));
  const requests = []; const responses = [];
  const server = createServer(async (request, response) => {
    try {
      if (request.method !== 'POST' || request.url !== '/v1/responses') { response.writeHead(404); response.end(); return; }
      const chunks = []; let size = 0;
      for await (const chunk of request) {
        size += chunk.length;
        if (size > 2 * 1024 * 1024) throw new Error('native_request_too_large');
        chunks.push(chunk);
      }
      const body = JSON.parse(Buffer.concat(chunks).toString('utf8'));
      requests.push(body);
      const events = syntheticReply(body, requests.length);
      responses.push(events);
      response.writeHead(200, { 'Content-Type': 'text/event-stream', 'Cache-Control': 'no-cache' });
      response.end(events.map((event) => `event: ${event.type}\ndata: ${JSON.stringify(event)}\n\n`).join(''));
    } catch {
      response.writeHead(400); response.end('Invalid probe request');
    }
  });
  try {
    await mkdir(join(isolated, 'codex'));
    server.listen(0, '127.0.0.1'); await once(server, 'listening');
    const origin = `http://127.0.0.1:${server.address().port}/v1`;
    const env = { PATH: process.env.PATH, HOME: isolated, CODEX_HOME: join(isolated, 'codex'),
      CONTEXT_PROBE_API_KEY: 'loopback-probe-only', OTEL_SDK_DISABLED: 'true', DO_NOT_TRACK: '1' };
    const { stdout: version } = await execute(binary, ['--version'], { env, signal, timeout: 15_000 });
    need(expectedVersion === null || version.trim() === expectedVersion, 'unqualified_native_version');
    const processResult = execute(binary, ['exec', '--skip-git-repo-check', '--ephemeral', '--sandbox', 'read-only', '--json', '-C', project,
      '-c', 'model="gpt-5.1-codex-mini"', '-c', 'model_provider="probe"',
      '-c', 'model_providers.probe.name="Loopback load probe"', '-c', `model_providers.probe.base_url="${origin}"`,
      '-c', 'model_providers.probe.wire_api="responses"', '-c', 'model_providers.probe.env_key="CONTEXT_PROBE_API_KEY"',
      '-c', 'model_providers.probe.requires_openai_auth=false', '-c', 'analytics.enabled=false',
      'Reply with a short acknowledgement. Do not invoke tools or change files.'],
    { env, signal, timeout: 60_000, maxBuffer: 8 * 1024 * 1024 });
    // Codex appends piped stdin to its prompt; execFile opens that pipe but does
    // not close it automatically. An empty closed stream is not an extra input.
    processResult.child.stdin.end();
    const { stdout } = await processResult;
    const events = stdout.trim().split('\n').filter(Boolean).map((line) => JSON.parse(line));
    need(await readFile(join(project, 'AGENTS.md'), 'utf8') === installed, 'native_probe_modified_instructions');
    const completed = events.some((event) => event.type === 'turn.completed')
      && !events.some((event) => event.type === 'turn.failed' || event.type === 'error');
    const observed = completed && requests.length === 1 && boundaryText(requests).includes(expectedInstructions.trimEnd());
    return { source: 'model_boundary', discovery: observed ? 'observed' : 'not_observed', delivery: observed ? 'observed' : 'not_observed',
      scope: `${version.trim().replace(/\s+/g, '-')}-root-AGENTS-only`, runtime_sha256: executableSha256, version: version.trim(),
      installed_sha256: hash(installed), requests, responses, process_events: events };
  } finally {
    server.closeAllConnections(); await new Promise((resolve) => server.close(resolve));
    await rm(isolated, { recursive: true, force: true });
  }
}
