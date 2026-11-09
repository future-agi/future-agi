import React from "react";
import { describe, expect, it, vi } from "vitest";
import { render, screen, within } from "src/utils/test-utils";

import { editorTabsFor } from "../orgConfigTabs";
import SecurityConfigTab from "../SecurityConfigTab";
import RoutingConfigTab from "../RoutingConfigTab";
import GuardrailConfigTab from "../GuardrailConfigTab";
import AnalyticsKpis from "../../analytics/AnalyticsKpis";
import { providerTabsFor } from "../../providers/providerTabs";
import CreateEditPropertyDialog from "../../custom-properties/CreateEditPropertyDialog";

vi.mock("src/utils/logger", () => ({ logger: { warn: vi.fn() } }));
vi.mock("../../hooks/useAvailableModels", () => ({
  useAvailableModels: () => ["gpt-4o"],
}));

const noop = () => {};

describe("Settings this gateway does not apply are not shown", () => {
  it("917: keeps the Cache tab in the Org config editor while caching is off", () => {
    expect(editorTabsFor({ cacheOff: true }).map((t) => t.id)).toContain(
      "cache",
    );
    expect(editorTabsFor({ cacheOff: false }).map((t) => t.id)).toContain(
      "cache",
    );
  });

  it("keeps the Providers Cache tab while caching is off", () => {
    expect(providerTabsFor({ cacheOff: true }).map((t) => t.slug)).toEqual([
      "",
      "routing",
      "cache",
    ]);
    expect(providerTabsFor({ cacheOff: false }).map((t) => t.slug)).toEqual([
      "",
      "routing",
      "cache",
    ]);
  });

  it("keeps historical Cache hit rate with a current off-state notice", () => {
    const overview = {
      total_requests: { value: 10 },
      cache_hit_rate: { value: 25 },
    };
    const { unmount } = render(<AnalyticsKpis overview={overview} cacheOff />);
    expect(screen.getByText("Cache hit rate")).toBeInTheDocument();
    expect(screen.getByText(/^25.0%/)).toBeInTheDocument();
    expect(
      screen.getAllByText("Response caching is off on this gateway."),
    ).toHaveLength(1);
    unmount();
    render(<AnalyticsKpis overview={overview} />);
    expect(screen.getByText("Cache hit rate")).toBeInTheDocument();
  });

  it("drops IP access control while the gateway does not apply it, and says so", () => {
    render(
      <SecurityConfigTab
        ipAcl={{ enabled: true, allow: ["10.0.0.0/8"] }}
        ipAclOff="client IP unknown (trusted_proxies not set)"
        onIpAclChange={noop}
        onPrivacyChange={noop}
        onToolPolicyChange={noop}
      />,
    );
    expect(screen.queryByText("Enable IP ACL")).toBeNull();
    expect(
      screen.getByText(
        "IP access control is off on this gateway: client IP unknown (trusted_proxies not set).",
      ),
    ).toBeInTheDocument();
  });

  it("shows no stored default model or conditional routes in the Routing editor", () => {
    render(
      <RoutingConfigTab
        routing={{
          default_model: "gpt-4o",
          conditional_routes: [{ field: "model", operator: "eq", value: "x" }],
        }}
        onChange={noop}
      />,
    );
    expect(screen.queryByText("Default model")).toBeNull();
    expect(screen.queryByText(/Conditional route 1/)).toBeNull();
    expect(
      screen.getByText(
        "This configuration also stores routing settings this gateway does not run (default model, conditional or scheduled routes, race mode). A save keeps them unchanged.",
      ),
    ).toBeInTheDocument();
  });

  it("shows no Mode or Fail Open control on the Guardrails tab", () => {
    render(
      <GuardrailConfigTab
        guardrails={{ checks: {} }}
        onChange={noop}
        nodePipeline={{ enabled: true, fail_open: false, mode: "sequential" }}
      />,
    );
    expect(screen.queryByRole("combobox", { name: "Mode" })).toBeNull();
    expect(screen.queryByRole("checkbox", { name: "Fail Open" })).toBeNull();
  });

  it("names the Validate API on Required and Allowed values", () => {
    render(
      <CreateEditPropertyDialog
        open
        onClose={noop}
        onSubmit={noop}
        property={{
          name: "tier",
          property_type: "enum",
          required: true,
          allowed_values: ["gold"],
        }}
      />,
    );
    const dialog = screen.getByRole("dialog");
    expect(
      within(dialog).getByText("Required by the Validate API"),
    ).toBeInTheDocument();
    expect(
      within(dialog).getByText("Allowed values, checked by the Validate API"),
    ).toBeInTheDocument();
  });
});
