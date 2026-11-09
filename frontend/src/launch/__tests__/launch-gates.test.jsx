import React from "react";
import { createHash } from "node:crypto";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import process from "node:process";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { render, renderHook, screen } from "src/utils/test-utils";
import { API_CONTRACT_EXCEPTIONS } from "src/api/contracts/api-contract-exceptions";
import prototypePaths from "../__fixtures__/registry-prototype-paths-705.json";
import { SURFACE_BY_ID } from "../launchSurfaces";
import { setLaunchOverrideForTest } from "../launchSwitch";
import { MADE_REAL, MADE_REAL_PATHS } from "../madeReal";
import {
  absentPathInterceptor,
  absentTemplateOf,
  absentTemplates,
  apiPathOf,
  CONTRACTED_PROTOTYPE_PATHS,
} from "../absentPathGuard";

// L6 launch switch: the gates in the shown surfaces' own files. Each test
// turns a launch build on (the default: experimentsBasic OFF, no lane has
// made anything real) and checks the component or helper hides, fires no
// request, or keeps only the dev-honoured copy; then it checks a prototype
// build (override off) still has the full UI.

vi.mock("src/auth/hooks", () => ({
  useAuthContext: () => ({ role: "owner" }),
}));

const machinesCalls = [];
vi.mock("src/api/develop/r4", () => ({
  r4Keys: { machines: () => ["r4", "machines"] },
  useConnectMachines: (opts) => {
    machinesCalls.push(opts);
    return { data: undefined, isLoading: false, isError: false };
  },
}));

const playground = vi.fn();
vi.mock("../../sections/develop-detail/Experiment/useEvalReadsOnRow", () => ({
  useEvalReadsOnRow: (...a) => {
    playground(...a);
    return { byEval: {}, isLoading: false };
  },
}));

const LAUNCH = { launch: true, experimentsBasic: false, madeReal: [] };
const BASIC = { launch: true, experimentsBasic: true, madeReal: [] };

// 748d add.4/4a: 450e7c72f11874b11d71c4879e9944c5c066eb49 backed splits.
// 6a40c258eb accepted saved grid views; their published paths now follow that
// claim through the same served-source proof. No generated contract is input.
// Existing split source pins remain unchanged. Saved-view source pins below
// bind views, serializers and routes at c2.5 triage head 3eaa8ce1.
const GRIDB_MADE_REAL = [
  {
    template: "/model-hub/datasets/{dataset_id}/splits/",
    owner: "dataset.splits",
    file: "futureagi/model_hub/views/dataset_splits.py",
    view: "DatasetSplitsView",
    operations: {
      get: [
        "    @validated_request(responses={200: DatasetSplitListResponseSerializer})",
        "    def get(self, request, dataset_id):",
      ].join("\n"),
      post: [
        "    @validated_request(",
        "        request_serializer=DatasetSplitWriteSerializer,",
        "        responses={200: DatasetSplitResponseSerializer},",
        "    )",
        "    def post(self, request, dataset_id):",
      ].join("\n"),
    },
  },
  {
    template: "/model-hub/datasets/{dataset_id}/splits/{split_id}/",
    owner: "dataset.splits",
    file: "futureagi/model_hub/views/dataset_splits.py",
    view: "DatasetSplitDetailView",
    operations: {
      patch: [
        "    @validated_request(",
        "        request_serializer=DatasetSplitPatchSerializer,",
        "        responses={200: DatasetSplitResponseSerializer},",
        "    )",
        "    def patch(self, request, dataset_id, split_id):",
      ].join("\n"),
      delete: [
        "    @validated_request(responses={200: DatasetSplitDeleteResponseSerializer})",
        "    def delete(self, request, dataset_id, split_id):",
      ].join("\n"),
    },
  },
  {
    template: "/model-hub/r3/saved-views/",
    owner: "dataset.savedViews",
    file: "futureagi/model_hub/views/dataset_views.py",
    view: "DatasetSavedViewsView",
    operations: {
      get: [
        "    @validated_request(",
        "        query_serializer=DatasetViewQuerySerializer,",
        "        responses={200: DatasetViewListResponseSerializer},",
        "    )",
        "    def get(self, request):",
      ].join("\n"),
      post: [
        "    @validated_request(",
        "        request_serializer=DatasetViewWriteSerializer,",
        "        responses={200: DatasetViewResponseSerializer},",
        "    )",
        "    def post(self, request):",
      ].join("\n"),
    },
  },
  {
    template: "/model-hub/r3/saved-views/{view_id}/",
    owner: "dataset.savedViews",
    file: "futureagi/model_hub/views/dataset_views.py",
    view: "DatasetSavedViewDetailView",
    operations: {
      delete: [
        "    @validated_request(responses={200: DatasetViewDeletedResponseSerializer})",
        "    def delete(self, request, view_id):",
      ].join("\n"),
    },
  },
];

