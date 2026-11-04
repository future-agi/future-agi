import React from "react";
import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { TracerUsersListResponse } from "src/generated/api-contracts/api.zod";
import { formatCost } from "../../LLMTracing/formatters";
import { getUsersColumnConfig } from "../common";

const envelope = (row) => ({
  status: true,
  result: { table: [row], total_count: 1, total_pages: 1 },
});
const coverage = (status, knownSubtotal, knownFacts, unknownFacts) => ({
  status,
  known_subtotal: knownSubtotal,
  known_fact_count: knownFacts,
  unknown_fact_count: unknownFacts,
});

function renderCost(row) {
  const column = getUsersColumnConfig().find((item) => item.field === "total_cost");
  const Renderer = column.cellRenderer;
  return render(<Renderer value={row.total_cost} data={row} colDef={column} />);
}

describe("Users monetary coverage", () => {
  it.each([0, 1.25])("keeps the old numeric contract and absent companion (%s)", (value) => {
    const parsed = TracerUsersListResponse.parse(envelope({ total_cost: value }));
    expect(parsed.result.table[0].total_cost).toBe(value);
    expect(parsed.result.table[0]).not.toHaveProperty("cost_coverage");
    const { container } = renderCost(parsed.result.table[0]);
    expect(container.textContent.replace(/\s/g, "")).toBe(formatCost(value));
  });

  it.each([
    ["complete", 1.25, 1, 0],
    ["complete", 0, 1, 0],
    ["partial", 1.25, 1, 1],
    ["partial", 0, 1, 1],
    ["unknown", 0, 0, 1],
  ])("retains the generated typed companion (%s, %s)", (status, subtotal, known, unknown) => {
    const companion = coverage(status, subtotal, known, unknown);
    const parsed = TracerUsersListResponse.parse(envelope({
      total_cost: subtotal,
      cost_coverage: companion,
    }));
    expect(parsed.result.table[0].total_cost).toBe(subtotal);
    expect(parsed.result.table[0].cost_coverage).toEqual(companion);
  });

  it("never renders unknown work as a free price", () => {
    const { container } = renderCost({
      total_cost: 0,
      cost_coverage: coverage("unknown", 0, 0, 1),
    });
    expect(screen.getByText(/Unknown/)).toBeTruthy();
    expect(container.textContent).not.toMatch(/\$\s*0/);
  });

  it("names partial money as a known subtotal, using the shared formatter", () => {
    const { container } = renderCost({
      total_cost: 1.25,
      cost_coverage: coverage("partial", 1.25, 1, 1),
    });
    expect(container.textContent).toContain("Partial");
    expect(container.textContent).toContain("Known subtotal");
    expect(container.textContent).toContain(formatCost(1.25));
  });

  it("keeps genuine zero displayable", () => {
    const { container } = renderCost({
      total_cost: 0,
      cost_coverage: coverage("complete", 0, 1, 0),
    });
    expect(container.textContent.replace(/\s/g, "")).toBe(formatCost(0));
    expect(container.textContent).not.toMatch(/Unknown|Partial/);
  });
});


it("uses one monetary presentation for cell, tooltip and export formatter", () => {
  const column = getUsersColumnConfig().find((item) => item.field === "total_cost");
  const data = { total_cost: 0, cost_coverage: coverage("partial", 0, 1, 1) };
  const params = { value: data.total_cost, data };
  expect(column.cellRenderer(params)).toBe("Partial — Known subtotal $0.00");
  expect(column.valueFormatter(params)).toBe(column.cellRenderer(params));
  expect(column.tooltipValueGetter(params)).toBe(column.cellRenderer(params));
  expect(data.total_cost).toBe(0);
});
