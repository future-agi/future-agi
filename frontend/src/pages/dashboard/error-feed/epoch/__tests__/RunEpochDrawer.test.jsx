import { beforeEach, describe, expect, it, vi } from "vitest";
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render } from "src/utils/test-utils";
import {
  SimulateApiAgentFixIssuesCreateBody,
  SimulateApiAgentFixIssuesRunBody,
} from "src/generated/api-contracts/api.zod";

// The real endpoints (contracted paths) over a fake transport.
const api = vi.hoisted(() => ({ get: vi.fn(), post: vi.fn() }));
vi.mock("src/utils/axios", async (importOriginal) => {
  const actual = await importOriginal();
  return { ...actual, default: { get: api.get, post: api.post } };
});
vi.mock("src/hooks/useCapabilities", async (importOriginal) => ({
  ...(await importOriginal()),
  useFeatureAllowed: () => ({ allowed: true }),
}));

const { default: RunEpochDrawer } = await import("../RunEpochDrawer");

const CLUSTER = "S-1A2B3C4D";
const PROJECT = "11111111-1111-4111-8111-111111111111";
const ENV = "22222222-2222-4222-8222-222222222222";
const VOICE_ENV = "33333333-3333-4333-8333-333333333333";
const JOB = "44444444-4444-4444-8444-444444444444";
const CASE_A = "55555555-5555-4555-8555-555555555555";
const CASE_B = "66666666-6666-4666-8666-666666666666";
const ISSUES = "/simulate/api/agent-fix-issues/";

const pick = (id, reason) => ({
  member_id: `m-${id}`,
  unit: "trace",
  source_id: `trace-${id}`,
  reason,
  started_at: "2026-11-18T10:00:00Z",
});
const DEFAULTS = {
  surfaces: [
    { id: "prompt", label: "Instructions", allowed: true, reason: null },
    { id: "code", label: "Code", allowed: false, reason: "Not in the repo" },
  ],
  budget_credits: 1500,
  budget_credits_max: 50000,
  max_minutes_max: 1440,
  reply_latency_margin_pct: 25,
  cost_margin_pct: 25,
};
const plan = (environment) => ({
  issue: { cluster_id: CLUSTER, project_id: PROJECT, title: "Quotes a fare" },
  environment,
  blocked: null,
  sample: {
    unit: "trace",
    picks: [pick(1, "example"), pick(2, "cited")],
    alternates: [pick(3, "recent")],
  },
  open_job_id: null,
  issue_scenarios: { held: 4, limit: 36 },
  defaults: DEFAULTS,
});
const envRow = (id, name, sourceKind) => ({
  id,
  name,
  source_kind: sourceKind,
  agent_type: "chat",
  status: "completed",
});
const parts = { transcript: { status: "present", reason: "", turns: [] } };
const evidenceCase = (id, status, duplicateOf = null) => ({
  id,
  position: 0,
  source_id: `trace-${id}`,
  status,
  reason: "",
  source_available: true,
  changed_since_import: false,
  scenario_key: null,
  new_checks: [],
  duplicate_of: duplicateOf,
  ...parts,
});
// A repeat of a scenario the suite holds, and one whose original is gone.
const CASE_GONE = "88888888-8888-4888-8888-888888888888";
const original = {
  import_id: "99999999-9999-4999-8999-999999999999",
  case_id: "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
  scenario_name: "Late rebooking",
};
const reviewJob = {
  id: JOB,
  phase: "review",
  issue: { cluster_id: CLUSTER, project_id: PROJECT, title: "Quotes a fare" },
  environment: { id: ENV, name: "Support line" },
  sample: [],
  ending: null,
  baseline: null,
  epoch: null,
  created_at: "2026-11-19T09:00:00Z",
  defaults: DEFAULTS,
  evidence_import: {
    id: "77777777-7777-4777-8777-777777777777",
    project_id: PROJECT,
    source_kind: "trace",
    simulated: false,
    production: true,
    drafting: false,
    cases: [
      evidenceCase(CASE_A, "drafted"),
      evidenceCase(CASE_B, "duplicate", original),
      evidenceCase(CASE_GONE, "duplicate"),
    ],
  },
};
// The batch status lists the job without its import and defaults.
const summaryOf = ({ evidence_import: _i, defaults: _d, ...job }) => job;
const listed = (job) => [
  { cluster_id: CLUSTER, project_id: PROJECT, job: summaryOf(job) },
];

