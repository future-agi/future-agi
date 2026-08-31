import { describe, it, expect, vi } from "vitest";
import { render, screen, userEvent, within } from "src/utils/test-utils";
import SessionPicker from "../SessionPicker";

const sessions = [
  { id: "s1", agent: "drive_thru" },
  { id: "s2", agent: "support_bot" },
];

const props = {
  sessions,
  openSessionId: "s1",
  busy: false,
  onOpen: () => {},
  onCreate: () => {},
  onDelete: () => {},
};

describe("SessionPicker", () => {
  it("says there is no session when none exists", () => {
    render(<SessionPicker {...props} sessions={[]} openSessionId={null} />);
    expect(screen.getByText(/no session/i)).toBeInTheDocument();
  });

  it("names the open session", () => {
    render(<SessionPicker {...props} />);
    expect(screen.getByText("drive_thru")).toBeInTheDocument();
  });

  it("starts a new session", async () => {
    const onCreate = vi.fn();
    render(<SessionPicker {...props} onCreate={onCreate} />);
    await userEvent.click(screen.getByRole("button", { name: /new/i }));
    expect(onCreate).toHaveBeenCalledTimes(1);
  });

  it("refuses every control while the harness is mid-turn", () => {
    render(<SessionPicker {...props} busy />);
    expect(screen.getByRole("button", { name: /new/i })).toBeDisabled();
    expect(screen.getByRole("button", { name: /^delete$/i })).toBeDisabled();
  });

  it("deletes only after confirmation", async () => {
    const onDelete = vi.fn();
    render(<SessionPicker {...props} onDelete={onDelete} />);
    await userEvent.click(screen.getByRole("button", { name: /^delete$/i }));
    expect(onDelete).not.toHaveBeenCalled();
    // Both the toolbar and the dialog say "Delete", so scope the confirm to the dialog.
    const dialog = screen.getByRole("dialog");
    await userEvent.click(within(dialog).getByRole("button", { name: /^delete$/i }));
    expect(onDelete).toHaveBeenCalledWith("s1");
  });
});
