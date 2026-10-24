// The Providers > Cache card shows only what the gateway applies for an
// org's cache: on or off and the default TTL (as the Org Config Cache
// tab and its summary). The backend, max entries, semantic and edge settings
// are never read for an org, so the card does not show them. Whether this
// gateway runs response caching at all comes from its own
// org_sections_enforced.
import React from "react";
import { describe, it, expect, vi } from "vitest";
import { render, screen } from "src/utils/test-utils";
import CacheStatusView from "./CacheStatusView";

vi.mock("../analytics/hooks/useAnalyticsOverview", () => ({
  useAnalyticsOverview: () => ({ data: {} }),
}));
vi.mock("./hooks/useOrgConfig", () => ({
  useOrgConfig: () => ({ data: null }),
  useCreateOrgConfig: () => ({ mutate: vi.fn(), isPending: false }),
}));
vi.mock("../../gateway/settings/OrgConfigEditor", () => ({
  default: () => null,
}));

const STORED = {
  enabled: true,
  default_ttl: "5m",
  backend: "redis",
  max_entries: 5000,
  semantic: { enabled: true, backend: "qdrant" },
  edge: { enabled: true },
};

// The value next to an InfoLine label (the first line with that label).
function valueOf(label) {
  return screen.getAllByText(label, { selector: "p" })[0].parentElement
    .lastChild.textContent;
}

describe("Providers > Cache card", () => {
  it("shows Enabled and Default TTL, and no backend, max entries, semantic or edge line", () => {
    render(
      <CacheStatusView
        config={{ cache: STORED, org_sections_enforced: ["cache"] }}
        gatewayId="gw-1"
      />,
    );
    expect(valueOf("Enabled")).toBe("Yes");
    expect(valueOf("Default TTL")).toBe("5m");
    for (const label of [
      "L1 Backend",
      "Max Entries",
      "Semantic Cache",
      "Edge Cache",
    ]) {
      expect(screen.queryByText(label)).toBeNull();
    }
    expect(screen.queryByText(/Off on this gateway/)).toBeNull();
    expect(screen.queryByText(/enforcement unknown/i)).toBeNull();
  });

  it("says caching is off on this gateway when the gateway does not enforce cache", () => {
    render(
      <CacheStatusView
        config={{ cache: STORED, org_sections_enforced: ["budgets"] }}
        gatewayId="gw-1"
      />,
    );
    expect(valueOf("On This Gateway")).toBe(
      "Off on this gateway: saved, not applied (response caching is off on this gateway)",
    );
  });

  it("says enforcement is unknown when the gateway does not report it", () => {
    render(<CacheStatusView config={{ cache: STORED }} gatewayId="gw-1" />);
    expect(valueOf("On This Gateway")).toBe("Enforcement unknown");
  });
});
