import React from "react";
import { describe, expect, it, vi } from "vitest";
import { act, fireEvent, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import QueryCatalog from "./QueryCatalog";
import { searchCatalog } from "./catalogSearch";

const datasets = [
  {
    name: "spans",
    available: true,
    description:
      "One live span in the selected start-time window; no prompt or output payloads.",
    columns: [
      { name: "name", type: "String" },
      { name: "attrs_number", type: "Map(String, Float64)" },
      { name: "raw_session_id", type: "Nullable(UUID)" },
    ],
  },
  {
    name: "users",
    available: true,
    description: "Users first seen in the window (not active-user counts).",
    columns: [
      { name: "id", type: "UUID" },
      { name: "first_seen", type: "DateTime64" },
    ],
  },
  {
    name: "derived_metrics",
    available: false,
    description:
      "Committed derived-metric attempts; windows fully contained in the selected window. Requires the derived metrics schema and reader grants.",
    unavailable_reason: "Derived metrics schema is not enabled.",
    columns: [{ name: "value", type: "Nullable(Float64)" }],
  },
];

describe("searchable catalog", () => {
  it.each([
    ["name", "String"],
    ["sampling_window_start", "Nullable(DateTime64(3))"],
  ])(
    "keeps %s and its full type in a compact row with a wrapping fallback",
    (name, type) => {
      render(
        <QueryCatalog
          datasets={[{ ...datasets[0], columns: [{ name, type }] }]}
          onInsert={vi.fn()}
        />,
      );
      const row = screen.getByRole("button", { name: `Insert spans.${name}` });
      const identifier = within(row).getByText(name);
      const dataType = within(row).getByText(type);
      expect(identifier.parentElement).toBe(row);
      expect(dataType.parentElement).toBe(row);
      expect(getComputedStyle(row).display).toBe("flex");
      expect(getComputedStyle(row).flexWrap).toBe("wrap");
      expect(getComputedStyle(row).minHeight).toBe("32px");
      expect(getComputedStyle(identifier).fontFamily).toBe("monospace");
      expect(getComputedStyle(dataType).fontFamily).toBe("monospace");
      expect(getComputedStyle(dataType).marginLeft).toBe("auto");
      expect(getComputedStyle(dataType).textAlign).toBe("right");
      expect(getComputedStyle(dataType).flexShrink).toBe("0");
      expect(getComputedStyle(dataType).maxWidth).toBe("100%");
      expect(getComputedStyle(dataType).overflowWrap).toBe("anywhere");
      expect(getComputedStyle(dataType).textOverflow).not.toBe("ellipsis");
      expect(dataType).toHaveTextContent(type);
    },
  );
  it.each([
    ["SPANS", ["spans"], ["name", "attrs_number", "raw_session_id"]],
    ["raw_session", ["spans"], ["raw_session_id"]],
    ["Nullable(UUID)", ["spans"], ["raw_session_id"]],
    ["users DateTime64", ["users"], ["first_seen"]],
    ["not active-user counts", ["users"], ["id", "first_seen"]],
  ])(
    "searches names, fields, exact types and descriptions: %s",
    (query, names, columns) => {
      const result = searchCatalog(datasets, query);
      expect(result.map((dataset) => dataset.name)).toEqual(names);
      expect(result[0].columns.map((column) => column.name)).toEqual(columns);
    },
  );
  it("does not mutate the catalog or invent column meanings", () => {
    expect(searchCatalog(datasets, "attrs")[0]).toMatchObject({
      description: datasets[0].description,
      columns: [datasets[0].columns[1]],
      totalColumns: 3,
    });
    expect(datasets[0].columns).toHaveLength(3);
    expect(
      searchCatalog(datasets, "   ").map((dataset) => dataset.columns),
    ).toEqual(datasets.map((dataset) => dataset.columns));
  });
  it("opens the first dataset and displays authoritative descriptions and full types", () => {
    render(<QueryCatalog datasets={datasets} onInsert={vi.fn()} />);
    expect(screen.getByText(datasets[0].description)).toBeVisible();
    expect(screen.getByText("Map(String, Float64)")).toBeVisible();
    expect(screen.getByText("Nullable(UUID)")).toBeVisible();
    expect(screen.getByText("3 columns")).toBeVisible();
    fireEvent.click(screen.getByText("users"));
    expect(screen.getByText(datasets[1].description)).toBeVisible();
    expect(screen.getByText("DateTime64")).toBeVisible();
  });
  it("inserts dataset and field identifiers safely with click and keyboard", async () => {
    const onInsert = vi.fn();
    const user = userEvent.setup();
    render(<QueryCatalog datasets={datasets} onInsert={onInsert} />);
    fireEvent.click(
      screen.getByRole("button", { name: "Insert dataset spans" }),
    );
    expect(onInsert).toHaveBeenLastCalledWith("`spans`");
    const button = screen.getByRole("button", {
      name: "Insert spans.raw_session_id",
    });
    act(() => button.focus());
    await user.keyboard("{Enter}");
    expect(onInsert).toHaveBeenLastCalledWith("`raw_session_id`");
    expect(button).toHaveFocus();
  });
  it("expands field search results with a matching-column count", () => {
    render(
      <QueryCatalog
        datasets={datasets}
        search="first_seen"
        onInsert={vi.fn()}
      />,
    );
    expect(
      screen.getByRole("button", { name: "Insert users.first_seen" }),
    ).toBeVisible();
    expect(screen.getByText("1 of 2 columns")).toBeVisible();
    expect(screen.queryByText("spans")).not.toBeInTheDocument();
  });
  it("explains unavailable datasets and blocks both insertion paths even on synthetic clicks", () => {
    const onInsert = vi.fn();
    render(
      <QueryCatalog
        datasets={datasets}
        search="derived_metrics"
        onInsert={onInsert}
      />,
    );
    expect(screen.getByText(datasets[2].unavailable_reason)).toBeVisible();
    expect(screen.getByText("Nullable(Float64)")).toBeVisible();
    const field = screen.getByRole("button", {
      name: "Insert derived_metrics.value",
    });
    expect(field).toHaveAttribute("aria-disabled", "true");
    fireEvent.click(field);
    const dataset = screen.getByRole("button", {
      name: "Insert dataset derived_metrics",
    });
    expect(dataset).toBeDisabled();
    fireEvent.click(dataset);
    expect(onInsert).not.toHaveBeenCalled();
  });
  it("offers a useful empty search without hiding the reason", () => {
    render(
      <QueryCatalog
        datasets={datasets}
        search="does-not-exist"
        onInsert={vi.fn()}
      />,
    );
    expect(
      within(screen.getByRole("status")).getByText(
        "No matching datasets or columns",
      ),
    ).toBeVisible();
    expect(screen.queryByRole("button")).not.toBeInTheDocument();
  });
});
