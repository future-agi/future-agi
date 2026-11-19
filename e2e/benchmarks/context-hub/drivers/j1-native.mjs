import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import { execFile } from 'node:child_process';
import { once } from 'node:events';
import { mkdir, mkdtemp, readFile, realpath, rm } from 'node:fs/promises';
import { createServer } from 'node:http';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { promisify } from 'node:util';

const execute = promisify(execFile);
const sha = (bytes) => createHash('sha256').update(bytes).digest('hex');
// npm @openai/codex@0.167.1 → @openai/codex-linux-x64 vendor musl executable.
// Same pin as the J5 native fixture; a different binary is not this evidence.
export const CODEX_VERSION = 'codex-cli 0.167.1';
const PLUGIN_ISOLATION = ['plugins', 'plugin_discovery_prefetch', 'remote_plugin', 'plugin_sharing', 'apps'];
export const CODEX_BINARY_SHA256 = 'dd5bfdf19d794dcc6fb482d2d5a2d9fb31d11f6caa44b303bb72890d461e8a02';

const inputTexts = (request) => (request.input ?? []).flatMap((item) => (typeof item.content === 'string' ? [item.content]
  : (item.content ?? []).filter((part) => typeof part.text === 'string').map((part) => part.text)));
const escape = (value) => value.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');

/** Derive what the host actually sent to the model, from raw request bodies only.
 * Root: the project AGENTS.md text Codex discovered, split at the context's
 * ownership markers. Discovery: the host's own skills listing. Delivery: exact
 * SKILL.md bytes the host read back through its sandboxed tool, per call. */
export function deriveDelivery(requests, { names }) {
  assert.ok(Array.isArray(requests) && requests.length === names.length + 1, 'native_request_count');
  const first = inputTexts(requests[0]);
  const agents = first.find((text) => text.startsWith('# AGENTS.md instructions for '));
  assert.ok(agents, 'native_root_instructions_missing');
  const body = agents.slice(agents.indexOf('<INSTRUCTIONS>\n') + '<INSTRUCTIONS>\n'.length, agents.lastIndexOf('</INSTRUCTIONS>'));
  const marker = body.match(/^<!-- futureagi:context:([0-9a-f-]{36}):begin -->\n/);
  // A managed block that is present but not first means the host also loaded an
  // enclosing directory's AGENTS.md (a git root above the project): not a missing block.
  assert.ok(marker || !/<!-- futureagi:context:[0-9a-f-]{36}:begin -->/.test(body), 'native_root_enclosing_instructions_precede_managed_block');
  assert.ok(marker, 'native_root_managed_block_missing');
  const end = `<!-- futureagi:context:${marker[1]}:end -->\n`;
  const close = body.indexOf(end);
  assert.ok(close > 0, 'native_root_managed_block_unterminated');
  const managed = body.slice(marker[0].length, close);
  // Codex appends one separator newline after the file; strip only that.
  const local = body.slice(close + end.length).replace(/\n$/, '');
  const listing = first.find((text) => text.includes('<skills_instructions>')) ?? '';
  const discovered = names.filter((name) => new RegExp(`^- ${escape(name)}: .+ \\(file: [^)]*/${escape(name)}/SKILL\\.md\\)$`, 'm').test(listing)).sort();
  const delivered = {};
  names.forEach((name, index) => {
    const outputs = (requests[index + 1].input ?? []).filter((item) => item.type === 'function_call_output' && item.call_id === `call_${index}`);
    assert.equal(outputs.length, 1, 'native_tool_output_missing');
    const text = typeof outputs[0].output === 'string' ? outputs[0].output : JSON.stringify(outputs[0].output);
    assert.match(text, /Process exited with code 0\n/, 'native_tool_failed');
    delivered[name] = sha(Buffer.from(text.slice(text.indexOf('\nOutput:\n') + '\nOutput:\n'.length), 'utf8'));
  });
  return { root_source_sha256: sha(Buffer.from(managed, 'utf8')), local_segment_sha256: sha(Buffer.from(local, 'utf8')), discovered, delivered };
}

const stream = (response, item, model) => {
  const final = { id: `resp_${item.id}`, object: 'response', created_at: 1, status: 'completed', model, output: [item],
    usage: { input_tokens: 1, output_tokens: 1, total_tokens: 2, input_tokens_details: { cached_tokens: 0 }, output_tokens_details: { reasoning_tokens: 0 } } };
  const events = [
    { type: 'response.created', response: { ...final, status: 'in_progress', output: [] } },
    { type: 'response.output_item.added', output_index: 0, item: item.type === 'message' ? { ...item, status: 'in_progress', content: [] } : { ...item, status: 'in_progress' } },
    { type: 'response.output_item.done', output_index: 0, item },
    { type: 'response.completed', response: final },
  ].map((event, index) => ({ ...event, sequence_number: index + 1 }));
  response.writeHead(200, { 'Content-Type': 'text/event-stream', 'Cache-Control': 'no-cache' });
  response.end(events.map((event) => `event: ${event.type}\ndata: ${JSON.stringify(event)}\n\n`).join(''));
};

