import { test as base } from '@playwright/test';
import { createServer } from 'node:http';
import type { AddressInfo } from 'node:net';
import { unzipSync, strFromU8 } from 'fflate';
import { test, expect } from '../lib/fixtures';
import { E2E } from '../lib/env';
import { skillsArchive, uploadSkillsArchive } from '../lib/skills-upload';
import { expectedSkillCommand, installSkillCommand, parseSkillToolResult, readSkillViaMcp,
  safeSkillPath, sha256, skillDigest, validateSkillCommand, verifySkillFiles } from '../lib/skills-consumption';
import type { SkillManifest } from '../lib/skills-consumption';

const UI_READY = 60_000;
// model_hub/serializers/skills_hub.py:PushRequest and ResolveQuery.
const SKILLS = '/model-hub/skills/';

base('Skills harness: deterministic synthetic ZIP preserves exact UTF-8 paths and bytes', () => {
  const entries = { 'policy/SKILL.md': '# café 漢字 🧪\n', 'policy/references/policy.md': 'synthetic only\n' };
  const archive = skillsArchive(entries);
  expect(archive).toEqual(skillsArchive(entries));
  expect(Object.fromEntries(Object.entries(unzipSync(archive)).map(([path, data]) => [path, strFromU8(data)])))
    .toEqual(entries);
});

base('Skills harness: digest, exact inventory and corruption checks fail closed', () => {
  const files = [{ path: 'réf.md', content: 'abc' }];
  const golden = '[["r\\u00e9f.md","ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"]]';
  expect(skillDigest(files)).toBe(`sha256:${sha256(golden)}`);
  const manifest: SkillManifest = { name: 'synthetic', skill_id: 'id', version: 1, version_id: 'version',
    digest: skillDigest(files), file_count: 1, next_file_offset: null,
    files: [{ path: 'réf.md', sha256: sha256('abc'), size_bytes: 3 }] };
  expect(() => verifySkillFiles(manifest, files)).not.toThrow();
  expect(() => verifySkillFiles(manifest, [{ path: 'réf.md', content: 'changed' }])).toThrow(/SHA mismatch/);
  expect(() => verifySkillFiles(manifest, [])).toThrow(/File set differs/);
  expect(() => verifySkillFiles(manifest, [...files, ...files])).toThrow(/File set differs/);
  expect(() => verifySkillFiles({ ...manifest, digest: 'sha256:broken' }, files)).toThrow(/bundle digest differs/);
  for (const path of ['../bad', '/absolute', 'a\\b', 'a//b', 'a/./b', 'a/x ', 'a/x.', 'a\0b']) {
    expect(() => safeSkillPath(path)).toThrow();
  }
});

base('Skills harness: MCP truncation, tool errors and dual-output disagreement are rejected', () => {
  const result = (payload: object) => ({ content: [{ type: 'text', text: JSON.stringify(payload) }], structuredContent: payload });
  expect(parseSkillToolResult(result({ version: 1 }))).toEqual({ version: 1 });
  expect(() => parseSkillToolResult(result({ _mcp: { truncated: true } }))).toThrow(/truncated/);
  expect(() => parseSkillToolResult({ ...result({}), isError: true })).toThrow(/tool error/);
  expect(() => parseSkillToolResult({ ...result({ version: 1 }), structuredContent: { version: 2 } })).toThrow(/disagreement/);
});

base('Skills harness: clipboard commands cannot inject shell text or a foreign host', () => {
  const origin = 'http://localhost:8100';
  const command = expectedSkillCommand(origin, 'synthetic');
  expect(() => validateSkillCommand(command, origin, 'synthetic')).not.toThrow();
  for (const changed of [command + '; echo injected', command.replace('localhost', 'example.invalid'), command.replace('synthetic', 'other')]) {
    expect(() => validateSkillCommand(changed, origin, 'synthetic')).toThrow(/frozen install/);
  }
  for (const origin of ['http://127.0.0.1:8100', 'http://localhost/path', 'http://user@localhost']) {
    expect(() => expectedSkillCommand(origin, 'synthetic')).toThrow();
  }
  expect(() => expectedSkillCommand(origin, 'bad;echo injected')).toThrow();
});

