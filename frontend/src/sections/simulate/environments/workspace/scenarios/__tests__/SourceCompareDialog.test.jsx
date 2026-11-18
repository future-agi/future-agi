import { describe, it, expect, vi } from "vitest";
import userEvent from "@testing-library/user-event";
import { screen, within } from "src/utils/test-utils";

const getHarnessSourceChanges = vi.fn(async () => ({
  from_version: 1,
  to_version: 2,
  truncated: false,
  changes: [
    {
      kind: "rule",
      ref: "",
      before: "Refunds are allowed within 30 days of delivery.",
      after: "Refunds are allowed within 14 days of delivery.",
      cited_by: ["refund-inside-window", "refund-refused-outside-window"],
    },
  ],
}));
vi.mock("src/api/harness/harness", async () => ({
  ...(await vi.importActual("src/api/harness/harness")),
  getHarnessSourceChanges: (...args) => getHarnessSourceChanges(...args),
}));

const { LifecycleSections } = await import("../ScenarioDetail");
const { renderWithClient } = await import("./scenariosTestUtils");

const row = {
  lifecycle: {
    state: "draft",
    reason_text: "Not validated on these versions yet.",
    sources: [
      {
        kind: "rule",
        ref: "",
        quote: "within 14 days",
        label: "Refunds are allowed",
        resolved: true,
      },
    ],
    source_changed: ["rule"],
    source_changed_version: 2,
    source_changed_version_id: "ev-2",
  },
};

describe("Source changed: Compare", () => {
  it("opens the change with the cited words marked and the cases that cite it", async () => {
    const user = userEvent.setup();
    renderWithClient(<LifecycleSections row={row} jobId="env-1" />);

    await user.click(screen.getByRole("button", { name: "Compare" }));

    const dialog = await screen.findByRole("dialog");
    expect(getHarnessSourceChanges).toHaveBeenCalledWith("env-1", "ev-2");
    expect(
      await within(dialog).findByText(
        "What changed between environment v1 and v2",
      ),
    ).toBeInTheDocument();
    expect(within(dialog).getByText("Before (v1)")).toBeInTheDocument();
    expect(within(dialog).getByTestId("compare-cited")).toHaveTextContent(
      "within 14 days",
    );
    expect(
      within(dialog).getByText(/Cited by 2 cases: refund-inside-window/),
    ).toBeInTheDocument();
  });

  it("offers no Compare without a changed source", () => {
    renderWithClient(
      <LifecycleSections
        row={{ lifecycle: { ...row.lifecycle, source_changed: [] } }}
        jobId="env-1"
      />,
    );
    expect(screen.queryByRole("button", { name: "Compare" })).toBeNull();
  });
});
