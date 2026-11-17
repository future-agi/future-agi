import React from "react";
import { cleanup, render, screen, within } from "@testing-library/react";
import { Table, TableBody } from "@mui/material";
import { createTheme, ThemeProvider } from "@mui/material/styles";
import { afterEach, describe, expect, it } from "vitest";

import AnnotationTaskTableRow from "src/sections/model/annotate/annotation-task-table-row";

// This is the historical consumer's prop boundary, not a routed task journey.
// The current router does not mount AnnotationTaskView. The wire contract is
// tested separately; no camel-case transport adapter is implied by this test.
const members = [
  { id: "member-same-org", name: "Same Member", email: "same@example.test" },
  { id: "member-multi-org", name: "Multi Member", email: "multi@example.test" },
];
const theme = createTheme({ customShadows: { z20: "none" } });

function renderTask(assignedUsers) {
  return render(
    <ThemeProvider theme={theme}>
      <Table>
        <TableBody>
          <AnnotationTaskTableRow
            row={{
              id: "task-id",
              taskName: "Synthetic task",
              aiModel: { userModelId: "Synthetic legacy model" },
              assignedUsers,
            }}
          />
        </TableBody>
      </Table>
    </ThemeProvider>,
  );
}

afterEach(cleanup);

describe("annotation task assignee avatar compatibility", () => {
  it("renders the same member initials from projected and historical members", () => {
    for (const assignedUsers of [
      members.map((member) => ({
        ...member,
        goals: ["private"],
        organization: { config: {} },
      })),
      members,
    ]) {
      renderTask(assignedUsers);
      const cells = within(screen.getByRole("row")).getAllByRole("cell");
      expect(cells[0]).toHaveTextContent("Synthetic task");
      expect(cells[1]).toHaveTextContent("Synthetic legacy model");
      expect(within(cells[3]).getByText("SM")).toBeVisible();
      expect(within(cells[3]).getByText("MM")).toBeVisible();
      expect(cells[3]).not.toHaveTextContent("private");
      cleanup();
    }
  });

  it("keeps the assignee cell empty for an unassigned task", () => {
    renderTask([]);
    const cells = within(screen.getByRole("row")).getAllByRole("cell");
    expect(cells[3].textContent).toBe("");
  });
});
