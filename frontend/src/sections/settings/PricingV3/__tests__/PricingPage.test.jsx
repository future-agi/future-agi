import React from "react";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor, fireEvent } from "src/utils/test-utils";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

// ── Mocks ────────────────────────────────────────────────────────────────────

const mockGet = vi.fn();
const mockPost = vi.fn();

vi.mock("src/utils/axios", () => ({
  default: {
    get: (...args) => mockGet(...args),
    post: (...args) => mockPost(...args),
  },
  endpoints: {
    settings: {
      v2: {
        plansAndAddons: "/usage/v2/plans-and-addons/",
        upgradeToPayg: "/usage/v2/upgrade-to-payg/",
        addAddon: "/usage/v2/add-addon/",
        removeAddon: "/usage/v2/remove-addon/",
      },
    },
  },
}));

vi.mock("src/utils/format-number", () => ({
  fCurrency: (val, compact) => `$${Number(val || 0).toFixed(compact ? 4 : 2)}`,
}));

vi.mock("notistack", () => ({
  enqueueSnackbar: vi.fn(),
}));

vi.mock("src/pages/dashboard/settings/stripeVariables", () => ({
  stripePromise: Promise.resolve({
    confirmCardSetup: vi
      .fn()
      .mockResolvedValue({ setupIntent: { status: "succeeded" } }),
  }),
}));

const MOCK_PLANS_RESPONSE = {
  data: {
    result: {
      current_plan: "payg",
      billing_interval: "monthly",
      tiers: [
        {
          key: "free",
          display_name: "Free",
          platform_fee_monthly: 0,
          features: {
            monitors: 3,
            has_knowledge_base: false,
            has_review_workflow: false,
            has_scim: false,
            has_audit_logs: false,
            has_data_masking: false,
            has_optimization: false,
            has_agentic_eval: false,
            retention_traces_days: 30,
          },
        },
        {
          key: "payg",
          display_name: "Pay-as-you-go",
          platform_fee_monthly: 0,
          features: {
            monitors: 3,
            has_knowledge_base: false,
            has_review_workflow: false,
            has_scim: false,
            has_audit_logs: false,
            has_data_masking: false,
            has_optimization: false,
            has_agentic_eval: false,
            retention_traces_days: 30,
          },
        },
      ],
      addons: [
        {
          key: "boost",
          display_name: "Boost",
          platform_fee_monthly: 250,
          features: {
            monitors: 15,
            has_knowledge_base: true,
            has_review_workflow: true,
            has_scim: false,
            has_audit_logs: true,
            has_data_masking: false,
            has_optimization: false,
            has_agentic_eval: true,
            retention_traces_days: 90,
          },
        },
        {
          key: "scale",
          display_name: "Scale",
          platform_fee_monthly: 750,
          features: {
            monitors: -1,
            has_knowledge_base: true,
            has_review_workflow: true,
            has_scim: true,
            has_project_rbac: true,
            has_audit_logs: true,
            has_data_masking: false,
            has_optimization: true,
            has_agentic_eval: true,
            retention_traces_days: 365,
          },
        },
        {
          key: "enterprise",
          display_name: "Enterprise",
          platform_fee_monthly: 2000,
          features: {
            monitors: -1,
            has_knowledge_base: true,
            has_review_workflow: true,
            has_scim: true,
            has_project_rbac: true,
            has_audit_logs: true,
            has_data_masking: true,
            has_optimization: true,
            has_agentic_eval: true,
            retention_traces_days: 2555,
          },
        },
      ],
      pricing: {
        storage: {
          display_name: "Storage",
          display_unit: "GB",
          tiers: [
            { up_to: 50, price_per_unit: 0 },
            { up_to: 500, price_per_unit: 2 },
            { up_to: null, price_per_unit: 1.5 },
          ],
        },
      },
    },
  },
};

function renderWithQuery(ui) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={qc}>{ui}</QueryClientProvider>);
}

// ── Tests ─────────────────────────────────────────────────────────────────────

