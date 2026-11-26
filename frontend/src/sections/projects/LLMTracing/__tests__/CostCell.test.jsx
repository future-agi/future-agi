import React from "react";
import { describe, expect, it, vi } from "vitest";
import { render, screen } from "src/utils/test-utils";
import CostCell from "../Renderers/CostCell";

vi.mock("src/components/iconify", () => ({ default: () => null }));

describe("CostCell precision", () => {
  it.each([
    [3e-8, "< $0.0000001"],
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