/**
 * Actual pinned Codex process in its own Linux sandbox; synthetic scripted model
 * transport. The host discovers project AGENTS.md and .agents/skills itself.
 * The scripted model asks it to open each installed SKILL.md once, so body
 * delivery is observed in the next model request. Evidence class: synthetic
 * fixture (no paid model, no natural skill selection, no behavioural claim).
 */
export async function observeCodexDelivery({ executable, executableSha256, project, skills, signal }) {
  assert.ok(process.platform === 'linux', 'native_probe_requires_cloud_linux');
  assert.equal(executableSha256, CODEX_BINARY_SHA256, 'unqualified_native_binary');
  const binary = await realpath(executable);
  assert.equal(sha(await readFile(binary)), executableSha256, 'native_binary_changed');
  const home = await mkdtemp(join(tmpdir(), 'j1-native-home-'));
  const requests = [];
  const server = createServer(async (request, response) => {
    try {
      if (request.method !== 'POST' || request.url !== '/v1/responses') { response.writeHead(404); response.end(); return; }
      const chunks = []; let size = 0;
      for await (const chunk of request) {
        size += chunk.length;
        if (size > 4 * 1024 * 1024) throw new Error('native_request_too_large');
        chunks.push(chunk);
      }
      const body = JSON.parse(Buffer.concat(chunks).toString('utf8'));
      requests.push(body);
      const index = requests.length - 1;
      if (index < skills.length) {
        stream(response, { id: `fc_${index}`, type: 'function_call', status: 'completed', call_id: `call_${index}`, name: 'exec_command',
          arguments: JSON.stringify({ cmd: `cat ${skills[index].path}` }) }, body.model);
      } else {
        stream(response, { id: `msg_${index}`, type: 'message', role: 'assistant', status: 'completed',
          content: [{ type: 'output_text', text: 'Fixture acknowledged.', annotations: [], logprobs: [] }] }, body.model);
      }
    } catch {
      response.writeHead(400); response.end('Invalid synthetic request');
    }
  });
  try {
    await mkdir(join(home, 'codex'));
    server.listen(0, '127.0.0.1'); await once(server, 'listening');
    const origin = `http://127.0.0.1:${server.address().port}/v1`;
    const env = { PATH: process.env.PATH, HOME: home, CODEX_HOME: join(home, 'codex'), J1_FAKE_API_KEY: 'synthetic-fixture-only', OTEL_SDK_DISABLED: 'true', DO_NOT_TRACK: '1' };
    const { stdout: version } = await execute(binary, ['--version'], { env, signal, timeout: 15_000 });
    assert.equal(version.trim(), CODEX_VERSION, 'unqualified_native_version');
    const run = execute(binary, ['exec', '--skip-git-repo-check', '--ephemeral', '--sandbox', 'read-only', '--json', '-C', project,
      '-c', 'model="gpt-5.1-codex-mini"', '-c', 'model_provider="fixture"', '-c', 'model_providers.fixture.name="Synthetic fixture"',
      '-c', `model_providers.fixture.base_url="${origin}"`, '-c', 'model_providers.fixture.wire_api="responses"',
      '-c', 'model_providers.fixture.env_key="J1_FAKE_API_KEY"', '-c', 'model_providers.fixture.requires_openai_auth=false', '-c', 'analytics.enabled=false',
      // Offline, project-only host: no remote plugin/app catalogue clones that
      // outlive the turn or add skills the fixture did not install.
      ...PLUGIN_ISOLATION.flatMap((feature) => ['-c', `features.${feature}=false`]),
      'Open each installed policy skill you are asked to read. Do not change files.'],
    { env, signal, timeout: 180_000, maxBuffer: 16 * 1024 * 1024 });
    run.child.stdin.end();
    const { stdout } = await run;
    const events = stdout.trim().split('\n').filter(Boolean).map((line) => JSON.parse(line));
    assert.ok(events.some((event) => event.type === 'turn.completed'), 'native_turn_not_completed');
    assert.ok(!events.some((event) => event.type === 'turn.failed' || event.type === 'error'), 'native_turn_failed');
    return { evidence_class: 'synthetic fixture', source: 'model_boundary', scope: 'codex-0.167.1-linux-project-AGENTS-and-.agents/skills',
      host: version.trim(), runtime_sha256: executableSha256, transport: 'synthetic scripted Responses stream; activation scripted, not natural selection',
      requests, process_events: events };
  } finally {
    server.closeAllConnections(); await new Promise((done) => server.close(done));
    await rm(home, { recursive: true, force: true, maxRetries: 5, retryDelay: 200 });
  }
}
