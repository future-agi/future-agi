import { createHash } from 'node:crypto';
import { execFileSync } from 'node:child_process';
import { mkdir, readFile, writeFile } from 'node:fs/promises';
import { dirname, resolve } from 'node:path';
import { pathToFileURL } from 'node:url';

// Synthetic J1 fixture (evidence class "synthetic fixture"). Everything a
// registration compares is derived here, BEFORE a run, from fixture bytes only:
// no product response, destination ID or installed byte is fed back into the
// preregistered expectations. Runs only on the task-owned cloud VM.
const sha = (bytes) => createHash('sha256').update(bytes).digest('hex');
const bundleDigest = (files) => `sha256:${sha(JSON.stringify(files.map((f) => [f.path, sha(f.content)])
  .sort((a, b) => (a[0] < b[0] ? -1 : a[0] > b[0] ? 1 : 0))))}`;
const manifestRow = (path, content) => ({ path, sha256: sha(Buffer.from(content, 'utf8')), size_bytes: Buffer.byteLength(content, 'utf8') });

export const AGENTS_SOURCE = '# Isolated pilot\nUse the explicitly linked policies.\n';
export const LOCAL_AGENTS = '# Team local rules\nKeep local review notes in notes/.\n';
export const LOCAL_NOTES = 'Local notes stay with the team.\n';
export const PILOT_NOTE = 'Developer note added during the pilot.\n';
export const LOCAL_POLICY_EDIT = 'Locally modified policy kept by the developer.\n';

export function fixtureFor(prefix) {
  const names = Array.from({ length: 15 }, (_, i) => `${prefix}-${i}`);
  const skill = (name, body) => `---\nname: ${name}\ndescription: Use when reviewing the ${name} synthetic private migration policy\n---\n\n${body}\n`;
  const files = Object.fromEntries(names.map((name) => [name, [
    { path: 'SKILL.md', content: skill(name, 'Keep exact café text.') },
    { path: 'references/policy.md', content: 'Private reference\r\n' },
  ]]));
  const [diverged, second] = names;
  const v2 = (name, body) => [{ path: 'SKILL.md', content: skill(name, body) }, files[name][1]];
  const update = {
    name: diverged,
    hub: v2(diverged, 'Hub-only divergence café.'),
    personal: v2(diverged, 'Private personal draft café.'),
    files: { [diverged]: v2(diverged, 'Reviewed source revision two café.'), [second]: v2(second, 'Second member source revision two café.') },
  };
  return { names, files, update, supported: names.slice(0, 14), unsupported: names[14] };
}

export function expectedObservations({ prefix, fixture }) {
  const { names, files, update, supported } = fixture;
  const finalFiles = (name) => update.files[name] ?? files[name];
  const roundtrip = supported.flatMap((name) => finalFiles(name).map((file) => manifestRow(`${name}/${file.path}`, file.content)));
  // Removal must leave exactly the developer's own bytes: local root segment,
  // an unrelated file, an unowned note in a managed tree and one edited managed file.
  const preserved = [
    manifestRow('AGENTS.md', LOCAL_AGENTS),
    manifestRow('notes/local.md', LOCAL_NOTES),
    manifestRow(`.agents/skills/${names[2]}/local-notes.md`, PILOT_NOTE),
    manifestRow(`.agents/skills/${names[3]}/references/policy.md`, LOCAL_POLICY_EDIT),
  ];
  const versions = Object.fromEntries(supported.map((name) => [name, name === update.name ? 3 : update.files[name] ? 2 : 1]));
  return {
    'complete-inventory': names,
    'migration-mappings': {
      members: supported.length,
      sources: supported.map((name, i) => (i < 7 ? `synthetic.invalid|${prefix}|resource-${i}` : `local:synthetic-j1|${prefix}|private/${name}`)).sort(),
      destinations_distinct: supported.length,
      second_import_same_destinations: Object.keys(update.files).length,
    },
    'source-production-unchanged': { resources: supported.length + 1, labels: 0, source_modified: false },
    'no-duplicate-import': { skills: supported.length, versions, initial_receipts: supported.length, update_receipts: Object.keys(update.files).length },
    'roundtrip-bytes': roundtrip,
    'local-work-preserved': preserved,
    'delivered-identity': {
      host: 'codex-cli 0.167.1', root_source_sha256: sha(AGENTS_SOURCE), local_segment_sha256: sha(LOCAL_AGENTS),
      discovered: [...supported].sort(), delivered: Object.fromEntries(supported.map((name) => [name, sha(files[name][0].content)])),
    },
    'credential-revoked': { before_status: 200, after_status: 403, after_code: 'context_credential_revoked', listed_revoked: true },
  };
}

