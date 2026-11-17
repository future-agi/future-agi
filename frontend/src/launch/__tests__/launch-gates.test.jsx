import React from "react";
import { createHash } from "node:crypto";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import process from "node:process";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { useForm } from "react-hook-form";
import { render, renderHook, screen } from "src/utils/test-utils";
import { API_CONTRACT_EXCEPTIONS } from "src/api/contracts/api-contract-exceptions";
import {
  getContractedApiMethods,
  isContractedApiPath,
} from "src/api/contracts/api-surface";
import prototypePaths from "../__fixtures__/registry-prototype-paths-705.json";
import { SURFACE_BY_ID } from "../launchSurfaces";
import { setLaunchOverrideForTest } from "../launchSwitch";
import { MADE_REAL, MADE_REAL_PATHS } from "../madeReal";
import { LAUNCH_HOLD_ROUTES } from "../launchHoldRoutes";
import {
  absentPathInterceptor,
  absentTemplateOf,
  absentTemplates,
  apiPathOf,
  CONTRACTED_PROTOTYPE_PATHS,
} from "../absentPathGuard";

// Launch-build tests run with the show-unreleased switch off, in the override
// (launchSwitch reads it) and in the export the product modules read.
const unreleased = vi.hoisted(() => ({ shown: false }));
vi.mock("../launchSwitch", async (importOriginal) => {
  const actual = await importOriginal();
  return {
    ...actual,
    get SHOW_UNRELEASED_PARTS() {
      return unreleased.shown;
    },
  };
});

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

// The jury controls' judge picker reads models and keys through its own
// providers. The shipped-state check renders the controls around a stand-in;
// with the switch off the controls return before the picker renders. The
// module's other exports stay real for the modules that import them.
vi.mock(
  "src/sections/evals/components/ModelSelector",
  async (importOriginal) => ({
    ...(await importOriginal()),
    default: () => <div data-testid="judge-model-picker" />,
  }),
);

const LAUNCH = {
  launch: true,
  showUnreleased: false,
  experimentsBasic: false,
  madeReal: [],
};
const BASIC = {
  launch: true,
  showUnreleased: false,
  experimentsBasic: true,
  madeReal: [],
};
// As shipped: the same launch builds with the show-unreleased switch on (the
// override leaves it to the build's own switch, and the export reads true).
const SHIPPED = { launch: true, experimentsBasic: false, madeReal: [] };
const SHIPPED_BASIC = { launch: true, experimentsBasic: true, madeReal: [] };

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

