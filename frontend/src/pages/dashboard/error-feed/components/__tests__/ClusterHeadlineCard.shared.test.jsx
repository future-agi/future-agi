import { describe, expect, it } from "vitest";
import { render, screen } from "src/utils/test-utils";
import ClusterHeadlineCard from "../ClusterHeadlineCard";
import { SharedIssueContext } from "../../sharedIssueContext";

const shared = { token: "tok", issue: {}, overview: null };

const renderShared = (error) =>
  render(
    <SharedIssueContext.Provider value={shared}>
      <ClusterHeadlineCard error={error} />
    </SharedIssueContext.Provider>,
  );

describe("ClusterHeadlineCard on a shared issue", () => {
  it("offers no analysis run when nothing was saved", () => {
    renderShared({ cluster_id: "E-1", fix_layer: "Prompt" });

    expect(
      screen.getByText("No analysis saved for this issue."),
    ).toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: /debug this cluster/i }),
    ).not.toBeInTheDocument();
  });

  it("shows the saved analysis without run or Linear actions", () => {
    renderShared({
      cluster_id: "E-1",
      fix_layer: "Prompt",
      rca: {
        synthesis: "The prompt says refunds are usually allowed.",
        fix: "State the 30 day rule.",
        confidence: "H",
      },
    });

    expect(
      screen.getByText("The prompt says refunds are usually allowed."),
    ).toBeInTheDocument();
    expect(screen.getByText("State the 30 day rule.")).toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: /linear|reasoning/i }),
    ).not.toBeInTheDocument();
    expect(screen.queryByLabelText(/re-run/i)).not.toBeInTheDocument();
  });
});
