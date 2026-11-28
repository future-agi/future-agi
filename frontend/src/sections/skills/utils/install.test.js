import { describe, expect, it } from 'vitest';
import { INSTALL_CLIENTS } from '../constants';
import {
  buildCurlInstall, buildCurlResolve, buildInstallCommand, buildMcpSnippet, buildPythonSnippet, buildSkillPlan, buildSkillUpdate, buildTypeScriptSnippet,
  cliApplyCommand, cliPlanLines, installerPreview, registryScope, selector,
} from './install';

const ORG = '22222222-2222-4222-8222-222222222222';
const WS = '33333333-3333-4333-8333-333333333333';
const scope = { origin: 'https://api.example.invalid', organizationId: ORG, workspaceId: WS };
const input = { ...scope, names: ['support-policy'], label: 'production' };
const plan = { ...scope, name: 'support-policy', label: 'production', profile: { id: 'claude-code' }, project: '.', preview: true };
const FULL_KEYS = /FI_API_KEY|FI_SECRET_KEY|X-Api-Key|X-Secret-Key|unzip/;
const env = [
  "export FI_CONTEXT_ORIGIN='https://api.example.invalid'",
  `export FI_CONTEXT_ORGANIZATION_ID='${ORG}'`,
  `export FI_CONTEXT_WORKSPACE_ID='${WS}'`,
  '# needs your read-only key in FI_CONTEXT_CREDENTIAL',
];

describe('skill plan commands', () => {
  it('builds the plan and inspect commands with the read-only key variables', () => {
    expect(buildSkillPlan(plan).split('\n')).toEqual([
      ...env,
      "python -m fi.skills plan --name 'support-policy' --label 'production' --client 'claude-code' --project '.' --allow-preview --out 'skill-plan.json'",
      "python -m fi.skills inspect --plan 'skill-plan.json' --diff",
    ]);
  });
  it('pins an immutable version without sending a label', () => {
    const command = buildSkillPlan({ ...plan, label: undefined, version: 4 });
    expect(command).toContain('--name \'support-policy\' --version 4 --client');
    expect(command).not.toContain('--label');
  });
  it('defaults to the production label', () => expect(buildSkillPlan({ ...plan, label: undefined })).toContain("--label 'production'"));
  it('always carries the preview opt-in for a skill, which the CLI requires, and installs a qualified package only when asked', () => {
    const qualified = { ...plan, preview: false, profile: { id: 'codex', state: 'verified', version: 'p1' }, packageVersion: '1.2.3' };
    const lines = buildSkillPlan(qualified).split('\n');
    expect(lines[0]).toBe("python -m pip install 'futureagi==1.2.3'");
    expect(lines.find((line) => line.includes(' plan '))).toContain('--allow-preview');
  });
  it('refuses an unqualified package or host profile instead of inventing one', () => {
    expect(() => buildSkillPlan({ ...plan, preview: false })).toThrow('not qualified');
    expect(() => buildSkillPlan({ ...plan, profile: { id: 'cursor' } })).toThrow('not qualified');
  });
  it('rejects mixed selectors, unsafe names, paths and labels', () => {
    expect(() => buildSkillPlan({ ...plan, version: 2 })).toThrow('not both');
    expect(() => buildSkillPlan({ ...plan, name: 'Bad Name' })).toThrow('Choose one skill');
    expect(() => buildSkillPlan({ ...plan, project: '--help' })).toThrow('project path');
    expect(() => buildSkillPlan({ ...plan, label: 'Has Space' })).toThrow('valid label');
    expect(() => buildSkillPlan({ ...plan, label: undefined, version: 0 })).toThrow('published version');
  });
  it.each(['file:///tmp/api', 'http://example.invalid', 'https://user:password@example.invalid', 'https://example.invalid/?secret=x', 'not a url'])('rejects unsafe API origin %s', (origin) => {
    expect(() => buildSkillPlan({ ...plan, origin })).toThrow('HTTPS origin');
  });
  it('rejects a missing workspace identity', () => expect(() => buildSkillPlan({ ...plan, workspaceId: undefined })).toThrow('explicit workspace identity'));
  it('quotes user-supplied project locations', () => expect(buildSkillPlan({ ...plan, project: "folder with ' quotes" })).toContain("--project 'folder with '\"'\"' quotes'"));
  it('applies the skill plan under its own plan file', () => expect(cliApplyCommand('skill')).toBe("python -m fi.skills apply --plan 'skill-plan.json' --quiescent"));
  it('checks for an update and then writes a plan to review', () => {
    expect(buildSkillUpdate({ ...scope, name: 'support-policy', project: '.' }).split('\n')).toEqual([
      ...env.slice(0, 3), env[3],
      "python -m fi.skills update --project '.' --name 'support-policy' --check --allow-preview",
      "python -m fi.skills update --project '.' --name 'support-policy' --allow-preview --plan 'skill-plan.json'",
      "python -m fi.skills inspect --plan 'skill-plan.json' --diff",
    ]);
  });
});

