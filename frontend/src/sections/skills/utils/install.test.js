import { describe, expect, it } from 'vitest';
import { INSTALL_CLIENTS } from '../constants';
import { buildInstallCommand, buildCurlResolve, buildPythonSnippet, buildTypeScriptSnippet, buildMcpSnippet } from './install';

const input = { hostApi: 'https://example.invalid/api/', names: ['support-policy'], label: 'production' };
const auth = '-H "X-Api-Key: $FI_API_KEY" -H "X-Secret-Key: $FI_SECRET_KEY"';
describe('install commands', () => {
  it.each(INSTALL_CLIENTS.flatMap((client) => [client.projectDir, client.personalDir]))('uses the exact command for %s', (dir) => {
    expect(buildInstallCommand({ ...input, dir })).toBe(`mkdir -p ${dir} && curl -fsSL ${auth} "https://example.invalid/api/model-hub/skills/download/?names=support-policy&label=production" -o /tmp/futureagi-skills.zip && unzip -oq /tmp/futureagi-skills.zip -d ${dir} && rm /tmp/futureagi-skills.zip`);
  });
  it('supports tags and defaults to production', () => expect(buildInstallCommand({ hostApi: input.hostApi, tag: 'support' })).toContain('download/?tag=support&label=production'));
  it('pins an immutable version without sending a label', () => {
    const command = buildInstallCommand({ ...input, label: undefined, version: 4 });
    expect(command).toContain('names=support-policy&version=4');
    expect(command).not.toContain('label=');
  });
  it('quotes user-supplied shell locations', () => expect(buildInstallCommand({ ...input, dir: "folder with ' quotes" })).toContain("mkdir -p 'folder with '\\'' quotes'"));
  it('rejects mixed selectors and unsupported version batches', () => {
    expect(() => buildInstallCommand({ ...input, version: 2 })).toThrow('not both');
    expect(() => buildInstallCommand({ hostApi: input.hostApi, tag: 'support', version: 2 })).toThrow('exactly one');
    expect(() => buildInstallCommand({ hostApi: input.hostApi })).toThrow('Choose skills');
  });
  it.each(['file:///tmp/api', 'https://user:password@example.invalid', 'https://example.invalid/?secret=x'])('rejects unsafe API base %s', (hostApi) => expect(() => buildInstallCommand({ ...input, hostApi })).toThrow('HTTP API'));
});
describe('consumer snippets', () => {
  it('builds cURL for the first name with complete content', () => expect(buildCurlResolve({ ...input, names: ['support-policy', 'another'] })).toBe(`curl -fsSL ${auth} "https://example.invalid/api/model-hub/skills/resolve/?name=support-policy&label=production&include_content=true"`));
  it('uses environment credentials in Python and TypeScript', () => {
    expect(buildPythonSnippet(input)).toContain('os.environ["FI_API_KEY"]');
    expect(buildPythonSnippet(input)).toContain('json.load(response)["result"]');
    expect(buildTypeScriptSnippet(input)).toContain('process.env.FI_SECRET_KEY!');
    expect(buildTypeScriptSnippet(input)).toContain('if (!response.ok)');
    expect(buildTypeScriptSnippet(input)).toContain('manifest.version_id, manifest.digest');
  });
  it('describes lossless MCP reads pinned to a resolved version', () => {
    expect(buildMcpSnippet(input)).toContain('"label": "production"');
    expect(buildMcpSnippet({ ...input, label: undefined, version: 3 })).toContain('"version": 3');
    expect(buildMcpSnippet(input)).toContain('Follow next_offset until eof');
    expect(buildMcpSnippet(input)).toContain('verify its UTF-8 sha256');
  });
  it.each([buildCurlResolve, buildPythonSnippet, buildTypeScriptSnippet, buildMcpSnippet])('rejects ambiguous selectors for each snippet', (builder) => expect(() => builder({ ...input, version: 1 })).toThrow('not both'));
});
