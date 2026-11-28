import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import { spawn } from 'node:child_process';
import { mkdtemp, readFile, readdir, lstat, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';

export interface SkillFile { path: string; content: string }
export interface SkillFileMeta { path: string; sha256: string; size_bytes: number }
export interface SkillManifest {
  skill_id: string; name: string; version: number; version_id: string; digest: string;
  files: SkillFileMeta[]; file_count: number; next_file_offset: number | null;
}
export interface SkillCredentials { apiKey: string; secretKey: string; workspaceId: string }
interface Chunk {
  path: string; sha256: string; size_bytes: number; total_chars: number;
  offset: number; next_offset: number | null; eof: boolean; text: string;
}
interface ToolResult {
  isError?: boolean; content: { type: string; text?: string }[]; structuredContent: unknown;
}

export const sha256 = (value: string | Buffer) => createHash('sha256').update(value).digest('hex');

// services/skills_hub/packages.py:manifest_digest uses Python code-point sorting
// and json.dumps(ensure_ascii=True, separators=(",", ":")). No product import.
export function skillDigest(files: SkillFile[]): string {
  const pairs = files.map(file => [file.path, sha256(file.content)]);
  pairs.sort(([a], [b]) => {
    const left = Array.from(a, char => char.codePointAt(0)!);
    const right = Array.from(b, char => char.codePointAt(0)!);
    for (let index = 0; index < Math.min(left.length, right.length); index++) {
      if (left[index] !== right[index]) return left[index] - right[index];
    }
    return left.length - right.length;
  });
  const canonical = JSON.stringify(pairs).replace(/[\u0080-￿]/g,
    char => `\\u${char.charCodeAt(0).toString(16).padStart(4, '0')}`);
  return `sha256:${sha256(canonical)}`;
}

export function safeSkillPath(path: string): string {
  assert.ok(typeof path === 'string' && path.length > 0 && path.length <= 255);
  assert.ok(!path.includes('\\') && !/[\x00-\x1f\x7f]/.test(path));
  const parts = path.split('/');
  assert.ok(parts.length <= 8 && parts.every(part => part && part !== '.' && part !== '..' && !/[. ]$/.test(part)),
    'Invalid manifest path');
  return path;
}

export function verifySkillFiles(manifest: SkillManifest, files: SkillFile[]) {
  assert.deepEqual(files.map(file => file.path).sort(), manifest.files.map(file => file.path).sort(), 'File set differs');
  assert.equal(new Set(files.map(file => file.path)).size, files.length, 'Duplicate file');
  for (const meta of manifest.files) {
    safeSkillPath(meta.path);
    const file = files.find(candidate => candidate.path === meta.path)!;
    assert.equal(sha256(file.content), meta.sha256, `SHA mismatch for ${meta.path}`);
    assert.equal(Buffer.byteLength(file.content), meta.size_bytes, `Size mismatch for ${meta.path}`);
  }
  assert.equal(skillDigest(files), manifest.digest, 'Canonical bundle digest differs');
}

function localApi(apiUrl: string): string {
  const url = new URL(apiUrl);
  assert.equal(url.hostname, 'localhost', 'Skills consumption harness requires localhost');
  assert.equal(url.protocol, 'http:');
  assert.equal(url.username + url.password + url.search + url.hash, '');
  assert.equal(url.pathname, '/');
  return url.origin;
}

// frontend/src/sections/skills/utils/install.js:buildInstallCommand; deliberately
// reproduce the frozen project/production recipe, never execute arbitrary clipboard text.
export function expectedSkillCommand(apiUrl: string, name: string): string {
  assert.match(name, /^[a-z0-9]+(-[a-z0-9]+)*$/);
  assert.ok(name.length <= 64);
  const url = `${localApi(apiUrl)}/model-hub/skills/download/?${new URLSearchParams({ names: name, label: 'production' })}`;
  return `mkdir -p .claude/skills && curl -fsSL -H "X-Api-Key: $FI_API_KEY" -H "X-Secret-Key: $FI_SECRET_KEY" "${url}" -o /tmp/futureagi-skills.zip && unzip -oq /tmp/futureagi-skills.zip -d .claude/skills && rm /tmp/futureagi-skills.zip`;
}

export function validateSkillCommand(command: string, apiUrl: string, name: string) {
  // Do not include the rejected command in diagnostics (it could contain a key).
  assert.ok(command.trim() === expectedSkillCommand(apiUrl, name), 'Copied command differs from the frozen install contract');
}

async function installedPaths(root: string, prefix = ''): Promise<string[]> {
  const paths: string[] = [];
  for (const name of await readdir(join(root, prefix))) {
    const path = prefix ? `${prefix}/${name}` : name;
    const stat = await lstat(join(root, path));
    assert.ok(!stat.isSymbolicLink(), 'Installed symlink is forbidden');
    if (stat.isDirectory()) paths.push(...await installedPaths(root, path));
    else { assert.ok(stat.isFile(), 'Installed entry is not a regular file'); paths.push(path); }
  }
  return paths.sort();
}

/** Run only the exact copied legacy recipe, inside an owned empty directory.
 * Lock/refuse an existing fixed ZIP before running; never execute bundled scripts.
 * Child output is discarded, and credentials are passed only in its environment.
 */
export async function installSkillCommand(command: string, apiUrl: string, credentials: SkillCredentials,
  manifest: SkillManifest): Promise<{ version: number; versionId: string; digest: string; files: SkillFile[] }> {
  validateSkillCommand(command, apiUrl, manifest.name);
  for (const file of manifest.files) safeSkillPath(file.path);
  const project = await mkdtemp(join(tmpdir(), 'e2e-skills-install-'));
  try {
    await new Promise<void>((resolve, reject) => {
      const guarded = 'test ! -e /tmp/futureagi-skills.zip && test ! -L /tmp/futureagi-skills.zip || exit 72; ' +
        'trap "rm -f /tmp/futureagi-skills.zip" EXIT; ' + command;
      const child = spawn('flock', ['-w', '10', '/tmp/futureagi-e2e-skills-install.lock', 'bash', '-eu', '-c', guarded], {
        cwd: project, detached: true, stdio: 'ignore',
        env: { PATH: process.env.PATH, HOME: project, FI_API_KEY: credentials.apiKey, FI_SECRET_KEY: credentials.secretKey },
      });
      const timer = setTimeout(() => {
        if (child.pid) { try { process.kill(-child.pid, 'SIGKILL'); } catch { /* already exited */ } }
      }, 110_000);
      child.on('error', () => { clearTimeout(timer); reject(new Error('Install process could not start')); });
      child.on('close', code => {
        clearTimeout(timer);
        if (code === 0) resolve(); else reject(new Error(`Copied install failed (exit ${code}; output suppressed)`));
      });
    });
    const root = join(project, '.claude/skills');
    assert.deepEqual(await installedPaths(root), [
      '.futureagi-skills.lock.json', ...manifest.files.map(file => `${manifest.name}/${file.path}`),
    ].sort(), 'Installed inventory differs');
    const lock = JSON.parse(await readFile(join(root, '.futureagi-skills.lock.json'), 'utf8'));
    assert.equal(lock.schema, 1);
    assert.equal(lock.origin.replace(/\/$/, ''), localApi(apiUrl));
    assert.deepEqual(lock.skipped, []);
    assert.deepEqual(lock.skills, [{ name: manifest.name, version: manifest.version,
      version_id: manifest.version_id, digest: manifest.digest, label: 'production' }]);
    const files = await Promise.all(manifest.files.map(async file => ({ path: file.path,
      content: await readFile(join(root, manifest.name, file.path), 'utf8') })));
    verifySkillFiles(manifest, files);
    return { version: manifest.version, versionId: manifest.version_id, digest: manifest.digest, files };
  } finally { await rm(project, { recursive: true, force: true }); }
}

export function parseSkillToolResult<T>(result: ToolResult): T {
  assert.notEqual(result.isError, true, 'MCP returned a tool error');
  const payload = JSON.parse(result.content.filter(item => item.type === 'text').map(item => item.text).join(''));
  // mcp_app.py emits the same executor-unwrapped object in both representations.
  assert.deepEqual(result.structuredContent, payload, 'MCP structured/text disagreement');
  assert.notEqual(payload._mcp?.truncated, true, 'MCP payload was truncated');
  return payload as T;
}

/** HTTP through /mcp exercises api_executor and bounded_response, not a REST substitute.
 * Pin resolved immutable versions for continuation; all requests share one 120s deadline.
 */
export async function readSkillViaMcp(apiUrl: string, credentials: SkillCredentials, name: string) {
  const origin = localApi(apiUrl);
  const signal = AbortSignal.timeout(120_000);
  let id = 0;
  let protocolVersion = '2025-03-26'; // tfc/asgi.py /mcp, stateless streamable JSON HTTP.
  const headers = () => ({ 'X-Api-Key': credentials.apiKey, 'X-Secret-Key': credentials.secretKey,
    'X-Workspace-Id': credentials.workspaceId, 'Content-Type': 'application/json',
    Accept: 'application/json, text/event-stream', 'MCP-Protocol-Version': protocolVersion });
  async function rpc<T>(method: string, params: unknown): Promise<T> {
    const response = await fetch(`${origin}/mcp`, { method: 'POST', headers: headers(), signal,
      redirect: 'error', body: JSON.stringify({ jsonrpc: '2.0', id: ++id, method, params }) });
    assert.equal(response.status, 200, `MCP ${method} HTTP status`);
    const message = await response.json();
    assert.equal(message.error, undefined, `MCP ${method} protocol error`);
    return message.result as T;
  }
  const init = await rpc<{ protocolVersion: string }>('initialize', { protocolVersion, capabilities: {},
    clientInfo: { name: 'skills-e2e', version: '1.0.0' } });
  protocolVersion = init.protocolVersion;
  const notification = await fetch(`${origin}/mcp`, { method: 'POST', headers: headers(), signal, redirect: 'error',
    body: JSON.stringify({ jsonrpc: '2.0', method: 'notifications/initialized' }) });
  assert.equal(notification.status, 202);
  async function tool<T>(name: string, args: unknown): Promise<T> {
    return parseSkillToolResult<T>(await rpc<ToolResult>('tools/call', { name, arguments: args }));
  }
  const list = await tool<{ results: { id: string; name: string }[] }>('list_skills', { search: name });
  assert.deepEqual(list.results.map(skill => skill.name), [name], 'MCP list exact name');
  // tools.yaml get_skill / resolve returns file_count and next_file_offset (25 per MCP page).
  const manifest = await tool<SkillManifest>('get_skill', { name, label: 'production' });
  assert.equal(list.results[0].id, manifest.skill_id);
  let next = manifest.next_file_offset;
  for (let page = 0; next != null; page++) {
    assert.ok(page < 8, 'Manifest did not terminate');
    assert.equal(next, manifest.files.length, 'Manifest continuation offset');
    const part = await tool<SkillManifest>('get_skill', { name, version: manifest.version, file_offset: next });
    assert.equal(part.version_id, manifest.version_id);
    assert.equal(part.digest, manifest.digest);
    assert.ok(part.files.length > 0, 'Empty manifest continuation');
    manifest.files.push(...part.files);
    next = part.next_file_offset;
  }
  assert.equal(manifest.files.length, manifest.file_count);
  assert.ok(manifest.files.every(file => !Object.hasOwn(file, 'content')));
  const files: SkillFile[] = [];
  let chunks = 0;
  for (const file of manifest.files) {
    safeSkillPath(file.path);
    let offset = 0;
    let content = '';
    for (let index = 0; ; index++) {
      assert.ok(index < 100, 'Chunk stream did not terminate');
      const part = await tool<Chunk>('read_skill_file', { skill_id: manifest.skill_id,
        number: manifest.version, path: file.path, offset, max_bytes: 24000 });
      chunks++;
      assert.equal(part.path, file.path);
      assert.equal(part.sha256, file.sha256);
      assert.equal(part.size_bytes, file.size_bytes);
      assert.equal(part.offset, offset);
      assert.ok(Buffer.byteLength(part.text) <= 24000);
      content += part.text;
      if (part.eof) {
        assert.equal(part.next_offset, null);
        assert.equal(Array.from(content).length, part.total_chars);
        break;
      }
      assert.equal(part.next_offset, offset + Array.from(part.text).length);
      assert.ok(part.next_offset! > offset);
      offset = part.next_offset!;
    }
    files.push({ path: file.path, content });
  }
  verifySkillFiles(manifest, files);
  return { manifest, files, chunks };
}