base('Skills harness: transport rejects one-sided corrupt hashes, offsets and incomplete manifests', async () => {
  const files = [{ path: 'SKILL.md', content: 'café 🧪' }];
  const manifest = { skill_id: 'synthetic-id', name: 'synthetic', version: 1, version_id: 'synthetic-version',
    digest: skillDigest(files), file_count: 1, next_file_offset: null,
    files: [{ path: 'SKILL.md', sha256: sha256(files[0].content), size_bytes: Buffer.byteLength(files[0].content) }] };
  let defect = '';
  const server = createServer(async (req, res) => {
    const bytes: Buffer[] = [];
    for await (const chunk of req) bytes.push(Buffer.from(chunk));
    const call = JSON.parse(Buffer.concat(bytes).toString());
    if (call.method === 'notifications/initialized') { res.writeHead(202).end(); return; }
    let payload: unknown;
    if (call.method === 'initialize') payload = { protocolVersion: '2025-03-26' };
    else {
      const value = call.params.name === 'list_skills' ? { results: [{ id: manifest.skill_id, name: 'synthetic' }] }
        : call.params.name === 'get_skill' ? { ...manifest, file_count: defect === 'inventory' ? 2 : 1 }
        : { path: 'SKILL.md', sha256: defect === 'hash' ? 'broken' : manifest.files[0].sha256,
          size_bytes: manifest.files[0].size_bytes, total_chars: Array.from(files[0].content).length,
          offset: 0, next_offset: defect === 'offset' ? 0 : null, eof: defect !== 'offset', text: files[0].content };
      payload = { content: [{ type: 'text', text: JSON.stringify(value) }], structuredContent: value };
    }
    res.writeHead(200, { 'Content-Type': 'application/json' }).end(JSON.stringify({ jsonrpc: '2.0', id: call.id, result: payload }));
  });
  await new Promise<void>(resolve => server.listen(0, 'localhost', resolve));
  const origin = `http://localhost:${(server.address() as AddressInfo).port}`;
  const credentials = { apiKey: 'synthetic', secretKey: 'synthetic', workspaceId: 'synthetic' };
  try {
    expect((await readSkillViaMcp(origin, credentials, 'synthetic')).files).toEqual(files);
    for (defect of ['hash', 'offset', 'inventory']) {
      await expect(readSkillViaMcp(origin, credentials, 'synthetic')).rejects.toThrow();
    }
  } finally { await new Promise<void>((resolve, reject) => server.close(error => error ? reject(error) : resolve())); }
});

test('Skills harness: real ZIP preview, copied-recipe install and paged UTF-8 MCP reconstruction', async ({ page, actor }, testInfo) => {
  test.setTimeout(420_000); // 2 UI stages × 60s + install 120s + MCP 120s + setup 60s.
  const name = `e2e-skills-harness-${testInfo.workerIndex}-${Date.now().toString(36)}`;
  const invalid = `${name}-bad`;
  const root = `---\nname: ${name}\ndescription: Use when verifying synthetic lossless skill transport in the test harness.\n---\n` +
    'Synthetic café 漢字 🧪; no commands or real policy.\n'.repeat(2500);
  const files = [{ path: 'SKILL.md', content: root }, ...Array.from({ length: 27 }, (_, index) => ({
    path: `references/ref-${String(index).padStart(2, '0')}.md`, content: `Synthetic reference ${index}\n`,
  }))];
  expect(Buffer.byteLength(root)).toBeGreaterThan(100 * 1024);
  await testInfo.attach('synthetic-inputs', { body: JSON.stringify({ name, invalid, hashes: files.map(file => [file.path, sha256(file.content)]) }),
    contentType: 'application/json' });
  await page.goto('/dashboard/skills', { waitUntil: 'domcontentloaded' });
  // LibraryEmptyState and the populated PageHeader expose the same action with
  // different labels. Do not race the temporary header rendered during loading.
  await page.getByRole('button', { name: /^(Import|Import a folder or ZIP)$/ }).click({ timeout: UI_READY });
  const dialog = page.getByRole('dialog');
  const archive = await uploadSkillsArchive(dialog.locator('input[type=file]:not([webkitdirectory])'), {
    [`${name}/SKILL.md`]: root,
    [`${invalid}/SKILL.md`]: `---\nname: ${invalid}\n---\n# Missing description\n`,
  });
  const previewPromise = page.waitForResponse(response => response.url().endsWith(`${SKILLS}import/preview/`), { timeout: UI_READY });
  await dialog.getByRole('button', { name: 'Preview import', exact: true }).click();
  const response = await previewPromise;
  expect(response.status()).toBe(200);
  const preview = (await response.json()).result;
  expect(preview.bundles.map((bundle: { name: string; status: string }) => [bundle.name, bundle.status]).sort())
    .toEqual([[name, 'new'], [invalid, 'invalid']].sort());
  await expect(dialog.getByRole('checkbox', { name: `Select ${invalid}`, exact: true })).toBeDisabled({ timeout: UI_READY });
  await testInfo.attach('upload-sha256', { body: sha256(archive), contentType: 'text/plain' });
  const seeded = await actor.api.post<{ result: { skill: { id: string }; version: { id: string } } }>(`${SKILLS}publish/`, {
    files, labels: ['production'], acknowledge_warnings: true, expected_release_revision: 0,
  });
  await testInfo.attach('seeded-skill', { body: JSON.stringify(seeded.result), contentType: 'application/json' });
  const { result: manifest } = await actor.api.get<{ result: SkillManifest }>(`${SKILLS}resolve/`, { name });
  const install = await installSkillCommand(expectedSkillCommand(E2E.apiUrl, name), E2E.apiUrl, actor, manifest);
  expect(install.files.sort((a, b) => a.path.localeCompare(b.path))).toEqual([...files].sort((a, b) => a.path.localeCompare(b.path)));
  const mcp = await readSkillViaMcp(E2E.apiUrl, actor, name);
  expect(mcp.manifest.version_id).toBe(manifest.version_id);
  expect(mcp.manifest.files).toHaveLength(28);
  expect(mcp.files).toEqual([...files].sort((a, b) => a.path.localeCompare(b.path)));
  expect(mcp.chunks).toBeGreaterThan(files.length);
  await testInfo.attach('consumption-receipt', { body: JSON.stringify({ versionId: manifest.version_id, digest: manifest.digest,
    files: mcp.manifest.files, chunks: mcp.chunks }), contentType: 'application/json' });
});
