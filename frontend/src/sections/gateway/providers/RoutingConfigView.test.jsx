import React from "react";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen } from "src/utils/test-utils";
import userEvent from "@testing-library/user-event";
import RoutingConfigView from "./RoutingConfigView";

const mockUpdateMutate = vi.fn();

vi.mock("./hooks/useGatewayConfig", () => ({
  useUpdateConfig: () => ({ mutate: mockUpdateMutate, isPending: false }),
}));

vi.mock("./hooks/useOrgConfig", () => ({
  useOrgConfig: () => ({ data: null }),
  useCreateOrgConfig: () => ({ mutate: vi.fn(), isPending: false }),
}));

vi.mock("../../gateway/settings/OrgConfigEditor", () => ({
  default: () => null,
}));

// The save button only shows once the form is dirty.
async function save(user) {
  await user.click(screen.getByRole("button", { name: "Save Routing Config" }));
  return mockUpdateMutate.mock.calls[0][0].config.routing;
}

describe("RoutingConfigView", () => {
  beforeEach(() => {
    mockUpdateMutate.mockClear();
  });

  it("saves a picked strategy under the key the gateway contract has", async () => {
    const user = userEvent.setup();
    render(<RoutingConfigView config={{ routing: {} }} gatewayId="default" />);

    await user.click(screen.getByLabelText("Default Strategy"));
    await user.click(screen.getByRole("option", { name: "Least Latency" }));
    const routing = await save(user);

    expect(routing.strategy).toBe("least_latency");
    expect(routing).not.toHaveProperty("default_strategy");
  });

  it("offers only the per-org strategy names", async () => {
    const user = userEvent.setup();
    render(<RoutingConfigView config={{ routing: {} }} gatewayId="default" />);

    await user.click(screen.getByLabelText("Default Strategy"));

    expect(
      screen.getAllByRole("option").map((option) => option.textContent),
    ).toEqual(["Round Robin", "Weighted", "Least Latency", "Cost Optimized"]);
  });

  it("keeps a stored strategy when another field changes", async () => {
    const user = userEvent.setup();
    render(
      <RoutingConfigView
        config={{ routing: { strategy: "weighted" } }}
        gatewayId="default"
      />,
    );

    await user.click(screen.getByRole("checkbox"));
    const routing = await save(user);

    expect(routing.strategy).toBe("weighted");
    expect(routing.failover.enabled).toBe(true);
  });
});
