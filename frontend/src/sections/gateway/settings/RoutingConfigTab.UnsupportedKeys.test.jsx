import { beforeEach, describe, it, expect, vi } from "vitest";
import { render, screen } from "src/utils/test-utils";
import { buildConfigPatch } from "../hooks/useConfigPatch";
import RoutingConfigTab from "./RoutingConfigTab";

// docs-agentcc-711, items 3 and 4: the gateway runs none of routing.mirror,
// routing.scheduled and routing.access_groups for an organization, and
// update-config refuses a new or changed value (400 unsupported_routing_key).
// Org Config > Routing offers no editor for them (Traffic Mirroring,
// Scheduled Completions, Model Access Groups), and a value already stored
// stays as it is, so a save of another routing setting is never refused.

vi.mock("../hooks/useAvailableModels", () => ({
  useAvailableModels: () => ["m1", "m2"],
}));

// This imported stored-settings contract now runs through the composed
// Owner/Admin write controls. Supply an actual org level, not a bypass of
// useGatewayAdmin, so the original save assertions exercise an editable form.
const organization = vi.hoisted(() => ({ orgLevel: 8 }));
vi.mock("src/contexts/OrganizationContext", () => ({
  useOrganization: () => organization,
}));
beforeEach(() => {
  organization.orgLevel = 8;
});

const STORED = {
  mirror: {
    enabled: true,
    rules: [
      {
        source_model: "m1",
        target_provider: "p",
        target_model: "m2",
        sample_rate: 1,
      },
    ],
  },
  scheduled: { enabled: true, max_pending_jobs: 10 },
  access_groups: { premium: { models: ["m1"], description: "" } },
  model_fallbacks: { m1: ["m2"] },
};

describe("Org Config > Routing: settings the gateway does not run", () => {
  it("offers no Traffic Mirroring, Scheduled Completions or Model Access Groups", () => {
    render(<RoutingConfigTab routing={STORED} onChange={vi.fn()} />);
    for (const text of [
      "Traffic Mirroring",
      "Enable Traffic Mirroring",
      "Add Mirror Rule",
      "Scheduled Completions",
      "Enable Scheduled Completions",
      "Model Access Groups",
      "Add Group",
    ]) {
      expect(screen.queryByText(text)).not.toBeInTheDocument();
    }
    // The sections the gateway does run are still offered.
    expect(screen.getByText("Routing Strategy")).toBeInTheDocument();
    expect(screen.getByText("Model Timeouts")).toBeInTheDocument();
  });

  it("keeps stored mirror, scheduled and access_groups on another change", () => {
    const onChange = vi.fn();
    render(<RoutingConfigTab routing={STORED} onChange={onChange} />);
    screen.getByRole("checkbox", { name: "Fallback Enabled" }).click();
    expect(onChange).toHaveBeenCalledTimes(1);
    const next = onChange.mock.calls[0][0];
    expect(next.fallback_enabled).toBe(false);
    expect(next.mirror).toEqual(STORED.mirror);
    expect(next.scheduled).toEqual(STORED.scheduled);
    expect(next.access_groups).toEqual(STORED.access_groups);
    // The save is a merge patch of what changed: none of the three is sent.
    expect(
      buildConfigPatch({ routing: STORED }, { routing: next }, ["routing"]),
    ).toEqual({ routing: { fallback_enabled: false } });
  });

  it.each([1, 3, null])(
    "does not let org level %s change routing or its stored settings",
    (orgLevel) => {
      organization.orgLevel = orgLevel;
      const onChange = vi.fn();
      render(<RoutingConfigTab routing={STORED} onChange={onChange} />);
      const toggle = screen.getByRole("checkbox", {
        name: "Fallback Enabled",
      });
      expect(toggle).toBeDisabled();
      toggle.click();
      expect(onChange).not.toHaveBeenCalled();
    },
  );
});
