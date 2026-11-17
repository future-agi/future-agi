// One install mechanism for skills and contexts (SKILL-TRACES UXF-06).
//
// Nothing here prints, reads or asks for the full API key pair. Every generated
// command and snippet takes the read-only key from FI_CONTEXT_CREDENTIAL and
// the workspace identity from FI_CONTEXT_ORIGIN, FI_CONTEXT_ORGANIZATION_ID and
// FI_CONTEXT_WORKSPACE_ID: the variables `python -m fi.context` and
// `python -m fi.skills` already read (sdk `fi/context/__main__.py`, `Registry`).

export const INSTALL_TOOLS = Object.freeze({
  agent_context: Object.freeze({ module: 'fi.context', planFile: 'context-plan.json', noun: 'context' }),
  skill: Object.freeze({ module: 'fi.skills', planFile: 'skill-plan.json', noun: 'skill' }),
});

const NAME_PATTERN = /^[a-z0-9]+(-[a-z0-9]+)*$/;
const UUID_PATTERN = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
const loopback = (host) => ['localhost', '127.0.0.1'].includes(host);

export const shellQuote = (value) => `'${String(value).replaceAll("'", "'\"'\"'")}'`;
export const safeText = (value) => typeof value === 'string' && value.length > 0 && [...value].every((char) => char.codePointAt(0) >= 32 && char.codePointAt(0) !== 127);

/** A label or a version, never both. Defaults to the production label. */
export function selector({ label, version } = {}) {
  if (version != null && label) throw new Error('Choose a label or a version, not both.');
  return version != null ? { version: Number(version) } : { label: label || 'production' };
}

function requireName(name) {
  if (typeof name !== 'string' || !NAME_PATTERN.test(name) || name.length > 64) throw new Error('Choose one skill to resolve.');
  return name;
}

/**
 * The one rule for the "Preview: not yet tested" badge. Both Install tabs call it,
 * so the badge shows on both or on neither. It clears only for a released package
 * and a verified host profile; installer e2e (`install-cli.spec.ts`) gates that.
 */
export function installerPreview({ qualification, profile } = {}) {
  return qualification?.released !== true || profile?.state !== 'verified';
}

/** The registry address and workspace identity, checked the same way for both kinds. */
export function registryScope({ origin, organizationId, workspaceId }) {
  let url;
  try { url = new URL(origin); } catch { throw new Error('Use an HTTPS origin and explicit workspace identity.'); }
  const local = url.protocol === 'http:' && loopback(url.hostname) && typeof window !== 'undefined' && loopback(window.location.hostname);
  if ((url.protocol !== 'https:' && !local) || url.username || url.password || url.search || url.hash || !UUID_PATTERN.test(organizationId) || !UUID_PATTERN.test(workspaceId)) {
    throw new Error('Use an HTTPS origin and explicit workspace identity.');
  }
  return url;
}

export function checkProfile({ profile, packageVersion, preview }) {
  if (!['codex', 'claude-code'].includes(profile?.id) || (!preview && (profile.state !== 'verified' || !profile.version || !/^\d+\.\d+\.\d+(?:[a-zA-Z0-9.+-]*)$/.test(packageVersion)))) {
    throw new Error('This package and host profile are not qualified.');
  }
}

export const registryEnvLines = ({ url, organizationId, workspaceId }) => [
  `export FI_CONTEXT_ORIGIN=${shellQuote(url.origin)}`,
  `export FI_CONTEXT_ORGANIZATION_ID=${shellQuote(organizationId)}`,
  `export FI_CONTEXT_WORKSPACE_ID=${shellQuote(workspaceId)}`,
];
export const CREDENTIAL_COMMENT = '# needs your read-only key in FI_CONTEXT_CREDENTIAL';

