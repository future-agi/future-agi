import { describe, it, expect, vi } from "vitest";
import userEvent from "@testing-library/user-event";
import { screen, within } from "src/utils/test-utils";

const getSourceChanges = vi.fn(async () => ({
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
vi.mock("src/api/simulate-environments/scenarios", async () => ({
  ...(await vi.importActual("src/api/simulate-environments/scenarios")),
  getSourceChanges: (...args) => getSourceChanges(...args),
}));

const { LifecycleSection } = await import("../ScenarioLifecycle");
const { renderWithClient } = await import("./scenariosTestUtils");

// eslint-disable-next-line react/prop-types
const Row = ({ label, children }) => (
  <div>
    <span>{label}</span>
    {children}
  </div>
);

const lifecycle = {
  state: "draft",
  reason_text: "Not validated on these versions yet.",
  negative_controls: [],
  sources: [{ kind: "rule", ref: "", quote: "within 14 days", resolved: true }],
  source_changed: ["rule"],
  source_changed_version: 2,
  source_changed_version_id: "ev-2",
};

describe("Source changed", () => {
  it("opens the before and after with the cited words marked and who cites them", async () => {
    renderWithClient(
      <LifecycleSection envId="env-1" lifecycle={lifecycle} Row={Row} />,
    );

    await userEvent
      .setup()
      .click(screen.getByRole("button", { name: "Compare" }));

    const dialog = await screen.findByRole("dialog");
    expect(getSourceChanges).toHaveBeenCalledWith("env-1", "ev-2");
    expect(
      await within(dialog).findByText(
        "What changed between environment v1 and v2",
      ),
    ).toBeInTheDocument();
    expect(within(dialog).getByTestId("compare-cited")).toHaveTextContent(
      "within 14 days",
    );
    expect(
      within(dialog).getByText(/Cited by 2 scenarios: refund-inside-window/),
    ).toBeInTheDocument();
  });

  it("offers no Compare when nothing it cites changed", () => {
    renderWithClient(
      <LifecycleSection
        envId="env-1"
        lifecycle={{ ...lifecycle, source_changed: [] }}
        Row={Row}
      />,
    );
    expect(screen.queryByRole("button", { name: "Compare" })).toBeNull();
  });
});
