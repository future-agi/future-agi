import { describe, expect, it, vi } from "vitest";
import { render, screen } from "src/utils/test-utils";
import ErrorMetadataPanel from "../ErrorMetadataPanel";
import { SharedIssueContext } from "../../sharedIssueContext";
import { useErrorFeedStore } from "../../store";

const mocks = vi.hoisted(() => ({
  useOrgMembers: vi.fn(() => ({ data: [] })),
  useErrorFeedSidebar: vi.fn(() => ({ data: undefined, isLoading: false })),
  useLinearTeams: vi.fn(() => ({ data: undefined, isLoading: false })),
  useSharedIssueSidebar: vi.fn(() => ({ data: undefined, isLoading: false })),
  mutate: vi.fn(),
}));

vi.mock("src/api/annotation-queues/annotation-queues", () => ({
  useOrgMembers: mocks.useOrgMembers,
}));
vi.mock("src/api/shared-links", () => ({
  useSharedIssueSidebar: mocks.useSharedIssueSidebar,
}));
vi.mock("src/contexts/OrganizationContext", () => ({
  useOrganization: () => ({ currentOrganizationId: "org-1" }),
}));
vi.mock("src/api/errorFeed/error-feed", () => ({
  useErrorFeedSidebar: mocks.useErrorFeedSidebar,
  useUpdateErrorFeedIssue: () => ({ mutate: mocks.mutate, isPending: false }),
  useLinearTeams: mocks.useLinearTeams,
  useCreateLinearIssue: () => ({ mutate: vi.fn(), isPending: false }),
}));

const error = {
  cluster_id: "E-1",
  status: "escalating",
  severity: "high",
  assignees: ["owner@acme.test"],
  trace_count: 8,
  first_seen: "2026-10-21T12:00:00Z",
  external_issue_url: "https://linear.app/acme/issue/TH-1",
  external_issue_id: "TH-1",
};

const shared = {
  token: "tok",
  sidebar: {
    timeline: { age_days: 5 },
    ai_metadata: { model: "gpt-4o-mini", project: "Support Bot" },
    evaluations: [],
    co_occurring_issues: [],
  },
};

describe("ErrorMetadataPanel on a shared issue", () => {
  it("shows triage and metadata as text with no editing or Linear actions", () => {
    render(
      <SharedIssueContext.Provider value={shared}>
        <ErrorMetadataPanel error={error} />
      </SharedIssueContext.Provider>,
    );

    expect(screen.getByText("owner@acme.test")).toBeInTheDocument();
    expect(screen.getByText("gpt-4o-mini")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /TH-1/ })).toHaveAttribute(
      "href",
      "https://linear.app/acme/issue/TH-1",
    );
    expect(
      screen.queryByRole("button", { name: /connect|create issue/i }),
    ).not.toBeInTheDocument();
    for (const [orgId] of mocks.useOrgMembers.mock.calls) {
      expect(orgId).toBeFalsy();
    }
    for (const [clusterId] of mocks.useErrorFeedSidebar.mock.calls) {
      expect(clusterId).toBeFalsy();
    }
    expect(mocks.useLinearTeams).not.toHaveBeenCalled();
  });

  it("follows the trace picked in Traces affected", () => {
    useErrorFeedStore.setState({
      selectedTraceIdByCluster: { "E-1": "trace-2" },
    });
    mocks.useSharedIssueSidebar.mockReturnValue({
      data: { ...shared.sidebar, ai_metadata: { model: "claude-haiku" } },
      isLoading: false,
    });

    render(
      <SharedIssueContext.Provider value={shared}>
        <ErrorMetadataPanel error={error} />
      </SharedIssueContext.Provider>,
    );

    expect(mocks.useSharedIssueSidebar).toHaveBeenCalledWith("tok", "trace-2");
    expect(screen.getByText("claude-haiku")).toBeInTheDocument();
    expect(screen.queryByText("gpt-4o-mini")).not.toBeInTheDocument();
  });

  it("does not show the first trace's metadata while the picked trace loads", () => {
    useErrorFeedStore.setState({
      selectedTraceIdByCluster: { "E-1": "trace-3" },
    });
    mocks.useSharedIssueSidebar.mockReturnValue({
      data: undefined,
      isLoading: true,
    });

    render(
      <SharedIssueContext.Provider value={shared}>
        <ErrorMetadataPanel error={error} />
      </SharedIssueContext.Provider>,
    );

    expect(screen.queryByText("gpt-4o-mini")).not.toBeInTheDocument();
  });

  it("never links a Linear URL that is not http or https", () => {
    render(
      <SharedIssueContext.Provider value={shared}>
        <ErrorMetadataPanel
          // Assembled so the lint rule against script URLs stays quiet.
          error={{
            ...error,
            external_issue_url: ["javascript", "alert(1)"].join(":"),
          }}
        />
      </SharedIssueContext.Provider>,
    );

    expect(
      screen.queryByRole("link", { name: /TH-1/ }),
    ).not.toBeInTheDocument();
    expect(screen.getByText("TH-1")).toBeInTheDocument();
  });
});
