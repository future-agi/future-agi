import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent } from "src/utils/test-utils";
import DeleteRowCellRenderer from "../DeleteRowCellRenderer";
import { CELL_STATE } from "../../common";

const auth = vi.hoisted(() => ({ role: "admin" }));

vi.mock("src/auth/hooks", () => ({
  useAuthContext: () => ({ role: auth.role }),
}));

vi.mock("src/components/svg-color", () => ({
  default: () => <span data-testid="svg" />,
}));

const E = CELL_STATE.EMPTY;
const emptyRow = { id: "1", _isLocal: true, TOPIC: E, "Output-v2": E };
const apiWith = (count) => ({ getDisplayedRowCount: () => count });

const renderRenderer = (props = {}) =>
  render(
    <DeleteRowCellRenderer
      data={emptyRow}
      api={apiWith(3)}
      onDelete={vi.fn()}
      {...props}
    />,
  );

describe("DeleteRowCellRenderer", () => {
  beforeEach(() => {
    auth.role = "admin";
  });

  it("renders the delete button for an unsaved row and calls onDelete with the row id", () => {
    const onDelete = vi.fn();
    renderRenderer({ onDelete });
    fireEvent.click(screen.getByRole("button", { name: /delete empty row/i }));
    expect(onDelete).toHaveBeenCalledWith("1");
  });

  it("renders nothing for viewer roles", () => {
    auth.role = "Viewer";
    renderRenderer();
    expect(screen.queryByRole("button")).toBeNull();
  });

  it("renders nothing for a persisted row", () => {
    renderRenderer({
      data: { id: "0", TOPIC: "UNICORN", "Output-v2": "a story" },
    });
    expect(screen.queryByRole("button")).toBeNull();
  });

  it("renders nothing for an empty row without the _isLocal flag", () => {
    renderRenderer({ data: { id: "0", TOPIC: E, "Output-v2": E } });
    expect(screen.queryByRole("button")).toBeNull();
  });

  it("renders nothing when only one row remains", () => {
    renderRenderer({ api: apiWith(1) });
    expect(screen.queryByRole("button")).toBeNull();
  });
});
