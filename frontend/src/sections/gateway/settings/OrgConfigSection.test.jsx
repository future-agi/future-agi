import React from "react";
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { userEvent, render, screen } from "src/utils/test-utils";
import OrgConfigSection from "./OrgConfigSection";

vi.mock("ag-grid-react", async () => {
  const { forwardRef } = await import("react");
  return {
    AgGridReact: forwardRef(function MockGrid() {
      return <div data-testid="ag-grid" />;
    }),
  };
});

let mockOrganizationId = "org-1";
let mockGatewayId = "gateway-1";
let mockApplyState = "not_applied";
vi.mock("src/contexts/OrganizationContext", () => ({
  useOrganization: () => ({
    orgLevel: 8,
    currentOrganizationId: mockOrganizationId,
  }),
}));
const mockMutate = vi.fn();
const NativeDateTimeFormat = Intl.DateTimeFormat;

let mockOrgConfigReturn = {
  data: null,
  isLoading: false,
  error: null,
};

let mockProviderHealthReturn = {
  data: { providers: [] },
};

vi.mock("src/auth/hooks", () => ({
  useAuthContext: () => ({
    user: { organization_role: "Owner" },
    role: "Owner",
  }),
}));

vi.mock("../providers/hooks/useOrgConfig", () => ({
  useOrgConfig: () => mockOrgConfigReturn,
  useCreateOrgConfig: () => ({ mutate: mockMutate, isPending: false }),
}));

vi.mock("../providers/hooks/useGatewayConfig", () => ({
  useProviderHealth: () => mockProviderHealthReturn,
  useGatewayConfig: () => ({ data: undefined, isLoading: false }),
}));

vi.mock("../context/useGatewayContext", () => ({
  useGatewayContext: () => ({ gatewayId: mockGatewayId }),
}));

vi.mock("./OrgConfigEditor", () => ({
  default: ({ open, defaultTab, onClose, onSaved, savedReceipt }) =>
    open ? (
      <div role="dialog" aria-label="Org config editor">
        <span>{defaultTab}</span>
        {savedReceipt && (
          <span>Editor receipt: {savedReceipt.applyState}</span>
        )}
        <button
          onClick={() =>
            onSaved({
              organizationId: mockOrganizationId,
              gatewayId: mockGatewayId,
              version: 6,
              applyState: mockApplyState,
            })
          }
        >
          Store config
        </button>
        <button onClick={onClose}>Close editor</button>
      </div>
    ) : null,
}));

vi.mock("./ConfigHistoryDrawer", () => ({
  default: () => <div>Config history drawer</div>,
}));

