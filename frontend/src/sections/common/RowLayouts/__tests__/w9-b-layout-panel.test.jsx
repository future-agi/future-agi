import React from "react";
import { describe, expect, it, vi } from "vitest";
import { fireEvent, screen, within } from "@testing-library/react";
import { render } from "src/utils/test-utils";
import LayoutPanel, { VIEWER_SHARE_HINT } from "../LayoutPanel";
import { defaultLayout } from "../layoutModel";
import { targetColumns } from "../layoutMatch";

const COLS = [
  { id: "c1", name: "customer_message", section: "input", editable: true },
  { id: "c3", name: "expected_reply", section: "expected", editable: true },
];
const props = (over = {}) => ({
  open: true,
  draft: defaultLayout(COLS),
  onDraft: vi.fn(),
  problems: [],
  viewer: false,
  editing: null,
  saving: false,
  onSave: vi.fn(),
  onClose: vi.fn(),
  ...over,
});

describe("LayoutPanel", () => {
  it("saves for everyone by default with the typed name", () => {
    const p = props();
    render(<LayoutPanel {...p} />);
    const save = screen.getByTestId("layout-save");
    expect(save).toBeDisabled(); // no name yet
    fireEvent.change(screen.getByTestId("layout-name"), {
      target: { value: " Refund review " },
    });
    fireEvent.click(save);
    expect(p.onSave).toHaveBeenCalledWith("Refund review", true, false);
  });

  it("a viewer can save only for themselves, and is told why", () => {
    const p = props({ viewer: true });
    render(<LayoutPanel {...p} />);
    expect(
      screen.getByLabelText("For everyone on this dataset"),
    ).toBeDisabled();
    expect(screen.getByText(VIEWER_SHARE_HINT)).toBeTruthy();
    fireEvent.change(screen.getByTestId("layout-name"), {
      target: { value: "Mine" },
    });
    fireEvent.click(screen.getByTestId("layout-save"));
    expect(p.onSave).toHaveBeenCalledWith("Mine", false, false);
  });

  it("shows the problems and blocks Save while there are any", () => {
    render(
      <LayoutPanel
        {...props({ problems: ["Every field is hidden; show at least one."] })}
      />,
    );
    fireEvent.change(screen.getByTestId("layout-name"), {
      target: { value: "X" },
    });
    expect(screen.getByTestId("layout-problems").textContent).toContain(
      "Every field is hidden",
    );
    expect(screen.getByTestId("layout-save")).toBeDisabled();
  });

  it("toggles a field, sets its view and side through onDraft", () => {
    const p = props({ draft: { ...defaultLayout(COLS), columns: 2 } });
    render(<LayoutPanel {...p} />);
    const row = screen.getByTestId("panel-field-expected_reply");
    fireEvent.click(within(row).getByLabelText("Hide expected_reply"));
    expect(p.onDraft.mock.calls[0][0].fields[1].hidden).toBe(true);
    expect(within(row).getByLabelText("Side of expected_reply")).toBeTruthy();
    // Custom is offered only once the layout has a renderer.
    fireEvent.mouseDown(within(row).getByLabelText("View expected_reply as"));
    const listbox = screen.getByRole("listbox");
    expect(within(listbox).queryByText("Custom")).toBeNull();
    fireEvent.click(within(listbox).getByText("Markdown"));
    expect(p.onDraft.mock.calls[1][0].fields[1].view).toBe("markdown");
  });

  it("editing a layout under its own name says Save changes", () => {
    const p = props({
      editing: { id: "v1", name: "Refund review", shared: false },
    });
    render(<LayoutPanel {...p} />);
    expect(screen.getByTestId("layout-save").textContent).toBe("Save changes");
    expect(screen.getByLabelText("Just for me")).toBeChecked();
    fireEvent.click(screen.getByTestId("layout-save"));
    expect(p.onSave).toHaveBeenCalledWith("Refund review", false, true);
  });
});

describe("targetColumns", () => {
  it("marks user columns editable and run outputs not", () => {
    expect(
      targetColumns(
        [{ id: "a", name: "q" }],
        [
          { id: "a", name: "q" },
          { id: "o", name: "answer" },
        ],
      ),
    ).toEqual([
      { id: "a", name: "q", editable: true },
      { id: "o", name: "answer", editable: false },
    ]);
  });
});
