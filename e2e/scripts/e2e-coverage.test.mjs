import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import {
  buildPinnedIndex,
  classifyChange,
  classifyPath,
  decide,
  detectHits,
  detectNewSurface,
  globToRegExp,
  parseArgs,
  parseDiff,
  parseMarker,
  renderHuman,
  resolveBase,
} from "./e2e-coverage.mjs";

const readE2E = (rel) =>
  readFileSync(new URL(`../${rel}`, import.meta.url), "utf8");
const REAL_SPECS = [
  "flows/auth/login.spec.ts",
  "flows/evals/eval-task.spec.ts",
  "flows/observe/span-filter-parity.spec.ts",
  "flows/observe/trace-ingestion.spec.ts",
].map((file) => ({ file, text: readE2E(file) }));
const REAL_CATALOG = readE2E("FLOWS.md");

test("globToRegExp handles **, *, and {a,b}", () => {
  assert.ok(
    globToRegExp("frontend/src/**/*.jsx").test("frontend/src/a/b/c.jsx"),
  );
  assert.ok(
    globToRegExp("**/tests/**").test("futureagi/tracer/tests/test_x.py"),
  );
  assert.ok(
    globToRegExp("futureagi/*/migrations/*.py").test(
      "futureagi/tracer/migrations/0001_a.py",
    ),
  );
  assert.ok(
    !globToRegExp("futureagi/*/migrations/*.py").test(
      "futureagi/ee/usage/migrations/0001_a.py",
    ),
  );
  assert.ok(globToRegExp("{a,b}/x").test("b/x"));
  assert.ok(!globToRegExp("frontend/src/*.jsx").test("frontend/src/a/b.jsx"));
});

test("globToRegExp handles nested {a,{b,c}} groups", () => {
  assert.ok(globToRegExp("a/{b,{c,d}}/e").test("a/b/e"));
  assert.ok(globToRegExp("a/{b,{c,d}}/e").test("a/c/e"));
  assert.ok(!globToRegExp("a/{b,{c,d}}/e").test("a/x/e"));
});

const cases = [
  ["e2e/FLOWS.md", "E0", null],
  ["api_contracts/openapi/swagger.json", "E0", null],
  ["README.md", "E1", null],
  [".github/workflows/e2e-ci.yml", "E2", null],
  ["futureagi/tracer/tests/test_eval_task.py", "E3", null],
  ["frontend/src/sections/evals/__tests__/x.test.jsx", "E3", null],
  ["e2e/flows/observe/trace-ingestion.spec.ts", "E3", null],
  ["futureagi/model_serving/api.py", "E4", null],
  ["futureagi/tfc/settings/base.py", "E5", null],
  ["futureagi/tracer/migrations/__init__.py", "E5", "observe"],
  ["agentcc-gateway/internal/anthropicfmt/x.go", "E6", "gateway"],
  ["agentcc-gateway/internal/providers/bedrock/client.go", "E6", "gateway"],
  ["frontend/src/theme/palette.js", "E7", null],
  ["docker-compose.yml", "B7", null],
  ["e2e/stack/docker-compose.e2e.yml", "B7", null],
  ["frontend/src/routes/sections/dashboard.jsx", "B1", null],
  ["frontend/src/pages/dashboard/alerts/index.jsx", "B1", null],
  [
    "frontend/src/sections/projects/LLMTracing/TraceFilterPanel.jsx",
    "B2",
    "observe",
  ],
  ["frontend/src/sections/projects/Alerts/common.js", "B2", "alerts"],
  ["frontend/src/components/traceDetail/SpanDetailPane.jsx", "B2", "observe"],
  ["frontend/src/sections/tasks/TaskList.jsx", "B2", "tasks"],
  ["futureagi/tracer/urls.py", "B3", "observe"],
  ["futureagi/tracer/views/eval_task.py", "B3", "evals"],
  ["futureagi/model_hub/views/annotation_queues.py", "B3", "annotations"],
  ["futureagi/simulate/serializers/scenario.py", "B3", "simulate"],
  ["futureagi/simulate/migrations/0078_alk.py", "B4", "simulate"],
  [
    "futureagi/tracer/services/clickhouse/v2/schema/002_spans_v2.sql",
    "B4",
    "observe",
  ],
  ["fi-collector/pkg/server/server.go", "B5", "observe"],
  ["agentcc-gateway/internal/providers/openai/client.go", "B6", "gateway"],
  ["agentcc-gateway/internal/server/handlers.go", "B6", "gateway"],
  ["futureagi/tracer/services/clickhouse/query_service.py", "E5", "observe"],
  ["frontend/src/utils/format-number.js", "B2", null],
  ["agentcc-gateway/internal/plugins/cache/store.go", "B6", "gateway"],
  ["agentcc-gateway/internal/plugins/audit/x.go", "E6", "gateway"],
  ["frontend/src/components/chip/Chip.jsx", "E7", null],
  ["somefile.txt", "E9", null],
];
for (const [p, cls, area] of cases) {
  test(`classifyPath ${p} → ${cls}/${area}`, () => {
    assert.deepEqual(classifyPath(p), { cls, area });
  });
}

