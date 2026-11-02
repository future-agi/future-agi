import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, within } from "@testing-library/react";
import useUiSample from "src/sections/simulate/environments/hooks/useUiSample";
import EvalMappingView from "../EvalMappingView";
import { NO_MISSELLING } from "./fixtures/evalEntries";

vi.mock("src/sections/simulate/environments/hooks/useUiSample", () => ({
  default: vi.fn(),
}));

// An `evaluations.selected[]` row as the Evaluations tab holds it.
const ITEM = { ...NO_MISSELLING, id: "cfg-1", runnable: true, editable: true };

const setup = (props = {}) => {
  const onClose = vi.fn();
  const onEdit = vi.fn();
  render(
    <EvalMappingView
      open
      item={ITEM}
      onClose={onClose}
      onEdit={onEdit}
      canEdit
      {...props}
    />,
  );
  return { onClose, onEdit };
};

beforeEach(() => {
  useUiSample.mockReset();
  useUiSample.mockReturnValue(false);
});

describe("EvalMappingView", () => {
  it("pencil opens the read-only mapping with Scoring line and an Edit button into the full config", () => {
    const { onEdit, onClose } = setup();

    expect(screen.getByText("no_misselling")).toBeInTheDocument();
    expect(screen.getByText(NO_MISSELLING.description)).toBeInTheDocument();

    // The mapping, as the API labels it, and nothing to change it with.
    expect(screen.getByText("Variable mapping")).toBeInTheDocument();
    expect(screen.getByText("{{agent_prompt}}")).toBeInTheDocument();
    expect(screen.getByText("Agent instructions")).toBeInTheDocument();
    expect(screen.getByText("{{conversation}}")).toBeInTheDocument();
    expect(screen.getByText("Call recording")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /^Remove/ })).toBeNull();
    expect(screen.queryByRole("textbox")).toBeNull();

    // Scoring: the API sends no threshold, and this row carries no judge
    // model, so both are named as coming.
    expect(screen.getByText("Scoring")).toBeInTheDocument();
    expect(screen.getByText("Pass threshold soon")).toBeInTheDocument();
    expect(screen.getByText("Judge model soon")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Edit" }));
    expect(onEdit).toHaveBeenCalledWith(ITEM);

    // The drawer's own X and the footer's Close both close it.
    const closes = screen.getAllByRole("button", { name: "Close" });
    fireEvent.click(closes[closes.length - 1]);
    expect(onClose).toHaveBeenCalled();
  });

  it("shows a judge model the row carries, and reads a run test's raw mapping", () => {
    setup({
      item: {
        id: "c1",
        name: "tone_check",
        model: "turing_large",
        mapping: { conversation: "voice_recording" },
      },
    });

    expect(screen.getByText(/judged by turing_large/)).toBeInTheDocument();
    expect(screen.queryByText("Judge model soon")).toBeNull();
    expect(screen.getByText("conversation")).toBeInTheDocument();
    expect(screen.getByText("voice_recording")).toBeInTheDocument();
  });

  it("says when nothing is mapped", () => {
    setup({ item: { id: "c2", name: "task_done", inputs: [] } });

    expect(
      screen.getByText(
        "Nothing mapped. This evaluation reads the run as a whole.",
      ),
    ).toBeInTheDocument();
  });

  it("holds Edit when the eval can't be edited, and says why", async () => {
    const { onEdit } = setup({
      canEdit: false,
      editTooltip: "Set by the harness, so it can't be edited here.",
    });

    const edit = screen.getByRole("button", { name: "Edit" });
    expect(edit).toBeDisabled();
    fireEvent.click(edit);
    expect(onEdit).not.toHaveBeenCalled();
    fireEvent.mouseOver(edit.parentElement);
    expect(
      await screen.findByText(
        "Set by the harness, so it can't be edited here.",
      ),
    ).toBeInTheDocument();
  });

  it("eval history is flagged and only in sample mode", () => {
    setup();
    expect(screen.queryByText("History")).toBeNull();
    expect(screen.queryByLabelText("UI only")).toBeNull();
    expect(useUiSample).toHaveBeenCalledWith("evalHistory");

    useUiSample.mockReturnValue(true);
    setup();
    const history = screen.getByRole("region", { name: "History" });
    expect(within(history).getByLabelText("UI only")).toBeInTheDocument();
    expect(
      within(history).getByText("Added no_misselling"),
    ).toBeInTheDocument();
  });
});
