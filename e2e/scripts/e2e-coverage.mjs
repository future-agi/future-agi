#!/usr/bin/env node
import { execFileSync } from "node:child_process";
import { existsSync, readFileSync, readdirSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const E2E_DIR = path.dirname(path.dirname(fileURLToPath(import.meta.url)));
const REPO_ROOT = path.dirname(E2E_DIR);

const escapeRe = (s) => s.replace(/[.+^$()|[\]\\]/g, "\\$&");

function matchingBrace(glob, open) {
  let depth = 0;
  for (let i = open; i < glob.length; i++) {
    if (glob[i] === "{") depth++;
    else if (glob[i] === "}" && --depth === 0) return i;
  }
  return glob.length;
}

function splitAlternatives(body) {
  const parts = [];
  let depth = 0;
  let start = 0;
  for (let i = 0; i < body.length; i++) {
    if (body[i] === "{") depth++;
    else if (body[i] === "}") depth--;
    else if (body[i] === "," && depth === 0) {
      parts.push(body.slice(start, i));
      start = i + 1;
    }
  }
  parts.push(body.slice(start));
  return parts;
}

function globToSource(glob) {
  let re = "";
  for (let i = 0; i < glob.length; i++) {
    const c = glob[i];
    if (c === "*" && glob[i + 1] === "*") {
      if (glob[i + 2] === "/") {
        re += "(?:.*/)?";
        i += 2;
      } else {
        re += ".*";
        i += 1;
      }
    } else if (c === "*") re += "[^/]*";
    else if (c === "?") re += "[^/]";
    else if (c === "{") {
      const end = matchingBrace(glob, i);
      re +=
        "(?:" +
        splitAlternatives(glob.slice(i + 1, end))
          .map(globToSource)
          .join("|") +
        ")";
      i = end;
    } else re += escapeRe(c);
  }
  return re;
}

export function globToRegExp(glob) {
  return new RegExp("^" + globToSource(glob) + "$");
}

// frontend/src/sections/<dir> → area; sub-dir overrides win.
export const FE_AREA = {
  auth: "auth",
  "oss-first-run": "auth",
  projects: "observe",
  project: "observe",
  overview: "observe",
  "project-detail": "prototype",
  evals: "evals",
  tasks: "tasks",
  develop: "datasets",
  "develop-detail": "datasets",
  "experiment-detail": "datasets",
  huggingface: "datasets",
  data: "datasets",
  prompt: "prompts",
  "prompt-v2": "prompts",
  workbench: "prompts",
  "workbench-v2": "prompts",
  agents: "agents",
  "agent-playground": "agents",
  "knowledge-base": "knowledge-base",
  annotations: "annotations",
  persona: "simulate",
  scenarios: "simulate",
  test: "simulate",
  "test-detail": "simulate",
  gateway: "gateway",
  error: "error-feed",
  alerts: "alerts",
  dashboards: "dashboards",
  "falcon-ai": "falcon-ai",
  "get-started": "get-started",
  journey: "get-started",
  settings: "settings",
  account: "settings",
  keys: "settings",
  user: "settings",
  address: "settings",
  model: "settings",
  sync: "settings",
};
const FE_SUBDIR_AREA = {
  "projects/Alerts": "alerts",
  "projects/MonitorsView": "alerts",
  "projects/ChartsView": "dashboards",
};
const FE_COMPONENT_AREA = {
  traceDetail: "observe",
  traceDetailDrawer: "observe",
  "filter-panel": "observe",
  ComplexFilter: "observe",
  AgentGraph: "observe",
  "run-insights": "observe",
  eval: "evals",
  "data-table": "datasets",
  "run-tests": "simulate",
  "agent-definitions": "simulate",
  CallLogsDrawer: "simulate",
  CallLogsDetailDrawer: "simulate",
  ChatDetailDrawerV2: "simulate",
  GraphBuilder: "agents",
  charts: "dashboards",
};
// futureagi/<app> → area; file-level overrides (relative to futureagi/) win.
export const BE_APP_AREA = {
  accounts: "auth",
  saml2_auth: "auth",
  tracer: "observe",
  model_hub: "datasets",
  simulate: "simulate",
  agentcc: "gateway",
  agent_playground: "agents",
  integrations: "settings",
  mcp_server: "settings",
  evaluations: "evals",
  agentic_eval: "evals",
  sdk: "sdk-ingestion",
  ai_tools: "datasets",
};
const BE_FILE_AREA = [
  [/^tracer\/views\/(eval_task|custom_eval_config)\.py$/, "evals"],
  [/^tracer\/services\/eval/, "evals"],
  [/^tracer\/views\/feed\//, "error-feed"],
  [/^tracer\/views\/error_analysis\.py$/, "error-feed"],
  [/^tracer\/views\/monitor\.py$/, "alerts"],
  [/^tracer\/utils\/monitor/, "alerts"],
  [/^tracer\/views\/(dashboard|charts)\.py$/, "dashboards"],
  [
    /^tracer\/services\/clickhouse\/query_builders\/dashboard\.py$/,
    "dashboards",
  ],
  [/^tracer\/views\/dataset\.py$/, "datasets"],
  [
    /^model_hub\/views\/(annotation_queues|develop_annotations|scores)\.py$/,
    "annotations",
  ],
  [
    /^model_hub\/views\/(standalone_eval|eval_runner|eval_group|eval_summary_templates|separate_evals|ai_eval_writer|metric)\.py$/,
    "evals",
  ],
  [/^model_hub\/views\/(prompt_|run_prompt)/, "prompts"],
  [/^model_hub\/views\/kb\.py$/, "knowledge-base"],
  [
    /^model_hub\/views\/(custom_model|secrets|tools|tts_voices)\.py$/,
    "settings",
  ],
  [/^accounts\/views\/(workspace|rbac|organization|keys|config)/, "settings"],
  [/^ee\/(prompts|agenthub|agent_opt)\//, "prompts"],
  [/^ee\/experiments\//, "datasets"],
  [/^ee\/(evals|turing)\//, "evals"],
  [/^ee\/falcon_ai\//, "falcon-ai"],
  [/^ee\/(usage|billing|licensing)/, "settings"],
  [/^ee\/voice\//, "simulate"],
  [/^ee\/protect\//, "gateway"],
];
// area → flow-id prefix (mirrors e2e/README.md "<AREA>-E2E-<nnn>").
export const AREA_OF_E2E_DIR = {
  auth: "AUTH",
  observe: "OBS",
  evals: "EVAL",
  tasks: "TASK",
  datasets: "DATA",
  prompts: "PROMPT",
  agents: "AGENT",
  prototype: "PROTO",
  "knowledge-base": "KB",
  annotations: "ANNOT",
  simulate: "SIM",
  gateway: "GW",
  "error-feed": "ERR",
  alerts: "ALERT",
  dashboards: "DASH",
  settings: "SET",
  "get-started": "GST",
  "falcon-ai": "FALCON",
  "sdk-ingestion": "SDK",
};

function areaOf(p) {
  let m = p.match(/^frontend\/src\/sections\/([^/]+)(?:\/([^/]+))?/);
  if (m) return FE_SUBDIR_AREA[`${m[1]}/${m[2]}`] ?? FE_AREA[m[1]] ?? null;
  m = p.match(/^frontend\/src\/components\/([^/]+)/);
  if (m) return FE_COMPONENT_AREA[m[1]] ?? null;
  if (p.startsWith("fi-collector/")) return "observe";
  if (p.startsWith("agentcc-gateway/")) return "gateway";
  m = p.match(/^futureagi\/(.+)$/);
  if (m) {
    for (const [re, area] of BE_FILE_AREA) if (re.test(m[1])) return area;
    const app = m[1].startsWith("ee/") ? null : m[1].split("/")[0];
    return app ? BE_APP_AREA[app] ?? null : null;
  }
  return null;
}

// Ordered; first match wins. Specific behaviour rules sit before the broad exempt rules they would otherwise fall into.
const RULES = [
  [
    "E0",
    "{api_contracts/openapi/**,frontend/src/generated/**,frontend/src/api/contracts/*.generated.*,agentcc-gateway/internal/contracts/generated/**,api_contracts/gateway/**,api_contracts/filter_contract.json,**/yarn.lock,**/uv.lock,**/go.sum,**/package-lock.json,futureagi/.test_durations,e2e/FLOWS.md}",
  ],
  [
    "E1",
    "{**/*.md,**/*.mdx,docs/**,.github/assets/**,.github/ISSUE_TEMPLATE/**,LICENSE*,NOTICE}",
  ],
  [
    "B7",
    "{docker-compose*.yml,futureagi/docker-compose*.yml,e2e/stack/**,futureagi/entrypoint.sh}",
  ],
  [
    "E2",
    "{.github/**,.husky/**,.lintstagedrc.cjs,scripts/**,package.json,.gitignore,**/.dockerignore,.gitleaks.toml,release-please-config.json,.release-please-manifest.json,deploy/**,.env.example,**/.env*.example,futureagi/.github/**,futureagi/.pre-commit-config.yaml,futureagi/Makefile,futureagi/bin/**,futureagi/.ci/**,agentcc-gateway/Makefile,agentcc-gateway/scripts/**,agentcc-gateway/config*.example.yaml,bin/install*,bin/uninstall,.vscode/**,.agents/**,.claude/**}",
  ],
  // A test lives in a test *directory*; `test_*.py` on its own is not a test rule. The
  // `futureagi/simulate` test-execution domain is a product concept, and `frontend/src/api/tests/`
  // is a production API module — a bare basename rule exempts both as `tests-only`.
  [
    "E3",
    "{futureagi/**/tests/**,**/__tests__/**,**/*.test.*,frontend/**/*.spec.js,frontend/**/*.spec.jsx,**/*_test.go,**/conftest.py,futureagi/.test_quarantine.json,futureagi/docker-compose.test.yml,futureagi/tfc/settings/test.py,frontend/src/setupTests.js,frontend/src/utils/test-utils.jsx,frontend/src/_mock/**,frontend/scripts/api-journeys/**,frontend/.storybook/**,**/*.stories.*,e2e/**,bin/e2e}",
  ],
  [
    "E4",
    "{futureagi/model_serving/**,futureagi/code-executor/**,Dockerfile.simulation-runner*,futureagi/simulate/temporal/activities/hosted_runner.py,futureagi/simulate/services/hosted_runner.py,scripts/verify-simulation-runner-deployment.sh}",
  ],
  [
    "E5",
    "{**/migrations/__init__.py,futureagi/tfc/settings/**,futureagi/tfc/temporal/common/registry.py,futureagi/tfc/celery.py,futureagi/tfc/logging/**,futureagi/tfc/telemetry/**,futureagi/tfc/deployment_telemetry/**,futureagi/tfc/licensing/**,futureagi/tfc/ee_*.py,futureagi/ee/licensing/**,futureagi/*/management/**,futureagi/**/apps.py,futureagi/**/admin.py,futureagi/*/constants/**,futureagi/*/types/**,futureagi/*/pydantic_schemas/**,futureagi/requirements*.txt,futureagi/pyproject.toml,futureagi/Dockerfile*,Dockerfile*}",
  ],
  [
    "B6",
    "{agentcc-gateway/internal/server/handlers.go,agentcc-gateway/internal/server/server.go,agentcc-gateway/internal/providers/openai/**,agentcc-gateway/internal/providers/registry.go,agentcc-gateway/internal/pipeline/**,agentcc-gateway/internal/plugins/{cache,toolpolicy,otel}/**,agentcc-gateway/internal/routing/**,agentcc-gateway/internal/models/**,agentcc-gateway/internal/streaming/**,agentcc-gateway/internal/config/**}",
  ],
  ["E6", "agentcc-gateway/**"],
  [
    "E7",
    "{frontend/src/theme/**,frontend/src/styles/**,frontend/src/global.css,frontend/src/assets/**,frontend/public/**,frontend/src/locales/**,frontend/src/animations/**,frontend/src/newrelic.jsx,frontend/src/config-global.js,frontend/src/main.jsx,frontend/vite.config.*,frontend/eslint*,frontend/.prettierrc*,frontend/package.json,frontend/Dockerfile,frontend/nginx*,frontend/index.html,frontend/src/TreeView/**,frontend/src/components/animate/**,frontend/src/components/{Accordion,chip,chip-selector,ChipContainer,circular-progress-with-label,custom-breadcrumbs,custom-popover,custom-slider,custom-status-chip,DummyWaveform,empty-content,EmptyLayout,file-thumbnail}/**}",
  ],
  [
    "B1",
    "{frontend/src/routes/**,frontend/src/pages/**,frontend/src/layouts/dashboard/config-navigation.jsx}",
  ],
  ["B2", "frontend/src/**"],
  [
    "B4",
    "{futureagi/*/migrations/*.py,futureagi/ee/*/migrations/*.py,futureagi/tracer/services/clickhouse/v2/schema/**}",
  ],
  [
    "B3",
    "{futureagi/**/urls.py,futureagi/**/views/**,futureagi/**/views.py,futureagi/**/serializers/**,futureagi/**/serializers.py,futureagi/**/contracts.py,futureagi/tfc/routers.py,futureagi/tfc/openapi_urls.py,futureagi/tfc/permissions/**,futureagi/tfc/middleware/**,futureagi/tfc/capabilities/**,futureagi/**/authentication.py,futureagi/**/permissions*.py}",
  ],
  ["E5", "futureagi/**"],
  ["B5", "fi-collector/**"],
].map(([cls, glob]) => [cls, globToRegExp(glob)]);

// Generated, docs, tooling, tests-only, not-in-stack and cosmetic files carry no area: they never decide coverage.
const AREA_CLASSES = new Set([
  "E5",
  "E6",
  "E9",
  "B1",
  "B2",
  "B3",
  "B4",
  "B5",
  "B6",
  "B7",
]);

export function classifyPath(p) {
  const cls = RULES.find(([, re]) => re.test(p))?.[0] ?? "E9";
  return { cls, area: AREA_CLASSES.has(cls) ? areaOf(p) : null };
}

const FLOW_ID_RE = /\b([A-Z]+-E2E-\d{3}):/;
const STRING_RE = /(["'`])(\/[A-Za-z0-9_\-./{}$:?=&]+)\1/g;
// Case-sensitive on purpose: specs write SQL keywords upper-case; a prose "from the app" must not index "the" as a table.
const SQL_TABLE_RE = /\b(?:FROM|JOIN)\s+([a-z_][a-z0-9_]{3,})/g;
const SELECTOR_RE = /(col-id="[^"]+"|data-filter-[a-z-]+|data-testid="[^"]+")/g;

export function buildPinnedIndex(specs) {
  const out = [];
  const push = (literal, flow, kind) => {
    if (literal.length < 6 && kind !== "table") return;
    if (!out.some((e) => e.literal === literal && e.flow === flow))
      out.push({ literal, flow, kind });
  };
  for (const { file, text } of specs) {
    const flow =
      text.match(FLOW_ID_RE)?.[1] ?? `harness:${path.basename(file)}`;
    for (const m of text.matchAll(STRING_RE)) {
      const raw = m[2].split("?")[0];
      const literal = raw.includes("${")
        ? raw.slice(0, raw.indexOf("${"))
        : raw;
      if (literal.length < 8) continue;
      push(literal, flow, "endpoint");
      for (const seg of literal.split("/").filter(Boolean)) {
        if (seg.length >= 8 && /[_-]/.test(seg) && !seg.includes("${"))
          push(seg, flow, "segment");
      }
    }
    for (const m of text.matchAll(SQL_TABLE_RE)) push(m[1], flow, "table");
    for (const m of text.matchAll(SELECTOR_RE)) push(m[1], flow, "selector");
  }
  return out;
}

// A changed spec, a backend test or a workflow that mentions an endpoint is not a product-surface change.
const NON_SURFACE_CLASSES = new Set(["E0", "E1", "E2", "E3"]);
const SQLISH_RE = /\b(FROM|JOIN|INSERT INTO|UPDATE|TABLE)\b/;
// A segment counts only as a whole token: `model-hub` must not match inside `model-hub-v2`.
const SEGMENT_RE = (seg) =>
  new RegExp(`(?<![A-Za-z0-9_-])${escapeRe(seg)}(?![A-Za-z0-9_-])`);

export function detectHits(index, fileDiffs) {
  const hits = [];
  const add = (id, via) => {
    if (!hits.some((h) => h.id === id && h.via === via)) hits.push({ id, via });
  };
  for (const [file, d] of Object.entries(fileDiffs)) {
    const { cls } = classifyPath(file);
    if (NON_SURFACE_CLASSES.has(cls)) continue;
    const lines = [...d.added, ...d.removed];
    for (const line of lines) {
      for (const e of index) {
        if (e.kind === "table") {
          if (
            SQLISH_RE.test(line) &&
            new RegExp(`\\b${escapeRe(e.literal)}\\b`).test(line)
          )
            add(e.flow, `${file}: table ${e.literal}`);
        } else if (e.kind === "segment") {
          if (SEGMENT_RE(e.literal).test(line))
            add(e.flow, `${file}: ${e.literal}`);
        } else if (line.includes(e.literal))
          add(e.flow, `${file}: ${e.literal}`);
      }
    }
  }
  return hits;
}

const PATH_KEY_RE = /^\s*([A-Za-z_]+):\s*/;

export function detectNewSurface(fileDiffs, titleType, areasWithoutFlows) {
  const signals = [];
  // Same guard `detectHits` carries: a spec, a unit test or a workflow is not a product surface,
  // whatever its path looks like. Without it an added `pages/**/__tests__/x.test.jsx` reads as a new
  // page, and NEW-FLOW is the one classification that refuses a declared exemption.
  const entries = Object.entries(fileDiffs).filter(
    ([f]) => !NON_SURFACE_CLASSES.has(classifyPath(f).cls),
  );
  const hasSerializer = entries.some(
    ([f]) => /serializers?(\/|\.py$)/.test(f) && f.startsWith("futureagi/"),
  );
  for (const [file, d] of entries) {
    if (
      /^frontend\/src\/routes\/sections\/.*\.jsx$/.test(file) &&
      d.added.some((l) => /^\s*path:\s*["'`]/.test(l))
    )
      signals.push(`added route in ${file}`);
    if (file === "frontend/src/routes/paths.js") {
      // An edited value re-adds a key that was also removed; only a genuinely new key is a signal.
      const removed = new Set(
        d.removed.map((l) => l.match(PATH_KEY_RE)?.[1]).filter(Boolean),
      );
      if (
        d.added.some((l) => {
          const k = l.match(PATH_KEY_RE)?.[1];
          return k && !removed.has(k);
        })
      )
        signals.push(`added path key in ${file}`);
    }
    if (d.status === "A" && /^frontend\/src\/pages\//.test(file))
      signals.push(`new page ${file}`);
    if (
      /^futureagi\/.*urls\.py$/.test(file) &&
      d.added.some((l) => /(\bpath\(|re_path\(|\.register\()/.test(l))
    )
      signals.push(`added route in ${file}`);
    if (d.status === "A" && /^futureagi\/.*\/views\//.test(file))
      signals.push(`new views module ${file}`);
    if (
      /^fi-collector\//.test(file) &&
      d.added.some((l) => /HandleFunc\(|\.Handle\(/.test(l))
    )
      signals.push(`new collector handler in ${file}`);
    if (
      d.status === "A" &&
      /\/migrations\/\d+_.*\.py$/.test(file) &&
      hasSerializer &&
      d.added.some((l) => /CreateModel\(|AddField\(/.test(l))
    )
      signals.push(`schema change surfaced by a serializer: ${file}`);
  }
  if (titleType === "feat") {
    const areas = new Set(
      entries
        .map(([f]) => classifyPath(f))
        .filter((c) => c.cls.startsWith("B") && c.area)
        .map((c) => c.area),
    );
    for (const a of areas)
      if (areasWithoutFlows.includes(a))
        signals.push(`feat: PR in area ${a}, which has no flows`);
  }
  return signals;
}

// Every word `DOMINANT_REASON` can print, plus the two an author states by hand.
const EXEMPT_REASONS = [
  "docs",
  "tooling",
  "tests-only",
  "not-in-stack",
  "backend-internal",
  "gateway-internal",
  "cosmetic",
  "generated",
  "stack-shape",
  "merge-back",
  "unclassified",
  "refactor",
  "test-support",
];
const ID_RE = /\b[A-Z]+-E2E-\d{3}\b/g;

export function parseMarker(body) {
  if (!body) return null;
  const visible = body.replace(/<!--[\s\S]*?-->/g, "");
  const lines = [...visible.matchAll(/^[^\S\r\n]*E2E:[^\S\r\n]*(.*)$/gim)]
    .map((m) => ({ raw: m[0].trim(), rest: m[1].trim() }))
    .filter((l) => l.rest);
  if (!lines.length) return null;
  const { raw, rest } = lines[0];
  const problems = lines.length > 1 ? ["more than one E2E: line"] : [];
  const kind =
    rest.match(/^(new|updated|covered-by|exempt)\b/i)?.[1]?.toLowerCase() ??
    null;
  const ids = [...rest.matchAll(ID_RE)].map((m) => m[0]);
  let reason = null;
  if (!kind) problems.push(`unrecognised marker: ${raw}`);
  else if (kind === "exempt") {
    reason = rest.match(/\((.+)\)/)?.[1]?.trim() ?? null;
    if (!reason) problems.push("exempt needs a (reason)");
    else if (
      !EXEMPT_REASONS.includes(reason) &&
      !reason.startsWith("harness-gap ")
    )
      problems.push(`unknown exemption reason: ${reason}`);
  } else if (!ids.length) problems.push("no flow id of the form AREA-E2E-nnn");
  return { raw, kind, ids, reason, problems };
}

const DOMINANT_REASON = {
  E1: "docs",
  E2: "tooling",
  E3: "tests-only",
  E4: "not-in-stack",
  E5: "backend-internal",
  E6: "gateway-internal",
  E7: "cosmetic",
  E8: "merge-back",
  E9: "unclassified",
  B7: "stack-shape",
  E0: "generated",
};

export function decide({
  classification,
  autoReason,
  hits,
  marker,
  flowsDiffAdded,
  changedFlowFiles,
  catalogIds,
}) {
  const out = (verdict, explanation) => ({
    classification,
    autoReason,
    marker,
    verdict,
    explanation,
  });
  // An auto-exempt diff owes no marker at all, so it is never blocked on one's grammar.
  if (classification === "EXEMPT")
    return out("pass", `exempt (${autoReason}); no declaration needed`);
  if (marker?.problems.length)
    return out("block", `malformed E2E marker: ${marker.problems.join("; ")}`);
  const hitIds = [
    ...new Set(
      hits.map((h) => h.id).filter((id) => !id.startsWith("harness:")),
    ),
  ];
  if (!marker)
    return out("needs-marker", needsMarkerHint(classification, hitIds));
  if (classification === "NEW-FLOW" && marker.kind !== "new")
    return out(
      "block",
      `diff introduces new user-visible surface but declares \`${marker.raw}\` — add a flow and declare \`E2E: new <ID>\`, or reviewer override required`,
    );
  if (marker.kind === "exempt") return out("pass", `declared ${marker.raw}`);
  if (marker.kind === "new") {
    const missing = marker.ids.filter(
      (id) => !flowsDiffAdded.some((l) => l.startsWith(`### ${id}`)),
    );
    return missing.length
      ? out(
          "block",
          `declared new ${missing.join(", ")} but FLOWS.md diff does not add them — run \`yarn catalog\` and commit FLOWS.md`,
        )
      : out("pass", `new ${marker.ids.join(", ")} present in FLOWS.md`);
  }
  if (marker.kind === "updated") {
    const missing = marker.ids.filter(
      (id) => !Object.values(changedFlowFiles).some((t) => t.includes(id)),
    );
    return missing.length
      ? out(
          "block",
          `declared updated ${missing.join(", ")} but no changed spec under e2e/flows contains them`,
        )
      : out("pass", `updated ${marker.ids.join(", ")}`);
  }
  const unknown = marker.ids.filter((id) => !catalogIds.includes(id));
  if (unknown.length)
    return out(
      "block",
      `covered-by names flows not in FLOWS.md: ${unknown.join(", ")}`,
    );
  return out(
    "pass",
    `covered by ${marker.ids.join(", ")} (unchanged; CI run is the proof)`,
  );
}

function needsMarkerHint(classification, hitIds) {
  if (classification === "UPDATE-EXISTING")
    return `add \`E2E: updated ${hitIds.join(", ")}\` (spec changed) or \`E2E: covered-by ${hitIds.join(", ")}\` (spec unchanged, CI proves it) to the PR body`;
  if (classification === "NEW-FLOW")
    return "add a flow and declare `E2E: new <ID>`, or declare `E2E: exempt (<reason>)` for the reviewer to accept";
  return "behaviour files changed with no recognised signal: declare `E2E: updated|covered-by <ID>` or `E2E: exempt (<reason>)`";
}

function catalogAreasAndIds(catalog) {
  const areas = [...catalog.matchAll(/^## (.+)$/gm)].map((m) => m[1].trim());
  const ids = [...catalog.matchAll(/^### ([A-Z]+-E2E-\d{3})/gm)].map(
    (m) => m[1],
  );
  return { areas, ids };
}

// `changedFlowFiles` (path → full head text of each changed spec) is what the CLI passes; tests pass nothing and the
// added-line text stands in for it.
export function classifyChange({
  fileDiffs,
  specs,
  catalog,
  flowsDiffAdded,
  titleType,
  body,
  changedFlowFiles: headSpecs,
}) {
  const files = Object.keys(fileDiffs).map((p) => ({
    path: p,
    ...classifyPath(p),
  }));
  const behaviour = files.filter((f) => /^B[1-6]$/.test(f.cls));
  const areas = [
    ...new Set(behaviour.map((f) => f.area).filter(Boolean)),
  ].sort();
  const { areas: coveredAreas, ids: catalogIds } = catalogAreasAndIds(catalog);
  const areasWithoutFlows = areas.filter((a) => !coveredAreas.includes(a));
  const index = buildPinnedIndex(specs);
  const hits = detectHits(index, fileDiffs);
  const newSurfaceSignals = detectNewSurface(
    fileDiffs,
    titleType,
    areasWithoutFlows,
  );
  let classification,
    autoReason = null;
  if (!behaviour.length) {
    classification = "EXEMPT";
    const counts = {};
    for (const f of files) counts[f.cls] = (counts[f.cls] ?? 0) + 1;
    const dominant =
      Object.entries(counts)
        .filter(([c]) => c !== "E0")
        .sort((a, b) => b[1] - a[1])[0]?.[0] ?? "E0";
    autoReason = files.length ? DOMINANT_REASON[dominant] : "no-changes";
  } else if (newSurfaceSignals.length) classification = "NEW-FLOW";
  else if (hits.some((h) => !h.id.startsWith("harness:")))
    classification = "UPDATE-EXISTING";
  else classification = "UNDETERMINED";
  const changedFlowFiles =
    headSpecs ??
    Object.fromEntries(
      Object.entries(fileDiffs)
        .filter(([p]) => p.startsWith("e2e/flows/"))
        .map(([p, d]) => [p, d.added.join("\n")]),
    );
  const decision = decide({
    classification,
    autoReason,
    hits,
    marker: parseMarker(body),
    flowsDiffAdded,
    changedFlowFiles,
    catalogIds,
  });
  return {
    files,
    areas,
    areasWithoutFlows,
    hits,
    newSurfaceSignals,
    ...decision,
  };
}

function git(args) {
  return execFileSync("git", args, {
    cwd: REPO_ROOT,
    encoding: "utf8",
    maxBuffer: 64 * 1024 * 1024,
  });
}

const VALUE_FLAGS = ["base", "head", "pr", "body", "title"];

export function parseArgs(argv) {
  const a = {
    base: null,
    head: "HEAD",
    pr: null,
    body: null,
    title: null,
    json: false,
  };
  for (let i = 0; i < argv.length; i++) {
    const k = argv[i];
    if (k === "--json") a.json = true;
    else if (k.startsWith("--") && VALUE_FLAGS.includes(k.slice(2)))
      a[k.slice(2)] = argv[++i];
    else
      throw new Error(
        `unknown flag: ${k} — expected --json or ${VALUE_FLAGS.map((f) => `--${f}`).join(", ")}`,
      );
  }
  return a;
}

export function resolveBase({ base, prBase }) {
  if (base) return base;
  if (prBase) return `origin/${prBase}`;
  return "origin/dev";
}

export function parseDiff(nameStatus, full) {
  const fileDiffs = {};
  for (const line of nameStatus.trim().split("\n").filter(Boolean)) {
    const [code, ...paths] = line.split("\t");
    fileDiffs[paths[paths.length - 1]] = {
      status: code[0],
      added: [],
      removed: [],
    };
  }
  // `--- a/x` and `+++ b/x` only mean "header" between `diff --git` and the first `@@`; further
  // down they are content, and a Python `-- note` or a C-ish `++i` line must reach the hit scan.
  let current = null;
  let inHeader = false;
  for (const line of full.split("\n")) {
    const m = line.match(/^diff --git a\/(.+?) b\/(.+)$/);
    if (m) {
      current =
        fileDiffs[m[2]] ??
        (fileDiffs[m[2]] = { status: "M", added: [], removed: [] });
      inHeader = true;
      continue;
    }
    if (!current) continue;
    if (line.startsWith("@@")) inHeader = false;
    else if (!inHeader) {
      if (line.startsWith("+")) current.added.push(line.slice(1));
      else if (line.startsWith("-")) current.removed.push(line.slice(1));
    }
  }
  return fileDiffs;
}

function readFileDiffs(base, head) {
  return parseDiff(
    git(["diff", "--name-status", "-M", `${base}...${head}`]),
    git(["diff", `${base}...${head}`]),
  );
}

function readSpecs() {
  const out = [];
  for (const dir of ["flows", "harness"]) {
    const root = path.join(E2E_DIR, dir);
    if (!existsSync(root)) continue;
    const walk = (d) => {
      for (const ent of readdirSync(d, { withFileTypes: true })) {
        const p = path.join(d, ent.name);
        if (ent.isDirectory()) walk(p);
        else if (ent.name.endsWith(".spec.ts"))
          out.push({
            file: path.relative(E2E_DIR, p),
            text: readFileSync(p, "utf8"),
          });
      }
    };
    walk(root);
  }
  return out;
}

export function renderHuman(result) {
  const flowIds = [
    ...new Set(
      result.hits.map((h) => h.id).filter((id) => !id.startsWith("harness:")),
    ),
  ];
  const hitText = result.hits.length
    ? result.hits.map((h) => `${h.id} (${h.via})`).join("; ")
    : "none";
  // Hits are computed for exempt diffs too, and an EXEMPT verdict never consults them. Say so,
  // rather than printing a flow list beside `pass` and leaving the reader to reconcile the two.
  const hitLabel =
    result.classification === "NEW-FLOW"
      ? `also update: ${flowIds.join(", ") || "none"}`
      : result.classification === "EXEMPT" && result.hits.length
        ? `flows hit (informational — an exempt diff owes no declaration; read the diff yourself): ${hitText}`
        : `flows hit: ${hitText}`;
  const lines = [
    `E2E coverage: ${result.classification}${result.autoReason ? ` (${result.autoReason})` : ""} — areas: ${result.areas.join(", ") || "none"} — ${hitLabel}`,
  ];
  if (result.newSurfaceSignals.length)
    lines.push(`New surface: ${result.newSurfaceSignals.join("; ")}`);
  if (result.areasWithoutFlows.length)
    lines.push(
      `Areas with no flows yet: ${result.areasWithoutFlows.join(", ")}`,
    );
  lines.push(`Marker: ${result.marker ? result.marker.raw : "missing"}`);
  lines.push(`Verdict: ${result.verdict} — ${result.explanation}`);
  return lines;
}

function main() {
  const args = parseArgs(process.argv.slice(2));
  let body = args.body ? readFileSync(args.body, "utf8") : null;
  let title = args.title;
  let prBase = null;
  if (args.pr) {
    const pr = JSON.parse(
      execFileSync(
        "gh",
        ["pr", "view", String(args.pr), "--json", "body,title,baseRefName"],
        { cwd: REPO_ROOT, encoding: "utf8" },
      ),
    );
    body ??= pr.body;
    title ??= pr.title;
    prBase = pr.baseRefName;
  }
  args.base = resolveBase({ base: args.base, prBase });
  const titleType =
    title?.match(
      /^(feat|fix|chore|docs|refactor|test|perf|ci|revert)(\(|:|!)/,
    )?.[1] ?? null;
  const fileDiffs = readFileDiffs(args.base, args.head);
  const catalog = readFileSync(path.join(E2E_DIR, "FLOWS.md"), "utf8");
  const flowsDiffAdded = fileDiffs["e2e/FLOWS.md"]?.added ?? [];
  const changedFlowFiles = Object.fromEntries(
    Object.keys(fileDiffs)
      .filter(
        (p) =>
          p.startsWith("e2e/flows/") && existsSync(path.join(REPO_ROOT, p)),
      )
      .map((p) => [p, readFileSync(path.join(REPO_ROOT, p), "utf8")]),
  );
  const result = classifyChange({
    fileDiffs,
    specs: readSpecs(),
    catalog,
    flowsDiffAdded,
    titleType,
    body,
    changedFlowFiles,
  });
  const commits = git(["log", `${args.base}..${args.head}`, "--format=%s%n%b"]);
  const branch = git(["rev-parse", "--abbrev-ref", args.head]).trim();
  result.tickets = [
    ...new Set(
      [
        ...`${branch}\n${commits}\n${title ?? ""}\n${body ?? ""}`.matchAll(
          /\bTH-?(\d{3,5})\b/gi,
        ),
      ].map((m) => `TH-${m[1]}`),
    ),
  ];
  result.base = args.base;
  result.head = args.head;
  result.branch = branch;
  if (args.json) console.log(JSON.stringify(result, null, 2));
  else for (const line of renderHuman(result)) console.log(line);
  process.exit(result.verdict === "pass" ? 0 : 1);
}

if (process.argv[1] === fileURLToPath(import.meta.url)) {
  try {
    main();
  } catch (err) {
    if (!/^unknown flag: /.test(err.message)) throw err;
    console.error(err.message);
    process.exit(2);
  }
}
