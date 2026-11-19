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
const evidenceCase = (id, status) => ({
  id,
  position: 0,
  source_id: `trace-${id}`,
  status,
  reason: "",
  source_available: true,
  changed_since_import: false,
  scenario_key: null,
  new_checks: [],
  ...parts,
});
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
    cases: [evidenceCase(CASE_A, "drafted"), evidenceCase(CASE_B, "duplicate")],
  },
};

let jobs;
let plans;
const answer = (url, config) => {
  if (url === ISSUES) return Promise.resolve({ data: { results: jobs } });
  if (url === `${ISSUES}plan/`) {
    const env = config?.params?.environment_id;
    return Promise.resolve({ data: plans[env ?? "none"] });
  }
  if (url === `${ISSUES}${JOB}/`) return Promise.resolve({ data: reviewJob });
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

const renderDrawer = () =>
  render(
    <QueryClientProvider
      client={
        new QueryClient({
          defaultOptions: {
            queries: { retry: false },
            mutations: { retry: false },
          },
        })
      }
    >
      <RunEpochDrawer
        open
        onClose={() => {}}
        clusterId={CLUSTER}
        projectId={PROJECT}
        issueTitle="Quotes a fare"
      />
    </QueryClientProvider>,
  );

beforeEach(() => {
  api.get.mockReset().mockImplementation(answer);
  api.post.mockReset();
  jobs = [];
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
});

describe("RunEpochDrawer: the review", () => {
  beforeEach(() => {
    jobs = [{ cluster_id: CLUSTER, project_id: PROJECT, job: reviewJob }];
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