// Every tracked production module whose path looks like a test: `test_*.py` outside any `tests/`
// directory, and the two frontend API modules under a directory literally named `tests`. None of
// them may reach E3, or a real API change reports `EXEMPT (tests-only)` and passes undeclared.
const PRODUCTION_PATHS_THAT_LOOK_LIKE_TESTS = [
  ["futureagi/simulate/serializers/test_execution.py", "B3", "simulate"],
  [
    "futureagi/simulate/serializers/requests/test_execution.py",
    "B3",
    "simulate",
  ],
  [
    "futureagi/simulate/serializers/response/test_execution.py",
    "B3",
    "simulate",
  ],
  ["futureagi/simulate/models/test_execution.py", "E5", "simulate"],
  ["futureagi/simulate/services/test_executor.py", "E5", "simulate"],
  [
    "futureagi/simulate/temporal/activities/test_execution.py",
    "E5",
    "simulate",
  ],
  ["futureagi/simulate/temporal/types/test_execution.py", "E5", "simulate"],
  [
    "futureagi/simulate/temporal/workflows/test_execution_workflow.py",
    "E5",
    "simulate",
  ],
  ["futureagi/simulate/utils/test_execution.py", "E5", "simulate"],
  ["futureagi/simulate/utils/test_execution_utils.py", "E5", "simulate"],
  ["futureagi/ee/voice/tasks/test_monitor.py", "E5", "simulate"],
  [
    "futureagi/ai_tools/tools/evaluations/test_eval_template.py",
    "E5",
    "datasets",
  ],
  [
    "futureagi/ee/agenthub/eval_orchestrator/test_orchestrator.py",
    "E5",
    "prompts",
  ],
  ["futureagi/ee/experiments/src/agent/test_flow.py", "E5", "datasets"],
  [
    "futureagi/ee/experiments/src/agent/test_flow_halueval.py",
    "E5",
    "datasets",
  ],
  [
    "futureagi/ee/experiments/src/playground/test_conversation.py",
    "E5",
    "datasets",
  ],
  ["futureagi/ee/experiments/src/playground/test_rag.py", "E5", "datasets"],
  ["frontend/src/api/tests/testRuns.js", "B2", null],
  ["frontend/src/api/tests/testDetails.js", "B2", null],
];
for (const [p, cls, area] of PRODUCTION_PATHS_THAT_LOOK_LIKE_TESTS) {
  test(`classifyPath does not exempt production module ${p}`, () => {
    assert.deepEqual(classifyPath(p), { cls, area });
  });
}

test("genuine tests are still E3", () => {
  for (const p of [
    "futureagi/tracer/tests/test_eval_task.py",
    "futureagi/agent_playground/tests/views/test_graph.py",
    "frontend/src/x/__tests__/y.test.jsx",
    "frontend/src/pages/dashboard/settings/WorkspaceSettings/__tests__/workspace-general-no-workspace.test.jsx",
    "e2e/flows/observe/trace-ingestion.spec.ts",
    "futureagi/conftest.py",
  ])
    assert.equal(classifyPath(p).cls, "E3", p);
});

test("a serializer rename plus its frontend API module is not tests-only", () => {
  const r = classifyChange({
    fileDiffs: {
      "futureagi/simulate/serializers/test_execution.py": {
        status: "M",
        added: ['    run_name = serializers.CharField(source="name")'],
        removed: ["    name = serializers.CharField()"],
      },
      "frontend/src/api/tests/testRuns.js": {
        status: "M",
        added: ["  const { run_name } = resp.data;"],
        removed: ["  const { name } = resp.data;"],
      },
    },
    specs: REAL_SPECS,
    catalog: REAL_CATALOG,
    flowsDiffAdded: [],
    titleType: "fix",
    body: null,
  });
  assert.notEqual(r.classification, "EXEMPT");
  assert.notEqual(r.verdict, "pass");
  assert.deepEqual(r.areas, ["simulate"]);
});

