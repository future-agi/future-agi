import React from "react";
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { userEvent, render, screen } from "src/utils/test-utils";
import OrgConfigSection from "./OrgConfigSection";

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

vi.mock("../providers/hooks/useOrgConfig", () => ({
  useOrgConfig: () => mockOrgConfigReturn,
  useCreateOrgConfig: () => ({ mutate: mockMutate, isPending: false }),
}));

vi.mock("../providers/hooks/useGatewayConfig", () => ({
  useProviderHealth: () => mockProviderHealthReturn,
}));

vi.mock("../context/useGatewayContext", () => ({
  useGatewayContext: () => ({ gatewayId: "gateway-1" }),
}));

vi.mock("./OrgConfigEditor", () => ({
  default: () => <div>Org config editor</div>,
}));

vi.mock("./ConfigHistoryDrawer", () => ({
  default: () => <div>Config history drawer</div>,
}));

describe("OrgConfigSection", () => {
  beforeEach(() => {
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

  it("discloses the full instant for the local Last Updated time", async () => {
    mockOrgConfigReturn.data = { created_at: "2025-10-31T00:00:00Z" };
    render(<OrgConfigSection />);
    const updated = screen.getByText("31 Oct 2025, 5:30 AM");
    expect(screen.getByText(/Last Updated:/)).toContainElement(updated);
    await userEvent.setup().tab();
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

    expect(screen.getByText(/Last Updated:/)).toBeInTheDocument();
    expect(
      screen.getByText(/Update guardrail pii-detector/),
    ).toBeInTheDocument();
    expect(screen.getAllByText("2")).toHaveLength(2);
    expect(screen.getAllByText(/2 configured/i)[0]).toBeInTheDocument();
    expect(screen.getAllByText(/2 overrides/i)[0]).toBeInTheDocument();
  });
});
