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

// Each mode as the server lists it: the fields it takes and what it means for the agent.
const BEFORE = {
  mode: "timeout_before_commit",
  label: "Times out before it runs",
  fields: ["on_call", "delay_seconds"],
  hint: "The call waits, then times out.",
};
const AFTER = {
  mode: "timeout_after_commit",
  label: "Runs, then the answer is lost",
  fields: ["on_call", "delay_seconds"],
  hint: "The world makes the change, then the agent hears a timeout.",
};
const ERROR = {
  mode: "error",
  label: "Answers with an error",
  fields: ["on_call", "status", "message"],
  hint: "The service answers with the status and message you set.",
};
const DEFAULTS = { on_call: 1, delay_seconds: 30, status: 500 };
const BOUNDS = {
  on_call: { min: 1 },
  delay_seconds: { min: 0, max: 30 },
  status: { min: 400, max: 599 },
  message: { max_length: 500 },
};
const target = (tool, action, reachable, modes) => ({
  tool,
  action,
  reachable,
  writes: modes.includes(AFTER),
  modes,
  defaults: DEFAULTS,
  bounds: BOUNDS,
});

const ENV = {
  faultTargets: [
    target("apply_discount", "crm.apply_discount", true, [BEFORE, AFTER, ERROR]),
    target("lookup_order", "orders.lookup", true, [BEFORE, ERROR]),
    target("local_tool", "local_tool", false, []),
  ],
};

const EDITING = {
  editable_fields: ["tests", "faults"],
  persona_fields: [],
  rework_fields: ["faults"],
};