const SPEC = `
test('OBS-E2E-001: SDK trace appears', {tag:['@flow']}, async ({page, probe}) => {
  const SPAN_LIST_PATH = "/tracer/observation-span/list_spans_observe/";
  await page.goto('/dashboard/observe', { waitUntil: 'domcontentloaded' });
  await probe.ch('SELECT count() AS n FROM spans FINAL WHERE trace_id = {t:String}', {});
  await probe.pg('SELECT id FROM tracer_project WHERE name = $1', []);
  page.locator('.ag-row [col-id="trace_name"]');
  const url = \`/dashboard/observe/\${projectId}/llm-tracing?tab=traces\`;
});`;

test("buildPinnedIndex extracts endpoints, segments, tables and selectors per flow", () => {
  const idx = buildPinnedIndex([
    { file: "flows/observe/trace-ingestion.spec.ts", text: SPEC },
  ]);
  const lits = Object.fromEntries(idx.map((e) => [e.literal, e]));
  assert.equal(
    lits["/tracer/observation-span/list_spans_observe/"].flow,
    "OBS-E2E-001",
  );
  assert.equal(lits["list_spans_observe"].kind, "segment");
  assert.equal(lits["observation-span"].kind, "segment");
  assert.equal(lits["/dashboard/observe"].kind, "endpoint");
  assert.equal(lits["/dashboard/observe/"].kind, "endpoint");
  assert.equal(lits["spans"].kind, "table");
  assert.equal(lits["tracer_project"].kind, "table");
  assert.equal(lits['col-id="trace_name"'].kind, "selector");
  assert.ok(!("/" in lits));
});

test("harness specs are indexed under a harness: label", () => {
  const idx = buildPinnedIndex([
    {
      file: "harness/mock-llm.spec.ts",
      text: `await req.post('/v1/chat/completions')`,
    },
  ]);
  assert.equal(
    idx.find((e) => e.literal === "/v1/chat/completions").flow,
    "harness:mock-llm.spec.ts",
  );
});

test("detectHits matches added/removed lines and urls.py segments", () => {
  const idx = buildPinnedIndex([
    { file: "flows/observe/x.spec.ts", text: SPEC },
  ]);
  const hits = detectHits(idx, {
    "frontend/src/components/traceDetail/SpanDetailPane.jsx": {
      status: "M",
      added: ['  <div col-id="trace_name">'],
      removed: [],
    },
    "futureagi/tracer/urls.py": {
      status: "M",
      added: [],
      removed: ['router.register(r"observation-span", ObservationSpanView)'],
    },
    "futureagi/tracer/models/x.py": {
      status: "M",
      added: ["x = 1"],
      removed: [],
    },
  });
  assert.deepEqual([...new Set(hits.map((h) => h.id))].sort(), ["OBS-E2E-001"]);
  assert.equal(hits.length, 2);
  assert.match(
    hits.find((h) => h.via.includes("urls.py")).via,
    /observation-span/,
  );
});

test("detectHits ignores table names outside SQL-looking lines", () => {
  const idx = buildPinnedIndex([
    { file: "flows/observe/x.spec.ts", text: SPEC },
  ]);
  const hits = detectHits(idx, {
    "frontend/src/sections/a/b.jsx": {
      status: "M",
      added: ["const spans = rows;"],
      removed: [],
    },
  });
  assert.deepEqual(hits, []);
});

test("detectHits ignores generated, docs, tooling and tests-only files", () => {
  const idx = buildPinnedIndex([
    { file: "flows/observe/x.spec.ts", text: SPEC },
  ]);
  const hits = detectHits(idx, {
    "e2e/flows/observe/x.spec.ts": {
      status: "M",
      added: [
        '  const p = "/tracer/observation-span/list_spans_observe/";',
        "  page.locator('[col-id=\"trace_name\"]');",
      ],
      removed: [],
    },
    "futureagi/tracer/tests/test_spans.py": {
      status: "M",
      added: [
        '    resp = client.get("/tracer/observation-span/list_spans_observe/")',
      ],
      removed: [],
    },
    ".github/workflows/e2e-ci.yml": {
      status: "M",
      added: ["      run: curl /tracer/observation-span/list_spans_observe/"],
      removed: [],
    },
    "e2e/FLOWS.md": {
      status: "M",
      added: ['- `col-id="trace_name"`'],
      removed: [],
    },
  });
  assert.equal(hits.length, 0);
});

