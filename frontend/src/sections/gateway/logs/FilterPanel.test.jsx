import React from "react";
import { describe, expect, it, vi } from "vitest";
import { render, screen, userEvent, within } from "src/utils/test-utils";
import FilterPanel from "./FilterPanel";

vi.mock("../hooks/useAvailableModels", () => ({
  useAvailableModels: () => [],
}));

vi.mock("./hooks/useMetadataValues", () => ({
  default: () => ({
    data: {
      application: ["checkout", "search"],
      service: ["answer", "recommendations"],
      tags: ["team:core", "team:growth"],
    },
  }),
}));

const pickerInput = (label) =>
  within(screen.getByText(label).parentElement).getByRole("combobox");

describe("FilterPanel", () => {
  it("applies the picked applications and custom tags as comma-separated filters", async () => {
    const user = userEvent.setup();
    const onApply = vi.fn();

    render(
      <FilterPanel
        open
        onClose={vi.fn()}
        filters={{ application: "checkout", tags: "team:growth" }}
        onApply={onApply}
      />,
    );

    await user.click(pickerInput("Application"));
    await user.click(screen.getByRole("option", { name: "search" }));

    const tagsInput = pickerInput("Custom Tags");
    await user.type(tagsInput, "nocolon{Enter}");
    expect(screen.queryByRole("button", { name: "nocolon" })).toBeNull();
    await user.clear(tagsInput);
    await user.type(tagsInput, "env:prod");

    await user.click(screen.getByRole("button", { name: "Apply" }));

    expect(onApply).toHaveBeenCalledWith({
      application: "checkout,search",
      tags: "team:growth,env:prod",
    });
  });

  it("leaves application, service and tags out when nothing is picked", async () => {
    const user = userEvent.setup();
    const onApply = vi.fn();

    render(
      <FilterPanel open onClose={vi.fn()} filters={{}} onApply={onApply} />,
    );

    await user.click(screen.getByRole("button", { name: "Apply" }));

    expect(onApply).toHaveBeenCalledWith({});
  });
});
