import { describe, it, expect, vi } from "vitest";
import { screen, fireEvent, waitFor, within } from "@testing-library/react";
import { render } from "src/utils/test-utils";
import { scenarioFromApi } from "src/api/simulate-environments/scenarios";
import ScenarioEditor from "../ScenarioEditor";

const ROW = scenarioFromApi({
  id: "s1",
  scenario_key: "refund_lost",
  name: "refund_lost",
  instruction: "Ask for a refund.",
  tests: "refunds once",
  persona: { name: "Marcus Chen" },
  faults: [],
});

const ENV = {
  doorReach: { apply_discount: true, local_tool: false },
  toolEntrypoints: [
    { tool: "apply_discount", mode: "service", endpoint: "crm/apply_discount" },
    { tool: "local_tool", mode: "unreachable" },
  ],
};

const EDITING = {
  editable_fields: ["tests", "faults"],
  persona_fields: [],
  rework_fields: ["faults"],
};

const openEditor = (onSave = vi.fn(async () => ({ refused: [] }))) => {
  const onClose = vi.fn();
  render(
    <ScenarioEditor
      open
      row={ROW}
      env={ENV}
      onClose={onClose}
      onSave={onSave}
      scenarioEditing={EDITING}
    />,
  );
  return { onSave, onClose };
};

const pickTool = (name) => {
  fireEvent.mouseDown(
    within(screen.getByTestId("test-conditions")).getAllByRole("combobox")[0],
  );
  fireEvent.click(screen.getByRole("option", { name }));
};

describe("ScenarioEditor test conditions", () => {
  it("adds a fault and saves it with the scenario", async () => {
    const { onSave, onClose } = openEditor();
    expect(
      screen.getByText("No test conditions. The tools answer normally."),
    ).toBeInTheDocument();
    fireEvent.click(
      screen.getByRole("button", { name: "Add a test condition" }),
    );
    pickTool("apply_discount");
    fireEvent.click(
      screen.getAllByRole("button", { name: "Add a test condition" }).at(-1),
    );
    fireEvent.click(screen.getByRole("button", { name: /save/i }));
    await waitFor(() => expect(onSave).toHaveBeenCalled());
    const [{ changes, rework }] = onSave.mock.calls[0];
    expect(rework).toBe(true);
    expect(changes).toEqual([
      {
        op: "set_field",
        scenario: "refund_lost",
        field: "faults",
        value: [
          {
            action: "crm.apply_discount",
            mode: "timeout_before_commit",
            on_call: 1,
            delay_seconds: 30,
          },
        ],
      },
    ]);
    await waitFor(() => expect(onClose).toHaveBeenCalled());
  });

  it("disables tools that run inside the agent", () => {
    openEditor();
    fireEvent.click(
      screen.getByRole("button", { name: "Add a test condition" }),
    );
    fireEvent.mouseDown(
      within(screen.getByTestId("test-conditions")).getAllByRole("combobox")[0],
    );
    expect(screen.getByRole("option", { name: "local_tool" })).toHaveAttribute(
      "aria-disabled",
      "true",
    );
    expect(
      screen.getByRole("option", { name: "apply_discount" }),
    ).not.toHaveAttribute("aria-disabled", "true");
  });

  it("shows the server's reason when a fault is refused", async () => {
    const onSave = vi.fn(async () => ({
      refused: [
        {
          field: "faults",
          why: "the test world cannot apply this test condition: on_call must be a whole number from 1",
        },
      ],
    }));
    const { onClose } = openEditor(onSave);
    fireEvent.click(
      screen.getByRole("button", { name: "Add a test condition" }),
    );
    pickTool("apply_discount");
    fireEvent.click(
      screen.getAllByRole("button", { name: "Add a test condition" }).at(-1),
    );
    fireEvent.click(screen.getByRole("button", { name: /save/i }));
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "on_call must be a whole number from 1",
    );
    expect(onClose).not.toHaveBeenCalled();
  });
});