describe("PricingPage", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockGet.mockResolvedValue(MOCK_PLANS_RESPONSE);
    mockPost.mockResolvedValue({ data: { result: {} } });
  });

  it("shows Plans couldn't be loaded with Retry instead of an empty tier picker when the request fails", async () => {
    mockGet
      .mockRejectedValueOnce(
        Object.assign(new Error("Not found"), { statusCode: 404 }),
      )
      .mockResolvedValue(MOCK_PLANS_RESPONSE);
    const { default: PricingPage } = await import("../PricingPage");
    renderWithQuery(<PricingPage />);

    expect(
      await screen.findByText("Plans couldn't be loaded"),
    ).toBeInTheDocument();
    expect(screen.queryByText("Choose your tier")).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Retry" }));
    expect(await screen.findByText("Choose your tier")).toBeInTheDocument();
    expect(
      screen.queryByText("Plans couldn't be loaded"),
    ).not.toBeInTheDocument();
  });

  it("should be importable", async () => {
    const module = await import("../PricingPage");
    expect(module.default).toBeDefined();
  });

  it("renders 2 tiers and 3 addons from API data", async () => {
    const { default: PricingPage } = await import("../PricingPage");
    renderWithQuery(<PricingPage />);

    // Wait for data to load — use findByText for async rendering
    expect(await screen.findByText("Choose your tier")).toBeInTheDocument();

    // Tier names rendered
    expect(screen.getAllByText("Free").length).toBeGreaterThan(0);
    expect(screen.getAllByText("Pay-as-you-go").length).toBeGreaterThan(0);

    // Addon names rendered
    expect(screen.getAllByText("Boost").length).toBeGreaterThan(0);
    expect(screen.getAllByText("Scale").length).toBeGreaterThan(0);
    expect(screen.getAllByText("Enterprise").length).toBeGreaterThan(0);
  });

  it("shows Current chip on active plan", async () => {
    const { default: PricingPage } = await import("../PricingPage");
    renderWithQuery(<PricingPage />);

    await waitFor(() => {
      expect(screen.getByText("Current")).toBeInTheDocument();
    });
  });

  it("shows add-addon confirmation dialog before calling API", async () => {
    const { default: PricingPage } = await import("../PricingPage");
    renderWithQuery(<PricingPage />);

    await waitFor(() => screen.getByText("Boost"));

    // Click "Add Boost" button
    const addBtn = screen.getByText("Add Boost");
    fireEvent.click(addBtn);

    // Should show confirmation dialog
    await waitFor(() => {
      expect(screen.getByText(/Add Boost Add-on/i)).toBeInTheDocument();
    });
  });

  // Ruling 491 (b): closing the Add dialog used to reset the plan and action at
  // once, so while it faded out it showed "Remove ?" and a Remove button.
  it("keeps the Add title and action while the Add Scale dialog closes", async () => {
    const { default: PricingPage } = await import("../PricingPage");
    renderWithQuery(<PricingPage />);

    fireEvent.click(await screen.findByText("Add Scale"));
    const dialog = await screen.findByRole("dialog");
    const titleText = () =>
      document.getElementById("custom-dialog-title")?.textContent ?? null;
    const removeButton = () =>
      Array.from(document.querySelectorAll('[role="dialog"] button')).some(
        (b) => /^\s*Remove\s*$/.test(b.textContent),
      );
    expect(titleText()).toMatch(/^Add Scale Add-on/);

    const seen = [];
    const record = () =>
      seen.push({ title: titleText(), remove: removeButton() });
    const observer = new MutationObserver(record);
    observer.observe(document.body, {
      subtree: true,
      childList: true,
      characterData: true,
      attributes: true,
    });
    fireEvent.keyDown(dialog, { key: "Escape" });
    record();
    // Still mounted: this is the fade-out, not after it.
    expect(document.getElementById("custom-dialog-title")).not.toBeNull();
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    observer.disconnect();

    const during = seen.filter((s) => s.title !== null);
    expect(during.length).toBeGreaterThan(0);
    expect(during.filter((s) => /^Remove/.test(s.title))).toEqual([]);
    expect(during.filter((s) => s.remove)).toEqual([]);
  });

  it("shows remove-addon warning dialog with plan parameter", async () => {
    // Switch to boost plan to see remove button
    mockGet.mockResolvedValue({
      data: {
        result: {
          ...MOCK_PLANS_RESPONSE.data.result,
          current_plan: "boost",
        },
      },
    });

    const { default: PricingPage } = await import("../PricingPage");
    renderWithQuery(<PricingPage />);

    await waitFor(() => screen.getByText("Remove add-on"));

    fireEvent.click(screen.getByText("Remove add-on"));

    // Should show warning dialog
    await waitFor(() => {
      expect(screen.getByText(/Remove Boost/i)).toBeInTheDocument();
    });
  });

  it("renders Enterprise Add button", async () => {
    const { default: PricingPage } = await import("../PricingPage");
    renderWithQuery(<PricingPage />);

    await waitFor(() => {
      expect(screen.getByText("Enterprise")).toBeInTheDocument();
    });

    // Enterprise card should have "Add Enterprise" button (same as other addons)
    expect(screen.getByText(/Add Enterprise/i)).toBeInTheDocument();
  });

  it("renders feature comparison matrix with API data for boolean features", async () => {
    const { default: PricingPage } = await import("../PricingPage");
    renderWithQuery(<PricingPage />);

    await waitFor(() => {
      expect(screen.getByText("Feature comparison")).toBeInTheDocument();
    });

    // Feature group headers
    expect(screen.getByText("Observability")).toBeInTheDocument();
    expect(screen.getByText("Advanced Features")).toBeInTheDocument();
    expect(screen.getByText("Data Retention")).toBeInTheDocument();
  });

  it("toggles annual billing and shows save badge", async () => {
    const { default: PricingPage } = await import("../PricingPage");
    renderWithQuery(<PricingPage />);

    await waitFor(() => screen.getByText("Save 20%"));

    // Toggle should exist
    const toggle = screen.getByRole("checkbox");
    expect(toggle).not.toBeChecked();

    fireEvent.click(toggle);
    expect(toggle).toBeChecked();
  });

  it("renders usage-based pricing tiers from API", async () => {
    const { default: PricingPage } = await import("../PricingPage");
    renderWithQuery(<PricingPage />);

    await waitFor(() => {
      expect(screen.getByText("Storage")).toBeInTheDocument();
    });
  });

  // Ruling 383: SCIM and project-level RBAC are not built, so no pricing copy
  // offers them, even though the plan entitlements still carry the flags.
  it("never offers SCIM or project RBAC in the matrix or the add-on cards", async () => {
    const { default: PricingPage } = await import("../PricingPage");
    renderWithQuery(<PricingPage />);

    expect(await screen.findByText("Feature comparison")).toBeInTheDocument();
    expect(screen.getAllByText("Custom Roles").length).toBeGreaterThan(0);
    expect(screen.queryByText(/scim/i)).toBeNull();
    expect(screen.queryByText(/project rbac/i)).toBeNull();
  });

  it("never lists SCIM or project RBAC in the add-on confirmation", async () => {
    const { default: PricingPage } = await import("../PricingPage");
    renderWithQuery(<PricingPage />);

    fireEvent.click(await screen.findByText("Add Scale"));
    const dialog = await screen.findByRole("dialog");
    expect(dialog).toHaveTextContent(
      "You\u2019ll get access to these features",
    );
    expect(dialog).toHaveTextContent("Optimization");
    expect(dialog.textContent).not.toMatch(/scim|rbac/i);
  });

  it("never lists SCIM or project RBAC among a custom plan's features", async () => {
    mockGet.mockResolvedValue({
      data: {
        result: {
          ...MOCK_PLANS_RESPONSE.data.result,
          current_plan: "custom",
          isCustomPricing: true,
          customDetails: {
            platform_fee: 12000,
            platform_fee_billing_cycle: 12,
            per_charge_amount: 12000,
            contract_end_date: "2026-12-31",
            features: {
              has_scim: true,
              has_project_rbac: true,
              has_custom_roles: true,
              monitors: 100,
            },
            pricing: {},
          },
        },
      },
    });
    const { default: PricingPage } = await import("../PricingPage");
    renderWithQuery(<PricingPage />);

    expect(await screen.findByText("Your plan features")).toBeInTheDocument();
    expect(screen.getByText("Custom Roles")).toBeInTheDocument();
    expect(screen.queryByText(/scim/i)).toBeNull();
    expect(screen.queryByText(/project rbac/i)).toBeNull();
  });

  it("renders custom pricing from the v2 camelCase contract", async () => {
    mockGet.mockResolvedValue({
      data: {
        result: {
          ...MOCK_PLANS_RESPONSE.data.result,
          current_plan: "custom",
          isCustomPricing: true,
          customDetails: {
            platform_fee: 12000,
            platform_fee_billing_cycle: 12,
            per_charge_amount: 12000,
            contract_end_date: "2026-12-31",
            features: {
              has_scim: true,
              monitors: 100,
            },
            pricing: {
              storage: {
                display_name: "Storage",
                display_unit: "GB",
                tiers: [{ start: 0, end: 500, rate: 1.25 }],
              },
            },
          },
        },
      },
    });

    const { default: PricingPage } = await import("../PricingPage");
    renderWithQuery(<PricingPage />);

    expect(await screen.findByText("Custom Pricing")).toBeInTheDocument();
    expect(screen.getByText("Your plan features")).toBeInTheDocument();
    expect(screen.getByText("Your pricing tiers")).toBeInTheDocument();
    expect(screen.queryByText("Choose your tier")).not.toBeInTheDocument();
  });
});
