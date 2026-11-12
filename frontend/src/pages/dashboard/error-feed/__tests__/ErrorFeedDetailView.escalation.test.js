import React from "react";
import PropTypes from "prop-types";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, userEvent } from "src/utils/test-utils";
import ErrorFeedDetailView from "../ErrorFeedDetailView";
import { SharedIssueContext } from "../sharedIssueContext";
import { useErrorFeedStore } from "../store";

const mocks = vi.hoisted(() => ({
  clusterId: "E-OBS-SW-24",
  status: "for_review",
  isPending: false,
  mutate: vi.fn(),
}));

vi.mock("react-router-dom", async (importOriginal) => ({
  ...(await importOriginal()),
  useParams: () => ({ id: mocks.clusterId }),
}));

const issueRow = () => ({
  cluster_id: mocks.clusterId,
  error: { name: "Request failed", type: "RuntimeError" },
  status: mocks.status,
  severity: "high",
  trace_count: 5,
});

vi.mock("src/api/errorFeed/error-feed", () => ({
  useErrorFeedRedirect: () => ({
    data: { resolved_cluster_id: mocks.clusterId },
    isLoading: false,
    isError: false,
  }),
  useErrorFeedDetail: () => ({
    data: { row: issueRow() },
    isLoading: false,
  }),
  useUpdateErrorFeedIssue: () => ({
    mutate: mocks.mutate,
    isPending: mocks.isPending,
  }),
  useExportErrorFeedTraces: () => ({ mutate: vi.fn(), isPending: false }),
}));

function FakeIconify({ icon }) {
  return React.createElement("span", {
    "data-icon": icon,
    "aria-hidden": true,
  });
}
FakeIconify.propTypes = { icon: PropTypes.string };

vi.mock("src/components/iconify", () => ({
  default: (props) => React.createElement(FakeIconify, props),
}));
vi.mock("src/components/share-dialog", () => ({ ShareDialog: () => null }));
vi.mock("src/components/snackbar", () => ({
  useSnackbar: () => ({ enqueueSnackbar: vi.fn() }),
}));
vi.mock("../useAnalyzeRunner", () => ({ useAnalyzeRunner: () => {} }));
vi.mock("../components/ErrorMetadataPanel", () => ({
  default: () => null,
  LinearTeamPicker: () => null,
}));
vi.mock("../components/OverviewTab", () => ({ default: () => null }));
vi.mock("../components/TracesTab", () => ({ default: () => null }));
vi.mock("../components/TrendsTab", () => ({ default: () => null }));
vi.mock("../components/ClusterHeadlineCard", () => ({ default: () => null }));
vi.mock("../components/AnalyzeTab", () => ({ default: () => null }));

// OBS-SW-24: the action must describe its actual escalation mutation, not
// promise an unsupported ignore/suppress operation.
describe("Error Feed detail: explicit escalation", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mocks.status = "for_review";
    mocks.isPending = false;
    useErrorFeedStore.setState({ activeTab: "overview" });
  });

  it("labels the action Escalate issue with the escalation status icon", () => {
    render(React.createElement(ErrorFeedDetailView));

    const button = screen.getByRole("button", { name: "Escalate issue" });
    expect(button).toBeEnabled();
    expect(
      button.querySelector('[data-icon="solar:graph-up-linear"]'),
    ).toBeInTheDocument();
    expect(
      button.querySelector('[data-icon="solar:eye-closed-linear"]'),
    ).not.toBeInTheDocument();
    expect(mocks.mutate).not.toHaveBeenCalled();
  });

  it("does not offer an unsupported Ignore issue action", () => {
    render(React.createElement(ErrorFeedDetailView));

    expect(
      screen.queryByRole("button", { name: /ignore issue/i }),
    ).not.toBeInTheDocument();
  });

  it.each(["for_review", "acknowledged", "resolved"])(
    "escalates a %s issue only after the explicit action is clicked",
    async (status) => {
      mocks.status = status;
      const user = userEvent.setup();
      render(React.createElement(ErrorFeedDetailView));
      expect(mocks.mutate).not.toHaveBeenCalled();

      await user.click(screen.getByRole("button", { name: "Escalate issue" }));

      expect(mocks.mutate).toHaveBeenCalledTimes(1);
      expect(mocks.mutate).toHaveBeenCalledWith({
        clusterId: mocks.clusterId,
        status: "escalating",
      });
    },
  );

  it("disables Escalate issue while an issue update is pending", async () => {
    mocks.isPending = true;
    const user = userEvent.setup();
    render(React.createElement(ErrorFeedDetailView));

    const button = screen.getByRole("button", { name: "Escalate issue" });
    expect(button).toBeDisabled();
    // Keyboard activation avoids the pointer-events restriction of MUI's
    // disabled buttons while still exercising a real user interaction.
    button.focus();
    await user.keyboard("{Enter}");
    expect(mocks.mutate).not.toHaveBeenCalled();
  });

  it.each([
    ["Resolve", "resolved"],
    ["Acknowledge", "acknowledged"],
  ])("preserves the %s action payload", async (name, status) => {
    const user = userEvent.setup();
    render(React.createElement(ErrorFeedDetailView));

    await user.click(screen.getByRole("button", { name }));

    expect(mocks.mutate).toHaveBeenCalledTimes(1);
    expect(mocks.mutate).toHaveBeenCalledWith({
      clusterId: mocks.clusterId,
      status,
    });
  });

  it("keeps shared issues read-only without escalation or ignore actions", () => {
    render(
      React.createElement(
        SharedIssueContext.Provider,
        { value: { token: "shared-test", issue: { row: issueRow() } } },
        React.createElement(ErrorFeedDetailView),
      ),
    );

    expect(screen.getByText("Request failed")).toBeInTheDocument();
    for (const name of [
      "Escalate issue",
      "Ignore issue",
      "Resolve",
      "Acknowledge",
    ]) {
      expect(screen.queryByRole("button", { name })).not.toBeInTheDocument();
    }
    expect(mocks.mutate).not.toHaveBeenCalled();
  });
});