describe("OrgConfigSection", () => {
  beforeEach(() => {
    mockOrganizationId = "org-1";
    mockGatewayId = "gateway-1";
    mockApplyState = "not_applied";
    window.history.replaceState(
      {},
      "",
      "/dashboard/gateway/settings?tab=org-config",
    );
    vi.spyOn(Intl, "DateTimeFormat").mockImplementation((locale, options) =>
      locale === undefined
        ? { resolvedOptions: () => ({ timeZone: "Asia/Kolkata" }) }
        : new NativeDateTimeFormat(locale, options),
    );
    mockMutate.mockReset();
    mockOrgConfigReturn = {
      data: null,
      isLoading: false,
      error: null,
    };
    mockProviderHealthReturn = {
      data: { providers: [] },
    };
  });
  afterEach(() => vi.restoreAllMocks());

  it.each([
    ["not_applied", "Saved · Not applied"],
    ["unconfirmed", "Saved, application not confirmed"],
    ["applied", "Saved · Applied to gateway"],
  ])(
    "retains the %s receipt after closing and passes it back when reopened",
    async (state, label) => {
      mockApplyState = state;
      mockOrgConfigReturn.data = { version: 5 };
      render(<OrgConfigSection />);
      await userEvent.click(
        screen.getByRole("button", { name: "Edit Config" }),
      );
      await userEvent.click(
        screen.getByRole("button", { name: "Store config" }),
      );
      await userEvent.click(
        screen.getByRole("button", { name: "Close editor" }),
      );
      expect(screen.getByText(label)).toBeInTheDocument();
      expect(screen.getByText(/Last save: version 6/)).toBeInTheDocument();
      expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
      await userEvent.click(
        screen.getByRole("button", { name: "Edit Config" }),
      );
      expect(screen.getByRole("dialog")).toHaveTextContent(
        `Editor receipt: ${state}`,
      );
      expect(mockMutate).not.toHaveBeenCalled();
    },
  );

  it.each(["organization", "gateway"])(
    "clears the saved receipt on a %s change",
    async (scope) => {
      mockOrgConfigReturn.data = { version: 5 };
      const { rerender } = render(<OrgConfigSection />);
      await userEvent.click(
        screen.getByRole("button", { name: "Edit Config" }),
      );
      await userEvent.click(
        screen.getByRole("button", { name: "Store config" }),
      );
      await userEvent.click(
        screen.getByRole("button", { name: "Close editor" }),
      );
      expect(screen.getByText("Saved · Not applied")).toBeInTheDocument();
      if (scope === "organization") mockOrganizationId = "org-2";
      else mockGatewayId = "gateway-2";
      rerender(<OrgConfigSection />);
      expect(
        screen.queryByText("Saved · Not applied"),
      ).not.toBeInTheDocument();
      mockOrganizationId = "org-1";
      mockGatewayId = "gateway-1";
      rerender(<OrgConfigSection />);
      expect(
        screen.queryByText("Saved · Not applied"),
      ).not.toBeInTheDocument();
      expect(mockMutate).not.toHaveBeenCalled();
    },
  );

  it("keeps the editor mounted through a background config read failure so its save receipt can arrive", async () => {
    const refetch = vi.fn();
    mockOrgConfigReturn.data = { version: 5 };
    const { rerender } = render(<OrgConfigSection />);
    await userEvent.click(
      screen.getByRole("button", { name: "Edit Config" }),
    );
    mockOrgConfigReturn = {
      ...mockOrgConfigReturn,
      error: { statusCode: 500 },
      refetch,
    };
    rerender(<OrgConfigSection />);
    expect(
      screen.getByRole("dialog", { name: "Org config editor" }),
    ).toBeInTheDocument();
    expect(
      screen.getByText(/Could not refresh organization config/),
    ).toBeInTheDocument();
    await userEvent.click(
      screen.getByRole("button", { name: "Store config" }),
    );
    await userEvent.click(
      screen.getByRole("button", { name: "Close editor" }),
    );
    expect(screen.getByText("Saved · Not applied")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Retry" }));
    expect(refetch).toHaveBeenCalledTimes(1);
    expect(mockMutate).not.toHaveBeenCalled();
  });

  it("opens only the recognized budget deep link and consumes it on close", async () => {
    const user = userEvent.setup();
    window.history.replaceState(
      {},
      "",
      "/dashboard/gateway/settings?tab=org-config&section=budgets&org=org-1&gateway=gw-1",
    );
    render(<OrgConfigSection />);
    expect(
      screen.getByRole("dialog", { name: "Org config editor" }),
    ).toHaveTextContent("budgets");
    await user.click(screen.getByRole("button", { name: "Close editor" }));
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    const params = new URLSearchParams(window.location.search);
    expect(params.get("section")).toBeNull();
    expect(params.get("tab")).toBe("org-config");
    expect(params.get("org")).toBe("org-1");
    expect(params.get("gateway")).toBe("gw-1");
    expect(mockMutate).not.toHaveBeenCalled();
  });

  it.each(["providers", "unknown"])(
    "does not open an editor from an unsupported section=%s",
    (section) => {
      window.history.replaceState(
        {},
        "",
        `/dashboard/gateway/settings?tab=org-config&section=${section}`,
      );
      render(<OrgConfigSection />);
      expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    },
  );

  it("waits for config loading and error recovery before opening the linked editor", () => {
    window.history.replaceState(
      {},
      "",
      "/dashboard/gateway/settings?tab=org-config&section=budgets",
    );
    mockOrgConfigReturn.isLoading = true;
    const { rerender } = render(<OrgConfigSection />);
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    mockOrgConfigReturn = {
      data: null,
      isLoading: false,
      error: new Error("Unavailable"),
      refetch: vi.fn(),
    };
    rerender(<OrgConfigSection />);
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    mockOrgConfigReturn = {
      data: { budgets: { org_period: "daily" } },
      isLoading: false,
      error: null,
    };
    rerender(<OrgConfigSection />);
    expect(screen.getByRole("dialog")).toHaveTextContent("budgets");
  });

  it("discloses the full instant for the local Last Updated time", async () => {
    mockOrgConfigReturn.data = { created_at: "2025-10-31T00:00:00Z" };
    render(<OrgConfigSection />);
    const updated = screen.getByText("31 Oct 2025, 5:30 AM");
    expect(screen.getByText(/Last updated:/)).toContainElement(updated);
    // View history and Edit config come first in the panel header.
    const user = userEvent.setup();
    await user.tab();
    await user.tab();
    await user.tab();
    expect(updated).toHaveFocus();
    const tooltip = await screen.findByRole("tooltip");
    expect(tooltip).toHaveTextContent("Asia/Kolkata (UTC+05:30)");
    expect(tooltip).toHaveTextContent("2025-10-31T00:00:00.000Z");
  });

  it("renders snake_case metadata and guardrail count from rules fallback", () => {
    const createdAt = "2026-04-14T08:34:37.245253Z";

    mockOrgConfigReturn = {
      data: {
        id: "cfg-1",
        version: 48,
        created_at: createdAt,
        change_description: "Update guardrail pii-detector",
        guardrails: {
          enabled: true,
          checks: {},
          rules: [
            { name: "pii-detector", stage: "pre", action: "block" },
            { name: "content-moderation", stage: "pre", action: "block" },
          ],
        },
        routing: { strategy: "fallback" },
        cache: { enabled: true, backend: "memory" },
      },
      isLoading: false,
      error: null,
    };

    mockProviderHealthReturn = {
      data: {
        providers: [{ name: "openai" }, { name: "anthropic" }],
      },
    };

    render(<OrgConfigSection />);

    expect(screen.getByText(/Last updated:/)).toBeInTheDocument();
    expect(
      screen.getByText(/Update guardrail pii-detector/),
    ).toBeInTheDocument();
    expect(screen.getAllByText("2")).toHaveLength(2);
    expect(screen.getAllByText(/2 configured/i)[0]).toBeInTheDocument();
    expect(screen.getAllByText(/2 overrides/i)[0]).toBeInTheDocument();
  });
});
