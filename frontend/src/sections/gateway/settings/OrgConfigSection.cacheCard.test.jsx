// The Organization Config summary's Cache card shows only what the gateway
// applies: on or off and the TTL. No backend name (a per-org backend selects
// nothing on the gateway), and "Off on this gateway" when the gateway reports it
// does not run response caching (org_sections_enforced).
import React from "react";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen } from "src/utils/test-utils";
import OrgConfigSection from "./OrgConfigSection";

const { state } = vi.hoisted(() => ({
  state: { orgConfig: null, gatewayConfig: { data: undefined } },
}));

vi.mock("../providers/hooks/useOrgConfig", () => ({
  useOrgConfig: () => ({
    data: state.orgConfig,
    isLoading: false,
    error: null,
  }),
  useCreateOrgConfig: () => ({ mutate: vi.fn(), isPending: false }),
}));
vi.mock("../providers/hooks/useGatewayConfig", () => ({
  useProviderHealth: () => ({ data: { providers: [] } }),
  useGatewayConfig: () => state.gatewayConfig,
}));
vi.mock("../context/useGatewayContext", () => ({
  useGatewayContext: () => ({ gatewayId: "gateway-1" }),
}));
vi.mock("./OrgConfigEditor", () => ({ default: () => null }));
vi.mock("./ConfigHistoryDrawer", () => ({ default: () => null }));

const cacheCard = () =>
  screen.getByText("Cache").closest(".MuiCardContent-root");

beforeEach(() => {
  state.orgConfig = {
    version: 3,
    cache: {
      enabled: true,
      default_ttl: 300,
      backend: "redis",
      semantic: { enabled: true, backend: "qdrant" },
    },
  };
  state.gatewayConfig = {
    data: { org_sections_enforced: ["cache"] },
    isLoading: false,
  };
});

describe("The summary's Cache card", () => {
  it("shows on and the TTL, never the backend", () => {
    render(<OrgConfigSection />);
    const card = cacheCard();
    expect(card).toHaveTextContent("Enabled");
    expect(card).toHaveTextContent("TTL 5m");
    expect(card.textContent).not.toMatch(/redis|qdrant|L1|L2|memory/i);
  });

  it("shows a TTL saved as a duration as it was saved", () => {
    state.orgConfig.cache.default_ttl = "1h";
    render(<OrgConfigSection />);
    expect(cacheCard()).toHaveTextContent("TTL 1h");
  });

  it("shows Disabled when the org has caching off", () => {
    state.orgConfig.cache = { enabled: false, backend: "disk" };
    render(<OrgConfigSection />);
    const card = cacheCard();
    expect(card).toHaveTextContent("Disabled");
    expect(card.textContent).not.toMatch(/disk|TTL/i);
  });

  it("says Off on this gateway when the gateway does not run response caching", () => {
    state.gatewayConfig = {
      data: { org_sections_enforced: ["budgets"] },
      isLoading: false,
    };
    render(<OrgConfigSection />);
    const card = cacheCard();
    expect(card).toHaveTextContent("Off on this gateway");
    expect(card).toHaveTextContent("Saved, not applied");
  });

  it("says enforcement unknown when the gateway does not report it", () => {
    state.gatewayConfig = { data: { gateway: {} }, isLoading: false };
    render(<OrgConfigSection />);
    const card = cacheCard();
    expect(card).toHaveTextContent("Enabled");
    expect(card).toHaveTextContent(/enforcement unknown/i);
  });
});
