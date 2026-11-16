import { describe, it, expect, vi } from "vitest";
import { screen, fireEvent, waitFor, within } from "@testing-library/react";
import { render } from "src/utils/test-utils";
import { scenarioFromApi } from "src/api/simulate-environments/scenarios";
import ScenarioEditor from "../ScenarioEditor";

const ROW_API = {
  id: "s1",
  scenario_key: "refund_lost",
  name: "refund_lost",
  instruction: "Ask for a refund.",
  tests: "refunds once",
  persona: { name: "Marcus Chen" },
  faults: [],
};
const ROW = scenarioFromApi(ROW_API);

// The tools a test condition can name, as the contract states them.
const ENV = {
  faultTargets: [
    {
      tool: "apply_discount",
      action: "crm.apply_discount",
      reachable: true,
      writes: true,
    },
    {
      tool: "lookup_order",
      action: "orders.lookup",
      reachable: true,
      writes: false,
    },
    {
      tool: "local_tool",
      action: "local_tool",
      reachable: false,
      writes: false,
    },
  ],
};

const EDITING = {
  editable_fields: ["tests", "faults"],
  persona_fields: [],
  rework_fields: ["faults"],
};

const openEditor = (
  onSave = vi.fn(async () => ({ refused: [] })),
  row = ROW,
) => {
  const onClose = vi.fn();
  render(
    <ScenarioEditor
      open
      row={row}
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
    expect(screen.getByText("Hidden · test conditions")).toBeInTheDocument();
    expect(screen.getByText("Tools behave normally")).toBeInTheDocument();
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

  it("names an added fault by its tool, with the route under it", () => {
    openEditor();
    fireEvent.click(
      screen.getByRole("button", { name: "Add a test condition" }),
    );
    pickTool("apply_discount");
    fireEvent.click(
      screen.getAllByRole("button", { name: "Add a test condition" }).at(-1),
    );
    const list = within(screen.getByTestId("test-conditions"));
    expect(
      list.getByText(
        "apply_discount: Times out before it runs on call 1 after 30 s",
      ),
    ).toBeInTheDocument();
    expect(list.getByText("crm.apply_discount")).toBeInTheDocument();
  });

  it("offers Runs, then the answer is lost only for a tool that changes records", async () => {
    openEditor();
    fireEvent.click(
      screen.getByRole("button", { name: "Add a test condition" }),
    );
    const whatHappens = () =>
      within(screen.getByTestId("test-conditions")).getAllByRole("combobox")[1];
    pickTool("apply_discount");
    fireEvent.mouseDown(whatHappens());
    fireEvent.click(
      screen.getByRole("option", { name: "Runs, then the answer is lost" }),
    );
    await waitFor(() => expect(screen.queryByRole("listbox")).toBeNull());
    // A read has nothing to lose: picking one takes the first mode it offers.
    pickTool("lookup_order");
    expect(whatHappens()).toHaveTextContent("Times out before it runs");
    await waitFor(() => expect(screen.queryByRole("listbox")).toBeNull());
    fireEvent.mouseDown(whatHappens());
    expect(
      screen.queryByRole("option", { name: "Runs, then the answer is lost" }),
    ).toBeNull();
    expect(
      screen.getByRole("option", { name: "Times out before it runs" }),
    ).toBeInTheDocument();
  });

  it("sends the numbers as typed, never clamped, so the server's refusal names them", async () => {
    const { onSave } = openEditor();
    fireEvent.click(
      screen.getByRole("button", { name: "Add a test condition" }),
    );
    pickTool("apply_discount");
    fireEvent.change(screen.getByLabelText("Delay in seconds"), {
      target: { value: "90" },
    });
    fireEvent.change(screen.getByLabelText("On which call"), {
      target: { value: "0" },
    });
    fireEvent.click(
      screen.getAllByRole("button", { name: "Add a test condition" }).at(-1),
    );
    fireEvent.click(screen.getByRole("button", { name: /save/i }));
    await waitFor(() => expect(onSave).toHaveBeenCalled());
    expect(onSave.mock.calls[0][0].changes[0].value).toEqual([
      {
        action: "crm.apply_discount",
        mode: "timeout_before_commit",
        on_call: 0,
        delay_seconds: 90,
      },
    ]);
  });

  it("keeps a stored condition's mode on its line even when its tool now only reads", () => {
    openEditor(
      undefined,
      scenarioFromApi({
        ...ROW_API,
        faults: [
          { action: "orders.lookup", mode: "timeout_after_commit", on_call: 1 },
        ],
      }),
    );
    expect(
      within(screen.getByTestId("test-conditions")).getByText(
        "lookup_order: Runs, then the answer is lost on call 1",
      ),
    ).toBeInTheDocument();
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
