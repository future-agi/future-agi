// Extract every refusal code the Omega path can send a Future AGI tab, straight from
// the sources that send them, and compare with the pinned contract
// (../fixtures/omega-refusal-codes.v1.json). Read-only: it only reads files.
//
//   node extract-refusal-codes.mjs --ee <EE checkout> --gateway <services/omega-gateway> \
//        [--django <futureagi dir>] [--write]
//
// --ee reads two sources from the EE checkout: `ee` (managed-auth/host-assertion-codes.ts)
// and `eeWorkspace` (the sandbox and deployment workspace routes' 401 and 403 throws,
// with their detail.reason, detail keys and creator-only verbs). --ee-workspace <checkout>
// reads only the second, for an EE head without host-assertion-codes.ts (before E-AUTH).
//
// Exit 1 when a source sends a code the contract does not list (the client would show
// fallback copy for it), or the contract lists one no source sends.
import { readFileSync, writeFileSync, readdirSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const here = path.dirname(fileURLToPath(import.meta.url));
const FIXTURE = path.join(here, "../fixtures/omega-refusal-codes.v1.json");
const args = Object.fromEntries(
  process.argv.slice(2).reduce((pairs, value, index, all) => {
    if (value.startsWith("--"))
      pairs.push([
        value.slice(2),
        all[index + 1]?.startsWith("--") || all[index + 1] === undefined
          ? true
          : all[index + 1],
      ]);
    return pairs;
  }, []),
);
const read = (file) => readFileSync(file, "utf8");
const add = (map, code, status, reasons = []) => {
  const entry = map.get(code) || { statuses: new Set(), reasons: new Set() };
  entry.statuses.add(Number(status));
  reasons.forEach((reason) => entry.reasons.add(reason));
  map.set(code, entry);
};

function eeCodes(root) {
  const out = new Map();
  const dir = path.join(root, "packages/server/src/managed-auth");
  const codes = read(path.join(dir, "host-assertion-codes.ts"));
  for (const m of codes.matchAll(/^\s+([a-z_]+): \{ status: (\d{3}),/gm))
    add(out, m[1], m[2]);
  const reasons =
    /HOST_ROUTE_REFUSAL_REASONS = Object\.freeze\(\[([^\]]+)\]/.exec(codes);
  const routeReasons = reasons
    ? [...reasons[1].matchAll(/"([a-z_]+)"/g)].map((m) => m[1])
    : [];
  add(out, "host_delegation_scope_denied", 403, routeReasons);
  return out;
}

// EE's workspace routes the tabs reach through the gateway: /workspace/sandboxes
// and /workspace/deployments (every sandboxes-*.ts, sandboxes/** and
// project-deployments/** file), and the two lists they read beside them,
// /workspace/credentials (credential-vault-routes.ts) and /workspace/environments
// (WorkspaceService.requireEnvironmentAuthority). Their 401 and 403 codes never
// show the server's words, so every code, `detail.reason` and creator-only verb
// needs Future AGI copy. The operator routes (sandboxes-operator-routes.ts) are
// not reachable from a tab.
const WORKSPACE_ROUTE_FILES =
  /^(?:sandboxes-(?!operator-routes)[a-z-]+\.ts|sandboxes\/.+\.ts|project-deployments\/.+\.ts|credential-vault-routes\.ts)$/;
const ENVIRONMENT_READ_GATE =
  /private requireEnvironmentAuthority\([\s\S]*?\n {2}\}\n/;

function walk(dir, base = dir) {
  return readdirSync(dir, { withFileTypes: true }).flatMap((entry) => {
    const file = path.join(dir, entry.name);
    if (entry.isDirectory()) return walk(file, base);
    return [path.relative(base, file).split(path.sep).join("/")];
  });
}

function eeWorkspaceCodes(root) {
  const out = new Map();
  const dir = path.join(root, "packages/server/src/workspace");
  const verbsSource = read(path.join(dir, "sandboxes/access/creator-only.ts"));
  const verbUnion = /export type SandboxAccessVerb = ([^;]+);/.exec(
    verbsSource,
  );
  const verbs = verbUnion
    ? [...verbUnion[1].matchAll(/"([a-z_]+)"/g)].map((m) => m[1])
    : [];
  const files = walk(dir).filter(
    (file) =>
      WORKSPACE_ROUTE_FILES.test(file) && !/\.(?:test|fixture)\.ts$/.test(file),
  );
  const texts = files.map((file) => read(path.join(dir, file)));
  const gate = ENVIRONMENT_READ_GATE.exec(read(path.join(dir, "service.ts")));
  if (gate) texts.push(gate[0]);
  else add(out, "environment_gate_not_found", 0);
  for (const text of texts) {
    // credential-vault-routes.ts answers with `error(status, code, message)`.
    for (const m of text.matchAll(/\berror\((40[13]), "([a-z_]+)"/g))
      add(out, m[2], m[1]);
    for (const m of text.matchAll(
      /new Workspace(?:Sandbox|Deployment)?Error\(\s*"([a-z_]+)",\s*(40[13]),([^;]*)\)/g,
    )) {
      const rest = m[3];
      const reason = /\breason: "([a-z_]+)"/.exec(rest);
      add(out, m[1], m[2], reason ? [reason[1]] : []);
      const entry = out.get(m[1]);
      entry.verbs ||= new Set();
      entry.detail ||= new Set();
      if (/\bverb\b/.test(rest)) {
        verbs.forEach((verb) => entry.verbs.add(verb));
        entry.detail.add("verb");
      }
      if (/\brole: /.test(rest)) entry.detail.add("role");
      if (reason) entry.detail.add("reason");
    }
  }
  return out;
}

function gatewayCodes(root) {
  const out = new Map();
  const src = path.join(root, "src");
  for (const file of readdirSync(src).filter((name) => name.endsWith(".mjs"))) {
    const text = read(path.join(src, file));
    for (const m of text.matchAll(
      /status: (\d{3}), error: "([a-z_]+)"(?:, reason: "([a-z_]+)")?/g,
    ))
      add(out, m[2], m[1], m[3] ? [m[3]] : []);
    for (const m of text.matchAll(
      /respond\((\d{3}), \{ error: "([a-z_]+)"(?:, reason: "?([a-z_.]+)"?)?/g,
    ))
      add(out, m[2], m[1], m[3] && !m[3].includes(".") ? [m[3]] : []);
    for (const m of text.matchAll(
      /status: (\d{3}), error: identity\.reason === "role_below_floor" \? "([a-z_]+)" : "([a-z_]+)"/g,
    )) {
      add(out, m[2], m[1], ["role_below_floor"]);
      add(out, m[3], m[1], ["scope_pivot"]);
    }
    for (const m of text.matchAll(
      /status, error: timedOut \? "([a-z_]+)" : "([a-z_]+)"/g,
    )) {
      add(out, m[1], 504);
      add(out, m[2], 502);
    }
    for (const m of text.matchAll(
      /writeHead\((\d{3}),[^\n]*\n[^\n]*error: "([a-z_]+)"/g,
    ))
      add(out, m[2], m[1]);
  }
  for (const m of read(path.join(src, "routes.mjs")).matchAll(
    /reason: "([a-z_]+)"/g,
  )) {
    if (["path_refused", "route_not_in_manifest"].includes(m[1]))
      add(out, "route_not_allowed", 403, [m[1]]);
  }
  return out;
}

function djangoCodes(root) {
  const out = new Map();
  const view = read(path.join(root, "accounts/views/omega_session.py"));
  for (const m of view.matchAll(/code="([a-z_]+)"/g)) add(out, m[1], 403);
  if (/Throttled\(/.test(view)) add(out, "throttled", 429);
  if (/status=503/.test(view)) add(out, "delegation_unavailable", 503);
  return out;
}

const plain = (map) =>
  Object.fromEntries(
    [...map.entries()]
      .sort(([a], [b]) => a.localeCompare(b))
      .map(([code, entry]) => [
        code,
        {
          statuses: [...entry.statuses].sort(),
          reasons: [...entry.reasons].sort(),
          ...(entry.verbs ? { verbs: [...entry.verbs].sort() } : {}),
          ...(entry.detail ? { detail: [...entry.detail].sort() } : {}),
        },
      ]),
  );

const found = {};
if (args.ee) found.ee = plain(eeCodes(args.ee));
const workspaceRoot = args["ee-workspace"] || args.ee;
if (workspaceRoot) found.eeWorkspace = plain(eeWorkspaceCodes(workspaceRoot));
if (args.gateway) found.gateway = plain(gatewayCodes(args.gateway));
if (args.django) found.django = plain(djangoCodes(args.django));
const fixture = JSON.parse(read(FIXTURE));
const drift = [];
for (const [source, codes] of Object.entries(found)) {
  const pinned = fixture.sources[source]?.codes || {};
  for (const [code, entry] of Object.entries(codes)) {
    const known = pinned[code];
    if (!known)
      drift.push(
        `${source}: sends ${code} (${entry.statuses}) not in the contract`,
      );
    else {
      for (const status of entry.statuses)
        if (!known.statuses.includes(status))
          drift.push(`${source}: ${code} also sends ${status}`);
      for (const reason of entry.reasons)
        if (!known.reasons.includes(reason))
          drift.push(
            `${source}: ${code} has reason ${reason} not in the contract`,
          );
      for (const verb of entry.verbs || [])
        if (!(known.verbs || []).includes(verb))
          drift.push(`${source}: ${code} has verb ${verb} not in the contract`);
      for (const key of entry.detail || [])
        if (!(known.detail || []).includes(key))
          drift.push(
            `${source}: ${code} carries detail.${key} not in the contract`,
          );
    }
  }
  for (const code of Object.keys(pinned))
    if (!codes[code] && !fixture.sources[source].legacy?.includes(code))
      drift.push(
        `${source}: contract lists ${code}, the source no longer sends it`,
      );
}
if (args.write) {
  for (const [source, codes] of Object.entries(found))
    fixture.sources[source].codes = {
      ...fixture.sources[source].codes,
      ...codes,
    };
  writeFileSync(FIXTURE, `${JSON.stringify(fixture, null, 2)}\n`);
}
process.stdout.write(`${JSON.stringify({ found, drift }, null, 2)}\n`);
process.exit(drift.length === 0 ? 0 : 1);