let jobs;
let plans;
let detail;
const answer = (url, config) => {
  if (url === ISSUES) return Promise.resolve({ data: { results: jobs } });
  if (url === `${ISSUES}plan/`) {
    const env = config?.params?.environment_id;
    return Promise.resolve({ data: plans[env ?? "none"] });
  }
  if (url === `${ISSUES}${JOB}/`) return detail();
  if (url === "/simulate/api/harness-environments/") {
    return Promise.resolve({
      data: {
        results: [
          envRow(ENV, "Support line", "github"),
          envRow(VOICE_ENV, "Phone line", "provider"),
        ],
      },
    });
  }
  return Promise.reject(new Error(`unexpected GET ${url}`));
};

const renderDrawer = () => {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  render(
    <QueryClientProvider client={client}>
      <RunEpochDrawer
        open
        onClose={() => {}}
        clusterId={CLUSTER}
        projectId={PROJECT}
        issueTitle="Quotes a fare"
      />
    </QueryClientProvider>,
  );
  return client;
};
const planReads = () =>
  api.get.mock.calls.filter(([url]) => url === `${ISSUES}plan/`);

beforeEach(() => {
  api.get.mockReset().mockImplementation(answer);
  api.post.mockReset();
  jobs = [];
  detail = () => Promise.resolve({ data: reviewJob });
  plans = {
    none: plan(null),
    [ENV]: plan({ id: ENV, name: "Support line" }),
  };
});