/** Where one prepared J1 run keeps its files. The source AGENTS.md lives in its own
 * directory: Codex concatenates every AGENTS.md from the enclosing git root down
 * to the project, so a source AGENTS.md in the project's parent would precede the
 * managed block whenever a run directory sits inside a git work tree. */
export const layoutFor = (target) => ({
  project: resolve(target, 'project'), private: resolve(target, 'private'),
  exchange: resolve(target, 'exchange.json'), exchangeV2: resolve(target, 'exchange-v2.json'),
  agentsDirectory: resolve(target, 'inputs'), agents: resolve(target, 'inputs', 'AGENTS.md'),
});

async function writeTree(root, entries) {
  for (const [path, content] of entries) {
    const target = resolve(root, path);
    await mkdir(dirname(target), { recursive: true });
    await writeFile(target, content);
  }
}

/** Build a fresh isolated account, sources, project and preregistration. */
export async function prepare({ root, cohort, evidence, apiUrl = 'http://localhost:8100', appUrl = 'http://localhost:3100',
  python, sdkPython, codex, codexSha256, provenance }) {
  if (process.platform !== 'linux') throw new Error('VM only');
  if (!['cold', 'configured'].includes(cohort)) throw new Error('cohort');
  const prefix = `j1-${cohort}-${Date.now().toString(36)}`;
  const target = resolve(evidence, prefix);
  const fixture = fixtureFor(prefix);
  const { names, files, update } = fixture;
  const exported = names.slice(0, 7).map((name, i) => ({ resource: `resource-${i}`, revision: 'v1', digest: bundleDigest(files[name]), files: files[name] }));
  const exchange = JSON.stringify({ format: 'langfuse-skills-v1', origin: 'synthetic.invalid', project: prefix, skills: exported });
  const updateExchange = JSON.stringify({ format: 'langfuse-skills-v1', origin: 'synthetic.invalid', project: prefix,
    skills: Object.entries(update.files).map(([name, rows]) => ({ resource: `resource-${names.indexOf(name)}`, revision: 'v2', digest: bundleDigest(rows), files: rows })) });
  const folderEntries = names.slice(7).flatMap((name) => files[name].map((file) => [`${name}/${file.path}`, file.content]));
  folderEntries.push([`${names[14]}/binary.bin`, Buffer.from([0, 255, 0, 1])]);
  await writeTree(resolve(target, 'private'), folderEntries);
  await writeTree(target, [['exchange.json', exchange], ['exchange-v2.json', updateExchange], ['inputs/AGENTS.md', AGENTS_SOURCE]]);
  await writeTree(resolve(target, 'project'), [['AGENTS.md', LOCAL_AGENTS], ['notes/local.md', LOCAL_NOTES]]);
  const api = async (path, data, auth) => {
    const response = await fetch(`${apiUrl}${path}`, { method: data ? 'POST' : 'GET', headers: { 'Content-Type': 'application/json', ...(auth ? { Authorization: `Bearer ${auth}` } : {}) }, ...(data ? { body: JSON.stringify(data) } : {}) });
    if (!response.ok) throw new Error(`Fixture API ${response.status}`);
    return response.json();
  };
  const email = `${prefix}@fixture.invalid`;
  const password = `Synthetic-${prefix}!`;
  await api('/accounts/e2e/clean-signup/', { email, full_name: 'Synthetic J1 owner', password });
  const tokens = await api('/accounts/token/', { email, password, remember_me: true });
  const user = await api('/accounts/user-info/', null, tokens.access);
  const input = { productRoot: root, outputDir: resolve(evidence, 'driver'), appUrl, apiUrl,
    folder: resolve(target, 'private'), exchange: resolve(target, 'exchange.json'), agents: layoutFor(target).agents,
    project: resolve(target, 'project'), python, sdkPython, codex: { executable: codex, sha256: codexSha256 },
    contextName: `${prefix}-context`, description: 'Synthetic isolated policy trial', sourceProject: prefix,
    names, files, selected: [...names.slice(7, 14).map((name) => `folder-0/${name}`), ...names.slice(0, 7).map((_, i) => `exchange-0/${i}`)],
    update: { ...update, exchange: resolve(target, 'exchange-v2.json') },
    localWork: { note: [`.agents/skills/${names[2]}/local-notes.md`, PILOT_NOTE], edit: [`.agents/skills/${names[3]}/references/policy.md`, LOCAL_POLICY_EDIT] },
    actor: { access: tokens.access, refresh: tokens.refresh, organizationId: user.organization.id, workspaceId: user.default_workspace_id },
    provenance: { ...provenance, fixture_hash: sha(JSON.stringify({ files, exchange, updateExchange })) } };
  await writeFile(resolve(target, 'input.json'), JSON.stringify(input), { mode: 0o600 });
  const registration = { schema: 1, job: 'J1', cohort, evidence_scope: 'synthetic',
    population: { library_members: 15, unsupported_members: 1, diverged_destinations: 1 },
    fault_ids: ['unsupported-member', 'hub-divergence', 'lost-import-response'],
    customer_input_hash: sha(JSON.stringify({ files, exchange, updateExchange })),
    initial_conditions_hash: sha(JSON.stringify({ cohort, account: 'isolated-existing-owner', sources: 'immutable-v1', runtime_credentials: 0, project: [LOCAL_AGENTS, LOCAL_NOTES] })),
    safety_requirements_hash: sha('No cutover, duplicates, lost local bytes or wrong immutable delivery'),
    complete_job_boundary_hash: sha('J1 complete inventory through install, delivery, update, export, managed removal and credential revocation'),
    expected_observations: expectedObservations({ prefix, fixture }) };
  await writeFile(resolve(target, 'registration.json'), JSON.stringify(registration, null, 2), { mode: 0o600 });
  return { input: resolve(target, 'input.json'), registration: resolve(target, 'registration.json'), prefix };
}

