import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen } from "../../../utils/test-utils";
import userEvent from "@testing-library/user-event";
import FallbacksSection from "./FallbacksSection";

// Budget, alerting, routing and fallback writes are Owner or Admin only
// (useGatewayAdmin); these cases act as an Admin.
vi.mock("src/contexts/OrganizationContext", () => ({
  useOrganization: () => ({ orgLevel: 8 }),
}));

// The Fallbacks page shows the value the gateway runs for both switches. The
// gateway runs an org's chains when fallback_enabled is unset and any chain
// exists (and never when it is false), and runs provider failover only when
// failover.enabled is true. The Default Model control is not offered (the
// gateway does not apply it).

let hookReturn;

vi.mock("./hooks/useFallbackConfig", () => ({
  useFallbackConfig: () => hookReturn,
}));

vi.mock("../providers/hooks/useOrgConfig", () => ({
  useOrgConfig: () => ({ data: null }),
}));

vi.mock("../context/useGatewayContext", () => ({
  useGatewayContext: () => ({
    gatewayId: "gw",
    gateway: { id: "gw" },
  }),
}));

vi.mock("../providers/hooks/useGatewayConfig", () => ({
  useProviderHealth: () => ({
    data: { providers: [{ name: "alpha", models: ["m1", "m2"] }] },
  }),
}));

const save = vi.fn();

function renderWith(routing) {
  hookReturn = {
    routing,
    isLoading: false,
    error: null,
    refetch: vi.fn(),
    saveRouting: save,
    isSaving: false,
  };
  return render(<FallbacksSection />);
}

const chainsSwitch = () =>
  screen.getByRole("checkbox", { name: "Model Fallback Chains" });
const failoverSwitch = () =>
  screen.getByRole("checkbox", { name: "Provider Failover" });

describe("Fallbacks page switches", () => {
  beforeEach(() => save.mockReset());

  it("shows chains on when the switch is unset and a chain exists", () => {
    renderWith({ model_fallbacks: { m1: ["m2"] } });
    expect(chainsSwitch()).toBeChecked();
  });

  it("shows chains off when the switch is unset and no chain exists", () => {
    renderWith({});
    expect(chainsSwitch()).not.toBeChecked();
  });

  it("shows an explicit false as off even with chains", () => {
    renderWith({ fallback_enabled: false, model_fallbacks: { m1: ["m2"] } });
    expect(chainsSwitch()).not.toBeChecked();
  });

  it("shows provider failover off when failover.enabled is unset", () => {
    renderWith({ model_fallbacks: { m1: ["m2"] } });
    expect(failoverSwitch()).not.toBeChecked();
  });

  it("shows provider failover as stored when set", () => {
    renderWith({ failover: { enabled: true } });
    expect(failoverSwitch()).toBeChecked();
  });

  it("shows the timeout switches as the gateway runs them", async () => {
    renderWith({ failover: { enabled: true }, retry: { enabled: true } });
    await userEvent.click(screen.getByText("Provider Failover"));
    await userEvent.click(screen.getByText("Retry"));
    // A failover or retry block without on_timeout runs with it off.
    expect(
      screen.getByRole("checkbox", { name: "Failover on timeout" }),
    ).not.toBeChecked();
    expect(
      screen.getByRole("checkbox", { name: "Retry on timeout" }),
    ).not.toBeChecked();
  });

  it("saves the switch it shows when the user turns chains off", async () => {
    renderWith({ model_fallbacks: { m1: ["m2"] } });
    await userEvent.click(chainsSwitch());
    await userEvent.click(
      screen.getAllByRole("button", { name: /Save & Apply/i })[0],
    );
    expect(save).toHaveBeenCalledTimes(1);
    expect(save.mock.calls[0][0].fallback_enabled).toBe(false);
  });

  it("does not offer the Default Model control", () => {
    renderWith({ default_model: "m1", model_fallbacks: { m1: ["m2"] } });
    expect(screen.queryByText("Default Model")).not.toBeInTheDocument();
  });
});