// Fail closed on source drift, including route shadowing, decorator removal,
// changed serializers or a different generator input. Re-review these pins
// with a source change, in that same commit; never refresh them blindly.
// The generator consumes @validated_request -> swagger_auto_schema, through
// openapi settings/URLconf, then the two frontend generators read Swagger.
// Re-reviewed at 708J 3e acting head 4bb9ac09c1 (int6 0d79f1240e was the last
// match). Each drift keeps both split routes, views and decorators unchanged:
// - model_hub/urls.py: additions only, after port 458296b468 (340a022c90's
//   version, baseline and experiment-run routes); none can match splits/.
// - tfc/urls.py: includes added after model-hub/ (340a022c90, 707d36ad53, via
//   458296b468), so model-hub/ still resolves first.
// - tfc/utils/api_contracts.py: 3defdf30fb (via 8381dd4cac) wraps only an
//   inferred 2xx; every split operation declares its 200, so stays as is.
// - scripts/generate-openapi-schema.sh: 34d3b04ee9 (via fcb50b8bfe) exempts
//   only two retired personal-key definitions from the shrink guard.
const GRIDB_SOURCE_PINS = {
  // Saved views use the same route/schema chain as splits, with their own
  // exact view and serializer bytes. Existing split pins are not refreshed.
  "futureagi/model_hub/views/dataset_views.py":
    "641427a3650a395bb9a5b8edb10ec23f197510419c618aa6e53a00fff592083b",
  "futureagi/model_hub/serializers/dataset_views.py":
    "ccea879e9f27576ede98af248a0eba28e784b64ae951a07d85738c0807216af1",
  // ROWAPI-EXC-r3b: re-verified 458296b468 -> 62433e2695 hunk by hunk.
  // Added imports/routes cannot shadow splits; split bindings stay unchanged.
  "futureagi/model_hub/urls.py":
    "85b5d75c4f3f7a364aac4e41d954375bca50864ae491fcd2540576d690955547",
  "futureagi/model_hub/views/dataset_splits.py":
    "71c8afd990543c1ee5f7eaa0039e17775eb08f16938c48a3e6c1089d3625f9f7",
  "futureagi/model_hub/serializers/dataset_splits.py":
    "a925b4c47620099aad7fab8ad4cf78c4bbdb56f2cc9211f067c1da0f27807540",
  "futureagi/tfc/utils/api_contracts.py":
    "8ae73336029b00c6ee738d52449bd1f9a8c138ad8f17d54b6515c473434842ba",
  "futureagi/tfc/urls.py":
    "d4f0802b2f06155e503f2218dcf7ecd7f0a7fc27db09491761f9c2510e7231a4",
  "futureagi/tfc/openapi_urls.py":
    "50fa207ec3df9a6d264a3d3fb71926d2968209809b98a2b3be9943ffdaf3feaa",
  // ROWAPI-EXC-r3b: re-verified 509636133e -> 62433e2695 hunk by hunk.
  // Import ordering, predicate formatting and F44 list metadata preserve the
  // split contracts and ROOT_URLCONF; no split serializer reads the F44 flag.
  "futureagi/tfc/settings/openapi.py":
    "f40502dcf204d99813dc508e101876fcaf51608538a6f30b7ce283f21e430b4d",
  "scripts/generate-openapi-schema.sh":
    "84e56ab192d26ad16ce4cb9531b2e38df82dcb58106f707adc214cad869496b2",
  "frontend/scripts/generate-api-surface-contract.mjs":
    "29efaefd7b625f73db6dc27d3c4f7e517f96b6fc3eef2757a5f1c502b5cf8a23",
  "frontend/scripts/generate-openapi-contract.mjs":
    "e8207300139b1606910e292413838e015d875fb26caa7905a416a8884e7ccd77",
};

const sha256 = (text) => createHash("sha256").update(text).digest("hex");
const escapeRe = (text) => text.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");

// 708J published five DSX templates and removed their exceptions. The two
// saved-view templates now belong to GRIDB_MADE_REAL after their GRID claim;
// versions, version detail and baseline remain published but unclaimed.
// A public schema alone is not a claim: these three owners still ship hidden.
// `methods` is the removed exception's inventory; `served` is every HTTP
// method its view source publishes.
const PUBLISHED_UNCLAIMED = [
  {
    template: "/model-hub/datasets/{dataset_id}/versions/",
    methods: ["get", "post"],
    owner: "dataset.versions",
    file: "futureagi/model_hub/views/dataset_versions.py",
    view: "DatasetVersionListView",
    served: {
      get: "    def get(self, request, dataset_id):",
      post: "    def post(self, request: Request, dataset_id: UUID) -> Response:",
    },
  },
  {
    // DSX's N5 PATCH (tag and description) is not served: GET only.
    template: "/model-hub/datasets/{dataset_id}/versions/{version_id}/",
    methods: ["patch"],
    owner: "dataset.versions",
    file: "futureagi/model_hub/views/dataset_versions.py",
    view: "DatasetVersionDetailView",
    served: { get: "    def get(self, request, dataset_id, version_id):" },
  },
  {
    template: "/model-hub/datasets/{dataset_id}/baseline/",
    methods: ["put", "delete"],
    owner: "exp.results.matrix",
    file: "futureagi/model_hub/views/dataset_runs.py",
    view: "DatasetBaselineView",
    served: {
      get: "    def get(self, request: Request, dataset_id: UUID) -> Response:",
      put: "    def put(self, request: Request, dataset_id: UUID) -> Response:",
      delete:
        "    def delete(self, request: Request, dataset_id: UUID) -> Response:",
    },
  },
];

