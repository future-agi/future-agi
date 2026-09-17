import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent } from "src/utils/test-utils";

import { MOCK_WORLD } from "src/api/simulate-environments/_fixtures/world";
import { generatedPool } from "src/api/simulate-environments/_fixtures/scenarioPool";
import ScenariosStep from "../ScenariosStep";

const env = MOCK_WORLD;
// The first twelve pooled rows are rule probes — four rules x three variants —
// so the fixture carries exactly four use cases with three scenarios each,
// which is what the search and filter assertions below lean on.
const rows = generatedPool(env).slice(0, 12);

const renderStep = (scenarios = rows) => {
  const patch = vi.fn();
  render(<ScenariosStep env={env} envState={{ scenarios }} patch={patch} />);
  return { patch };
};

describe("ScenariosStep", () => {
  it("renders the scenario count", () => {
    renderStep();
    expect(screen.getByText("(12)")).toBeInTheDocument();
  });

  it("narrows the list on search, shows N of M, and clears", () => {
    renderStep();
    const search = screen.getByPlaceholderText(/Search scenarios/i);
    fireEvent.change(search, { target: { value: rows[0].name } });

    expect(screen.getByText("1 of 12")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Clear" }));
    expect(screen.queryByText("1 of 12")).not.toBeInTheDocument();
    expect(search).toHaveValue("");
  });

  it("toggles a use case through the filter popover", () => {
    renderStep();
    fireEvent.click(screen.getByRole("button", { name: /^Filter/ }));

    // Popover renders into a portal on document.body, appended after the table,
    // so the use-case label appears both as a group header and as an option —
    // the popover option is the last match.
    expect(screen.getByText("Filter by use case")).toBeInTheDocument();
    const labels = screen.getAllByText(rows[0].useCase);
    fireEvent.click(labels[labels.length - 1]);

    // The selected rule use case carries three of the twelve scenarios.
    expect(screen.getByText("3 of 12")).toBeInTheDocument();
  });

  it("switches between the table and list views", () => {
    renderStep();
    expect(screen.getByRole("table")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("tab", { name: "List" }));
    expect(screen.queryByRole("table")).not.toBeInTheDocument();
    expect(screen.getByText(rows[0].name)).toBeInTheDocument();
  });

  it("removes a scenario through patch without that id", () => {
    const { patch } = renderStep();
    const removes = screen.getAllByRole("button", {
      name: "Remove from this environment",
    });
    fireEvent.click(removes[0]);

    expect(patch).toHaveBeenCalledTimes(1);
    const next = patch.mock.calls[0][0].scenarios;
    expect(next).toHaveLength(11);
    expect(next.map((s) => s.id)).not.toContain(rows[0].id);
  });

  it("shows the empty placeholder with a disabled (coming-soon) Add when there are no scenarios", () => {
    renderStep([]);
    expect(screen.getByText("No scenarios yet")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Add scenarios" })).toBeDisabled();
  });

  it("enables the edit pencil", () => {
    renderStep();
    const pencils = screen.getAllByRole("button", { name: "Edit scenario" });
    expect(pencils[0]).toBeEnabled();
  });
});

describe("groupScenarios", () => {
  it("buckets rows by their use case, largest group first", async () => {
    const { groupScenarios } = await import("../scenarios.constants");
    const groups = groupScenarios(rows);
    expect(groups).toHaveLength(4);
    groups.forEach((g) => expect(g.rows).toHaveLength(3));
  });
});
