import React from "react";
import { fireEvent, render, screen } from "src/utils/test-utils";
import { describe, expect, it, vi } from "vitest";
import TagInput from "../TagInput";

vi.mock("src/components/iconify", () => ({
  default: () => <span data-testid="iconify" />,
}));

const PROJECT_TAGS = [
  { name: "Production", color: "#8B5CF6" },
  { name: "Project Alpha", color: "#3B82F6" },
  { name: "Existing", color: "#06B6D4" },
];

describe("TagInput project tag suggestions", () => {
  it("shows matching suggestions and excludes tags already applied", () => {
    render(
      <TagInput
        onAdd={vi.fn()}
        existingNames={["existing"]}
        suggestions={PROJECT_TAGS}
        autoFocus={false}
      />,
    );

    fireEvent.change(screen.getByPlaceholderText("Add tag..."), {
      target: { value: "pro" },
    });

    expect(screen.getByRole("option", { name: "Production" })).toBeVisible();
    expect(screen.getByRole("option", { name: "Project Alpha" })).toBeVisible();
    expect(screen.queryByRole("option", { name: "Existing" })).toBeNull();
  });

  it("adds the exact selected tag and clears the input", () => {
    const onAdd = vi.fn();
    render(
      <TagInput onAdd={onAdd} suggestions={PROJECT_TAGS} autoFocus={false} />,
    );

    const input = screen.getByPlaceholderText("Add tag...");
    fireEvent.change(input, { target: { value: "prod" } });
    fireEvent.click(screen.getByRole("option", { name: "Production" }));

    expect(onAdd).toHaveBeenCalledWith(PROJECT_TAGS[0]);
    expect(input).toHaveValue("");
  });
});