test("detectHits does not fire on a lowercase update or on prose", () => {
  const idx = buildPinnedIndex(REAL_SPECS);
  assert.deepEqual(
    detectHits(idx, {
      "futureagi/tracer/views/span.py": {
        status: "M",
        added: ["    def update(self, instance, spans):"],
        removed: [],
      },
    }),
    [],
  );
  assert.deepEqual(
    detectHits(idx, {
      "frontend/src/sections/projects/LLMTracing/x.jsx": {
        status: "M",
        added: ["  // update the traces list after a refetch"],
        removed: [],
      },
    }),
    [],
  );
});

test("detectHits still fires on a real SQL line in a backend view", () => {
  const idx = buildPinnedIndex(REAL_SPECS);
  const hits = detectHits(idx, {
    "futureagi/tracer/views/span.py": {
      status: "M",
      added: ['    q = "SELECT id FROM spans FINAL WHERE project_id = %s"'],
      removed: [],
    },
  });
  assert.ok(hits.length > 0);
  assert.ok(hits.every((h) => h.via.includes("table spans")));
});

test("detectHits requires a whole-token match for segments", () => {
  const idx = buildPinnedIndex(REAL_SPECS);
  assert.deepEqual(
    detectHits(idx, {
      "futureagi/model_hub/views/x.py": {
        status: "M",
        added: ['    url = "/model-hub-v2/list/"'],
        removed: [],
      },
    }),
    [],
  );
  const hits = detectHits(idx, {
    "futureagi/model_hub/urls.py": {
      status: "M",
      added: ['    path("model-hub/custom_models/create/", V)'],
      removed: [],
    },
  });
  assert.ok(hits.some((h) => h.via.includes("model-hub")));
});

test("detectNewSurface fires on routes, urls.py, new views, new pages, collector handlers, migrations+serializer", () => {
  const signals = detectNewSurface(
    {
      "frontend/src/routes/sections/dashboard.jsx": {
        status: "M",
        added: ['      path: "alerts-v2",'],
        removed: [],
      },
      "frontend/src/pages/dashboard/alerts-v2.jsx": {
        status: "A",
        added: ["export default 1"],
        removed: [],
      },
      "futureagi/tracer/urls.py": {
        status: "M",
        added: ['router.register(r"alert-rule", AlertRuleView)'],
        removed: [],
      },
      "futureagi/tracer/views/alert_rule.py": {
        status: "A",
        added: ["class AlertRuleView: pass"],
        removed: [],
      },
      "fi-collector/pkg/server/server.go": {
        status: "M",
        added: ['mux.HandleFunc("/v1/logs", s.h)'],
        removed: [],
      },
      "futureagi/tracer/migrations/0099_alert.py": {
        status: "A",
        added: ["migrations.CreateModel("],
        removed: [],
      },
      "futureagi/tracer/serializers/alert.py": {
        status: "A",
        added: ["class AlertSerializer: pass"],
        removed: [],
      },
    },
    "fix",
    [],
  );
  assert.equal(signals.length, 6);
});

test("detectNewSurface fires on feat title in an area with no flows", () => {
  const signals = detectNewSurface(
    {
      "frontend/src/sections/alerts/List.jsx": {
        status: "M",
        added: ["x"],
        removed: [],
      },
    },
    "feat",
    ["alerts"],
  );
  assert.deepEqual(signals, ["feat: PR in area alerts, which has no flows"]);
});

test("detectNewSurface ignores added files that classifyPath calls non-surface", () => {
  // Fixtures hardcode `status: "M"`, so only a unit case can carry an added file.
  assert.deepEqual(
    detectNewSurface(
      {
        "frontend/src/pages/dashboard/settings/WorkspaceSettings/__tests__/workspace-general-no-workspace.test.jsx":
          { status: "A", added: ["it('renders', () => {})"], removed: [] },
        "futureagi/agent_playground/tests/views/test_graph.py": {
          status: "A",
          added: ["def test_graph(): pass"],
          removed: [],
        },
      },
      "fix",
      [],
    ),
    [],
  );
  assert.deepEqual(
    detectNewSurface(
      {
        "frontend/src/pages/dashboard/alerts.jsx": {
          status: "A",
          added: ["export default function Alerts() {}"],
          removed: [],
        },
      },
      "fix",
      [],
    ),
    ["new page frontend/src/pages/dashboard/alerts.jsx"],
  );
});

