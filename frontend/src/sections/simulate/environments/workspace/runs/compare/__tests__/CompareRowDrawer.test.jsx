import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent } from "src/utils/test-utils";

vi.mock("src/api/simulate-environments/runDetail", async (importOriginal) => ({
  ...(await importOriginal()),
  useCallDetail: (id) => ({
    callDetail: { turns: [{ role: "agent", text: `Hello from ${id}` }] },
    isLoading: false,
  }),
}));

const { default: CompareRowDrawer } = await import("../CompareRowDrawer");

const RUNS = [{ id: "run-1", ordinal: 1, colorIndex: 0 }];
const rowOf = (rowId) => ({
  rowId,
  scenarioKey: rowId,
  calls: {
    "run-1": [1, 2, 3].map((trialIndex) => ({
      id: `${rowId}-c${trialIndex}`,
      trialIndex,
      status: "passed",
    })),
  },
});
const props = () => ({ onClose: vi.fn(), onOpenCall: vi.fn() });

describe("CompareRowDrawer", () => {
  it("opens another row on its first trial, not the one picked before", () => {
    const props = { runs: RUNS, onClose: vi.fn(), onOpenCall: vi.fn() };
    const { rerender } = render(
      <CompareRowDrawer row={rowOf("a")} {...props} />,
    );
    fireEvent.click(screen.getByRole("button", { name: "Trial 3" }));
    expect(screen.getByText("Hello from a-c3")).toBeInTheDocument();

    rerender(<CompareRowDrawer row={null} {...props} />);
    rerender(<CompareRowDrawer row={rowOf("b")} {...props} />);

    expect(screen.getByText("Hello from b-c1")).toBeInTheDocument();
    expect(screen.queryByText("Hello from b-c3")).toBeNull();
  });

  it("titles the drawer with the row's own scenario, not the shared name", () => {
    const row = {
      ...rowOf("a"),
      scenario: "Uber Voice Agent",
      calls: {
        "run-1": [{ id: "a-c1", trialIndex: 1, sourceScenario: "late-pickup" }],
      },
    };
    render(<CompareRowDrawer row={row} runs={RUNS} {...props()} />);

    expect(screen.getByText("late-pickup")).toBeInTheDocument();
    expect(screen.queryByText("Uber Voice Agent")).toBeNull();
  });

  it("names each run column by its number, so same-version runs differ", () => {
    const runs = [
      { id: "run-1", ordinal: 2, colorIndex: 0, agentLabel: "v1" },
      { id: "run-2", ordinal: 5, colorIndex: 1, agentLabel: "v1" },
    ];
    const row = { ...rowOf("a"), calls: { ...rowOf("a").calls, "run-2": [] } };
    render(<CompareRowDrawer row={row} runs={runs} {...props()} />);

    expect(screen.getByText(/^Run 2 · agent v1/)).toBeInTheDocument();
    expect(screen.getByText(/^Run 5 · agent v1/)).toBeInTheDocument();
  });
});