/** The plan and inspect commands for either kind. `selectorFlags` is `--version 2` or `--label production`. */
export function cliPlanLines({ kind, name, selectorFlags, client, project, preview, url, organizationId, workspaceId, packageVersion }) {
  const tool = INSTALL_TOOLS[kind];
  // `python -m fi.skills plan` refuses to run without --allow-preview until the SDK qualifies a host profile.
  const allowPreview = preview || kind === 'skill';
  return [
    ...(!preview ? [`python -m pip install ${shellQuote(`futureagi==${packageVersion}`)}`] : []),
    ...registryEnvLines({ url, organizationId, workspaceId }),
    CREDENTIAL_COMMENT,
    `python -m ${tool.module} plan --name ${shellQuote(name)} ${selectorFlags} --client ${shellQuote(client)} --project ${shellQuote(project)}${allowPreview ? ' --allow-preview' : ''} --out ${shellQuote(tool.planFile)}`,
    `python -m ${tool.module} inspect --plan ${shellQuote(tool.planFile)} --diff`,
  ];
}

export const cliApplyCommand = (kind) => `python -m ${INSTALL_TOOLS[kind].module} apply --plan ${shellQuote(INSTALL_TOOLS[kind].planFile)} --quiescent`;

/**
 * Plan commands for one skill. A label install resolves when the plan runs and the
 * plan then pins the digest; a numbered version is fixed from the start.
 */
export function buildSkillPlan({ name, label, version, origin, organizationId, workspaceId, profile, packageVersion, project, preview = false }) {
  const url = registryScope({ origin, organizationId, workspaceId });
  const chosen = selector({ label, version });
  if (!safeText(project) || project.startsWith('-')) throw new Error('Choose a skill and a project path.');
  requireName(name);
  if ('version' in chosen && (!Number.isSafeInteger(chosen.version) || chosen.version < 1)) throw new Error('Choose a published version.');
  if ('label' in chosen && !/^[a-z0-9][a-z0-9-]{0,39}$/.test(chosen.label)) throw new Error('Choose a valid label.');
  checkProfile({ profile, packageVersion, preview });
  return cliPlanLines({
    kind: 'skill', name, client: profile.id, project, preview, url, organizationId, workspaceId, packageVersion,
    selectorFlags: 'version' in chosen ? `--version ${chosen.version}` : `--label ${shellQuote(chosen.label)}`,
  }).join('\n');
}

/** Check for a newer release of an installed skill, then write an update plan to review. */
export function buildSkillUpdate({ name, origin, organizationId, workspaceId, project }) {
  const url = registryScope({ origin, organizationId, workspaceId });
  if (!safeText(project) || project.startsWith('-')) throw new Error('Choose a skill and a project path.');
  requireName(name);
  return [
    ...registryEnvLines({ url, organizationId, workspaceId }),
    CREDENTIAL_COMMENT,
    `python -m fi.skills update --project ${shellQuote(project)} --name ${shellQuote(name)} --check --allow-preview`,
    `python -m fi.skills update --project ${shellQuote(project)} --name ${shellQuote(name)} --allow-preview --plan ${shellQuote(INSTALL_TOOLS.skill.planFile)}`,
    `python -m fi.skills inspect --plan ${shellQuote(INSTALL_TOOLS.skill.planFile)} --diff`,
  ].join('\n');
}

const curlAuth = '-H "Authorization: Context $FI_CONTEXT_CREDENTIAL" -H "X-Organization-Id: $FI_CONTEXT_ORGANIZATION_ID" -H "X-Workspace-Id: $FI_CONTEXT_WORKSPACE_ID"';
const selectorArgs = (chosen) => ('version' in chosen ? `--data-urlencode "version=${chosen.version}"` : `--data-urlencode ${shellQuote(`label=${chosen.label}`)}`);

function curlScope({ origin, organizationId, workspaceId }) {
  const url = registryScope({ origin, organizationId, workspaceId });
  return [...registryEnvLines({ url, organizationId, workspaceId }), '# needs your read-only key in FI_CONTEXT_CREDENTIAL'];
}

/** One request that resolves a skill with the read-only key. A quick way to test the key. */
export function buildCurlResolve({ origin, organizationId, workspaceId, names = [], label, version }) {
  const name = requireName(names[0]);
  const chosen = selector({ label, version });
  return [
    ...curlScope({ origin, organizationId, workspaceId }),
    `curl -fsS -G ${curlAuth} --data-urlencode ${shellQuote(`name=${name}`)} ${selectorArgs(chosen)} "$FI_CONTEXT_ORIGIN/model-hub/skills/consumption/resolve/"`,
  ].join('\n');
}