// node drivers/j1-fixture.mjs <cohort> <evidence-dir> <inventory-python> <sdk-python> <codex>
if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  const [cohort, evidence, python, sdkPython, codex] = process.argv.slice(2);
  const root = resolve(dirname(new URL(import.meta.url).pathname), '../../../..');
  const sdkRoot = process.env.J1_SDK_ROOT;
  const image = execFileSync('docker', ['inspect', 'futureagi-e2e-standalone-app-1', '--format', '{{.Image}}'], { encoding: 'utf8' }).trim().replace(/^sha256:/, '');
  const codexSha256 = sha(await readFile(codex));
  const provenance = { environment: 'gcp', project: 'fagi-omega-staging', product_base: process.env.J1_PRODUCT_BASE,
    // The synced lane has no .git; the operator attests the committed head/tree
    // (and whether the synced tree had uncommitted changes) from the source checkout.
    product_head: process.env.J1_PRODUCT_HEAD, product_tree: process.env.J1_PRODUCT_TREE, sdk_commit: process.env.J1_SDK_COMMIT,
    dirty: process.env.J1_DIRTY !== 'false', runtime_digest: image,
    package_hashes: [sha(await readFile(resolve(root, 'e2e/package.json'))), codexSha256, ...(sdkRoot ? [sha(await readFile(resolve(sdkRoot, 'pyproject.toml')))] : [])],
    lockfile_hashes: [sha(await readFile(resolve(root, 'e2e/yarn.lock')))] };
  console.log(JSON.stringify(await prepare({ root, cohort, evidence, python, sdkPython, codex, codexSha256, provenance })));
}