const openEditor = (
  onSave = vi.fn(async () => ({ refused: [], failed: false })),
  row = ROW,
  env = ENV,
) => {
  const onClose = vi.fn();
  render(
    <ScenarioEditor
      open
      row={row}
      env={env}
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
        "apply_discount: Times out before it runs",
      ),
    ).toBeInTheDocument();
    expect(list.getByText("crm.apply_discount")).toBeInTheDocument();
  });

  it("offers each tool only the modes the server says the world can apply to it", async () => {
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
    // A tool that does not offer the picked mode takes the first one it does.
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
          {
            action: "orders.lookup",
            tool: "lookup_order",
            mode: "timeout_after_commit",
            mode_label: "Runs, then the answer is lost",
            line: "lookup_order: Runs, then the answer is lost on call 1",
            on_call: 1,
            delay_seconds: null,
            status: null,
            message: "",
          },
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
          problems: [],
        },
      ],
      failed: false,
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

  it("fills a picked tool's numbers from its own defaults and bounds the inputs by them", () => {
    openEditor();
    fireEvent.click(
      screen.getByRole("button", { name: "Add a test condition" }),
    );
    pickTool("apply_discount");
    const delay = screen.getByLabelText("Delay in seconds");
    expect(delay).toHaveValue(30);
    expect(delay).toHaveAttribute("max", "30");
    expect(screen.getByLabelText("On which call")).toHaveValue(1);
  });

  it("sends a stored condition back as the scenario file keeps it, unchanged when only another field changes", async () => {
    const { onSave } = openEditor(
      undefined,
      scenarioFromApi({
        ...ROW_API,
        faults: [
          {
            action: "crm.apply_discount",
            tool: "apply_discount",
            mode: "error",
            mode_label: "Answers with an error",
            line: "apply_discount: Answers with an error on call 2 (503)",
            on_call: 2,
            delay_seconds: null,
            status: 503,
            message: "",
          },
        ],
      }),
    );
    fireEvent.click(
      within(screen.getByTestId("test-conditions")).getByRole("button", {
        name: "Remove",
      }),
    );
    fireEvent.click(screen.getByRole("button", { name: /save/i }));
    await waitFor(() => expect(onSave).toHaveBeenCalled());
    expect(onSave.mock.calls[0][0].changes[0].value).toEqual([]);
  });

  it("keeps the editor open with the edits when the save fails, and sends one save per click while one is in flight", async () => {
    let answer;
    const onSave = vi.fn(
      () =>
        new Promise((resolve) => {
          answer = resolve;
        }),
    );
    const { onClose } = openEditor(onSave);
    fireEvent.click(
      screen.getByRole("button", { name: "Add a test condition" }),
    );
    pickTool("apply_discount");
    fireEvent.click(
      screen.getAllByRole("button", { name: "Add a test condition" }).at(-1),
    );
    const save = screen.getByRole("button", { name: /save/i });
    fireEvent.click(save);
    expect(save).toBeDisabled();
    fireEvent.click(save);
    expect(onSave).toHaveBeenCalledTimes(1);
    answer({ refused: [], failed: true });
    await waitFor(() => expect(save).toBeEnabled());
    expect(onClose).not.toHaveBeenCalled();
    expect(
      within(screen.getByTestId("test-conditions")).getByText(
        /apply_discount: Times out before it runs/,
      ),
    ).toBeInTheDocument();
  });

  it("shows only the fields the picked mode takes, with its hint and bounds, and sends each as typed", async () => {
    const SLOW = {
      mode: "slow",
      label: "Answers slowly",
      fields: ["on_call", "delay_seconds"],
      hint: "The answer comes late but comes.",
    };
    const { onSave } = openEditor(undefined, ROW, {
      faultTargets: [target("apply_discount", "crm.apply_discount", true, [ERROR, SLOW])],
    });
    fireEvent.click(
      screen.getByRole("button", { name: "Add a test condition" }),
    );
    pickTool("apply_discount");
    // An error takes a status and a message, never a delay.
    expect(
      screen.getByText("The service answers with the status and message you set."),
    ).toBeInTheDocument();
    expect(screen.queryByLabelText("Delay in seconds")).toBeNull();
    expect(screen.getByLabelText("Message")).toHaveAttribute("maxlength", "500");
    expect(screen.getByLabelText("On which call")).toHaveAttribute("min", "1");
    // A mode the FE has never heard of shows the fields the server lists for it.
    fireEvent.mouseDown(
      within(screen.getByTestId("test-conditions")).getAllByRole("combobox")[1],
    );
    fireEvent.click(screen.getByRole("option", { name: "Answers slowly" }));
    expect(screen.getByText("The answer comes late but comes.")).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText("Delay in seconds"), {
      target: { value: "" },
    });
    fireEvent.click(
      screen.getAllByRole("button", { name: "Add a test condition" }).at(-1),
    );
    fireEvent.click(screen.getByRole("button", { name: /save/i }));
    await waitFor(() => expect(onSave).toHaveBeenCalled());
    expect(onSave.mock.calls[0][0].changes[0].value).toEqual([
      { action: "crm.apply_discount", mode: "slow", on_call: 1, delay_seconds: null },
    ]);
  });

  it("takes the approval a mode needs from the served choices and its conditions as JSON, and sends both", async () => {
    const APPROVAL = {
      mode: "approval_required",
      label: "Needs approval first",
      fields: ["approval", "approved_when"],
      hint: "The call is refused until the approval is recorded.",
      approval_options: [
        { value: "approvals.put", label: "A manager's approval" },
      ],
    };
    const { onSave } = openEditor(undefined, ROW, {
      faultTargets: [
        target("apply_discount", "crm.apply_discount", true, [APPROVAL]),
        target("lookup_order", "orders.lookup", true, [BEFORE]),
      ],
    });
    fireEvent.click(
      screen.getByRole("button", { name: "Add a test condition" }),
    );
    pickTool("apply_discount");
    fireEvent.mouseDown(screen.getByLabelText("Approval it needs"));
    fireEvent.click(
      screen.getByRole("option", { name: "A manager's approval" }),
    );
    const conditions = screen.getByLabelText("Approved when (optional)");
    const add = () =>
      screen.getAllByRole("button", { name: "Add a test condition" }).at(-1);
    fireEvent.change(conditions, { target: { value: "status approved" } });
    expect(add()).toBeDisabled();
    fireEvent.change(conditions, {
      target: { value: '{"status": "approved"}' },
    });
    expect(add()).toBeEnabled();
    fireEvent.click(add());
    fireEvent.click(screen.getByRole("button", { name: /save/i }));
    await waitFor(() => expect(onSave).toHaveBeenCalled());
    expect(onSave.mock.calls[0][0].changes[0].value).toEqual([
      {
        action: "crm.apply_discount",
        mode: "approval_required",
        approval: "approvals.put",
        approved_when: { status: "approved" },
      },
    ]);
  });

  it("offers a mode only for the tools the server serves it on", () => {
    openEditor(undefined, ROW, {
      faultTargets: [target("lookup_order", "orders.lookup", true, [BEFORE])],
    });
    fireEvent.click(
      screen.getByRole("button", { name: "Add a test condition" }),
    );
    pickTool("lookup_order");
    fireEvent.mouseDown(
      within(screen.getByTestId("test-conditions")).getAllByRole("combobox")[1],
    );
    expect(
      screen.queryByRole("option", { name: "Needs approval first" }),
    ).toBeNull();
  });

  it("places each fault problem under the fault and field it names, not in one alert above the list", async () => {
    const onSave = vi.fn(async () => ({
      refused: [
        {
          field: "faults",
          why: "Two test conditions cannot be applied",
          problems: [
            { index: 1, field: "delay_seconds", message: "At most 30" },
            { index: 0, field: null, message: "This tool cannot be held" },
          ],
        },
      ],
      failed: false,
    }));
    const stored = (line, action) => ({
      action,
      tool: action,
      mode: "timeout_before_commit",
      mode_label: "Times out before it runs",
      line,
      on_call: 1,
      delay_seconds: 10,
      status: null,
      message: "",
    });
    openEditor(
      onSave,
      scenarioFromApi({
        ...ROW_API,
        faults: [
          stored("first: Times out", "first"),
          stored("second: Times out", "second"),
        ],
      }),
    );
    // A third condition makes the edit; the refusal names the two stored ones by their place in the list.
    fireEvent.click(
      screen.getByRole("button", { name: "Add a test condition" }),
    );
    pickTool("apply_discount");
    fireEvent.click(
      screen.getAllByRole("button", { name: "Add a test condition" }).at(-1),
    );
    fireEvent.click(screen.getByRole("button", { name: /save/i }));
    const delay = await screen.findByText("Delay in seconds: At most 30");
    expect(delay.parentElement).toHaveTextContent("second: Times out");
    expect(delay.parentElement).not.toHaveTextContent("first: Times out");
    expect(
      screen.getByText("This tool cannot be held").parentElement,
    ).toHaveTextContent("first: Times out");
    expect(screen.queryByText("Two test conditions cannot be applied")).toBeNull();
  });
});
