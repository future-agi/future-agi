import React from "react";
import { describe, expect, it, vi } from "vitest";
import userEvent from "@testing-library/user-event";
import { render, screen } from "src/utils/test-utils";
import SamplingPrecisionPlanner from "./SamplingPrecisionPlanner";
import { applyCellQuotas } from "./samplingPrecisionPlan";
import { defaultSamplingPolicy } from "./samplingPolicyForm";

const cells = [100, 50, 10].map((N, i) => ({
  stratum: JSON.stringify(["default", [JSON.stringify([true, `group-${i}`])]]),
  eligible_units: N,
  selected_units: i === 0 ? 10 : 5,
}));
const policy = () => ({
  ...defaultSamplingPolicy(),
  group_by: [{ source: "span", key: "group" }],
});
const open = async () =>
  userEvent.click(screen.getByText("Precision planner · advisory"));

describe("advisory precision planner", () => {
  it("shows minimum n and margins but changes no policy until Apply", async () => {
    const onApply = vi.fn();
    const original = policy();
    render(
      <SamplingPrecisionPlanner
        policy={original}
        preview={{ strata: cells }}
        onApply={onApply}
      />,
    );
    await open();
    expect(
      screen.getByRole("columnheader", { name: "Minimum n for margin" }),
    ).toBeInTheDocument();
    const budget = screen.getByLabelText("Total unit budget");
    const minimum = screen.getByLabelText("Per-cell minimum");
    await userEvent.clear(budget);
    await userEvent.type(budget, "16");
    await userEvent.clear(minimum);
    await userEvent.type(minimum, "2");
    await userEvent.click(
      screen.getByRole("button", { name: "Suggest quotas" }),
    );
    expect(onApply).not.toHaveBeenCalled();
    expect(original.cell_quotas).toBeUndefined();
    expect(screen.getByText(/Suggested total: 16 units/)).toBeInTheDocument();
    await userEvent.click(
      screen.getByRole("button", { name: "Apply suggested quotas" }),
    );
    expect(onApply).toHaveBeenCalledWith({
      ...original,
      cell_quotas: Object.fromEntries(
        cells.map((c, i) => [c.stratum, [9, 5, 2][i]]),
      ),
    });
    // No new slice predicates: classification is unchanged.
    expect(onApply.mock.calls[0][0].strata).toEqual(original.strata);
  });
  it("invalidates a suggestion on parameter changes and rejects an empty budget", async () => {
    const onApply = vi.fn();
    render(
      <SamplingPrecisionPlanner
        policy={policy()}
        preview={{ strata: cells }}
        onApply={onApply}
      />,
    );
    await open();
    await userEvent.click(
      screen.getByRole("button", { name: "Suggest quotas" }),
    );
    expect(
      screen.getByRole("button", { name: "Apply suggested quotas" }),
    ).toBeEnabled();
    await userEvent.clear(screen.getByLabelText("Total unit budget"));
    expect(
      screen.queryByRole("button", { name: "Apply suggested quotas" }),
    ).not.toBeInTheDocument();
    await userEvent.click(
      screen.getByRole("button", { name: "Suggest quotas" }),
    );
    expect(screen.getByRole("alert")).toHaveTextContent(
      "non-negative safe integers",
    );
    expect(onApply).not.toHaveBeenCalled();
  });
  it("keeps live planning read-only and marks zero samples unestimable", async () => {
    render(
      <SamplingPrecisionPlanner
        policy={{ ...policy(), mode: "live" }}
        preview={{ strata: [{ ...cells[0], selected_units: 0 }] }}
        onApply={vi.fn()}
      />,
    );
    await open();
    expect(screen.getByText("Not estimable")).toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "Suggest quotas" }),
    ).not.toBeInTheDocument();
  });
  it("makes Neyman assumptions explicit and allows per-cell input", async () => {
    render(
      <SamplingPrecisionPlanner
        policy={policy()}
        preview={{ strata: cells }}
        onApply={vi.fn()}
      />,
    );
    await open();
    await userEvent.click(
      screen.getByRole("combobox", { name: /Allocation method/ }),
    );
    await userEvent.click(screen.getByRole("option", { name: "Neyman" }));
    expect(
      screen.getAllByRole("spinbutton", { name: /Anticipated proportion/ }),
    ).toHaveLength(3);
    await userEvent.clear(
      screen.getByLabelText("Anticipated proportion (%) for cell 1"),
    );
    await userEvent.click(
      screen.getByRole("button", { name: "Suggest quotas" }),
    );
    expect(screen.getByRole("alert")).toHaveTextContent("Proportions");
  });
});

describe("applying quotas", () => {
  it("preserves unlisted cells, policy structure and provenance controls", () => {
    const original = {
      ...policy(),
      coordination: "project",
      panel: "panel",
      cell_quotas: { '["old",[]]': 5 },
    };
    const next = applyCellQuotas(original, cells, [9, 5, 2]);
    expect(next.cell_quotas['["old",[]]']).toBe(5);
    expect(next.coordination).toBe("project");
    expect(next.panel).toBe("panel");
    expect(original.cell_quotas).toEqual({ '["old",[]]': 5 });
  });
  it("fails closed for quotas outside API bounds or an oversized policy", () => {
    expect(() => applyCellQuotas(policy(), cells, [1, 2, 1000001])).toThrow(
      "1,000,000",
    );
    expect(() =>
      applyCellQuotas({ ...policy(), mode: "live" }, cells, [1, 2, 3]),
    ).toThrow("closed");
    expect(() =>
      applyCellQuotas(policy(), [cells[0], cells[0]], [1, 2]),
    ).toThrow("unique");
    expect(() =>
      applyCellQuotas(
        { ...policy(), panel: "a".repeat(65536) },
        cells,
        [1, 2, 3],
      ),
    ).toThrow("size limit");
  });
});