// Templates a slot claim backs that left the prototype exceptions for the
// generated contract: the slot claims the owner and the path, so the guard
// does not refuse them, and the backend serves the route.
// `slot` is the claim's group in madeReal.js.
const CONTRACTED_MADE_REAL = [
  // DSX-WALK-RES-21: actual contracted templates, not the P0 target alias.
  {
    template: "/model-hub/datasets/{dataset_id}/rows/from-results/",
    prototypeTemplate:
      "/model-hub/datasets/{target_dataset_id}/rows/from-results/",
    owner: "exp.results.addToDataset",
    slot: "RES",
    view: "ResultsToDatasetView",
    file: "futureagi/model_hub/views/results_to_dataset.py",
    methods: ["post"],
    copyOperation: "dataset_rows_from_results",
  },
  {
    template: "/model-hub/datasets/new/rows/from-results/",
    prototypeTemplate:
      "/model-hub/datasets/{target_dataset_id}/rows/from-results/",
    owner: "exp.results.addToDataset",
    slot: "RES",
    view: "NewResultsToDatasetView",
    parent: "ResultsToDatasetView",
    file: "futureagi/model_hub/views/results_to_dataset.py",
    methods: ["post"],
    copyOperation: "dataset_new_rows_from_results",
  },
  {
    template: "/model-hub/datasets/{dataset_id}/results/",
    owner: "exp.results.addToDataset",
    prototypeOwner: "exp.results.matrix",
    slot: "RES",
    view: "DatasetResultsView",
    file: "futureagi/model_hub/views/dataset_results.py",
    methods: ["get"],
  },
  // DSX-WALK-RES-51: Summary analysis and both hosts' run/config reads.
  {
    template: "/model-hub/datasets/{dataset_id}/results/analysis/",
    owner: "exp.results.summary",
    slot: "RES",
    view: "ResultsAnalysisView",
    file: "futureagi/model_hub/views/results_analysis.py",
    methods: ["get"],
  },
  {
    template: "/model-hub/datasets/{dataset_id}/runs/",
    owner: "exp.results.summary",
    prototypeOwner: "palette.runs",
    slot: "RES",
    view: "ResultsRunsView",
    file: "futureagi/model_hub/views/results_runs.py",
    methods: ["get"],
  },
  {
    template: "/model-hub/datasets/{dataset_id}/rows/{row_id}/runs/",
    owner: "dataset.rowExtras.runs",
    slot: "RES",
    view: "DatasetRowRunsView",
    file: "futureagi/model_hub/views/row_runs.py",
    methods: ["get"],
  },
  // EXP-E2E-010: the workspace tag registry and an experiment's tags.
  {
    template: "/model-hub/tags/",
    owner: "dataset.collab.tags",
    slot: "COLLAB",
    view: "WorkspaceTagsView",
    file: "futureagi/model_hub/views/workspace_tags.py",
    methods: ["get", "post"],
  },
  {
    template: "/model-hub/tags/{tag_id}/",
    owner: "dataset.collab.tags",
    slot: "COLLAB",
    view: "WorkspaceTagDetailView",
    file: "futureagi/model_hub/views/workspace_tags.py",
    methods: ["patch", "delete"],
  },
  {
    template: "/model-hub/experiments/v2/{experiment_id}/tags/",
    owner: "dataset.collab.tags",
    slot: "COLLAB",
    view: "ExperimentTagsView",
    file: "futureagi/model_hub/views/workspace_tags.py",
    methods: ["put"],
  },
  // DSX-WALK-ORG-55: personal dataset stars already use the contract.
  {
    template: "/model-hub/datasets/{dataset_id}/star/",
    owner: "list.organize.star",
    slot: "ORG",
    view: "DatasetStarView",
    file: "futureagi/model_hub/views/dataset_stars.py",
    methods: ["post", "delete"],
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
// - tfc/urls.py (c2.6: the c2.5 includes omega-hub/, omega-rollout/, webhooks/,
//   the "" agentcc.urls_mcp_wellknown mount and webhooks/ops/ are kept (main
//   6982d475), and upstream api/host-update-status/ is added after model-hub/,
//   which still resolves first): includes added after model-hub/ (340a022c90, 707d36ad53, via
//   458296b468), so model-hub/ still resolves first.
// - tfc/utils/api_contracts.py: 3defdf30fb (via 8381dd4cac) wraps only an
//   inferred 2xx; every split operation declares its 200, so stays as is.
//   The dev merge then adds EncodedInputDefaultInspector ahead of the default
//   field inspectors. It only rewrites the documented default of a string
//   field that declares swagger_empty_input_default; no split or saved-view
//   serializer or view declares one, so the four routes keep their schemas.
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
  // R6-TESTFIX: d10ac7b614ef adds only Results imports/routes; all four GRID
  // routes remain served and unshadowed. Per-route proof: PIN-PROOF-c.json.
  // Re-reviewed with the row runs read: it adds one import and one route,
  // datasets/<uuid:dataset_id>/rows/<uuid:row_id>/runs/, after the last
  // block. The four GRID routes' lines are unchanged and nothing added can
  // match them.
  // Re-reviewed with the tag routes (EXP-E2E-010): one T-COLLAB block after
  // the last block, importing three views from model_hub.views.workspace_tags
  // and adding tags/, tags/<uuid:tag_id>/ and
  // experiments/v2/<uuid:experiment_id>/tags/. No line above it changes, and
  // none of the three can match splits/ or r3/saved-views/.
  // Re-reviewed on the FA t35 line (DSX train 14eb200b): 23 added lines,
  // none removed. Three imports (PairwisePreferenceView,
  // ExternalRunEvaluationView, ExperimentCISummaryView) and four routes:
  // experiment-runs/<uuid:run_id>/evaluate/, experiments/v2/<uuid:experiment_id>/
  // pairwise/, .../pairwise/summary/ and .../ci-summary/. No existing line
  // changes, and none of the four can match splits/ or r3/saved-views/.
  // EVAL-E2E-083 adds only the scoped eval-template improve route and import;
  // it cannot shadow splits, saved views, or the existing agreement read.
  // Re-reviewed with the remote evaluator routes (REMOTE-E2E-001): one block
  // after T-EVALB, importing five views from model_hub.views.remote_eval and
  // adding five eval-templates/ routes. No line above it changes, and none of
  // the five can match splits/ or r3/saved-views/.
  // Re-pinned for the three pairwise judge imports and create/list,
  // detail and cancel routes after the summary evaluator routes; none of
  // these additions can match splits/ or r3/saved-views/.
  // The CI comment adds only its read-only sibling route and view import.
  "futureagi/model_hub/urls.py":
    "d39f0b209a9377147d4d82b53470157f112622f78c5cff93204aa92a229bb980",
  "futureagi/model_hub/views/dataset_splits.py":
    "71c8afd990543c1ee5f7eaa0039e17775eb08f16938c48a3e6c1089d3625f9f7",
  "futureagi/model_hub/serializers/dataset_splits.py":
    "a925b4c47620099aad7fab8ad4cf78c4bbdb56f2cc9211f067c1da0f27807540",
  "futureagi/tfc/utils/api_contracts.py":
    "5e108da7aa05363bdbfc651e3d4f3e6a8cbd0fc87b9561295eaefa3c472caa70",
  "futureagi/tfc/urls.py":
    "8d6c933c1523c3f5cfd650246d21d9fd4ca2896603ed57493d7194d14d4ff2de",
  "futureagi/tfc/openapi_urls.py":
    "50fa207ec3df9a6d264a3d3fb71926d2968209809b98a2b3be9943ffdaf3feaa",
  // ROWAPI-EXC-r3b: re-verified 509636133e -> 62433e2695 hunk by hunk.
  // Import ordering, predicate formatting and F44 list metadata preserve the
  // split contracts and ROOT_URLCONF; no split serializer reads the F44 flag.
  "futureagi/tfc/settings/openapi.py":
    "de9210b1b16b2ff3865fac1a2440bd69364de046870ae02762586051d9544e7d",
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
    // DSX's N5 PATCH (tag and description) is served now (GAP-VTAGS); the
    // owner still ships hidden until its slot claims it.
    template: "/model-hub/datasets/{dataset_id}/versions/{version_id}/",
    methods: ["patch"],
    owner: "dataset.versions",
    file: "futureagi/model_hub/views/dataset_versions.py",
    view: "DatasetVersionDetailView",
    served: {
      get: "    def get(self, request, dataset_id, version_id):",
      patch: "    def patch(self, request, dataset_id, version_id):",
    },
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
    // W96B moves the three Results GETs to explicit holds: 72 -> 69 exceptions.
    // Soft assertions still fail the test; 713's reverted-hunk arm records
    // each changed assertion instead of stopping at its first count mismatch.
    // The decision-model listing is now in the contract and moves to
    // CONTRACTED_PROTOTYPE_PATHS: 67 -> 66 exceptions, the inventory stays 83.
    // The row runs read is in the contract and claimed, so it leaves the
    // exceptions and the inventory, as the made-real matrix's templates did:
    // 66 -> 65 exceptions, 83 -> 82.
    // The three tag paths (EXP-E2E-010) did the same: 65 -> 62, 82 -> 79.
    expect.soft(dsx).toHaveLength(62);
    // The inventory includes exceptions, contracted prototypes and explicit
    // holds. The three accepted Results GETs leave the hold inventory:
    // 79 -> 76. Copy is already a contracted prototype; the new target's
    // literal route was outside this union. No other path leaves it.
    const inventory = new Set([
      ...dsx.map(([template]) => template),
      ...CONTRACTED_PROTOTYPE_PATHS,
      ...LAUNCH_HOLD_ROUTES,
    ]);
    expect.soft(inventory.size).toBe(76);
    expect(
      inventory.has("/model-hub/experiments/v2/{experiment_id}/resume/"),
    ).toBe(true);
    const backed = new Set(Object.values(MADE_REAL_PATHS).flat());
    // CUT D's 12, plus the agreement read (EVAL-E2E-080) and the five claimed
    // Results paths. Improve (EVAL-E2E-083) and the five remote evaluator
    // paths (REMOTE-E2E-001) stay refused until their walks pass (t37).
    expect(backed.size).toBe(18);
    expect(
      new Set(absentTemplates(API_CONTRACT_EXCEPTIONS, new Set())),
    ).toEqual(
      new Set([
        ...dsx.map(([template]) => template),
        ...CONTRACTED_PROTOTYPE_PATHS,
        ...LAUNCH_HOLD_ROUTES,
      ]),
    );
    expect(new Set(absentTemplates())).toEqual(
      new Set([
        ...[
          ...dsx.map(([template]) => template),
          ...CONTRACTED_PROTOTYPE_PATHS,
        ].filter((t) => !backed.has(t)),
        ...LAUNCH_HOLD_ROUTES,
      ]),
    );
    // Every currently claimed path bypasses the guard; unclaimed paths do not.
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
    // Only the accepted Results reads and copies are released. Unsupported
    // Summary reads and baseline/Iterate mutations remain refused.
    [
      "/model-hub/datasets/d1/baseline/",
      "/model-hub/datasets/d1/classification/",
      "/model-hub/experiments/v2/e1/runs/",
      "/model-hub/datasets/d1/result-columns/",
      "/model-hub/datasets/d1/result-columns/values/",
      "/model-hub/datasets/d1/derived-metrics/",
      "/model-hub/develops/d1/add_expression_column/",
    ].forEach((url) => expect(absentTemplateOf(url), url).not.toBeNull());
    [
      "/model-hub/datasets/d1/runs/?page=1",
      "/model-hub/datasets/d1/results/",
      "/model-hub/datasets/d1/results/analysis/",
      "/model-hub/datasets/d1/rows/from-results/",
      "/model-hub/datasets/new/rows/from-results/",
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
    const dsx = [
      ...new Set([
        ...[...exceptions, ...CONTRACTED_PROTOTYPE_PATHS].filter(
          (template) => !backed.has(template),
        ),
        ...LAUNCH_HOLD_ROUTES,
      ]),
    ].map((template) => [template]);
    // 62 exceptions + 14 contracted - 7 backed + 3 explicit Results holds.
    // The contracted row read is not a prototype and adds no refusal.
    // The three tag paths left the exceptions for the generated contract and
    // are accounted by their made-real proof (CONTRACTED_MADE_REAL): 75 -> 72.
    // Star was already outside this union, so its claim keeps 72 refused.
    // The claimed Results owners move three GETs and the existing-target copy
    // out of the refused set (the literal new-target copy adds a served
    // proof, not another refusal): 72 -> 68. Improve (EVAL-E2E-083) and the
    // five remote evaluator paths (REMOTE-E2E-001) stay listed as exceptions
    // and stay refused until their walks pass (main ruling t37).
    const improve = "/model-hub/eval-templates/{template_id}/improve/";
    expect(backed.has(improve)).toBe(false);
    const remote = [
      "/model-hub/eval-templates/create-remote/",
      "/model-hub/eval-templates/{template_id}/remote/",
      "/model-hub/eval-templates/remote/test/",
      "/model-hub/eval-templates/{template_id}/remote/score-run/",
      "/model-hub/eval-templates/remote/jobs/{job_id}/",
    ];
    expect(remote.filter((template) => backed.has(template))).toEqual([]);
    expect.soft(dsx).toHaveLength(68);
    expect(dsx).toContainEqual([
      "/model-hub/eval-templates/{template_id}/composite/jury/",
    ]);
    // Bind the >=72 floor to the accounted total: both accepted saved-view
    // routes move to the made-real matrix and the row runs read and the three
    // tag paths to the contracted made-real list. Star adds one served
    // proof without removing a refusal, and the Results claims add four
    // served proofs plus the literal new-target copy. Improve and the five
    // remote paths stay refusals: 68 + 9 + 5 + 0 + 0 = 82.
    expect
      .soft({
        meetsFloor: dsx.length >= 62,
        accountedLiveCount:
          dsx.length +
          GRIDB_MADE_REAL.length +
          CONTRACTED_MADE_REAL.length +
          Number(backed.has(improve)) +
          remote.filter((template) => backed.has(template)).length,
      })
      .toEqual({ meetsFloor: true, accountedLiveCount: 82 });
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
    // The gateway console group and the two bulk-export routes are covered
    // in api-surface.test.js.
    // The row read now belongs to the generated contract, not this manifest.
    // Every other non-prototype, non-gateway exception must still fail here.
    expect(
      entries
        .filter((entry) => !isDsx(entry))
        .filter(
          ([template, meta]) =>
            (meta.group !== "agentcc" || !template.startsWith("/agentcc/")) &&
            (meta.group !== "tracer" ||
              !template.startsWith("/tracer/bulk-exports/")),
        )
        .map(([template]) => template),
    ).toEqual([]);
    const exceptions = entries
      .filter(isDsx)
      .map(([template, { methods }]) => [template, [...methods].sort()]);
    const madeReal = [
      ...GRIDB_MADE_REAL.map(({ template, operations }) => [
        template,
        Object.keys(operations).sort(),
      ]),
      ...CONTRACTED_MADE_REAL.map(({ template, methods }) => [
        template,
        [...methods].sort(),
      ]),
    ];
    // Accounted by refusal: see "published-unclaimed ... stays refused".
    const published = PUBLISHED_UNCLAIMED.map(({ template, methods }) => [
      template,
      [...methods].sort(),
    ]);
    const held = LAUNCH_HOLD_ROUTES.map((template) => [template, ["get"]]);
    const accounted = [...exceptions, ...madeReal, ...published, ...held].sort(
      ([a], [b]) => (a < b ? -1 : a > b ? 1 : 0),
    );
    expect(accounted).toHaveLength(79);
    expect(new Set(accounted.map(([template]) => template)).size).toBe(79);
    // 450e7c72f1's parent inventory minus agreement (70306c2513, generated
    // contract): 66 prototype + 4 GRID + 3 published-unclaimed + 3 held GETs.
    // The contracted row read stays outside this prototype inventory.
    // The decision-model listing left the exceptions for the contract (the
    // guard refuses it through CONTRACTED_PROTOTYPE_PATHS): 77 -> 76.
    // The row runs read left the exceptions for the contract and is accounted
    // by its made-real proof instead (65 prototype + 5 made real), so the
    // total and the digest stay. The three tag paths did the same with the
    // same methods (62 prototype + 8 made real): total and digest stay.
    // Star adds its existing POST/DELETE contract to the served proofs:
    // 62 prototype + 9 made real + 3 published + 3 held = 77.
    // The three Results GETs move from holds to made-real, unchanged.
    // Both copy POST templates add served proofs: 62 + 14 + 3 = 79.
    // A count alone misses a same-size swap, so retain the exact pin.
    expect(sha256(JSON.stringify(accounted))).toBe(
      "e9f0694bad7ceb47760832d4607b1ffd24a20b7a68a50d151f913f0638b3866b",
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

  it.each(CONTRACTED_MADE_REAL)(
    "contracted made-real $template is claimed with its owner, served and not refused",
    ({
      template,
      owner,
      slot,
      view,
      file,
      methods,
      prototypeTemplate = template,
      prototypeOwner = owner,
      parent = "APIView",
      copyOperation,
    }) => {
      expect(API_CONTRACT_EXCEPTIONS).not.toHaveProperty(template);
      expect(isContractedApiPath(template)).toBe(true);
      expect([...getContractedApiMethods(template)].sort()).toEqual(
        [...methods].sort(),
      );
      expect(prototypePaths.owners[prototypeTemplate]).toEqual([
        prototypeOwner,
      ]);
      expect(SURFACE_BY_ID[owner]).toMatchObject({
        ships: "hidden",
        madeRealBy: slot,
      });
      expect(MADE_REAL[slot]).toContain(owner);
      expect(MADE_REAL_PATHS[slot]).toContain(template);
      const fixtureId = "11111111-1111-4111-8111-111111111111";
      const url = template.replace(/\{[^}]+\}/g, fixtureId);
      expect(absentTemplateOf(url.slice(0, -1)), template).toBeNull();
      expect(absentTemplateOf(url), template).toBeNull();
      expect(absentTemplates()).not.toContain(template);
      const read = (path) =>
        readFileSync(resolve(process.cwd(), "..", path), "utf8");
      const route = template
        .slice("/model-hub/".length)
        .replace(/\{([^}]+)\}/g, "<uuid:$1>");
      const urls = read("futureagi/model_hub/urls.py");
      expect(urls.split(`"${route}",`), route).toHaveLength(2);
      expect(urls).toMatch(
        new RegExp(`"${escapeRe(route)}",\\s+${view}\\.as_view\\(\\),`),
      );
      const views = read(file);
      const classHeader = `class ${view}(${parent}):\n`;
      expect(views.split(classHeader)).toHaveLength(2);
      const classBody = views.split(classHeader)[1].split("\nclass ")[0];
      expect(
        [...classBody.matchAll(/^ {4}def (get|post|put|patch|delete)\(/gm)]
          .map(([, method]) => method)
          .sort(),
      ).toEqual([...methods].sort());
      if (copyOperation) {
        expect(classBody).toContain(`@_copy_contract("${copyOperation}")`);
        const decorator = views
          .split("def _copy_contract(operation_id):\n")[1]
          .split("\nclass ")[0];
        expect(decorator).toContain("return validated_request(");
        expect(decorator).toContain("operation_id=operation_id,");
        expect(decorator).toContain("reject_unknown_fields=True,");
        expect(classBody).toContain(
          view === "NewResultsToDatasetView"
            ? 'return self._copy(request, "new")'
            : "return self._copy(request, dataset_id)",
        );
      } else {
        expect(classBody).toContain("@validated_request(");
      }
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

// The same surfaces as the release ships them: the launch override is set
// without showUnreleased (so the build's switch, on, decides) and the switch
// export reads true. Each surface shows. A part this release does not build
// says so in the words its source gives.
describe("gates in shown surfaces as shipped (unreleased parts shown)", () => {
  beforeEach(() => {
    unreleased.shown = true;
  });
  afterEach(() => {
    unreleased.shown = false;
  });

  it("Run on these rows shows its rows door with experimentsBasic ON", async () => {
    const { default: RunOnRowsButton } = await import(
      "src/sections/common/RunSources/RunOnRowsButton"
    );
    const { QueryClient, QueryClientProvider } = await import(
      "@tanstack/react-query"
    );
    setLaunchOverrideForTest(SHIPPED_BASIC);
    render(
      <QueryClientProvider client={new QueryClient()}>
        <RunOnRowsButton
          datasetId="d1"
          rowIds={["r1", "r2"]}
          label="Re-test these rows"
          variant="toastAction"
        />
      </QueryClientProvider>,
    );
    // Running on picked rows is claimed in madeReal.js (exp.new.scope).
    expect(
      screen.getByRole("button", { name: "Re-test these rows" }),
    ).toBeVisible();
  });

  it("New experiment shows the kinds menu toggle next to it", async () => {
    const { default: NewExperimentButton } = await import(
      "src/sections/develop-detail/Experiment/NewExperimentButton"
    );
    setLaunchOverrideForTest(SHIPPED);
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
    // The kinds menu is claimed in madeReal.js (exp.new.kindMenu).
    expect(screen.getByTestId("new-experiment-kinds")).toBeVisible();
    client.clear();
  });

  it("the reads table shows what each evaluation reads on the sample row", async () => {
    const { default: ReadsOnSampleTable } = await import(
      "src/sections/develop-detail/Experiment/StepperComponentExperiment/ReadsOnSampleTable"
    );
    const { QueryClient, QueryClientProvider } = await import(
      "@tanstack/react-query"
    );
    // One evaluation with no template picked yet: its saved-input read stays
    // off, so the table draws with no request.
    const Harness = () => {
      const { control } = useForm({
        defaultValues: {
          promptConfig: [],
          userEvalMetrics: [{ id: "e1", name: "Tone check" }],
          experimentType: "llm",
        },
      });
      return (
        <ReadsOnSampleTable
          control={control}
          sampleRow={{ row_id: "r1" }}
          datasetId="d1"
        />
      );
    };
    setLaunchOverrideForTest(SHIPPED_BASIC);
    render(
      <QueryClientProvider client={new QueryClient()}>
        <Harness />
      </QueryClientProvider>,
    );
    expect(screen.getByTestId("reads-on-sample")).toBeVisible();
    expect(screen.getByText("What each run and eval reads")).toBeVisible();
    expect(screen.getByTestId("reads-eval")).toHaveTextContent("Tone check");
  });

  it("the connected machines query stays off: this build does not serve its path", async () => {
    const { useMachines } = await import(
      "src/sections/common/Connect/useConnect"
    );
    setLaunchOverrideForTest(SHIPPED);
    renderHook(() => useMachines({ enabled: true }));
    expect(machinesCalls.at(-1)).toMatchObject({ enabled: false });
    expect(machinesCalls.at(-1).poll).toBeUndefined();
  });

  it("Decision tree, Remote scorer, Agreement and the jury controls show", async () => {
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
    setLaunchOverrideForTest(SHIPPED);
    expect(typedTabsOf(types).map((t) => t.value)).toEqual(["dag", "remote"]);
    expect(hasTypedDetail("dag")).toBe(true);
    expect(hasTypedDetail("remote")).toBe(true);
    expect(AgreementTab.ready).toBe(true);
    render(
      <CompositeChildControls
        child={{ child_id: "c1", kind: "eval", seat: 1 }}
        rows={[]}
        onChildrenChange={() => {}}
      />,
    );
    expect(screen.getByTestId("composite-child-controls")).toBeVisible();
    expect(screen.getByRole("button", { name: "Add judge" })).toBeVisible();
  });

  it("a link with add=production opens the production sheet", async () => {
    const { addParamAction } = await import(
      "src/sections/develop-detail/DataTab/dataAccessGate"
    );
    setLaunchOverrideForTest(SHIPPED);
    expect(
      addParamAction({
        addParam: "production",
        datasetId: "d1",
        can: { edit: true },
      }),
    ).toEqual({
      kind: "open",
      request: {
        source: { kind: "sessions" },
        target: { dataset_id: "d1" },
      },
    });
  });

  it("the palette keeps every verb, link and recent", async () => {
    const { registeredCommands, launchCommandShown } = await import(
      "src/sections/common/CommandPalette/commandRegistry"
    );
    setLaunchOverrideForTest(SHIPPED);
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
      "dataset.newExperiment",
      "dataset.auditLog",
      "dataset.copyId",
      "dataset.share",
      "run.r1",
      "go.leaderboards",
      "x.addProd",
    ]);
    expect(launchCommandShown({ id: "help.glossary" })).toBe(true);
    expect(launchCommandShown({ id: "help.shortcuts" })).toBe(true);
  });

  it("shortcuts: the sheet and the key handler keep Version history and Save a version", async () => {
    const { actionForEvent, shortcutRows } = await import("src/utils/keymap");
    setLaunchOverrideForTest(SHIPPED);
    expect(actionForEvent({ key: "h" }, "grid")).toBe("history");
    expect(actionForEvent({ key: "f" }, "grid")).toBe("filter");
    expect(shortcutRows("grid").map((r) => r.action)).toContain("history");
    expect(shortcutRows("row").map((r) => r.action)).toContain("saveVersion");
  });

  it("the source picker offers every card and says which ways are not in this release", async () => {
    const catalog = await import("src/sections/common/RunSources/catalog");
    setLaunchOverrideForTest(SHIPPED_BASIC);
    expect(catalog.cardsForKind("rows").map((c) => c.id)).toEqual([
      "prompt",
      "agent",
      "running",
    ]);
    // The ways this release does not build follow them, greyed, each with
    // the release line (runSourceAvailability.js NOT_IN_RELEASE_REASON).
    const held = catalog.stepCardsFor("rows").cards.filter((c) => c.held);
    expect(held.map((c) => c.title)).toEqual([
      "Your code (SDK / CLI)",
      "Agent harness",
      "Voice agent",
    ]);
    held.forEach((c) =>
      expect(catalog.cardUnavailableReason(c), c.title).toBe(
        "Not available in this release",
      ),
    );
  });

  it("the cell menu has comment, assign and split items", async () => {
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
    setLaunchOverrideForTest(SHIPPED);
    const shipped = buildCellContextMenu(params, {
      splits: [{ id: "s1", name: "hard" }],
      handlers,
    })
      .filter((i) => i !== "separator")
      .map((i) => i.name);
    ["Comment on row", "Assign row to…", "Assign split"].forEach((n) =>
      expect(shipped, n).toContain(n),
    );
    expect(shipped).toContain("Open row");
    expect(shipped).toContain("Copy row link");
  });

  it("the grid footer points at experiments", async () => {
    const { dataHelpText, dataFooterParts } = await import(
      "src/sections/develop-detail/DataTab/DataGrid/dataHelp"
    );
    setLaunchOverrideForTest(SHIPPED);
    const args = { kind: "rows", total: 3, columns: [] };
    expect(dataHelpText(args).lead).toBe(
      "3 rows. Mark an input and an expected column to run experiments.",
    );
    expect(dataFooterParts(args).each).toBe(
      "mark an input and an expected column to run experiments",
    );
  });

  it("the estimate method text names each run's tries and a cost cap", async () => {
    const { estimateHelpOf } = await import(
      "src/sections/develop-detail/Experiment/costEstimateModel"
    );
    setLaunchOverrideForTest(SHIPPED_BASIC);
    const lines = estimateHelpOf({ promptConfig: [] });
    expect(lines[0]).toBe(
      "Tries = rows × each run's tries (its own Tries, else Trials).",
    );
    expect(lines).toContain(
      "A source you host bills you for its own calls. Its cost cap counts judge spend: the run stops once the next try's judges would pass the cap, so only the tries under it run and are judged.",
    );
  });
});