beforeEach(() => {
  machinesCalls.length = 0;
  playground.mockClear();
});
afterEach(() => setLaunchOverrideForTest(null));

describe("L6 absent-path guard", () => {
  it("refuses every DSX prototype path and only those", () => {
    const dsx = Object.entries(API_CONTRACT_EXCEPTIONS).filter(
      ([, m]) => m.reason === "dsx prototype (PRODUCT-SPEC §6.2)",
    );
    // 748d add.4: GRIDB made two templates real; pin the remaining exceptions.
    // 708J then published five templates (two now claimed): 78 -> 73.
    // 70306c2513: agreement uses the generated contract, leaving 72. The row
    // exception has a different reason, so it is outside the prototype count.
    // Soft assertions still fail the test; 713's reverted-hunk arm records
    // each changed assertion instead of stopping at its first count mismatch.
    expect.soft(dsx).toHaveLength(72);
    // The guard inventory is both lists: a template that gains a public
    // schema moves from the exception manifest to CONTRACTED_PROTOTYPE_PATHS
    // (F06 resume) and stays guarded until its slot claims it.
    const inventory = new Set([
      ...dsx.map(([template]) => template),
      ...CONTRACTED_PROTOTYPE_PATHS,
    ]);
    expect.soft(inventory.size).toBe(83);
    expect(
      inventory.has("/model-hub/experiments/v2/{experiment_id}/resume/"),
    ).toBe(true);
    const backed = new Set(Object.values(MADE_REAL_PATHS).flat());
    expect(
      new Set(absentTemplates(API_CONTRACT_EXCEPTIONS, new Set())),
    ).toEqual(
      new Set([
        ...dsx.map(([template]) => template),
        ...CONTRACTED_PROTOTYPE_PATHS,
      ]),
    );
    expect(new Set(absentTemplates())).toEqual(
      new Set(
        [
          ...dsx.map(([template]) => template),
          ...CONTRACTED_PROTOTYPE_PATHS,
        ].filter((t) => !backed.has(t)),
      ),
    );
    // Every slot's claimed path bypasses the guard; unclaimed paths do not.
    for (const template of backed) {
      const url = template.replace(/\{[^}]+\}/g, "fixture-id");
      expect(absentTemplateOf(url), template).toBeNull();
    }
    [
      ["/model-hub/r4/connect/machines/", "/model-hub/r4/connect/machines/"],
      [
        "/model-hub/datasets/d1/runs/?page=1",
        "/model-hub/datasets/{dataset_id}/runs/",
      ],
      [
        "http://localhost:8100/model-hub/eval-templates/create-dag/",
        "/model-hub/eval-templates/create-dag/",
      ],
      // shared-edit(F69): Judge refinement is still a prototype endpoint.
      [
        "/model-hub/eval-templates/t1/improve/",
        "/model-hub/eval-templates/{template_id}/improve/",
      ],
      [
        "/model-hub/eval-templates/t1/composite/jury/",
        "/model-hub/eval-templates/{template_id}/composite/jury/",
      ],
    ].forEach(([url, t]) =>
      expect(absentTemplateOf(url), url).toBe(backed.has(t) ? null : t),
    );
    expect(absentTemplateOf("/model-hub/datasets/d1/runs/?page=1")).toBe(
      "/model-hub/datasets/{dataset_id}/runs/",
    );
    // 748d: Results and expressions are not served by default in int6.
    // These are refusal regressions, not restored allowed-path checks.
    // CONTRACTED_NOT_SERVED === [] must not be restored here; a served
    // configuration needs the Results owner's guard-contract packet (708i).
    [
      "/model-hub/datasets/d1/runs/?page=1",
      "/model-hub/datasets/d1/results/",
      "/model-hub/datasets/d1/results/analysis/",
      "/model-hub/datasets/d1/result-columns/",
      "/model-hub/datasets/d1/result-columns/values/",
      "/model-hub/datasets/d1/derived-metrics/",
      "/model-hub/develops/d1/add_expression_column/",
    ].forEach((url) => expect(absentTemplateOf(url), url).not.toBeNull());
    [
      "/model-hub/develops/get-datasets/",
      "/model-hub/eval-playground/",
      "/model-hub/experiments/v2/",
      "/model-hub/experiments/v2/e1/rows/",
      "/accounts/get-user-profile-details/",
      // shared-edit(F69): The real read endpoint is no longer absent.
      "/model-hub/eval-templates/t1/agreement/",
    ].forEach((url) => expect(absentTemplateOf(url), url).toBeNull());
    expect(apiPathOf("/a/b?x=1")).toBe("/a/b/");
  });

  it("pins the live refused union, including contracted unclaimed jury", () => {
    const exceptions = Object.entries(API_CONTRACT_EXCEPTIONS)
      .filter(([, m]) => m.reason === "dsx prototype (PRODUCT-SPEC §6.2)")
      .map(([template]) => template);
    const backed = new Set(Object.values(MADE_REAL_PATHS).flat());
    // 748d add.2: dsx is the LIVE refused union, not the exception manifest.
    // Keep the 0341046c exact-set/slash block byte-identical below.
    const dsx = [...new Set([...exceptions, ...CONTRACTED_PROTOTYPE_PATHS])]
      .filter((template) => !backed.has(template))
      .map((template) => [template]);
    // 72 exceptions + 11 contracted - 7 backed (including saved views).
    // The default-off row exception is not a prototype and adds no refusal.
    expect.soft(dsx).toHaveLength(76);
    expect(dsx).toContainEqual([
      "/model-hub/eval-templates/{template_id}/composite/jury/",
    ]);
    // Bind the >=76 floor to the accounted total: both accepted saved-view
    // routes move to the made-real matrix, while the accounted total stays 80.
    expect
      .soft({
        meetsFloor: dsx.length >= 76,
        accountedLiveCount: dsx.length + GRIDB_MADE_REAL.length,
      })
      .toEqual({ meetsFloor: true, accountedLiveCount: 80 });
    expect(absentTemplates()).toHaveLength(dsx.length);
    expect(absentTemplates().sort()).toEqual(dsx.map(([path]) => path).sort());
    dsx.forEach(([template]) => {
      const url = template.replace(/\{[^}]+\}/g, "fixture-id");
      for (const path of [
        url.replace(/\/$/, ""),
        `${url.replace(/\/$/, "")}/`,
      ]) {
        expect(absentTemplateOf(path), path).toBe(template);
      }
    });
  });

  it("accounts for every original exception with a refusal or made-real proof", () => {
    const entries = Object.entries(API_CONTRACT_EXCEPTIONS);
    const isDsx = ([, meta]) =>
      meta.reason === "dsx prototype (PRODUCT-SPEC §6.2)";
    // Every int6 exception was DSX. 708J added the gateway console group
    // (340a022c90, 2b077d57c9: agentcc, no methods), which api-surface.test.js
    // owns. Option 2/604 separately allows only the exact default-off row
    // exception and ruled reason; it is not part of the original DSX inventory.
    // Every other non-DSX, non-agentcc exception must still fail here.
    expect(
      entries
        .filter((entry) => !isDsx(entry))
        .filter(
          ([template, meta]) =>
            meta.group !== "agentcc" || !template.startsWith("/agentcc/"),
        )
        .filter(
          ([template, meta]) =>
            !(
              template === "/model-hub/datasets/{dataset_id}/rows/{row_id}/" &&
              meta.group === "model-hub" &&
              meta.reason ===
                "gated by DSX_DATASET_ROW_SOURCE_ENABLED, off by default; not in the public surface while off"
            ),
        )
        .map(([template]) => template),
    ).toEqual([]);
    const exceptions = entries
      .filter(isDsx)
      .map(([template, { methods }]) => [template, [...methods].sort()]);
    const madeReal = GRIDB_MADE_REAL.map(({ template, operations }) => [
      template,
      Object.keys(operations).sort(),
    ]);
    // Accounted by refusal: see "published-unclaimed ... stays refused".
    const published = PUBLISHED_UNCLAIMED.map(({ template, methods }) => [
      template,
      [...methods].sort(),
    ]);
    const accounted = [...exceptions, ...madeReal, ...published].sort(
      ([a], [b]) => (a < b ? -1 : a > b ? 1 : 0),
    );
    expect(accounted).toHaveLength(79);
    expect(new Set(accounted.map(([template]) => template)).size).toBe(79);
    // 450e7c72f1's parent inventory minus agreement (70306c2513, generated
    // contract): 72 prototype + 4 GRID + 3 published-unclaimed templates.
    // The row exception's non-prototype reason keeps it outside this count.
    // Re-derived from these sources; restoring agreement GET yields the old
    // digest. A count alone misses a same-size swap, so retain the exact pin.
    expect(sha256(JSON.stringify(accounted))).toBe(
      "c2ae62bceb6f22fd59322d26b88061bea0aaab63d46e225605eb6ed21b7e0f46",
    );
  });

  it.each(GRIDB_MADE_REAL)(
    "GRID-made-real $template resolves to a served source-contract route",
    ({ template, owner, file, view, operations }) => {
      expect(API_CONTRACT_EXCEPTIONS).not.toHaveProperty(template);
      expect(prototypePaths.owners[template]).toEqual([owner]);
      expect(SURFACE_BY_ID[owner]).toMatchObject({
        ships: "hidden",
        madeRealBy: "GRID",
      });
      expect(MADE_REAL.GRID).toContain(owner);
      if (CONTRACTED_PROTOTYPE_PATHS.includes(template)) {
        expect(MADE_REAL_PATHS.GRID).toContain(template);
      }
      const fixtureId = "11111111-1111-4111-8111-111111111111";
      const url = template.replace(/\{[^}]+\}/g, fixtureId);
      expect(absentTemplateOf(url.slice(0, -1)), template).toBeNull();
      expect(absentTemplateOf(url), template).toBeNull();

      const source = Object.fromEntries(
        Object.entries(GRIDB_SOURCE_PINS).map(([path, digest]) => {
          const text = readFileSync(resolve(process.cwd(), "..", path), "utf8");
          expect(sha256(text), path).toBe(digest);
          return [path, text];
        }),
      );
      expect(source["futureagi/tfc/urls.py"]).toContain(
        'path("model-hub/", include("model_hub.urls"))',
      );
      expect(source["futureagi/tfc/openapi_urls.py"]).toContain(
        "urlpatterns = list(runtime_urlpatterns)",
      );
      expect(source["futureagi/tfc/settings/openapi.py"]).toContain(
        'ROOT_URLCONF = "tfc.openapi_urls"',
      );
      const route = template
        .slice("/model-hub/".length)
        .replace(/\{([^}]+)\}/g, "<uuid:$1>");
      const urls = source["futureagi/model_hub/urls.py"];
      expect(urls.split(`"${route}",`), route).toHaveLength(2);
      expect(urls).toMatch(
        new RegExp(`"${escapeRe(route)}",\\s+${view}\\.as_view\\(\\),`),
      );
      const views = source[file];
      const classHeader = `class ${view}(APIView):\n`;
      expect(views.split(classHeader)).toHaveLength(2);
      const classBody = views.split(classHeader)[1].split("\nclass ")[0];
      expect(Object.keys(operations).length).toBeGreaterThan(0);
      // Preserve the complete served-method census when a published route
      // becomes claimed, and pin each exact validated serializer declaration.
      expect(
        [...classBody.matchAll(/^ {4}def (get|post|put|patch|delete)\(/gm)]
          .map(([, method]) => method)
          .sort(),
      ).toEqual(Object.keys(operations).sort());
      for (const [method, declaration] of Object.entries(operations)) {
        expect(declaration).toContain(`def ${method}(self, request`);
        expect(declaration).toContain("@validated_request(");
        expect(classBody, `${template} ${method}`).toContain(declaration);
      }
      expect(source["futureagi/tfc/utils/api_contracts.py"]).toContain(
        "return swagger_auto_schema(**swagger_options)(wrapper)",
      );
    },
  );

  it.each(PUBLISHED_UNCLAIMED)(
    "published-unclaimed $template has a served schema and a hidden owner",
    ({ template, methods, owner, file, view, served }) => {
      expect(API_CONTRACT_EXCEPTIONS).not.toHaveProperty(template);
      expect(methods.length).toBeGreaterThan(0);
      const read = (path) =>
        readFileSync(resolve(process.cwd(), "..", path), "utf8");
      const urls = read("futureagi/model_hub/urls.py");
      const route = template
        .slice("/model-hub/".length)
        .replace(/\{([^}]+)\}/g, "<uuid:$1>");
      expect(urls.split(`"${route}",`), route).toHaveLength(2);
      expect(urls).toMatch(
        new RegExp(`"${escapeRe(route)}",\\s+${view}\\.as_view\\(\\),`),
      );
      const views = read(file);
      const classHeader = `class ${view}(APIView):\n`;
      expect(views.split(classHeader)).toHaveLength(2);
      const classBody = views.split(classHeader)[1].split("\nclass ")[0];
      // Every HTTP method the view defines, each published by the generator.
      expect(
        [...classBody.matchAll(/^ {4}def (get|post|put|patch|delete)\(/gm)]
          .map(([, method]) => method)
          .sort(),
      ).toEqual(Object.keys(served).sort());
      for (const [method, declaration] of Object.entries(served)) {
        expect(declaration).toContain(`def ${method}(self, request`);
        const at = classBody.indexOf(`${declaration}\n`);
        expect(at, `${template} ${method}`).toBeGreaterThan(-1);
        const head = classBody.slice(0, at);
        expect(
          head.slice(head.lastIndexOf("\n    def ") + 1),
          `${template} ${method}`,
        ).toContain("@validated_request(");
      }
      // Dark: the P0 owner ships hidden and no slot claims it or the path.
      expect(prototypePaths.owners[template]).toEqual([owner]);
      expect(SURFACE_BY_ID[owner]).toMatchObject({ ships: "hidden" });
      expect(Object.values(MADE_REAL).flat()).not.toContain(owner);
      expect(Object.values(MADE_REAL_PATHS).flat()).not.toContain(template);
    },
  );

  it.each(PUBLISHED_UNCLAIMED)(
    "published-unclaimed $template stays refused until its slot claims it",
    ({ template }) => {
      expect.soft(CONTRACTED_PROTOTYPE_PATHS).toContain(template);
      const url = template.replace(/\{[^}]+\}/g, "fixture-id");
      for (const path of [url.slice(0, -1), url]) {
        expect(absentTemplateOf(path), path).toBe(template);
      }
    },
  );

  it("rejects a refused request before the network, 404-shaped", async () => {
    const cfg = { url: "/model-hub/r4/connect/machines/", method: "get" };
    await expect(absentPathInterceptor(cfg)).rejects.toMatchObject({
      launchAbsent: "/model-hub/r4/connect/machines/",
      response: { status: 404 },
    });
    expect(window.__dsxLaunchAbsent.at(-1)).toMatchObject({ method: "GET" });
    const ok = { url: "/model-hub/develops/get-datasets/" };
    expect(absentPathInterceptor(ok)).toBe(ok);
  });
});

