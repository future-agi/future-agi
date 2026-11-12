import React from "react";
import { describe, expect, it, vi } from "vitest";
import { render, screen } from "src/utils/test-utils";

import RoutingConfigView from "./RoutingConfigView";

vi.mock("src/contexts/OrganizationContext", () => ({
  useOrganization: () => ({ orgLevel: 8 }),
}));
vi.mock("./hooks/useGatewayConfig", () => ({
  useUpdateConfig: () => ({ mutate: vi.fn(), isPending: false }),
}));
vi.mock("./hooks/useOrgConfig", () => ({
  useOrgConfig: () => ({ data: null }),
  useCreateOrgConfig: () => ({ mutate: vi.fn(), isPending: false }),
}));
vi.mock("../../gateway/settings/OrgConfigEditor", () => ({
  default: () => null,
}));

// The row a switch sits in.
const rowOf = (input) => input.closest(".MuiStack-root");

// Failover "Enabled" had its label first and the switch
// after it; "Retry on Timeout" put the switch first, 7 px inside the card's
// content edge, and its label after. Both rows now read label, then switch.
describe("Providers > Routing switches (layout)", () => {
  it("lays out both switch rows the same way: label first, then the switch", () => {
    render(
      <RoutingConfigView
        config={{
          routing: {
            failover: { enabled: true, max_attempts: 2 },
            retry: { on_timeout: true },
          },
        }}
        gatewayId="default"
      />,
    );
    const failover = screen.getByRole("checkbox", { name: "Failover enabled" });
    expect(rowOf(failover).firstElementChild).toHaveTextContent(/^Enabled$/);
    const timeout = screen.getByRole("switch", { name: "Retry on Timeout" });
    expect(rowOf(timeout).firstElementChild).toHaveTextContent(
      /^Retry on Timeout/,
    );
  });
});
