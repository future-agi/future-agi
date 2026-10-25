import React from "react";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, within } from "src/utils/test-utils";
import userEvent from "@testing-library/user-event";
import RoutingConfigView from "./RoutingConfigView";

// Budget, alerting, routing and fallback writes are Owner or Admin only
// (useGatewayAdmin); these cases act as an Admin.
vi.mock("src/contexts/OrganizationContext", () => ({
  useOrganization: () => ({ orgLevel: 8 }),
}));

// Providers > Routing writes
// "Retry on Status Codes" to routing.retry.on_status_codes and "Retry on
// Timeout" to routing.retry.on_timeout; the failover list stays
// routing.failover.on_status_codes, labelled for what it drives.

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

async function saveBody(user) {
  await user.click(screen.getByRole("button", { name: "Save Routing Config" }));
  return mockUpdateMutate.mock.calls[0][0].config.routing;
}

async function addCode(user, label, code) {
  const input = screen.getByLabelText(label);
  await user.click(input);
  await user.type(input, `${code}{Enter}`);
}

describe("Providers > Routing status lists", () => {
  beforeEach(() => mockUpdateMutate.mockClear());

  it("writes the retry list to routing.retry and the failover list to routing.failover", async () => {
    const user = userEvent.setup();
    render(
      <RoutingConfigView
        config={{
          routing: {
            failover: { enabled: true, on_status_codes: [502] },
            retry: { enabled: true },
          },
        }}
        gatewayId="gw"
      />,
    );

    await addCode(user, "Retry on Status Codes", "503");
    await addCode(user, "Failover on Status Codes", "429");
    const routing = await saveBody(user);

    expect(routing.retry.on_status_codes).toEqual([503]);
    expect(routing.failover.on_status_codes).toEqual([502, 429]);
  });

  it("writes Retry on Timeout to routing.retry.on_timeout", async () => {
    const user = userEvent.setup();
    render(
      <RoutingConfigView
        config={{ routing: { retry: { enabled: true } } }}
        gatewayId="gw"
      />,
    );
    const sw = screen.getByRole("switch", { name: "Retry on Timeout" });
    expect(sw).not.toBeChecked(); // unset runs as off
    await user.click(sw);
    const routing = await saveBody(user);
    expect(routing.retry.on_timeout).toBe(true);
  });

  it("shows the stored retry list, not the failover list, under Retry", () => {
    render(
      <RoutingConfigView
        config={{
          routing: {
            failover: { enabled: true, on_status_codes: [502] },
            retry: { enabled: true, on_status_codes: [503] },
          },
        }}
        gatewayId="gw"
      />,
    );
    const retry = screen.getByTestId("routing-retry-codes");
    const failover = screen.getByTestId("routing-failover-codes");
    expect(within(retry).getByText("503")).toBeInTheDocument();
    expect(within(retry).queryByText("502")).not.toBeInTheDocument();
    expect(within(failover).getByText("502")).toBeInTheDocument();
  });

  it("says what each list and the timeout switch drive", () => {
    render(<RoutingConfigView config={{ routing: {} }} gatewayId="gw" />);
    expect(
      screen.getByText(/move a request to another of your providers/i),
    ).toBeInTheDocument();
    expect(screen.getByText(/retry the same provider/i)).toBeInTheDocument();
    expect(
      screen.getByText(/retries a provider call that timed out/i),
    ).toBeInTheDocument();
  });

  it("leaves routing.retry out of the save when no retry field changed", async () => {
    const user = userEvent.setup();
    render(
      <RoutingConfigView
        config={{
          routing: { retry: { enabled: true, on_status_codes: [503] } },
        }}
        gatewayId="gw"
      />,
    );
    await user.click(screen.getByRole("checkbox"));
    const routing = await saveBody(user);
    expect(routing).not.toHaveProperty("retry");
  });
});

// An unset routing.strategy runs as "the first of the
// org's providers by ID" for its keys, so the control shows that, and a save
// sends strategy only when the user changed it (null clears a stored one:
// update-config takes an RFC 7386 merge patch).
describe("Providers > Routing strategy", () => {
  beforeEach(() => mockUpdateMutate.mockClear());

  const strategySelect = () =>
    screen.getByRole("combobox", { name: "Default Strategy" });

  async function choose(user, name) {
    await user.click(strategySelect());
    await user.click(screen.getByRole("option", { name }));
  }

  it("shows an unset strategy as the first provider by ID", () => {
    render(<RoutingConfigView config={{ routing: {} }} gatewayId="gw" />);
    expect(strategySelect()).toHaveTextContent("None: first provider by ID");
  });

  it("leaves strategy out of the save when it did not change", async () => {
    const user = userEvent.setup();
    render(
      <RoutingConfigView
        config={{ routing: { failover: { enabled: true } } }}
        gatewayId="gw"
      />,
    );
    await addCode(user, "Failover on Status Codes", "429");
    const routing = await saveBody(user);
    expect(routing).not.toHaveProperty("strategy");
    expect(routing.failover.on_status_codes).toEqual([429]);
  });

  it("sends the strategy chosen", async () => {
    const user = userEvent.setup();
    render(<RoutingConfigView config={{ routing: {} }} gatewayId="gw" />);
    await choose(user, "Weighted");
    const routing = await saveBody(user);
    expect(routing.strategy).toBe("weighted");
  });

  it("clears a stored strategy when None is chosen", async () => {
    const user = userEvent.setup();
    render(
      <RoutingConfigView
        config={{ routing: { strategy: "weighted" } }}
        gatewayId="gw"
      />,
    );
    expect(strategySelect()).toHaveTextContent("Weighted");
    await choose(user, "None: first provider by ID");
    const routing = await saveBody(user);
    expect(routing).toHaveProperty("strategy", null);
  });
});