test("detectNewSurface: a bug fix plus its regression test is not NEW-FLOW", () => {
  const r = classifyChange({
    fileDiffs: {
      "frontend/src/sections/projects/LLMTracing/Toolbar.jsx": {
        status: "M",
        added: ["const a = 1;"],
        removed: [],
      },
      "frontend/src/pages/dashboard/settings/WorkspaceSettings/__tests__/x.test.jsx":
        { status: "A", added: ["it('x', () => {})"], removed: [] },
    },
    specs: [],
    catalog: "## observe\n### OBS-E2E-001 — x\n",
    flowsDiffAdded: [],
    titleType: "fix",
    body: null,
  });
  assert.deepEqual(r.newSurfaceSignals, []);
  assert.equal(r.classification, "UNDETERMINED");
});

test("detectNewSurface: editing a paths.js value does not fire the key signal", () => {
  const edit = {
    "frontend/src/routes/paths.js": {
      status: "M",
      added: ["    alerts: `${ROOTS.DASHBOARD}/alerts-v2`,"],
      removed: ["    alerts: `${ROOTS.DASHBOARD}/alerts`,"],
    },
  };
  assert.deepEqual(detectNewSurface(edit, "fix", []), []);
  const add = {
    "frontend/src/routes/paths.js": {
      status: "M",
      added: ["    alertsOverview: `${ROOTS.DASHBOARD}/alerts-v2`,"],
      removed: [],
    },
  };
  assert.deepEqual(detectNewSurface(add, "fix", []), [
    "added path key in frontend/src/routes/paths.js",
  ]);
});

test("parseMarker reads each kind and flags problems", () => {
  assert.deepEqual(parseMarker("blah\nE2E: new OBS-E2E-003, OBS-E2E-004\n"), {
    raw: "E2E: new OBS-E2E-003, OBS-E2E-004",
    kind: "new",
    ids: ["OBS-E2E-003", "OBS-E2E-004"],
    reason: null,
    problems: [],
  });
  assert.equal(parseMarker("E2E: exempt (docs)").reason, "docs");
  assert.equal(
    parseMarker("E2E: exempt (harness-gap second user)").reason,
    "harness-gap second user",
  );
  assert.deepEqual(parseMarker("E2E: exempt (because)").problems, [
    "unknown exemption reason: because",
  ]);
  assert.deepEqual(parseMarker("E2E: new obs-e2e-1").problems, [
    "no flow id of the form AREA-E2E-nnn",
  ]);
  assert.equal(parseMarker("E2E:\n"), null);
  assert.equal(parseMarker(null), null);
  assert.deepEqual(
    parseMarker("E2E: new A-E2E-001\nE2E: exempt (docs)").problems,
    ["more than one E2E: line"],
  );
});

test("parseMarker ignores the template's HTML-comment examples", () => {
  const body =
    "## E2E coverage\n\n<!-- One line.\n     E2E: new <ID>  a flow added\n     E2E: exempt (<reason>) -->\n\nE2E: covered-by OBS-E2E-001\n";
  assert.deepEqual(parseMarker(body), {
    raw: "E2E: covered-by OBS-E2E-001",
    kind: "covered-by",
    ids: ["OBS-E2E-001"],
    reason: null,
    problems: [],
  });
  assert.equal(parseMarker("<!-- E2E: new <ID> -->\nE2E:\n"), null);
});

test("parseMarker treats the template's unfilled E2E: line as absent", () => {
  const template = readFileSync(
    new URL("../../.github/PULL_REQUEST_TEMPLATE.md", import.meta.url),
    "utf8",
  );
  assert.equal(parseMarker(template), null);
  assert.equal(
    parseMarker(
      "## E2E coverage\n\nE2E:\n\n---\n\n## 1) What changes were done\n",
    ),
    null,
  );
});

const base = {
  flowsDiffAdded: [],
  changedFlowFiles: {},
  catalogIds: ["OBS-E2E-001", "OBS-E2E-002", "EVAL-E2E-001", "AUTH-E2E-001"],
};