/**
 * Install without Python: resolve, then read every file through the consumption routes
 * with the read-only key. It needs curl and jq. It follows the manifest and chunk
 * continuations, refuses paths that leave the folder, and does not verify digests;
 * its loop variable is not `path`, which zsh ties to $PATH;
 * the Python installer does.
 */
export function buildCurlInstall({ origin, organizationId, workspaceId, names = [], label, version, dir = '.claude/skills' }) {
  const name = requireName(names[0]);
  const chosen = selector({ label, version });
  if (!safeText(dir) || dir.startsWith('-')) throw new Error('Choose an install folder.');
  const target = dir.startsWith('~/') ? `"$HOME"/${shellQuote(`${dir.slice(2)}/${name}`)}` : shellQuote(`${dir}/${name}`);
  return [
    ...curlScope({ origin, organizationId, workspaceId }),
    '(',
    'set -eu',
    `dir=${target}`,
    'base="$FI_CONTEXT_ORIGIN/model-hub/skills/consumption"',
    `get() { curl -fsS ${curlAuth} "$@"; }`,
    `page=$(get -G --data-urlencode ${shellQuote(`name=${name}`)} ${selectorArgs(chosen)} "$base/resolve/")`,
    'first=$page',
    'id=$(jq -r .result.resource_id <<<"$first")',
    'number=$(jq -r .result.version <<<"$first")',
    'lease=$(jq -r .result.resolution_id <<<"$first")',
    'paths=""',
    'while :; do',
    '  paths="$paths$(jq -r \'.result.files[].path\' <<<"$page")"$\'\\n\'',
    '  next=$(jq -r \'.result.next_file_offset // empty\' <<<"$page")',
    '  [ -n "$next" ] || break',
    '  page=$(get -H "X-Context-Resolution: $lease" -G --data-urlencode "file_offset=$next" --data-urlencode "file_limit=25" "$base/$id/versions/$number/manifest/")',
    'done',
    'printf %s "$paths" | while IFS= read -r entry; do',
    '  [ -n "$entry" ] || continue',
    '  case "$entry" in /*|..|../*|*/..|*/../*) echo "Refusing unsafe path: $entry" >&2; exit 1;; esac',
    '  mkdir -p "$(dirname "$dir/$entry")"',
    '  : > "$dir/$entry"',
    '  offset=0',
    '  while [ -n "$offset" ]; do',
    '    chunk=$(get -H "X-Context-Resolution: $lease" -G --data-urlencode "path=$entry" --data-urlencode "offset=$offset" --data-urlencode "max_bytes=32768" "$base/$id/versions/$number/files/")',
    '    jq -j .result.text <<<"$chunk" >> "$dir/$entry"',
    '    offset=$(jq -r \'.result.next_offset // empty\' <<<"$chunk")',
    '  done',
    'done',
    'echo "Installed v$number ($(jq -r .result.digest <<<"$first")) into $dir"',
    ')',
  ].join('\n');
}

const SDK_IMPORT_PYTHON = 'import os\n\nfrom fi.platform import Client, ContextAuth\nfrom fi.skills import SkillsClient\n\nclient = Client(\n    auth=ContextAuth(os.environ["FI_CONTEXT_CREDENTIAL"]),\n    base_url=os.environ["FI_CONTEXT_ORIGIN"],\n    workspace_id=os.environ["FI_CONTEXT_WORKSPACE_ID"],\n    default_headers={"X-Organization-Id": os.environ["FI_CONTEXT_ORGANIZATION_ID"]},\n)';

/** Application code: the SDK `SkillsClient` verifies every file and makes loads recordable. */
export function buildPythonSnippet({ names = [], label, version }) {
  const name = requireName(names[0]);
  const chosen = selector({ label, version });
  const argument = 'version' in chosen ? `version=${chosen.version}` : `label=${JSON.stringify(chosen.label)}`;
  return `${SDK_IMPORT_PYTHON}\n\nskill = SkillsClient(client).resolve(${JSON.stringify(name)}, ${argument})\nprint(skill.identity.version_id, skill.identity.digest)\nprint(skill.read_file("SKILL.md"))`;
}

