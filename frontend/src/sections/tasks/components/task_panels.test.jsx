import React from "react";
import { describe, expect, it, vi } from "vitest";
import { render, screen, userEvent } from "src/utils/test-utils";
import TaskPanels from "./task_panels";

vi.mock("@mui/material", async (original) => ({
  ...(await original()),
  useMediaQuery: () => true,
}));
describe("task-only responsive workspace", () => {
  it("switches panes by keyboard without a resize and keeps draft mapping inputs mounted", async () => {
    render(
      <TaskPanels
        leftPanel={<input aria-label="Draft name" defaultValue="saved" />}
        rightPanel={<input aria-label="Row mapping" defaultValue="path" />}
      />,
    );
    await userEvent.type(
      screen.getByRole("textbox", { name: "Draft name" }),
      " draft",
    );
    const tab = screen.getByRole("tab", { name: "Task settings" });
    tab.focus();
    await userEvent.keyboard("{ArrowRight}{Enter}");
    expect(screen.getByRole("tab", { name: "Matching row" })).toHaveAttribute(
      "aria-selected",
      "true",
    );
    expect(screen.getByRole("tabpanel")).toHaveAccessibleName("Matching row");
    expect(screen.getByRole("textbox", { name: "Row mapping" })).toHaveValue(
      "path",
    );
    expect(screen.getByLabelText("Draft name")).toHaveValue("saved draft");
    await userEvent.keyboard("{ArrowLeft}{Enter}");
    expect(screen.getByRole("tabpanel")).toHaveAccessibleName("Task settings");
    expect(screen.getByRole("textbox", { name: "Draft name" })).toHaveValue(
      "saved draft",
    );
  });
});