test("decide: EXEMPT passes without marker", () => {
  assert.equal(
    decide({
      ...base,
      classification: "EXEMPT",
      autoReason: "docs",
      hits: [],
      marker: null,
    }).verdict,
    "pass",
  );
});
test("decide: UPDATE-EXISTING needs updated/covered-by", () => {
  const hits = [{ id: "OBS-E2E-001", via: "x" }];
  assert.equal(
    decide({ ...base, classification: "UPDATE-EXISTING", hits, marker: null })
      .verdict,
    "needs-marker",
  );
  assert.equal(
    decide({
      ...base,
      classification: "UPDATE-EXISTING",
      hits,
      marker: parseMarker("E2E: covered-by OBS-E2E-001"),
    }).verdict,
    "pass",
  );
  assert.equal(
    decide({
      ...base,
      classification: "UPDATE-EXISTING",
      hits,
      marker: parseMarker("E2E: updated OBS-E2E-001"),
      changedFlowFiles: {
        "e2e/flows/observe/trace-ingestion.spec.ts": "test('OBS-E2E-001: x'",
      },
    }).verdict,
    "pass",
  );
  assert.equal(
    decide({
      ...base,
      classification: "UPDATE-EXISTING",
      hits,
      marker: parseMarker("E2E: updated OBS-E2E-001"),
    }).verdict,
    "block",
  );
  assert.equal(
    decide({
      ...base,
      classification: "UPDATE-EXISTING",
      hits,
      marker: parseMarker("E2E: covered-by OBS-E2E-009"),
    }).verdict,
    "block",
  );
});
test("decide: NEW-FLOW needs a new id visible in FLOWS.md; exempt requires override", () => {
  assert.equal(
    decide({
      ...base,
      classification: "NEW-FLOW",
      hits: [],
      marker: parseMarker("E2E: new ALERT-E2E-001"),
      flowsDiffAdded: ["### ALERT-E2E-001 — x"],
    }).verdict,
    "pass",
  );
  assert.equal(
    decide({
      ...base,
      classification: "NEW-FLOW",
      hits: [],
      marker: parseMarker("E2E: new ALERT-E2E-001"),
    }).verdict,
    "block",
  );
  const r = decide({
    ...base,
    classification: "NEW-FLOW",
    hits: [],
    marker: parseMarker("E2E: exempt (refactor)"),
  });
  assert.equal(r.verdict, "block");
  assert.match(r.explanation, /reviewer override/);
  assert.equal(
    decide({ ...base, classification: "NEW-FLOW", hits: [], marker: null })
      .verdict,
    "needs-marker",
  );
});
test("decide: UNDETERMINED passes with any well-formed marker", () => {
  assert.equal(
    decide({
      ...base,
      classification: "UNDETERMINED",
      hits: [],
      marker: parseMarker("E2E: exempt (cosmetic)"),
    }).verdict,
    "pass",
  );
  assert.equal(
    decide({ ...base, classification: "UNDETERMINED", hits: [], marker: null })
      .verdict,
    "needs-marker",
  );
});

test("classifyChange end to end on a docs-only diff", () => {
  const r = classifyChange({
    fileDiffs: { "README.md": { status: "M", added: ["x"], removed: [] } },
    specs: [],
    catalog: "# E2E Flow Catalog\n## observe\n### OBS-E2E-001 — x\n",
    flowsDiffAdded: [],
    titleType: "docs",
    body: null,
  });
  assert.equal(r.classification, "EXEMPT");
  assert.equal(r.autoReason, "docs");
  assert.equal(r.verdict, "pass");
});

test("classifyChange reports no-changes on an empty diff", () => {
  const r = classifyChange({
    fileDiffs: {},
    specs: [],
    catalog: "# E2E Flow Catalog\n## observe\n### OBS-E2E-001 — x\n",
    flowsDiffAdded: [],
    titleType: null,
    body: null,
  });
  assert.equal(r.classification, "EXEMPT");
  assert.equal(r.autoReason, "no-changes");
  assert.equal(r.verdict, "pass");
});

test("classifyChange still reports generated when every changed file is generated", () => {
  const r = classifyChange({
    fileDiffs: {
      "api_contracts/openapi/swagger.json": {
        status: "M",
        added: ["x"],
        removed: [],
      },
    },
    specs: [],
    catalog: "## observe\n### OBS-E2E-001 — x\n",
    flowsDiffAdded: [],
    titleType: null,
    body: null,
  });
  assert.equal(r.autoReason, "generated");
});

