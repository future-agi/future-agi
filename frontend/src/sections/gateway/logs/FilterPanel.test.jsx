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

  it("sends the date range as instants in the viewer's zone", async () => {
    const user = userEvent.setup();
    const onApply = vi.fn();
    const start = new Date(2026, 9, 20, 10, 30);
    render(
      <FilterPanel
        open
        onClose={vi.fn()}
        filters={{ startedAfter: start.toISOString() }}
        onApply={onApply}
      />,
    );
    // The input shows local time, not the UTC string.
    expect(screen.getByLabelText("Start Date")).toHaveValue("2026-10-20T10:30");
    const end = screen.getByLabelText("End Date");
    await user.clear(end);
    await user.type(end, "2026-10-20T11:30");
    await user.click(screen.getByRole("button", { name: "Apply" }));
    expect(onApply).toHaveBeenCalledWith({
      startedAfter: start.toISOString(),
      startedBefore: new Date(2026, 9, 20, 11, 30).toISOString(),
    });
  });
});
