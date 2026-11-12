import React from "react";
import { describe, expect, it, vi } from "vitest";
import { render, screen } from "src/utils/test-utils";

import SelectProject from "../SelectProject";

// L05: in dark theme, Create alert > "Choose a project" showed the "Project"
// label on a full-width dark bar. The field painted --bg-paper behind its
// label, but the label sits above the field on the dialog surface, which is
// a different colour. The label must carry no background of its own.

vi.mock("src/sections/projects/LLMTracing/common", async (importOriginal) => ({
  ...(await importOriginal()),
  useProjectList: () => ({
    data: [{ id: "p1", name: "checkout-service" }],
    isLoading: false,
  }),
}));

describe("Choose a project dialog", () => {
  it("draws the Project label on the dialog surface, with no background bar", () => {
    render(
      <SelectProject
        open
        value={null}
        onChange={vi.fn()}
        onClose={vi.fn()}
        onAction={vi.fn()}
      />,
    );

    const label = screen.getByText("Project", { selector: "label" });
    expect(label).not.toBeNull();
    expect(label.style.background).toBe("");
    expect(label.style.backgroundColor).toBe("");
  });
});
