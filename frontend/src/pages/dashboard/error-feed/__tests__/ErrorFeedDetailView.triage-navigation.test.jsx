import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { Link, Route, Routes } from "react-router-dom";
import {
  act,
  cleanup,
  renderWithRouter,
  screen,
  userEvent,
  waitFor,
  within,
} from "src/utils/test-utils";
import ErrorFeedDetailView from "../ErrorFeedDetailView";
import { useErrorFeedStore } from "../store";

const mocks = vi.hoisted(() => ({ get: vi.fn(), patch: vi.fn() }));

// Keep the real query hooks, router, detail view and metadata panel. Only the
// transport and unrelated services/tab bodies are replaced.
vi.mock("src/utils/axios", () => ({
  default: { get: mocks.get, patch: mocks.patch },
  endpoints: {
    errorFeed: {
      detail: (id) => `/issues/${id}`,
      redirect: (id) => `/issues/${id}/redirect`,
      sidebar: (id) => `/issues/${id}/sidebar`,
      update: (id) => `/issues/${id}`,
      linearTeams: "/linear/teams",
    },
  },
}));
vi.mock("src/api/annotation-queues/annotation-queues", () => ({
  useOrgMembersInfinite: () => ({
    data: [
      { name: "Member A", email: "member-a@example.test" },
      { name: "Member B", email: "member-b@example.test" },
    ],
  }),
}));
vi.mock("src/api/shared-links", () => ({
  useSharedIssueSidebar: () => ({ data: undefined, isLoading: false }),
}));
vi.mock("src/contexts/OrganizationContext", () => ({
  useOrganization: () => ({ currentOrganizationId: "org-1" }),
}));
vi.mock("src/auth/hooks", () => ({
  useAuthContext: () => ({ role: "Member" }),
}));
vi.mock("src/components/iconify", () => ({ default: () => null }));
vi.mock("src/components/snackbar", () => ({
  useSnackbar: () => ({ enqueueSnackbar: vi.fn() }),
}));
vi.mock("src/components/share-dialog", () => ({ ShareDialog: () => null }));
vi.mock("../useAnalyzeRunner", () => ({ useAnalyzeRunner: () => {} }));
vi.mock("../components/OverviewTab", () => ({ default: () => null }));
vi.mock("../components/TracesTab", () => ({ default: () => null }));
vi.mock("../components/TrendsTab", () => ({ default: () => null }));
vi.mock("../components/ClusterHeadlineCard", () => ({ default: () => null }));
vi.mock("../components/AnalyzeTab", () => ({ default: () => null }));

const issueA = {
  cluster_id: "E-A",
  error: { name: "Issue A", type: "llm" },
  status: "acknowledged",
  severity: "low",
  assignees: ["member-a@example.test"],
  trace_count: 3,
};
const issueB = {
  cluster_id: "E-B",
  error: { name: "Issue B", type: "llm" },
  status: "escalating",
  severity: "critical",
  assignees: ["member-b@example.test"],
  trace_count: 12,
};
const response = (result) => ({ data: { result } });
const responses = new Map([
  ["/issues/E-A", response({ row: issueA })],
  ["/issues/E-B", response({ row: issueB })],
  ["/issues/E-A/redirect", response({ resolved_cluster_id: "E-A" })],
  ["/issues/E-B/redirect", response({ resolved_cluster_id: "E-B" })],
  [
    "/issues/E-A/sidebar",
    response({
      co_occurring_issues: [{ id: "E-B", title: "Issue B", count: 2 }],
    }),
  ],
  ["/issues/E-B/sidebar", response({ co_occurring_issues: [] })],
  ["/linear/teams", response({ connected: false, teams: [] })],
]);
const getResponse = (url) => {
  if (!responses.has(url)) throw new Error(`Unexpected request: ${url}`);
  return Promise.resolve(responses.get(url));
};
const triageField = (label) => within(screen.getByText(label).parentElement);
let queryClient;

const renderDetail = () =>
  renderWithRouter(
    <QueryClientProvider client={queryClient}>
      <Link to="/dashboard/error-feed/E-A">Open issue A</Link>
      <Routes>
        <Route
          path="/dashboard/error-feed/:id"
          element={<ErrorFeedDetailView />}
        />
      </Routes>
    </QueryClientProvider>,
    { route: "/dashboard/error-feed/E-B" },
  );

