import React, { useState } from "react";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, userEvent, within } from "src/utils/test-utils";
import CompositeDetailPanel from "../CompositeDetailPanel";

const { capturedDrawerProps } = vi.hoisted(() => ({
  capturedDrawerProps: { current: null },
}));

vi.mock("src/sections/common/EvalPicker/EvalPickerDrawer", () => ({
  default: (props) => {
    capturedDrawerProps.current = props;
    if (!props.open) return null;
    return (
      <div data-testid="eval-picker-drawer-mock">
        <button
          type="button"
          onClick={() =>
            props.onEvalAdded({
              id: "toxicity-check",
              name: "Toxicity Check",
              evalType: "llm",
            })
          }
        >
          mock-pick-toxicity
        </button>
      </div>
    );
  },
}));

vi.mock("../../hooks/useCompositeChildrenKeys", () => ({
  useCompositeChildrenSchemas: () => ({}),
}));

function Harness(initial = {}) {
  const [name, setName] = useState(initial.name ?? "my-composite");
  const [description, setDescription] = useState(initial.description ?? "");
  const [aggregationEnabled, setAggregationEnabled] = useState(
    initial.aggregationEnabled ?? true,
  );
  const [aggregationFunction, setAggregationFunction] = useState(
    initial.aggregationFunction ?? "weighted_avg",
  );
  const [compositeChildAxis, setCompositeChildAxis] = useState(
    initial.compositeChildAxis ?? "pass_fail",
  );
  const [childrenList, setChildrenList] = useState(initial.children ?? []);
  const [childWeights, setChildWeights] = useState(initial.childWeights ?? {});

  return (
    <CompositeDetailPanel
      editable
      name={name}
      description={description}
      aggregationEnabled={aggregationEnabled}
      aggregationFunction={aggregationFunction}
      compositeChildAxis={compositeChildAxis}
      childWeights={childWeights}
      children={childrenList}
      onNameChange={setName}
      onDescriptionChange={setDescription}
      onAggregationEnabledChange={setAggregationEnabled}
      onAggregationFunctionChange={setAggregationFunction}
      onCompositeChildAxisChange={setCompositeChildAxis}
      onChildrenChange={setChildrenList}
      onChildWeightsChange={setChildWeights}
    />
  );
}

describe("CompositeDetailPanel", () => {
  beforeEach(() => {
    capturedDrawerProps.current = null;
  });

  it("shows the weighted-average description by default and switches when a new aggregation method is selected", async () => {
    render(<Harness />);

    expect(
      screen.getByText(/Sum of \(score × weight\) divided by sum of weights/),
    ).toBeInTheDocument();

    await userEvent.click(screen.getByRole("combobox"));
    await userEvent.click(
      await screen.findByRole("option", { name: "Minimum (safety gate)" }),
    );

    expect(
      screen.getByText(/Composite equals the lowest child score/),
    ).toBeInTheDocument();
  });

  it("hides the aggregation function selector when aggregation is disabled", async () => {
    render(<Harness aggregationEnabled={false} />);

    expect(screen.queryByText("Aggregation function")).not.toBeInTheDocument();

    await userEvent.click(screen.getByRole("checkbox"));

    expect(await screen.findByText("Aggregation function")).toBeInTheDocument();
  });

  it("adding a child eval via the picker updates the visible children list", async () => {
    render(<Harness />);

    expect(screen.getByText("Children (0)")).toBeInTheDocument();

    await userEvent.click(screen.getByText("Add evaluation"));
    expect(capturedDrawerProps.current.open).toBe(true);

    await userEvent.click(screen.getByText("mock-pick-toxicity"));

    expect(await screen.findByText("Children (1)")).toBeInTheDocument();
    expect(screen.getByText("Toxicity Check")).toBeInTheDocument();
  });

  it("passes the locked filters for the current axis and existing child ids to the picker", async () => {
    render(
      <Harness
        children={[
          { child_id: "already-added", child_name: "Existing", order: 0, weight: 1 },
        ]}
      />,
    );

    await userEvent.click(screen.getByText("Add evaluation"));

    expect(capturedDrawerProps.current.lockedFilters).toEqual({
      output_type: ["pass_fail"],
      template_type: ["single"],
    });
    expect(capturedDrawerProps.current.existingEvals).toEqual([
      { id: "already-added" },
    ]);
  });

  it("removing a child removes it from the list and re-numbers the remaining ones", async () => {
    render(
      <Harness
        children={[
          { child_id: "c1", child_name: "First child", order: 0, weight: 1 },
          { child_id: "c2", child_name: "Second child", order: 1, weight: 1 },
        ]}
      />,
    );

    expect(screen.getByText("Children (2)")).toBeInTheDocument();

    const firstChildRow = screen.getByText("First child").closest("div");
    const firstChildDeleteButton = within(
      firstChildRow.parentElement,
    ).getByRole("button");
    await userEvent.click(firstChildDeleteButton);

    expect(await screen.findByText("Children (1)")).toBeInTheDocument();
    expect(screen.getByText("Second child")).toBeInTheDocument();
    expect(screen.queryByText("First child")).not.toBeInTheDocument();
    expect(screen.getByText("#1")).toBeInTheDocument();
  });
});
