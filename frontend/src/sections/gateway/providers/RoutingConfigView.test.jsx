import React from "react";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, within } from "src/utils/test-utils";
import userEvent from "@testing-library/user-event";
import RoutingConfigView from "./RoutingConfigView";
import {
  ADMIN_LEVELS,
  NOT_ADMIN_LEVELS,
  expectAdminOnly,
  expectEditable,
} from "../testing/adminOnly";

// The gateway admin role check (useGatewayAdmin): saving is Owner or Admin only.
// The cases below run as an Admin unless one says otherwise.
let mockOrgLevel = 8;
vi.mock("src/contexts/OrganizationContext", () => ({
  useOrganization: () => ({ orgLevel: mockOrgLevel }),
}));

const mockUpdateMutate = vi.fn();

vi.mock("./hooks/useGatewayConfig", () => ({
  useUpdateConfig: () => ({ mutate: mockUpdateMutate, isPending: false }),
}));

vi.mock("./hooks/useOrgConfig", () => ({
  useOrgConfig: () => ({ data: null }),
  useCreateOrgConfig: () => ({ mutate: vi.fn(), isPending: false }),
}));

// The editor stands in as the tab it was asked to open on.
vi.mock("../../gateway/settings/OrgConfigEditor", () => ({
  default: (props) =>
    props.open ? <div>editor open at {String(props.defaultTab)}</div> : null,
}));

// The save button only shows once the form is dirty.
async function save(user) {
  await user.click(screen.getByRole("button", { name: "Save Routing Config" }));
  return mockUpdateMutate.mock.calls[0][0].config.routing;
}

describe("RoutingConfigView", () => {
  beforeEach(() => {
    mockOrgLevel = 8;
    mockUpdateMutate.mockClear();
  });

  // Routing saves the org's gateway config, as Fallbacks and the Org config
  // editor do: only an Owner or Admin changes it. Anyone else reads every
  // setting, with each control disabled and naming the role it needs, and
  // has nothing to save.
  it.each(NOT_ADMIN_LEVELS)(
    "%s reads every routing setting with each control disabled, with the role it needs",
    async (_who, level) => {
      mockOrgLevel = level;
      render(
        <RoutingConfigView
          config={{
            routing: {
              strategy: "weighted",
              failover: {
                enabled: true,
                max_attempts: 4,
                on_status_codes: [503],
              },
              retry: { on_status_codes: [429], on_timeout: true },
            },
          }}
          gatewayId="default"
        />,
      );
      const failover = screen.getByRole("checkbox", {
        name: "Failover enabled",
      });
      expectAdminOnly(failover);
      expect(failover).toBeChecked();
      const attempts = screen.getByLabelText("Max Attempts");
      expectAdminOnly(attempts);
      expect(attempts).toHaveValue(4);
      for (const testId of ["routing-failover-codes", "routing-retry-codes"]) {
        expectAdminOnly(
          within(screen.getByTestId(testId)).getByRole("combobox"),
        );
      }
      expect(screen.getByText("503")).toBeInTheDocument();
      expect(screen.getByText("429")).toBeInTheDocument();
      const timeout = screen.getByRole("switch", { name: "Retry on Timeout" });
      expectAdminOnly(timeout);
      expect(timeout).toBeChecked();
      expectAdminOnly(
        screen.getByRole("button", { name: "Configure Advanced Routing" }),
      );
      const strategy = screen.getByRole("combobox", {
        name: "Default Strategy",
      });
      expect(strategy).toHaveAttribute("aria-disabled", "true");
      expect(
        strategy.closest('[aria-label="Owner or Admin only"]'),
      ).not.toBeNull();
      // The read-only lines stay.
      expect(screen.getByText("Complexity Routing")).toBeInTheDocument();

      await userEvent.click(failover, { pointerEventsCheck: 0 });
      expect(failover).toBeChecked();
      expect(
        screen.queryByRole("button", { name: "Save Routing Config" }),
      ).toBeNull();
      expect(screen.queryByText(/editor open/)).toBeNull();
      expect(mockUpdateMutate).not.toHaveBeenCalled();
    },
  );

  it.each(ADMIN_LEVELS)(
    "%s can change every routing setting",
    (_who, level) => {
      mockOrgLevel = level;
      render(
        <RoutingConfigView
          config={{
            routing: {
              strategy: "weighted",
              failover: { enabled: true, max_attempts: 4 },
            },
          }}
          gatewayId="default"
        />,
      );
      expectEditable(
        screen.getByRole("checkbox", { name: "Failover enabled" }),
      );
      expectEditable(screen.getByLabelText("Max Attempts"));
      for (const testId of ["routing-failover-codes", "routing-retry-codes"]) {
        expectEditable(
          within(screen.getByTestId(testId)).getByRole("combobox"),
        );
      }
      expectEditable(screen.getByRole("switch", { name: "Retry on Timeout" }));
      expectEditable(
        screen.getByRole("button", { name: "Configure Advanced Routing" }),
      );
      const strategy = screen.getByRole("combobox", {
        name: "Default Strategy",
      });
      expect(strategy).not.toHaveAttribute("aria-disabled");
      expect(strategy.closest('[aria-label="Owner or Admin only"]')).toBeNull();
    },
  );

  it.each(ADMIN_LEVELS)("%s can save routing", async (_who, level) => {
    mockOrgLevel = level;
    const user = userEvent.setup();
    render(
      <RoutingConfigView
        config={{ routing: { strategy: "weighted" } }}
        gatewayId="default"
      />,
    );
    await user.click(screen.getByRole("checkbox"));
    expectEditable(screen.getByRole("button", { name: "Save Routing Config" }));
  });

  // The editor's Routing tab is named by its id, so the tab this opens does
  // not depend on which editor tabs come before it.
  it("opens the org config editor on its Routing tab", async () => {
    const user = userEvent.setup();
    render(
      <RoutingConfigView
        config={{ routing: { strategy: "weighted" } }}
        gatewayId="default"
      />,
    );
    await user.click(
      screen.getByRole("button", { name: "Configure Advanced Routing" }),
    );
    expect(screen.getByText("editor open at routing")).toBeInTheDocument();
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

    // None is the unset strategy (the first provider by ID), never written.
    expect(
      screen.getAllByRole("option").map((option) => option.textContent),
    ).toEqual([
      "None: first provider by ID",
      "Round Robin",
      "Weighted",
      "Least Latency",
      "Cost Optimized",
    ]);
  });

  it("keeps a stored strategy when another field changes", async () => {
    const user = userEvent.setup();
    render(
      <RoutingConfigView
        config={{ routing: { strategy: "weighted" } }}
        gatewayId="default"
      />,
    );

    expect(screen.getByLabelText("Default Strategy")).toHaveTextContent(
      "Weighted",
    );
    await user.click(screen.getByRole("checkbox"));
    const routing = await save(user);

    // update-config merges the patch into the stored config, so a save that
    // leaves strategy out keeps the stored one as it is.
    expect(routing).not.toHaveProperty("strategy");
    expect(routing.failover.enabled).toBe(true);
  });
});
