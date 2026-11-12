import React from "react";
import { describe, expect, it, vi } from "vitest";
import {
  render,
  screen,
  userEvent,
  within,
  fireEvent,
} from "src/utils/test-utils";
import { localInputToIso, isoToLocalInput } from "./explorerFilters";
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
  it.each(["true", "false"])(
    "round-trips every boolean value %s through an unrelated edit",
    (value) => {
      const onApply = vi.fn();
      const filters = {
        isError: value,
        cacheHit: value,
        guardrailTriggered: value,
      };
      render(
        <FilterPanel
          open
          onClose={vi.fn()}
          filters={filters}
          onApply={onApply}
          hideCacheHits
          hideGuardrails
        />,
      );
      fireEvent.change(screen.getByPlaceholderText("e.g., user_123"), {
        target: { value: "user-1" },
      });
      fireEvent.click(screen.getByRole("button", { name: "Apply" }));
      expect(onApply).toHaveBeenCalledWith({ ...filters, userId: "user-1" });
    },
  );

  it.each([
    ["isError", "Error"],
    ["cacheHit", "Cache hit"],
    ["guardrailTriggered", "Guardrail triggered"],
  ])(
    "lets an explicit No for %s be changed to Yes or cleared to Any",
    async (key, label) => {
      const user = userEvent.setup();
      const onApply = vi.fn();
      render(
        <FilterPanel
          open
          onClose={vi.fn()}
          filters={{ [key]: "false" }}
          onApply={onApply}
        />,
      );
      await user.click(screen.getByLabelText(label));
      await user.click(screen.getByRole("option", { name: "Yes" }));
      await user.click(screen.getByRole("button", { name: "Apply" }));
      expect(onApply).toHaveBeenLastCalledWith({ [key]: "true" });
      await user.click(screen.getByLabelText(label));
      await user.click(screen.getByRole("option", { name: "Any" }));
      await user.click(screen.getByRole("button", { name: "Apply" }));
      expect(onApply).toHaveBeenLastCalledWith({});
    },
  );

  it("clears explicit No flags with Clear All", () => {
    const onApply = vi.fn();
    render(
      <FilterPanel
        open
        onClose={vi.fn()}
        onApply={onApply}
        filters={{
          isError: "false",
          cacheHit: "false",
          guardrailTriggered: "false",
        }}
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: "Clear All" }));
    fireEvent.click(screen.getByRole("button", { name: "Apply" }));
    expect(onApply).toHaveBeenCalledWith({});
  });

  it("blocks a reversed custom range until corrected, preserving other filters", () => {
    const onApply = vi.fn();
    render(
      <FilterPanel
        open
        onClose={vi.fn()}
        onApply={onApply}
        filters={{
          search: "timeout",
          model: "gpt-4o",
          isError: "true",
          sort: "cost",
          page: "3",
        }}
      />,
    );
    fireEvent.change(screen.getByLabelText("Start Date"), {
      target: { value: "2026-11-09T12:00" },
    });
    fireEvent.change(screen.getByLabelText("End Date"), {
      target: { value: "2026-11-09T11:00" },
    });
    expect(
      screen.getByText("End time must be on or after start time."),
    ).toBeInTheDocument();
    const apply = screen.getByRole("button", { name: "Apply" });
    expect(apply).toBeDisabled();
    fireEvent.click(apply);
    expect(onApply).not.toHaveBeenCalled();
    fireEvent.change(screen.getByLabelText("End Date"), {
      target: { value: "2026-11-09T13:00" },
    });
    expect(apply).toBeEnabled();
    fireEvent.click(apply);
    expect(onApply).toHaveBeenCalledWith({
      search: "timeout",
      model: "gpt-4o",
      isError: "true",
      sort: "cost",
      startedAfter: new Date(2026, 10, 9, 12).toISOString(),
      startedBefore: new Date(2026, 10, 9, 13).toISOString(),
    });
  });

  it("keeps exact preset instants when Custom is applied without changing the dates", () => {
    const filters = {
      startedAfter: "2026-11-01T09:30:42.123Z",
      startedBefore: "2026-11-01T10:30:42.123Z",
    };
    const onApply = vi.fn();
    render(
      <FilterPanel
        open
        onClose={vi.fn()}
        filters={filters}
        onApply={onApply}
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: "Apply" }));
    expect(onApply).toHaveBeenCalledWith(filters);
  });

  it("continues to convert local times across daylight-saving changes", () => {
    for (const value of [
      "2026-03-08T01:30",
      "2026-03-08T03:30",
      "2026-11-01T00:30",
      "2026-11-01T02:30",
    ]) {
      expect(localInputToIso(value)).toBe(new Date(value).toISOString());
      expect(isoToLocalInput(localInputToIso(value))).toBe(value);
    }
  });

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
