function selector({ label, version }) {
  if (version != null && label) throw new Error('Choose a label or a version, not both.');
  return version != null ? { version: String(version) } : { label: label || 'production' };
}

function apiUrl(hostApi, endpoint, params) {
  const base = new URL(hostApi);
  if (!['https:', 'http:'].includes(base.protocol) || base.username || base.password || base.search || base.hash) {
    throw new Error('Use an HTTP API base URL without credentials or a query.');
  }
  return `${base.href.replace(/\/$/, '')}/model-hub/skills/${endpoint}/?${new URLSearchParams(params)}`;
}

const doubleQuote = (value) => `"${value.replace(/[\\"$`]/g, '\\$&')}"`;
const shellDir = (dir) => {
  if (/^(~\/)?[a-zA-Z0-9._/-]+$/.test(dir) && !dir.startsWith('-')) return dir;
  const quoted = `'${dir.replace(/'/g, "'\\''")}'`;
  return dir.startsWith('~/') ? `$HOME/${"'"}${dir.slice(2).replace(/'/g, "'\\''")}'` : quoted;
};
const headers = '-H "X-Api-Key: $FI_API_KEY" -H "X-Secret-Key: $FI_SECRET_KEY"';

function resolveUrl({ hostApi, names = [], label, version }) {
  if (!names[0]) throw new Error('Choose one skill to resolve.');
  return apiUrl(hostApi, 'resolve', { name: names[0], ...selector({ label, version }), include_content: 'true' });
}

export function buildInstallCommand({ hostApi, names = [], tag, label, version, dir = '.claude/skills' }) {
  if (version != null && (names.length !== 1 || tag)) throw new Error('A pinned version requires exactly one skill.');
  if (!names.length && !tag) throw new Error('Choose skills or a tag to install.');
  const url = apiUrl(hostApi, 'download', { ...(names.length ? { names: names.join(',') } : {}), ...(tag ? { tag } : {}), ...selector({ label, version }) });
  const target = shellDir(dir);
  return `mkdir -p ${target} && curl -fsSL ${headers} ${doubleQuote(url)} -o /tmp/futureagi-skills.zip && unzip -oq /tmp/futureagi-skills.zip -d ${target} && rm /tmp/futureagi-skills.zip`;
}

export function buildCurlResolve(options) {
  return `curl -fsSL ${headers} ${doubleQuote(resolveUrl(options))}`;
}

export function buildPythonSnippet(options) {
  return `import json\nimport os\nfrom urllib.request import Request, urlopen\n\nrequest = Request(\n    ${JSON.stringify(resolveUrl(options))},\n    headers={\n        "X-Api-Key": os.environ["FI_API_KEY"],\n        "X-Secret-Key": os.environ["FI_SECRET_KEY"],\n    },\n)\nwith urlopen(request) as response:\n    manifest = json.load(response)["result"]\n\nprint(manifest["version_id"], manifest["digest"])\nfor file in manifest["files"]:\n    print(file["path"], file["content"])`;
}

export function buildTypeScriptSnippet(options) {
  return `const response = await fetch(${JSON.stringify(resolveUrl(options))}, {\n  headers: {\n    "X-Api-Key": process.env.FI_API_KEY!,\n    "X-Secret-Key": process.env.FI_SECRET_KEY!,\n  },\n});\nif (!response.ok) throw new Error(\`Skill fetch failed: \${response.status}\`);\nconst { result: manifest } = await response.json();\nconsole.log(manifest.version_id, manifest.digest);\nfor (const file of manifest.files) {\n  console.log(file.path, file.content);\n}`;
}

export function buildMcpSnippet({ names = [], label, version }) {
  if (!names[0]) throw new Error('Choose one skill to resolve.');
  const selected = version != null ? { version: Number(version) } : selector({ label, version });
  selector({ label, version });
  return `Use the connected Future AGI MCP server:\n\nget_skill(${JSON.stringify({ name: names[0], ...selected }, null, 2)})\n\nThen call read_skill_file with the returned skill_id, immutable version number,\npath "SKILL.md", offset 0, and max_bytes 24000. Follow next_offset until eof\nis true, concatenate text, and verify its UTF-8 sha256 against the manifest.\nRead supporting files only when needed. A missing label is an error, not a\nfallback to latest.`;
}