const PROMPT_LIBRARY_DIFF = {
  "futureagi/model_hub/urls.py": {
    status: "M",
    added: [
      '    path("model-hub/prompt-library/", PromptLibraryView.as_view()),',
    ],
    removed: [],
  },
  "frontend/src/sections/prompt/PromptLibrary.jsx": {
    status: "A",
    added: ['const URL = "/model-hub/prompt-library/";'],
    removed: [],
  },
};
const promptLibraryChange = (body) =>
  classifyChange({
    fileDiffs: PROMPT_LIBRARY_DIFF,
    specs: REAL_SPECS,
    catalog: REAL_CATALOG,
    flowsDiffAdded: [],
    titleType: "feat",
    body,
  });

test("classifyChange: new surface outranks a hit on an existing flow's pinned segment", () => {
  const r = promptLibraryChange(null);
  assert.equal(r.classification, "NEW-FLOW");
  assert.equal(r.verdict, "needs-marker");
  assert.ok(r.hits.some((h) => h.id === "EVAL-E2E-001"));
});

test("classifyChange: covered-by an existing flow does not satisfy NEW-FLOW", () => {
  const r = promptLibraryChange("E2E: covered-by EVAL-E2E-001");
  assert.equal(r.verdict, "block");
  assert.match(r.explanation, /reviewer override/);
});

test("resolveBase prefers --base, then the PR's base branch, then origin/dev", () => {
  assert.equal(
    resolveBase({ base: "origin/main", prBase: "dev" }),
    "origin/main",
  );
  assert.equal(resolveBase({ base: null, prBase: "main" }), "origin/main");
  assert.equal(resolveBase({ base: null, prBase: null }), "origin/dev");
});

test("classifyChange: behaviour file in a covered area with no hits and no signal is UNDETERMINED", () => {
  const r = classifyChange({
    fileDiffs: {
      "frontend/src/sections/projects/LLMTracing/Toolbar.jsx": {
        status: "M",
        added: ["const a = 1;"],
        removed: [],
      },
    },
    specs: [],
    catalog: "## observe\n### OBS-E2E-001 — x\n",
    flowsDiffAdded: [],
    titleType: "fix",
    body: null,
  });
  assert.equal(r.classification, "UNDETERMINED");
  assert.deepEqual(r.areas, ["observe"]);
});

test("parseMarker accepts every reason the tool itself prints", () => {
  for (const reason of [
    "docs",
    "tooling",
    "tests-only",
    "not-in-stack",
    "backend-internal",
    "gateway-internal",
    "cosmetic",
    "refactor",
    "test-support",
    "stack-shape",
    "generated",
    "unclassified",
    "merge-back",
  ])
    assert.deepEqual(
      parseMarker(`E2E: exempt (${reason})`).problems,
      [],
      reason,
    );
});

test("decide: an auto-exempt diff is never blocked on marker grammar", () => {
  assert.equal(
    decide({
      ...base,
      classification: "EXEMPT",
      autoReason: "stack-shape",
      hits: [],
      marker: parseMarker("E2E: exempt (because)"),
    }).verdict,
    "pass",
  );
  // A malformed marker still blocks wherever a declaration is actually owed.
  assert.equal(
    decide({
      ...base,
      classification: "UNDETERMINED",
      hits: [],
      marker: parseMarker("E2E: exempt (because)"),
    }).verdict,
    "block",
  );
});

test("decide: the needs-marker hint lists each flow id once", () => {
  const r = decide({
    ...base,
    classification: "UPDATE-EXISTING",
    hits: [
      { id: "OBS-E2E-001", via: "a.py: spans" },
      { id: "OBS-E2E-001", via: "b.jsx: /dashboard/observe" },
      { id: "harness:mock-llm.spec.ts", via: "c.py: /v1/chat" },
    ],
    marker: null,
  });
  assert.equal(r.verdict, "needs-marker");
  assert.equal(r.explanation.match(/OBS-E2E-001/g).length, 2); // once per suggested line
  assert.doesNotMatch(r.explanation, /harness:/);
});

