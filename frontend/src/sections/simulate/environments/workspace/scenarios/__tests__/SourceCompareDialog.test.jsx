import { describe, it, expect, vi } from "vitest";
import userEvent from "@testing-library/user-event";
import { screen, waitFor, within } from "src/utils/test-utils";

const getSourceChanges = vi.fn(async () => ({
  from_version: 1,
  to_version: 2,
  truncated: false,
  changes: [
    {
      kind: "rule",
      // The server names a rule change by its words; a scenario's own source numbers its sources.
      ref: "rule-5f1c",
      before: "Refunds are allowed within 30 days of delivery.",
      after: "Refunds are allowed within 14 days of delivery.",
      cited_by: ["refund-inside-window", "refund-refused-outside-window"],
    },
  ],
}));
const validateScenarios = vi.fn(async () => ({
  state: "validating",
  job_id: "val-1",
}));
vi.mock("src/api/simulate-environments/scenarios", async () => ({
  ...(await vi.importActual("src/api/simulate-environments/scenarios")),
  validateScenarios: (...args) => validateScenarios(...args),
  getRunningScenarioValidation: async () => ({
    job_id: null,
    scenario_keys: null,
    to_prove: 3,
  }),
  getScenarioValidation: async () => ({ state: "validating" }),
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
  sources: [
    { kind: "rule", ref: "rule:1", quote: "within 14 days", resolved: true },
  ],
  source_changed: ["rule:1"],
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

  it("names no versions while it can't read them, and says the failure once", async () => {
    getSourceChanges.mockRejectedValueOnce({ statusCode: 500 });
    const { client } = renderWithClient(
      <LifecycleSection envId="env-1" lifecycle={lifecycle} Row={Row} />,
    );
    await userEvent
      .setup()
      .click(screen.getByRole("button", { name: "Compare" }));
    const dialog = await screen.findByRole("dialog");
    expect(
      await within(dialog).findByText("Couldn't read the change. Try again."),
    ).toBeInTheDocument();
    expect(within(dialog).getByText("What changed")).toBeInTheDocument();
    // The dialog says it, so the app-wide error toast stays quiet.
    const [read] = client.getQueryCache().getAll();
    expect(read.options.meta).toEqual({ errorHandled: true });
  });

  it("marks a file's quote only in that file", async () => {
    getSourceChanges.mockResolvedValueOnce({
      from_version: 1,
      to_version: 2,
      changes: ["file:a.md", "file:b.md"].map((ref) => ({
        kind: "file",
        ref,
        before: "within 30 days",
        after: "within 14 days",
        cited_by: ["refund-inside-window"],
      })),
    });
    renderWithClient(
      <LifecycleSection
        envId="env-1"
        lifecycle={{
          ...lifecycle,
          sources: [
            { kind: "file", ref: "file:b.md", quote: "within 14 days" },
          ],
        }}
        Row={Row}
      />,
    );
    await userEvent
      .setup()
      .click(screen.getByRole("button", { name: "Compare" }));
    expect(await screen.findAllByTestId("compare-cited")).toHaveLength(1);
  });

  it("offers to validate every scenario the changes affect, once each", async () => {
    getSourceChanges.mockResolvedValueOnce({
      from_version: 1,
      to_version: 2,
      changes: [["a", "b"], ["b"]].map((cited_by, at) => ({
        kind: "rule",
        ref: `rule-${at}`,
        before: "x",
        after: "y",
        cited_by,
      })),
    });
    renderWithClient(
      <LifecycleSection envId="env-1" lifecycle={lifecycle} Row={Row} />,
    );
    const user = userEvent.setup();
    await user.click(screen.getByRole("button", { name: "Compare" }));
    const button = await screen.findByRole("button", {
      name: "Validate 2 scenarios",
    });
    await waitFor(() => expect(button).toBeEnabled());
    await user.click(button);
    await waitFor(() =>
      expect(validateScenarios).toHaveBeenCalledWith("env-1", {
        keys: ["a", "b"],
      }),
    );
  });

  it.each([
    ["more than the server validates by name", 51, false],
    ["fewer than it changed", 2, true],
  ])(
    "validates every scenario that needs it when the changes list %s",
    async (_, count, truncated) => {
      getSourceChanges.mockResolvedValueOnce({
        from_version: 1,
        to_version: 2,
        truncated,
        changes: [
          {
            kind: "rule",
            ref: "rule-1",
            before: "x",
            after: "y",
            cited_by: Array.from({ length: count }, (__, at) => `case-${at}`),
          },
        ],
      });
      renderWithClient(
        <LifecycleSection envId="env-1" lifecycle={lifecycle} Row={Row} />,
      );
      const user = userEvent.setup();
      await user.click(screen.getByRole("button", { name: "Compare" }));
      const button = await screen.findByRole("button", {
        name: "Validate cases not yet proved",
      });
      await waitFor(() => expect(button).toBeEnabled());
      await user.click(button);
      await waitFor(() =>
        expect(validateScenarios).toHaveBeenLastCalledWith("env-1", {}),
      );
      expect(screen.queryByText("Only the first changes are shown.")).toEqual(
        truncated ? expect.anything() : null,
      );
    },
  );

  it("names three citing scenarios and keeps the rest one click away", async () => {
    getSourceChanges.mockResolvedValueOnce({
      from_version: 1,
      to_version: 2,
      changes: [
        {
          kind: "rule",
          ref: "rule-1",
          before: "x",
          after: "y",
          cited_by: ["a", "b", "c", "d", "e"],
        },
      ],
    });
    renderWithClient(
      <LifecycleSection envId="env-1" lifecycle={lifecycle} Row={Row} />,
    );
    const user = userEvent.setup();
    await user.click(screen.getByRole("button", { name: "Compare" }));
    expect(
      await screen.findByText("Cited by 5 scenarios: a, b, c"),
    ).toBeInTheDocument();
    expect(screen.queryByText("d, e")).toBeNull();
    await user.click(screen.getByRole("button", { name: "Show 2 more" }));
    expect(screen.getByText("d, e")).toBeInTheDocument();
  });

  it("offers no Validate to someone who can't", async () => {
    renderWithClient(
      <LifecycleSection envId="env-1" lifecycle={lifecycle} Row={Row} locked />,
    );
    await userEvent
      .setup()
      .click(screen.getByRole("button", { name: "Compare" }));
    expect(await screen.findByText(/Cited by 2 scenarios/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Validate/ })).toBeNull();
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