export function buildTypeScriptSnippet({ names = [], label, version }) {
  const name = requireName(names[0]);
  const chosen = selector({ label, version });
  const argument = 'version' in chosen ? `{ version: ${chosen.version} }` : `{ label: ${JSON.stringify(chosen.label)} }`;
  return `import { Client, SkillsClient } from "@future-agi/sdk";\n\nconst client = new Client({\n  auth: { type: "context", credential: process.env.FI_CONTEXT_CREDENTIAL! },\n  baseUrl: process.env.FI_CONTEXT_ORIGIN!,\n  workspaceId: process.env.FI_CONTEXT_WORKSPACE_ID!,\n  defaultHeaders: { "X-Organization-Id": process.env.FI_CONTEXT_ORGANIZATION_ID! },\n});\n\nconst skill = await new SkillsClient(client).resolve(${JSON.stringify(name)}, ${argument});\nconsole.log(skill.identity.versionId, skill.identity.digest);\nconsole.log(await skill.readFile("SKILL.md"));`;
}

export function buildMcpSnippet({ names = [], label, version }) {
  if (!names[0]) throw new Error('Choose one skill to resolve.');
  const selected = version != null ? { version: Number(version) } : selector({ label, version });
  selector({ label, version });
  return `Use the connected Future AGI MCP server:\n\nget_skill(${JSON.stringify({ name: names[0], ...selected }, null, 2)})\n\nThen call read_skill_file with the returned skill_id, immutable version number,\npath "SKILL.md", offset 0, and max_bytes 24000. Follow next_offset until eof\nis true, concatenate text, and verify its UTF-8 sha256 against the manifest.\nRead supporting files only when needed. A missing label is an error, not a\nfallback to latest.`;
}

// ---------------------------------------------------------------------------------------------
// Deprecated. The one-line "Copy install command" actions in the skill page header menu and the
// library row menu (SkillDetailView.jsx, library/SkillRowMenu.jsx) still call this ZIP command,
// which needs the full API key pair. Those two callers belong to other lanes; they must stop
// calling it (for example by opening the Install tab) so this function can be deleted. Nothing
// on the Install tabs uses it.
function legacyApiUrl(hostApi, endpoint, params) {
  const base = new URL(hostApi);
  if (!['https:', 'http:'].includes(base.protocol) || base.username || base.password || base.search || base.hash) {
    throw new Error('Use an HTTP API base URL without credentials or a query.');
  }
  return `${base.href.replace(/\/$/, '')}/model-hub/skills/${endpoint}/?${new URLSearchParams(params)}`;
}
const legacyDoubleQuote = (value) => `"${value.replace(/[\\"$`]/g, '\\$&')}"`;
const legacyShellDir = (dir) => {
  if (/^(~\/)?[a-zA-Z0-9._/-]+$/.test(dir) && !dir.startsWith('-')) return dir;
  const quoted = `'${dir.replace(/'/g, "'\\''")}'`;
  return dir.startsWith('~/') ? `$HOME/${"'"}${dir.slice(2).replace(/'/g, "'\\''")}'` : quoted;
};
const legacyHeaders = '-H "X-Api-Key: $FI_API_KEY" -H "X-Secret-Key: $FI_SECRET_KEY"';

/** @deprecated Needs the full API key pair. Use the Install tab's read-only key flow. */
export function buildInstallCommand({ hostApi, names = [], tag, label, version, dir = '.claude/skills' }) {
  if (version != null && (names.length !== 1 || tag)) throw new Error('A pinned version requires exactly one skill.');
  if (!names.length && !tag) throw new Error('Choose skills or a tag to install.');
  const url = legacyApiUrl(hostApi, 'download', { ...(names.length ? { names: names.join(',') } : {}), ...(tag ? { tag } : {}), ...selector({ label, version }) });
  const target = legacyShellDir(dir);
  return `mkdir -p ${target} && curl -fsSL ${legacyHeaders} ${legacyDoubleQuote(url)} -o /tmp/futureagi-skills.zip && unzip -oq /tmp/futureagi-skills.zip -d ${target} && rm /tmp/futureagi-skills.zip`;
}