test("parseDiff keeps content lines that start with -- or ++", () => {
  const status =
    "M\tfutureagi/tracer/views/span.py\nM\tfi-collector/pkg/x.go\n";
  const full = [
    "diff --git a/futureagi/tracer/views/span.py b/futureagi/tracer/views/span.py",
    "index 1111111..2222222 100644",
    "--- a/futureagi/tracer/views/span.py",
    "+++ b/futureagi/tracer/views/span.py",
    "@@ -1,3 +1,3 @@",
    ' q = "SELECT 1"',
    // Content lines that render as `--- ` / `+++ ` once the diff marker is
    // prepended: a removed SQL comment `-- keep …` and an added `++ …` line.
    // These are the only shapes the old unanchored `/^(\\+\\+\\+|---) /` header
    // regex could swallow — a fixture whose lines merely start with `--`/`++`
    // passes on unfixed code and pins nothing.
    "--- keep the projection FROM spans FINAL",
    "+++ counter for spans",
    "diff --git a/fi-collector/pkg/x.go b/fi-collector/pkg/x.go",
    "--- a/fi-collector/pkg/x.go",
    "+++ b/fi-collector/pkg/x.go",
    "@@ -1 +1 @@",
    "-++i",
    "+++i + 1",
    "",
  ].join("\n");
  const d = parseDiff(status, full);
  assert.deepEqual(d["futureagi/tracer/views/span.py"].added, [
    "++ counter for spans",
  ]);
  assert.deepEqual(d["futureagi/tracer/views/span.py"].removed, [
    "-- keep the projection FROM spans FINAL",
  ]);
  assert.deepEqual(d["fi-collector/pkg/x.go"].added, ["++i + 1"]);
  assert.deepEqual(d["fi-collector/pkg/x.go"].removed, ["++i"]);
});

test("parseDiff records rename status from --name-status", () => {
  const d = parseDiff(
    "A\tfrontend/src/pages/dashboard/alerts.jsx\nR100\ta/old.py\ta/new.py\n",
    "",
  );
  assert.equal(d["frontend/src/pages/dashboard/alerts.jsx"].status, "A");
  assert.equal(d["a/new.py"].status, "R");
});

test("parseArgs rejects an unknown flag instead of silently ignoring it", () => {
  assert.deepEqual(parseArgs(["--title", "feat(x): y", "--json"]), {
    base: null,
    head: "HEAD",
    pr: null,
    body: null,
    title: "feat(x): y",
    json: true,
  });
  assert.throws(() => parseArgs(["--titel", "feat(x): y"]), /unknown flag/);
  assert.throws(() => parseArgs(["origin/dev"]), /unknown flag/);
});

test("renderHuman marks pinned hits under a passing EXEMPT as informational", () => {
  const exempt = renderHuman({
    classification: "EXEMPT",
    autoReason: "backend-internal",
    areas: [],
    hits: [{ id: "OBS-E2E-001", via: "spans.py: table spans" }],
    newSurfaceSignals: [],
    areasWithoutFlows: [],
    marker: null,
    verdict: "pass",
    explanation: "exempt (backend-internal); no declaration needed",
  }).join("\n");
  assert.match(exempt, /informational/);
  assert.match(exempt, /OBS-E2E-001/);

  const update = renderHuman({
    classification: "UPDATE-EXISTING",
    autoReason: null,
    areas: ["observe"],
    hits: [{ id: "OBS-E2E-001", via: "spans.py: table spans" }],
    newSurfaceSignals: [],
    areasWithoutFlows: [],
    marker: null,
    verdict: "needs-marker",
    explanation: "…",
  }).join("\n");
  assert.match(update, /flows hit: OBS-E2E-001/);
  assert.doesNotMatch(update, /informational/);
});

for (const n of [2265, 2182, 1976, 2321, 2358, 2300]) {
  test(`fixture PR #${n} classifies as recorded`, async () => {
    const fx = JSON.parse(
      readFileSync(
        new URL(`./fixtures/e2e-coverage/${n}.json`, import.meta.url),
        "utf8",
      ),
    );
    const fileDiffs = Object.fromEntries(
      fx.files.map((f) => [
        f,
        { status: "M", added: fx.addedLines?.[f] ?? [], removed: [] },
      ]),
    );
    const r = classifyChange({
      fileDiffs,
      specs: fx.specs ?? [],
      catalog: fx.catalog,
      flowsDiffAdded: [],
      titleType: fx.titleType,
      body: null,
    });
    assert.equal(r.classification, fx.expected.classification);
    assert.deepEqual(r.areas, fx.expected.areas);
  });
}