beforeEach(() => {
  mocks.get.mockReset().mockImplementation(getResponse);
  // Leave saves pending so refetches cannot mask the optimistic state checks.
  mocks.patch.mockReset().mockImplementation(() => new Promise(() => {}));
  queryClient = new QueryClient({
    defaultOptions: {
      queries: { retry: false, staleTime: Infinity, gcTime: Infinity },
      mutations: { retry: false },
    },
  });
  useErrorFeedStore.setState({
    activeTab: "overview",
    selectedTraceIdByCluster: {},
  });
});

afterEach(() => {
  cleanup();
  queryClient.clear();
});

describe("H16-C25-001: cached issue triage navigation", () => {
  it("shows B's severity and assignee after following A's co-occurring link to cached B", async () => {
    const user = userEvent.setup();
    let resolveIssueA;
    const pendingIssueA = new Promise((resolve) => {
      resolveIssueA = resolve;
    });
    mocks.get.mockImplementation((url) =>
      url === "/issues/E-A" ? pendingIssueA : getResponse(url),
    );
    renderDetail();

    // Opening B warms its real per-ID redirect and detail queries.
    await screen.findByText("Member B");
    expect(triageField("Severity").getByText("Critical")).toBeInTheDocument();
    expect(queryClient.getQueryData(["errorFeed", "redirect", "E-B"])).toEqual(
      responses.get("/issues/E-B/redirect"),
    );
    expect(queryClient.getQueryData(["errorFeed", "detail", "E-B"])).toEqual(
      responses.get("/issues/E-B"),
    );
    expect(
      queryClient.getQueryData(["errorFeed", "detail", "E-A"]),
    ).toBeUndefined();

    // A is uncached: its loading branch unmounts B's panel before A mounts.
    await user.click(screen.getByRole("link", { name: "Open issue A" }));
    await waitFor(() =>
      expect(screen.queryByText("Triage")).not.toBeInTheDocument(),
    );
    await act(async () => resolveIssueA(responses.get("/issues/E-A")));
    await screen.findByText("Member A");
    expect(triageField("Severity").getByText("Low")).toBeInTheDocument();

    // Use the actual Co-occurring Issues navigation, not a rerender or remount.
    await user.click(await screen.findByText("Issue B"));
    expect(window.location.pathname).toBe("/dashboard/error-feed/E-B");
    expect(screen.getByTitle("Issue B")).toBeInTheDocument();
    expect(screen.getByText("12 traces")).toBeInTheDocument();
    expect(triageField("Status").getByText("Escalating")).toBeInTheDocument();
    expect
      .soft(triageField("Severity").queryByText("Critical"))
      .toBeInTheDocument();
    expect
      .soft(triageField("Assignee").queryByText("Member B"))
      .toBeInTheDocument();
    expect
      .soft(triageField("Severity").queryByText("Low"))
      .not.toBeInTheDocument();
    expect
      .soft(triageField("Assignee").queryByText("Member A"))
      .not.toBeInTheDocument();
    // No second fetch/loading cycle or reload was needed to show B's triage.
    expect(
      mocks.get.mock.calls.filter(([url]) => url === "/issues/E-B"),
    ).toHaveLength(1);
    expect(
      mocks.get.mock.calls.filter(([url]) => url === "/issues/E-B/redirect"),
    ).toHaveLength(1);
  });

  it("preserves local triage edits when data for the same issue rerenders", async () => {
    const user = userEvent.setup();
    renderDetail();
    await screen.findByText("Member B");

    await user.click(triageField("Severity").getByText("Critical"));
    await user.click(screen.getByRole("menuitem", { name: "High" }));
    await user.click(triageField("Assignee").getByText("Member B"));
    await user.click(screen.getByRole("menuitem", { name: /Member A/ }));
    await waitFor(() => {
      expect(mocks.patch).toHaveBeenCalledWith("/issues/E-B", {
        severity: "high",
      });
      expect(mocks.patch).toHaveBeenCalledWith("/issues/E-B", {
        assignee: "member-a@example.test",
      });
    });

    act(() => {
      queryClient.setQueryData(
        ["errorFeed", "detail", "E-B"],
        response({ row: { ...issueB, trace_count: 13 } }),
      );
    });
    await screen.findByText("13 traces");
    expect(triageField("Severity").getByText("High")).toBeInTheDocument();
    expect(triageField("Assignee").getByText("Member A")).toBeInTheDocument();
  });
});