describe('the shared steps', () => {
  it('builds the context command exactly as before from the same lines', () => {
    const url = registryScope(scope);
    expect(cliPlanLines({ kind: 'agent_context', name: 'support-agent', selectorFlags: '--version 3', client: 'codex', project: '.', preview: true, url, organizationId: ORG, workspaceId: WS }).slice(-2)).toEqual([
      "python -m fi.context plan --name 'support-agent' --version 3 --client 'codex' --project '.' --allow-preview --out 'context-plan.json'",
      "python -m fi.context inspect --plan 'context-plan.json' --diff",
    ]);
    expect(cliApplyCommand('agent_context')).toBe("python -m fi.context apply --plan 'context-plan.json' --quiescent");
  });
  it('shows the preview badge until a released package and a verified profile exist, the same for both', () => {
    expect(installerPreview({})).toBe(true);
    expect(installerPreview({ qualification: { released: true }, profile: { state: 'preview' } })).toBe(true);
    expect(installerPreview({ qualification: { released: false }, profile: { state: 'verified' } })).toBe(true);
    expect(installerPreview({ qualification: { released: true }, profile: { state: 'verified' } })).toBe(false);
  });
  it('picks a label or a version, never both', () => {
    expect(selector({})).toEqual({ label: 'production' });
    expect(selector({ version: '3' })).toEqual({ version: 3 });
    expect(() => selector({ label: 'a', version: 1 })).toThrow('not both');
  });
});

