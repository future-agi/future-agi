import { describe, expect, it, vi } from "vitest";
import { render, screen, userEvent, within } from "src/utils/test-utils";
import ItemAssignmentPanel from "../annotate/item-assignment-panel";

vi.mock("src/components/iconify", () => ({
  default: ({ icon }) => <span data-testid="iconify" data-icon={icon} />,
}));

const annotators = [
  {
    user_id: "user-1",
    name: "Kartik",
    email: "kartik.nvj@futureagi.com",
  },
  {
    user_id: "user-2",
    name: "Nikhil",
    email: "nikhil@futureagi.com",
  },
];

describe("ItemAssignmentPanel", () => {
  it("shows unassigned state and lets an annotator assign the item to themself", async () => {
    const user = userEvent.setup();
    const onAssign = vi.fn();

    render(
      <ItemAssignmentPanel
        item={{ id: "item-1", assigned_users: [] }}
        annotators={annotators}
        currentUserId="user-1"
        canAnnotate
        onAssign={onAssign}
      />,
    );

    expect(screen.getByText("Unassigned")).toBeInTheDocument();
    expect(
      screen.queryByText(/Assigned to another annotator/i),
    ).not.toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: /assign to me/i }));

    expect(onAssign).toHaveBeenCalledWith({
      itemIds: ["item-1"],
      userIds: ["user-1"],
      action: "add",
    });
  });

  it("lets managers assign the item to another annotator", async () => {
    const user = userEvent.setup();
    const onAssign = vi.fn();

    render(
      <ItemAssignmentPanel
        item={{
          id: "item-1",
          assigned_users: [{ id: "user-1", name: "Kartik" }],
        }}
        annotators={annotators}
        currentUserId="user-1"
        canAnnotate
        canManageAssignments
        onAssign={onAssign}
      />,
    );

    await user.click(screen.getByRole("button", { name: /^assign$/i }));
    const list = screen.getByRole("list");
    await user.click(within(list).getByText("Nikhil"));
    await user.click(screen.getByRole("button", { name: /apply/i }));

    expect(onAssign).toHaveBeenCalledWith({
      itemIds: ["item-1"],
      userIds: ["user-1", "user-2"],
      action: "set",
    });
  });

  it("does not show the assignee picker to non-managers", () => {
    render(
      <ItemAssignmentPanel
        item={{
          id: "item-1",
          assigned_users: [{ id: "user-2", name: "Nikhil" }],
        }}
        annotators={annotators}
        currentUserId="user-1"
        canAnnotate
        onAssign={vi.fn()}
      />,
    );

    expect(
      screen.queryByRole("button", { name: /^assign$/i }),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: /assign to me/i }),
    ).not.toBeInTheDocument();
  });
});
