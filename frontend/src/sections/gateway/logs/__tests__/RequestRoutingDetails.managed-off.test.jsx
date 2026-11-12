import React from "react";
import { describe, expect, it } from "vitest";
import { render, screen } from "src/utils/test-utils";

import { BillingSection } from "../RequestRoutingDetails";

describe("F1 request billing copy (S005)", () => {
  it("keeps the billing cost row without a new zero-markup claim", () => {
    render(<BillingSection log={{ cost: 0.12, billing: { mode: "byok" } }} />);
    expect(screen.getByText("Billing")).toBeInTheDocument();
    expect(screen.getByText("Cost")).toBeInTheDocument();
    expect(screen.getByTitle("0.12 USD")).toBeInTheDocument();
    expect(screen.getByText(/using your own provider key/)).toBeInTheDocument();
    expect(screen.queryByText(/no markup/)).toBeNull();
  });
});