describe("RunEpochDrawer: the plan", () => {
  it("asks where it runs the first time, listing code-agent environments only", async () => {
    const user = userEvent.setup();
    renderDrawer();
    const list = await screen.findByTestId("epoch-environments");
    expect(within(list).getByText("Support line")).toBeInTheDocument();
    expect(within(list).queryByText("Phone line")).toBeNull();
    await user.click(within(list).getByRole("radio"));
    await user.click(screen.getByRole("button", { name: "Continue" }));
    expect(await screen.findByTestId("epoch-plan")).toBeInTheDocument();
    const planCalls = api.get.mock.calls.filter(
      ([url]) => url === `${ISSUES}plan/`,
    );
    expect(planCalls.map(([, c]) => c.params)).toEqual([
      { cluster_id: CLUSTER, project_id: PROJECT },
      { cluster_id: CLUSTER, project_id: PROJECT, environment_id: ENV },
    ]);
  });

  it("drafts the picks after a swap, with no launch settings, as the contract takes them", async () => {
    plans.none = plans[ENV];
    api.post.mockResolvedValue({ data: { ...reviewJob, phase: "drafting" } });
    const user = userEvent.setup();
    renderDrawer();
    const rows = await screen.findAllByTestId("epoch-pick");
    expect(rows.map((r) => r.textContent)).toEqual([
      expect.stringContaining("Typical of the issue"),
      expect.stringContaining("Cited in the root cause"),
    ]);
    expect(
      screen.getByText("One builder turn, billed as authoring credits."),
    ).toBeInTheDocument();
    expect(
      screen.getByText("Holds 4 of at most 36 scenarios from issues."),
    ).toBeInTheDocument();
    await user.click(within(rows[0]).getByRole("button", { name: "Swap" }));
    await user.click(screen.getByTestId("epoch-draft-scenarios"));
    await waitFor(() => expect(api.post).toHaveBeenCalledTimes(1));
    const [url, body] = api.post.mock.calls[0];
    expect(url).toBe(ISSUES);
    expect(body).toEqual({
      cluster_id: CLUSTER,
      project_id: PROJECT,
      environment_id: ENV,
      member_ids: ["m-3", "m-2"],
    });
    expect(() => SimulateApiAgentFixIssuesCreateBody.parse(body)).not.toThrow();
  });

  it("says the environments could not be read, instead of offering to build one", async () => {
    const reads = answer;
    api.get.mockImplementation((url, config) =>
      url === "/simulate/api/harness-environments/"
        ? Promise.reject(new Error("network down"))
        : reads(url, config),
    );
    renderDrawer();
    expect(
      await screen.findByText("Could not load your environments."),
    ).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Retry" })).toBeInTheDocument();
    expect(
      screen.queryByText("No environment can run an epoch yet"),
    ).toBeNull();
  });

  it("says why nothing can be drafted when no trace is left to pick, and offers another environment", async () => {
    plans.none = {
      ...plans[ENV],
      sample: { unit: "trace", picks: [], alternates: [] },
    };
    const user = userEvent.setup();
    renderDrawer();
    expect(
      await screen.findByText(
        "No trace of this issue can be drafted in this environment.",
      ),
    ).toBeInTheDocument();
    expect(screen.getByTestId("epoch-draft-scenarios")).toBeDisabled();
    await user.click(
      screen.getByRole("button", { name: "Pick another environment" }),
    );
    expect(await screen.findByTestId("epoch-environments")).toBeInTheDocument();
  });

  it("says why a code-agent environment can't take it, and drafts nothing", async () => {
    plans.none = {
      ...plans[ENV],
      blocked: {
        code: "fix_draft_open",
        message: "This environment has unsaved changes.",
      },
    };
    renderDrawer();
    expect(
      await screen.findByText("This environment has unsaved changes."),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("link", { name: "Review unsaved changes" }),
    ).toHaveAttribute(
      "href",
      `/dashboard/simulate/environments/${ENV}?tab=scenarios`,
    );
    expect(screen.getByTestId("epoch-draft-scenarios")).toBeDisabled();
  });

  it("offers to build an environment when the plan says none can run an epoch, without listing them", async () => {
    plans.none = {
      ...plan(null),
      blocked: { code: "environment_none", message: "No environment yet." },
    };
    renderDrawer();
    expect(
      await screen.findByText("No environment can run an epoch yet"),
    ).toBeInTheDocument();
    expect(screen.getByTestId("epoch-build-environment")).toBeInTheDocument();
    expect(
      api.get.mock.calls.some(
        ([url]) => url === "/simulate/api/harness-environments/",
      ),
    ).toBe(false);
  });

  it("lists an environment still building, so the plan can say what to do once it's picked", async () => {
    const reads = answer;
    api.get.mockImplementation((url, config) =>
      url === "/simulate/api/harness-environments/"
        ? Promise.resolve({
            data: {
              results: [{ ...envRow(ENV, "Support line", "github"), status: "queued" }],
            },
          })
        : reads(url, config),
    );
    renderDrawer();
    const list = await screen.findByTestId("epoch-environments");
    expect(within(list).getByText("Support line")).toBeInTheDocument();
  });

  it("says the environment is still being built and links to it, and drafts nothing", async () => {
    plans.none = {
      ...plans[ENV],
      blocked: {
        code: "environment_not_built",
        message: "This environment is still being built.",
      },
    };
    renderDrawer();
    expect(
      await screen.findByText("This environment is still being built."),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("link", { name: "Open the environment" }),
    ).toHaveAttribute("href", `/dashboard/simulate/environments/${ENV}`);
    expect(screen.getByTestId("epoch-draft-scenarios")).toBeDisabled();
  });

  it("says how the winner ships, and where to connect GitHub for a pull request", async () => {
    plans.none = {
      ...plans[ENV],
      ship: {
        path: "pull_request",
        blocked: {
          code: "fix_github_not_connected",
          message: "Connect the agent's repository to get a pull request.",
        },
      },
    };
    renderDrawer();
    // Until a pull request can open, the winner ships as a patch.
    expect(await screen.findByText("A patch to download")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Connect GitHub" })).toHaveAttribute(
      "href",
      "/dashboard/settings/integrations",
    );
    // A pull request it can't open yet does not stop drafting.
    expect(screen.getByTestId("epoch-draft-scenarios")).toBeEnabled();
  });
});