describe("L6 gates in shown surfaces", () => {
  it("Run on these rows: absent while dev runs every row, even with experimentsBasic ON", async () => {
    const { default: RunOnRowsButton } = await import(
      "src/sections/common/RunSources/RunOnRowsButton"
    );
    const { QueryClient, QueryClientProvider } = await import(
      "@tanstack/react-query"
    );
    const withQuery = (ui) => (
      <QueryClientProvider client={new QueryClient()}>{ui}</QueryClientProvider>
    );
    const rows = withQuery(
      <RunOnRowsButton
        datasetId="d1"
        rowIds={["r1", "r2"]}
        label="Re-test these rows"
        variant="toastAction"
      />,
    );
    setLaunchOverrideForTest(BASIC);
    const { container, unmount } = render(rows);
    expect(container.innerHTML).toBe("");
    unmount();
    // Positive control: the same flag shows the door that picks no rows.
    render(
      withQuery(
        <RunOnRowsButton
          source="prompt"
          label="Run on a dataset…"
          variant="toastAction"
        />,
      ),
    );
    expect(
      screen.getByRole("button", { name: "Run on a dataset…" }),
    ).toBeTruthy();
    // A prototype build keeps the rows door.
    setLaunchOverrideForTest(null);
    render(rows);
    expect(
      screen.getByRole("button", { name: "Re-test these rows" }),
    ).toBeTruthy();
  });

  // J09 REVISED / 767: preserve the classic prompt-create door, not the kinds menu.
  it("New experiment remains visible in a launch build", async () => {
    const { default: NewExperimentButton } = await import(
      "src/sections/develop-detail/Experiment/NewExperimentButton"
    );
    setLaunchOverrideForTest(LAUNCH);
    const { QueryClient, QueryClientProvider } = await import(
      "@tanstack/react-query"
    );
    const client = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    });
    render(
      <QueryClientProvider client={client}>
        <NewExperimentButton />
      </QueryClientProvider>,
    );
    expect(
      screen.getByRole("button", { name: "New experiment" }),
    ).toBeVisible();
    expect(screen.queryByTestId("new-experiment-kinds")).toBeNull();
    client.clear();
  });

  it("wiz:reads-table renders nothing and never calls eval-playground", async () => {
    const { default: ReadsOnSampleTable } = await import(
      "src/sections/develop-detail/Experiment/StepperComponentExperiment/ReadsOnSampleTable"
    );
    setLaunchOverrideForTest(BASIC);
    const { container } = render(
      <ReadsOnSampleTable
        control={{}}
        sampleRow={{ row_id: "r1" }}
        datasetId="d1"
      />,
    );
    expect(container.innerHTML).toBe("");
    expect(playground).not.toHaveBeenCalled();
  });

  it("r4/connect: no machines query and no poll in a launch build", async () => {
    const { useMachines } = await import(
      "src/sections/common/Connect/useConnect"
    );
    setLaunchOverrideForTest(LAUNCH);
    renderHook(() => useMachines({ enabled: true }));
    expect(machinesCalls.at(-1)).toMatchObject({ enabled: false });
    expect(machinesCalls.at(-1).poll).toBeUndefined();
    setLaunchOverrideForTest(null);
    renderHook(() => useMachines());
    expect(machinesCalls.at(-1)).toMatchObject({ enabled: true, poll: 5000 });
  });

  it("W7: no Decision tree / Remote scorer tab or detail, no Agreement, no jury controls", async () => {
    const { typedTabsOf, hasTypedDetail } = await import(
      "src/sections/evals/types/typedViews"
    );
    const { default: AgreementTab } = await import(
      "src/sections/evals/alignment/AgreementTab"
    );
    const { default: CompositeChildControls } = await import(
      "src/sections/evals/types/CompositeChildControls"
    );
    const types = [
      { value: "dag", label: "Decision tree" },
      { value: "remote", label: "Remote scorer" },
    ];
    setLaunchOverrideForTest(LAUNCH);
    expect(typedTabsOf(types)).toEqual([]);
    expect(hasTypedDetail("dag")).toBe(false);
    expect(hasTypedDetail("remote")).toBe(false);
    expect(AgreementTab.ready).toBe(false);
    const { container } = render(
      <CompositeChildControls
        child={{ child_id: "c1", kind: "eval", seat: 1 }}
        rows={[]}
        onChildrenChange={() => {}}
      />,
    );
    expect(container.innerHTML).toBe("");
    // A prototype build keeps them where their editors are real.
    setLaunchOverrideForTest(null);
    expect(typedTabsOf(types).map((t) => t.value)).toContain("remote");
  });

  it("Copy request id shows only for an id the browser can read", async () => {
    const { default: ErrorState } = await import(
      "src/sections/common/ux/ErrorState"
    );
    setLaunchOverrideForTest(LAUNCH);
    const { unmount } = render(<ErrorState error={{ requestId: "body-id" }} />);
    expect(screen.queryByText("Request id")).toBeNull();
    unmount();
    setLaunchOverrideForTest({ ...LAUNCH, madeReal: ["errors.requestId"] });
    render(
      <ErrorState
        error={{ response: { headers: { "x-request-id": "hdr-1" } } }}
      />,
    );
    expect(screen.getByText("Request id")).toBeTruthy();
  });

  it("?add=production does nothing in a launch build", async () => {
    const { addParamAction } = await import(
      "src/sections/develop-detail/DataTab/dataAccessGate"
    );
    const args = {
      addParam: "production",
      datasetId: "d1",
      can: { edit: true },
    };
    setLaunchOverrideForTest(LAUNCH);
    expect(addParamAction(args)).toBeNull();
    setLaunchOverrideForTest(null);
    expect(addParamAction(args)).toMatchObject({ kind: "open" });
  });

  it("the palette drops hidden verbs, hidden links and hidden recents", async () => {
    const { registeredCommands, launchCommandShown } = await import(
      "src/sections/common/CommandPalette/commandRegistry"
    );
    setLaunchOverrideForTest(LAUNCH);
    const sources = {
      header: [
        {
          id: "dataset.newExperiment",
          label: "Run an experiment on this dataset",
        },
        { id: "dataset.auditLog", label: "Open the dataset's audit log" },
        { id: "dataset.copyId", label: "Copy dataset id" },
        { id: "dataset.share", label: "Share" },
      ],
      runs: [
        {
          id: "run.r1",
          label: "#1A",
          href: "/dashboard/develop/experiment/e1/data",
        },
        {
          id: "go.leaderboards",
          label: "Go to Leaderboards",
          href: "/dashboard/develop/leaderboards",
        },
        {
          id: "x.addProd",
          label: "Add",
          href: "/dashboard/develop/d1?tab=data&add=production",
        },
      ],
    };
    expect(registeredCommands(sources).map((c) => c.id)).toEqual([
      "dataset.copyId",
    ]);
    expect(launchCommandShown({ id: "help.glossary" })).toBe(false);
    expect(launchCommandShown({ id: "help.shortcuts" })).toBe(true);
    setLaunchOverrideForTest(null);
    expect(registeredCommands(sources)).toHaveLength(7);
  });

  it("shortcuts: the sheet and the key handler skip hidden actions", async () => {
    const { actionForEvent, shortcutRows } = await import("src/utils/keymap");
    setLaunchOverrideForTest(LAUNCH);
    expect(actionForEvent({ key: "h" }, "grid")).toBeNull();
    expect(actionForEvent({ key: "f" }, "grid")).toBe("filter");
    expect(shortcutRows("results")).toEqual([]);
    expect(shortcutRows("leaderboard")).toEqual([]);
    expect(shortcutRows("grid").map((r) => r.action)).not.toContain("history");
    expect(shortcutRows("row").map((r) => r.action)).not.toContain(
      "saveVersion",
    );
    setLaunchOverrideForTest(null);
    expect(actionForEvent({ key: "h" }, "grid")).toBe("history");
  });

  it("the shortcut sheet has no Glossary tab and is not named for one", async () => {
    const { default: ShortcutSheet } = await import(
      "src/sections/common/RowPanel/ShortcutSheet"
    );
    setLaunchOverrideForTest(LAUNCH);
    const launch = render(
      <ShortcutSheet open onClose={() => {}} surface="grid" tab="glossary" />,
    );
    const dialog = screen.getByRole("dialog");
    expect(dialog).toHaveAccessibleName(/Keyboard shortcuts/);
    expect(dialog).not.toHaveAccessibleName(/glossary/i);
    expect(screen.queryByRole("tab", { name: "Glossary" })).toBeNull();
    expect(screen.queryByText("Version history")).toBeNull();
    launch.unmount();
    setLaunchOverrideForTest(null);
    render(
      <ShortcutSheet open onClose={() => {}} surface="grid" tab="shortcuts" />,
    );
    expect(screen.getByRole("tab", { name: "Glossary" })).toBeInTheDocument();
  });

  it("the source picker offers Prompt only, and its copy says so", async () => {
    const catalog = await import("src/sections/common/RunSources/catalog");
    setLaunchOverrideForTest(BASIC);
    expect(catalog.cardsForKind("rows").map((c) => c.id)).toEqual(["prompt"]);
    expect(catalog.STEP1_COPY.disambiguation).not.toMatch(/A2A|MCP|endpoint/);
    expect(catalog.STEP1_COPY.searchPlaceholder(9)).toBe(
      "Search prompts and models",
    );
    expect(catalog.stepWayNames()).toEqual(["Prompt"]);
    expect(catalog.searchCatalog("http")).toEqual([]);
    expect(catalog.kindRecommendation("task_suite").reason).not.toMatch(
      /agent graph/,
    );
    setLaunchOverrideForTest(null);
    expect(catalog.cardsForKind("rows").map((c) => c.id)).toEqual([
      "prompt",
      "agent",
      "running",
    ]);
  });

  it("the cell menu has no comment, assign, translate or split items", async () => {
    const { buildCellContextMenu } = await import(
      "src/sections/develop-detail/DataTab/DataGrid/gridContextMenu"
    );
    const noop = () => {};
    const handlers = {
      filterTo: noop,
      exclude: noop,
      showAllIn: noop,
      openRow: noop,
      copyValue: noop,
      copyRowLink: noop,
      addToQueue: noop,
      toggleSplit: noop,
      manageSplits: noop,
      commentOnRow: noop,
      assignRow: noop,
      translateCell: noop,
    };
    const params = {
      value: "hello there",
      node: { data: { row_id: "r1", row_meta: {} } },
      column: {
        getColId: () => "c1",
        getColDef: () => ({ col: { id: "c1" }, dataType: "text" }),
      },
    };
    const names = () =>
      buildCellContextMenu(params, {
        splits: [{ id: "s1", name: "hard" }],
        handlers,
      })
        .filter((i) => i !== "separator")
        .map((i) => i.name);
    setLaunchOverrideForTest(LAUNCH);
    const launch = names();
    ["Comment on row", "Assign row to…", "Assign split"].forEach((n) =>
      expect(launch, n).not.toContain(n),
    );
    expect(launch.some((n) => /^Translate/.test(n))).toBe(false);
    expect(launch).toContain("Open row");
    expect(launch).toContain("Copy row link");
    setLaunchOverrideForTest(null);
    const proto = names();
    ["Comment on row", "Assign row to…", "Assign split"].forEach((n) =>
      expect(proto, n).toContain(n),
    );
  });

  it("the grid footer never points at hidden experiments", async () => {
    const { dataHelpText, dataFooterParts } = await import(
      "src/sections/develop-detail/DataTab/DataGrid/dataHelp"
    );
    setLaunchOverrideForTest(LAUNCH);
    const help = dataHelpText({ kind: "rows", total: 3, columns: [] });
    expect(help.lead).toBe("3 rows.");
    expect(JSON.stringify(help)).not.toMatch(/experiment/i);
    expect(dataFooterParts({ kind: "rows", total: 3, columns: [] }).each).toBe(
      "",
    );
    setLaunchOverrideForTest(BASIC);
    expect(dataHelpText({ kind: "rows", total: 3, columns: [] }).lead).toMatch(
      /to run experiments/,
    );
  });

  it("the Row view, its header and the toolbar read the switch for each door", async () => {
    const read = (p) => readFileSync(resolve(process.cwd(), p), "utf8");
    const dir = "src/sections/develop-detail/";
    const rowView = read(`${dir}DataTab/DatapointDrawerV2/RowView.jsx`);
    expect(rowView).toMatch(/isSurfaceShown\("dataset\.rowExtras\.runs"\)/);
    expect(rowView).toMatch(/\.filter\(\(t\) => isRowTabShown\(t\.value\)\)/);
    const header = read(`${dir}DataTab/DatapointDrawerV2/RowViewHeader.jsx`);
    expect(header).toMatch(
      /canEdit && isSurfaceShown\("dataset\.splits"\) && \(\s*<AddSplitChip/,
    );
    expect(header).toMatch(
      /isSurfaceShown\("dataset\.collab\.comments"\) \|\|\s*isSurfaceShown\("dataset\.collab\.assignments"\)\) && \(\s*<RowCollabPills/,
    );
    const left = read(`${dir}DevelopBarRightSection/DevelopBarLeftSection.jsx`);
    expect(left).toMatch(
      /isSurfaceShown\("dataset\.filtersSystem\.expressions"\) &&\s*!hideFilter &&\s*!disabledAll && \(\s*<ExpressionFilterButton/,
    );
    const right = read(
      `${dir}DevelopBarRightSection/DevelopDataRightSection.jsx`,
    );
    expect(right).toMatch(/savedViewsShown\s*\?/);
    expect(right).toMatch(/\.\.\.\(addRowsDoor/);
    const sheet = read("src/sections/develop/AddRowDrawer/AddRowDrawer.jsx");
    expect(sheet).toMatch(/addRowsTiles\(/);
  });

  it("the estimate method text matches dev (one try per row, no endpoint caps)", async () => {
    const { estimateHelpOf } = await import(
      "src/sections/develop-detail/Experiment/costEstimateModel"
    );
    setLaunchOverrideForTest(BASIC);
    const text = estimateHelpOf({ promptConfig: [] }).join("\n");
    expect(text).toMatch(/each run tries every row once/);
    expect(text).not.toMatch(/A2A|MCP|cost cap|Trials/);
  });
});
