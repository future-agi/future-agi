import React from "react";
import { describe, expect, it, vi } from "vitest";
import { render, screen } from "src/utils/test-utils";
import CostCell from "../Renderers/CostCell";

vi.mock("src/components/iconify", () => ({ default: () => null }));

describe("CostCell precision", () => {
  it.each([
    [3e-8, "$0.00000003"],
    [2.37e-5, "$0.0000237"],
    [0, "$0.00"],
    [null, "-"],
  ])(
    "renders %s without confusing positive usage with zero",
    (value, expected) => {
      render(<CostCell value={value} data={{}} />);
      expect(screen.getByText(expected, { exact: true })).toBeInTheDocument();
    },
  );
});

// The trace list API returns a trace's cost as the float sum of its spans'
// costs (0 + 0.1 + 0.2 + 0.3 = 0.6000000000000001). The grid reads it by the
// trace drawer's rule (formatCost: twelve significant digits), so grid and
// drawer agree and binary noise never reaches the cell.
describe("CostCell drops float noise from summed costs", () => {
  const receivedCallsOnly = {
    _captureCoverage: {
      scope: "observed_requests",
      source_summary_present: true,
      request_count: 3,
      coverage_unknown: true,
    },
  };
  it.each([
    ["0.1 + 0.2 + 0.3", 0.1 + 0.2 + 0.3, {}, "$0.60"],
    [
      "0.1 + 0.2 + 0.3 counting only received calls",
      0.1 + 0.2 + 0.3,
      receivedCallsOnly,
      "$0.60*",
    ],
    ["0.1 + 0.2", 0.1 + 0.2, {}, "$0.30"],
  ])("renders %s as money", (_name, value, data, expected) => {
    const { container } = render(<CostCell value={value} data={data} />);
    expect(container.textContent).toBe(expected);
  });
});
