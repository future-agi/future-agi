import React from "react";
import { describe, expect, it, vi } from "vitest";
import { render, screen } from "src/utils/test-utils";

import CreditBalanceChip from "../CreditBalanceChip";

vi.mock("src/auth/hooks", () => ({
  useAuthContext: () => ({ user: { organization_role: "Owner" } }),
}));
vi.mock("src/contexts/WorkspaceContext", () => ({
  useWorkspace: () => ({ currentWorkspaceRole: null }),
}));
vi.mock("src/sections/settings/BillingV2/useCentralBilling", () => ({
  useCentralBilling: () => ({
    creditsAvailable: true,
    credits: { available_usd: "34.99" },
  }),
}));

// The Credits chip sat at 30 px between two 38 px header
// buttons on Keys and Providers. It is as tall as the buttons beside it.
describe("Credits chip", () => {
  it("is as tall as the page header's 38 px buttons", () => {
    render(<CreditBalanceChip />);
    const chip = screen.getByRole("link", {
      name: "Credits $34.99, open Billing",
    });
    expect(chip).toHaveStyle({ height: "38px" });
  });
});
