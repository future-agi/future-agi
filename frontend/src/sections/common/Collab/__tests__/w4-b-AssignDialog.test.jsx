// W4-2 (Lane B): AssignDialog: title, members with No access disabled,
// quick due picks, the move line and the POST body.
import React from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";

const create = vi.fn().mockResolvedValue({ created: 2 });
vi.mock("src/api/develop/row-collab", () => ({
  collabError: (e, f) => f,
  useAssignments: () => ({
    data: {
      assignments: [
        {
          id: "a1",
          row_id: "r1",
          status: "open",
          assignee: { id: "u-asha", name: "Asha Rao" },
        },
      ],
      members: [
        { id: "u-meera", name: "Meera Iyer", can_view: true },
        { id: "u-alice", name: "Alice Park", can_view: false },
      ],
      limits: { max_rows: 500 },
    },
  }),
  useCreateAssignments: () => ({ mutateAsync: create, isPending: false }),
}));
const notify = vi.fn();
vi.mock("src/sections/common/ux/toast", () => ({
  notify: (...a) => notify(...a),
  default: vi.fn(),
}));

const { default: AssignDialog } = await import("../AssignDialog");

afterEach(cleanup);

describe("AssignDialog", () => {
  it("assigns rows to a member with a due date and a note", async () => {
    const onClose = vi.fn();
    render(
      <AssignDialog
        open
        onClose={onClose}
        datasetId="ds"
        rowIds={["r1", "r2"]}
      />,
    );
    expect(screen.getByText("Assign 2 rows", { selector: "h2" })).toBeTruthy();
    fireEvent.mouseDown(screen.getByRole("combobox"));
    const list = within(screen.getByRole("listbox"));
    expect(
      list.getByText("Alice Park").closest("li").getAttribute("aria-disabled"),
    ).toBe("true");
    fireEvent.click(list.getByText("Meera Iyer"));
    expect(screen.getByTestId("assign-move-line").textContent).toBe(
      "1 of these rows is assigned to Asha Rao; it moves to Meera Iyer.",
    );
    fireEvent.click(screen.getByTestId("assign-due-tomorrow"));
    fireEvent.change(screen.getByTestId("assign-note"), {
      target: { value: "Check the refund timeline" },
    });
    fireEvent.click(screen.getByTestId("assign-submit"));
    await waitFor(() => expect(create).toHaveBeenCalled());
    const body = create.mock.calls[0][0];
    expect(body).toMatchObject({
      row_ids: ["r1", "r2"],
      assignee_id: "u-meera",
      note: "Check the refund timeline",
    });
    expect(body.due_at).toMatch(/T/);
    await waitFor(() => expect(onClose).toHaveBeenCalled());
    expect(notify.mock.calls[0][0]).toMatch(
      /^Assigned 2 rows to Meera Iyer · due /,
    );
  });
});