describe('fallback without Python', () => {
  it('resolves with the read-only key in one request', () => {
    expect(buildCurlResolve(input).split('\n')).toEqual([
      ...env,
      `curl -fsS -G -H "Authorization: Context $FI_CONTEXT_CREDENTIAL" -H "X-Organization-Id: $FI_CONTEXT_ORGANIZATION_ID" -H "X-Workspace-Id: $FI_CONTEXT_WORKSPACE_ID" --data-urlencode 'name=support-policy' --data-urlencode 'label=production' "$FI_CONTEXT_ORIGIN/model-hub/skills/consumption/resolve/"`,
    ]);
  });
  it('sends a numbered version instead of a label', () => {
    const command = buildCurlResolve({ ...input, label: undefined, version: 4 });
    expect(command).toContain('--data-urlencode "version=4"');
    expect(command).not.toContain('label=');
  });
  it.each(INSTALL_CLIENTS.flatMap((client) => [client.projectDir, client.personalDir]))('writes into the %s folder', (dir) => {
    const script = buildCurlInstall({ ...input, dir });
    expect(script).toContain(dir.startsWith('~/') ? `dir="$HOME"/'${dir.slice(2)}/support-policy'` : `dir='${dir}/support-policy'`);
  });
  it('reads the manifest and every file through the lease, and follows both continuations', () => {
    const script = buildCurlInstall(input);
    expect(script).toContain('"$base/resolve/"');
    expect(script).toContain('X-Context-Resolution: $lease');
    expect(script).toContain('.result.next_file_offset');
    expect(script).toContain('.result.next_offset');
    expect(script).toContain('--data-urlencode "path=$entry"');
    expect(script).not.toMatch(/\$path\b|read -r path\b/);
    expect(script).toContain('jq -j .result.text');
    expect(script).toContain('/model-hub/skills/consumption"');
    expect(script.startsWith(env.join('\n'))).toBe(true);
    expect(script).toMatch(/\n\(\nset -eu\n/);
    expect(script.trimEnd().endsWith('\n)')).toBe(true);
  });
  it('refuses paths that leave the install folder', () => expect(buildCurlInstall(input)).toContain('/*|..|../*|*/..|*/../*) echo "Refusing unsafe path'));
  it('quotes a folder with spaces and quotes', () => expect(buildCurlInstall({ ...input, dir: "my skills/it's" })).toContain(`dir='my skills/it'"'"'s/support-policy'`));
  it('rejects a folder that could be read as an option, and mixed selectors', () => {
    expect(() => buildCurlInstall({ ...input, dir: '-rf' })).toThrow('install folder');
    expect(() => buildCurlInstall({ ...input, version: 2 })).toThrow('not both');
    expect(() => buildCurlInstall({ ...input, names: [] })).toThrow('Choose one skill');
  });
  it('cannot embed a shell expansion through the skill name', () => {
    expect(() => buildCurlInstall({ ...input, names: ['$(touch x)'] })).toThrow('Choose one skill');
    expect(() => buildCurlResolve({ ...input, names: ['a"; rm -rf /'] })).toThrow('Choose one skill');
  });
});

describe('application snippets', () => {
  it('fetches through the SDK SkillsClient with the read-only key in Python', () => {
    const code = buildPythonSnippet(input);
    expect(code).toContain('from fi.platform import Client, ContextAuth');
    expect(code).toContain('from fi.skills import SkillsClient');
    expect(code).toContain('auth=ContextAuth(os.environ["FI_CONTEXT_CREDENTIAL"])');
    expect(code).toContain('base_url=os.environ["FI_CONTEXT_ORIGIN"]');
    expect(code).toContain('workspace_id=os.environ["FI_CONTEXT_WORKSPACE_ID"]');
    expect(code).toContain('{"X-Organization-Id": os.environ["FI_CONTEXT_ORGANIZATION_ID"]}');
    expect(code).toContain('SkillsClient(client).resolve("support-policy", label="production")');
    expect(code).toContain('skill.read_file("SKILL.md")');
  });
  it('fetches through the SDK SkillsClient with the read-only key in TypeScript', () => {
    const code = buildTypeScriptSnippet({ ...input, label: undefined, version: 3 });
    expect(code).toContain('import { Client, SkillsClient } from "@future-agi/sdk";');
    expect(code).toContain('auth: { type: "context", credential: process.env.FI_CONTEXT_CREDENTIAL! }');
    expect(code).toContain('.resolve("support-policy", { version: 3 })');
    expect(code).toContain('await skill.readFile("SKILL.md")');
  });
  it('pins a version in Python without a label', () => {
    const code = buildPythonSnippet({ ...input, label: undefined, version: 3 });
    expect(code).toContain('.resolve("support-policy", version=3)');
    expect(code).not.toContain('label=');
  });
  it('describes lossless MCP reads pinned to a resolved version', () => {
    expect(buildMcpSnippet(input)).toContain('"label": "production"');
    expect(buildMcpSnippet({ ...input, label: undefined, version: 3 })).toContain('"version": 3');
    expect(buildMcpSnippet(input)).toContain('Follow next_offset until eof');
    expect(buildMcpSnippet(input)).toContain('verify its UTF-8 sha256');
  });
  it.each([buildCurlResolve, buildCurlInstall, buildPythonSnippet, buildTypeScriptSnippet, buildMcpSnippet])('rejects ambiguous selectors for each snippet', (builder) => expect(() => builder({ ...input, version: 1 })).toThrow('not both'));
});

describe('no generated text needs the full API keys', () => {
  it.each([
    ['plan', () => buildSkillPlan(plan)],
    ['qualified plan', () => buildSkillPlan({ ...plan, preview: false, profile: { id: 'codex', state: 'verified', version: 'p1' }, packageVersion: '1.0.0' })],
    ['update', () => buildSkillUpdate({ ...scope, name: 'support-policy', project: '.' })],
    ['curl resolve', () => buildCurlResolve(input)],
    ['curl install', () => buildCurlInstall(input)],
    ['curl install, personal', () => buildCurlInstall({ ...input, dir: '~/.claude/skills' })],
    ['python', () => buildPythonSnippet(input)],
    ['typescript', () => buildTypeScriptSnippet(input)],
    ['mcp', () => buildMcpSnippet(input)],
  ])('%s', (_name, build) => expect(build()).not.toMatch(FULL_KEYS));
});

// The ZIP command is kept only for the two header-menu copy actions in other lanes' files.
describe('deprecated ZIP command', () => {
  const auth = '-H "X-Api-Key: $FI_API_KEY" -H "X-Secret-Key: $FI_SECRET_KEY"';
  it.each(INSTALL_CLIENTS.flatMap((client) => [client.projectDir, client.personalDir]))('still builds the unchanged command for %s', (dir) => {
    expect(buildInstallCommand({ hostApi: 'https://example.invalid/api/', names: ['support-policy'], label: 'production', dir })).toBe(`mkdir -p ${dir} && curl -fsSL ${auth} "https://example.invalid/api/model-hub/skills/download/?names=support-policy&label=production" -o /tmp/futureagi-skills.zip && unzip -oq /tmp/futureagi-skills.zip -d ${dir} && rm /tmp/futureagi-skills.zip`);
  });
  it('keeps its selector rules', () => {
    expect(buildInstallCommand({ hostApi: 'https://example.invalid/api/', names: ['a'], version: 4 })).toContain('names=a&version=4');
    expect(() => buildInstallCommand({ hostApi: 'https://example.invalid/api/', names: ['a'], label: 'x', version: 2 })).toThrow('not both');
    expect(() => buildInstallCommand({ hostApi: 'https://example.invalid/api/' })).toThrow('Choose skills');
  });
});