describe("RunEpochDrawer: the review", () => {
  beforeEach(() => {
    jobs = listed(reviewJob);
  });

  it("waits for the job's own read instead of a review with no drafts or defaults", async () => {
    let land;
    detail = () =>
      new Promise((resolve) => {
        land = () => resolve({ data: reviewJob });
      });
    renderDrawer();
    expect(await screen.findByTestId("run-epoch-drawer")).toHaveTextContent(
      "Review the drafted scenarios",
    );
    expect(screen.queryByTestId("epoch-step-review")).toBeNull();
    expect(screen.queryByRole("checkbox")).toBeNull();
    await waitFor(() => expect(land).toBeDefined());
    land();
    expect(await screen.findByTestId("epoch-step-review")).toBeInTheDocument();
  });

  it("offers a repeat only while the scenario it repeats is still there", async () => {
    renderDrawer();
    const boxes = await screen.findAllByRole("checkbox", {
      name: "Keep this scenario",
    });
    expect(boxes).toHaveLength(2);
    expect(screen.getByTestId("epoch-step-review")).toHaveTextContent(
      "2 scenarios to review.",
    );
  });

  it("shows how the job ended when Run Epoch's refusal ended it, and reads no plan", async () => {
    api.post.mockRejectedValue({
      statusCode: 409,
      error: "evidence_draft_stale",
      message: "raw",
      retryable: false,
    });
    const ended = {
      ...reviewJob,
      phase: "ended",
      ending: { code: "evidence_draft_stale", message: "raw" },
    };
    const user = userEvent.setup();
    renderDrawer();
    const run = await screen.findByTestId("epoch-run");
    // The server ended the job as it refused; review isn't polled, so only the
    // refusal's own refresh can read that.
    jobs = listed(ended);
    detail = () => Promise.resolve({ data: ended });
    await user.click(run);
    expect(
      await screen.findByText(
        /changed after the drafts were reviewed.*Nothing changed/,
      ),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "Run Epoch again" }),
    ).toBeInTheDocument();
    expect(planReads()).toEqual([]);
  });

  it("shows the ending when measuring ends with the drawer open, and plans only on a click", async () => {
    const measuring = { ...reviewJob, phase: "measuring" };
    jobs = listed(measuring);
    detail = () => Promise.resolve({ data: measuring });
    const user = userEvent.setup();
    const client = renderDrawer();
    expect(
      await screen.findByTestId("epoch-step-measuring"),
    ).toBeInTheDocument();
    const ended = {
      ...reviewJob,
      phase: "ended",
      ending: { code: "fix_targets_passed", message: "raw" },
    };
    jobs = listed(ended);
    detail = () => Promise.resolve({ data: ended });
    await client.refetchQueries();
    expect(await screen.findByText("Could not reproduce")).toBeInTheDocument();
    expect(planReads()).toEqual([]);
    await user.click(screen.getByRole("button", { name: "Run Epoch again" }));
    await waitFor(() => expect(planReads()).toHaveLength(1));
  });

  it("runs the kept scenarios with the launch rows, as the contract takes them", async () => {
    api.post.mockResolvedValue({ data: { ...reviewJob, phase: "measuring" } });
    const user = userEvent.setup();
    renderDrawer();
    const boxes = await screen.findAllByRole("checkbox", {
      name: "Keep this scenario",
    });
    expect(boxes).toHaveLength(2);
    await user.click(boxes[1]);
    expect(screen.getByTestId("epoch-launch-form")).toHaveTextContent(
      "Locked to the models the current version calls",
    );
    await user.click(screen.getByTestId("epoch-run"));
    await waitFor(() => expect(api.post).toHaveBeenCalledTimes(1));
    const [url, body] = api.post.mock.calls[0];
    expect(url).toBe(`${ISSUES}${JOB}/run/`);
    expect(body).toEqual({
      case_ids: [CASE_A],
      surfaces: ["prompt"],
      locked_paths: [],
      budget_credits: 1500,
      reply_latency_margin_pct: 25,
      cost_margin_pct: 25,
    });
    expect(() => SimulateApiAgentFixIssuesRunBody.parse(body)).not.toThrow();
  });

  it("names the cap of scenarios from issues and where to make room", async () => {
    api.post.mockRejectedValue({
      statusCode: 409,
      error: "issue_scenarios_limit",
      message: "raw",
      retryable: false,
      held: 36,
      limit: 36,
    });
    const user = userEvent.setup();
    renderDrawer();
    await user.click(await screen.findByTestId("epoch-run"));
    expect(
      await screen.findByText(
        "This environment already holds 36 of at most 36 scenarios from issues. Remove some on its Scenarios tab, then run the epoch.",
      ),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("link", { name: "Open scenarios" }),
    ).toHaveAttribute(
      "href",
      `/dashboard/simulate/environments/${ENV}?tab=scenarios`,
    );
  });
});
