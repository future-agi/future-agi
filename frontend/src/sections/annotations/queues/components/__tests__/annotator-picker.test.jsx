import { useState } from "react";
import { describe, it, expect, vi } from "vitest";
import { render, screen, userEvent, within } from "src/utils/test-utils";
import AnnotatorPicker from "../annotator-picker";

vi.mock("src/auth/hooks", () => ({
  useAuthContext: () => ({
    user: { organization: { id: "org-1" } },
  }),
}));

vi.mock("src/api/annotation-queues/annotation-queues", () => ({
  useOrgMembersInfinite: () => ({
    data: [
      { id: "user-1", name: "Alice", email: "alice@example.com" },
      { id: "user-2", name: "Bob", email: "bob@example.com" },
    ],
    fetchNextPage: vi.fn(),
    hasNextPage: false,
    isFetchingNextPage: false,
  }),
}));

vi.mock("src/components/iconify", () => ({
  default: ({ icon, ...props }) => (
    <span data-testid="iconify" data-icon={icon} {...props} />
  ),
}));

describe("AnnotatorPicker", () => {
  it("shows and edits multiple roles for one member", async () => {
    const user = userEvent.setup();
    const onChange = vi.fn();

    function Harness() {
      const [value, setValue] = useState([
        {
          userId: "user-1",
          role: "manager",
          roles: ["manager", "reviewer", "annotator"],
        },
      ]);
      return (
        <AnnotatorPicker
          value={value}
          creatorId="user-1"
          onChange={(next) => {
            onChange(next);
            setValue(next);
          }}
        />
      );
    }

    render(<Harness />);

    const creatorRow = within(screen.getByTestId("annotator-row-user-1"));
    expect(creatorRow.getByLabelText("Manager")).toBeChecked();
    expect(creatorRow.getByLabelText("Reviewer")).toBeChecked();
    expect(creatorRow.getByLabelText("Annotator")).toBeChecked();

    const bobRow = within(screen.getByTestId("annotator-row-user-2"));
    await user.click(bobRow.getByLabelText("Reviewer"));

    expect(onChange).toHaveBeenLastCalledWith([
      {
        userId: "user-1",
        role: "manager",
        roles: ["manager", "reviewer", "annotator"],
      },
      {
        userId: "user-2",
        role: "reviewer",
        roles: ["reviewer"],
      },
    ]);
  });
});
