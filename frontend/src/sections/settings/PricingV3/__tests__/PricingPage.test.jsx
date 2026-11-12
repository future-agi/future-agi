import React from "react";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor, fireEvent, act } from "src/utils/test-utils";
import { enqueueSnackbar } from "notistack";
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
        downgradeToFree: "/usage/v2/downgrade-to-free/",
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
  return {
    ...render(<QueryClientProvider client={qc}>{ui}</QueryClientProvider>),
    queryClient: qc,
  };
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

  // Closing the Add dialog used to reset the plan and action at
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

  it.each([
    ["boost", "Boost", "$250.00"],
    ["scale", "Scale", "$750.00"],
    ["enterprise", "Enterprise", "$2,000.00"],
  ])(
    "quotes only the purchasable monthly price for %s, with annual billing disabled",
    async (plan, name, fee) => {
      const { default: PricingPage } = await import("../PricingPage");
      renderWithQuery(<PricingPage />);

      await screen.findByText("Choose your tier");
      // The annual selector is visible but off and disabled, carrying the line;
      // monthly stays the working default, so no annual price is quoted.
      const annual = screen.getByLabelText(/Annual billing/i);
      expect(annual).toBeDisabled();
      expect(annual).not.toBeChecked();
      expect(
        screen.getByText("Not available in this release"),
      ).toBeInTheDocument();
      fireEvent.click(annual);
      expect(annual).not.toBeChecked();
      expect(screen.queryByText("Save 20%")).not.toBeInTheDocument();
      expect(screen.queryByText(/billed annually/i)).not.toBeInTheDocument();
      expect(screen.queryByText(/coming soon/i)).not.toBeInTheDocument();
      expect(screen.getByText(fee)).toBeInTheDocument();

      fireEvent.click(screen.getByRole("button", { name: `Add ${name}` }));
      const confirm = await screen.findByRole("button", {
        name: `Add for ${fee}/mo`,
      });
      expect(mockPost).not.toHaveBeenCalled();
      fireEvent.click(confirm);

      await waitFor(() => {
        expect(mockPost).toHaveBeenCalledTimes(1);
        expect(mockPost).toHaveBeenCalledWith("/usage/v2/add-addon/", { plan });
      });
    },
  );

  it.each(["boost", "scale", "enterprise"])(
    "guides %s through removal and a separate Free confirmation after it takes effect",
    async (plan) => {
      mockGet.mockResolvedValue({
        data: {
          result: { ...MOCK_PLANS_RESPONSE.data.result, current_plan: plan },
        },
      });
      const { default: PricingPage } = await import("../PricingPage");
      const { queryClient } = renderWithQuery(<PricingPage />);

      fireEvent.click(
        await screen.findByRole("button", { name: "Remove add-on first" }),
      );
      expect(
        screen.queryByText("Are you sure you want to downgrade to free?"),
      ).not.toBeInTheDocument();
      expect(
        screen.getByText(/This only removes the add-on; you will remain on/),
      ).toHaveTextContent(
        "To switch to Free, return here after removal takes effect and choose Switch to Free.",
      );
      expect(mockPost).not.toHaveBeenCalled();

      mockGet.mockResolvedValue({
        data: {
          result: {
            ...MOCK_PLANS_RESPONSE.data.result,
            current_plan: plan,
            pending_cancel: true,
            cancel_at: "2030-12-01T12:00:00Z",
          },
        },
      });
      fireEvent.click(screen.getByRole("button", { name: "Remove" }));
      await waitFor(() => {
        expect(mockPost).toHaveBeenCalledTimes(1);
        expect(mockPost).toHaveBeenCalledWith("/usage/v2/remove-addon/", {
          plan,
        });
        expect(
          screen.getByRole("button", { name: "Add-on removal pending" }),
        ).toBeDisabled();
      });
      expect(screen.getByText(/Removal is scheduled for/)).toHaveTextContent(
        new Date("2030-12-01T12:00:00Z").toLocaleDateString(),
      );
      expect(enqueueSnackbar).not.toHaveBeenCalledWith(
        "Downgraded to Free plan",
        expect.anything(),
      );

      // The next server refresh reports that period-end removal has taken effect.
      mockGet.mockResolvedValue(MOCK_PLANS_RESPONSE);
      await act(async () => {
        await queryClient.invalidateQueries({
          queryKey: ["v2-plans-and-addons"],
        });
      });
      fireEvent.click(
        await screen.findByRole("button", { name: "Switch to Free" }),
      );
      expect(
        await screen.findByText("Are you sure you want to downgrade to free?"),
      ).toBeInTheDocument();
      expect(mockPost).toHaveBeenCalledTimes(1);
      mockGet.mockResolvedValue({
        data: {
          result: { ...MOCK_PLANS_RESPONSE.data.result, current_plan: "free" },
        },
      });
      fireEvent.click(screen.getByRole("button", { name: "Downgrade Plan" }));
      await waitFor(() => {
        expect(mockPost).toHaveBeenCalledTimes(2);
        expect(mockPost).toHaveBeenNthCalledWith(
          2,
          "/usage/v2/downgrade-to-free/",
          {},
        );
        expect(enqueueSnackbar).toHaveBeenCalledWith(
          "Downgraded to Free plan",
          {
            variant: "success",
          },
        );
      });
    },
  );

  it.each(["boost", "scale", "enterprise"])(
    "does not repeat removal when %s is already pending cancellation on load",
    async (plan) => {
      mockGet.mockResolvedValue({
        data: {
          result: {
            ...MOCK_PLANS_RESPONSE.data.result,
            current_plan: plan,
            pending_cancel: true,
            cancel_at: null,
          },
        },
      });
      const { default: PricingPage } = await import("../PricingPage");
      renderWithQuery(<PricingPage />);

      const pending = await screen.findByRole("button", {
        name: "Add-on removal pending",
      });
      expect(pending).toBeDisabled();
      fireEvent.click(pending);
      expect(
        screen.getByText(/Removal is scheduled for the end/),
      ).toHaveTextContent(
        "Once removal takes effect, return here and choose Switch to Free.",
      );
      expect(
        screen.queryByRole("button", { name: "Switch to Free" }),
      ).toBeNull();
      expect(
        screen.getByRole("button", { name: "Reinstate add-on" }),
      ).toBeEnabled();
      expect(mockPost).not.toHaveBeenCalled();
    },
  );

  it("cancels the prerequisite dialog without changing either plan or add-on", async () => {
    mockGet.mockResolvedValue({
      data: {
        result: { ...MOCK_PLANS_RESPONSE.data.result, current_plan: "boost" },
      },
    });
    const { default: PricingPage } = await import("../PricingPage");
    renderWithQuery(<PricingPage />);

    fireEvent.click(
      await screen.findByRole("button", { name: "Remove add-on first" }),
    );
    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
    await waitFor(() =>
      expect(screen.queryByRole("dialog")).not.toBeInTheDocument(),
    );
    expect(mockPost).not.toHaveBeenCalled();
  });

  it("does not promise or request a Free downgrade when prerequisite removal fails", async () => {
    mockGet.mockResolvedValue({
      data: {
        result: { ...MOCK_PLANS_RESPONSE.data.result, current_plan: "boost" },
      },
    });
    mockPost.mockRejectedValue(new Error("Removal failed"));
    const { default: PricingPage } = await import("../PricingPage");
    renderWithQuery(<PricingPage />);

    fireEvent.click(
      await screen.findByRole("button", { name: "Remove add-on first" }),
    );
    fireEvent.click(screen.getByRole("button", { name: "Remove" }));
    await waitFor(() => {
      expect(enqueueSnackbar).toHaveBeenCalledWith("Failed to remove add-on", {
        variant: "error",
      });
    });
    expect(mockPost).toHaveBeenCalledTimes(1);
    expect(mockPost).toHaveBeenCalledWith("/usage/v2/remove-addon/", {
      plan: "boost",
    });
    expect(enqueueSnackbar).not.toHaveBeenCalledWith(
      "Downgraded to Free plan",
      expect.anything(),
    );
  });

  it("keeps direct add-on removal separate from the Free downgrade", async () => {
    mockGet.mockResolvedValue({
      data: {
        result: { ...MOCK_PLANS_RESPONSE.data.result, current_plan: "boost" },
      },
    });
    const { default: PricingPage } = await import("../PricingPage");
    renderWithQuery(<PricingPage />);

    fireEvent.click(
      await screen.findByRole("button", { name: "Remove add-on" }),
    );
    fireEvent.click(screen.getByRole("button", { name: "Remove" }));
    await waitFor(() => {
      expect(mockPost).toHaveBeenCalledTimes(1);
      expect(mockPost).toHaveBeenCalledWith("/usage/v2/remove-addon/", {
        plan: "boost",
      });
    });
  });

  it("keeps PAYG's confirmed Free downgrade on the downgrade endpoint", async () => {
    const { default: PricingPage } = await import("../PricingPage");
    renderWithQuery(<PricingPage />);

    fireEvent.click(
      await screen.findByRole("button", { name: "Switch to Free" }),
    );
    expect(mockPost).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "Downgrade Plan" }));
    await waitFor(() => {
      expect(mockPost).toHaveBeenCalledTimes(1);
      expect(mockPost).toHaveBeenCalledWith("/usage/v2/downgrade-to-free/", {});
    });
  });

  it.each([
    { current_plan: "free" },
    { current_plan: "boost", planChangeLocked: true },
    { current_plan: "custom", isCustomPricing: true },
  ])(
    "does not offer a Free transition for $current_plan when unavailable",
    async (overrides) => {
      mockGet.mockResolvedValue({
        data: { result: { ...MOCK_PLANS_RESPONSE.data.result, ...overrides } },
      });
      const { default: PricingPage } = await import("../PricingPage");
      const { container } = renderWithQuery(<PricingPage />);

      // The title also shows while plans load, so wait for the plans instead.
      await waitFor(() =>
        expect(container.querySelector(".MuiSkeleton-root")).toBeNull(),
      );
      expect(
        screen.queryByRole("button", { name: "Switch to Free" }),
      ).toBeNull();
      expect(
        screen.queryByRole("button", { name: "Remove add-on first" }),
      ).toBeNull();
      expect(mockPost).not.toHaveBeenCalled();
    },
  );

  it("renders usage-based pricing tiers from API", async () => {
    const { default: PricingPage } = await import("../PricingPage");
    renderWithQuery(<PricingPage />);

    await waitFor(() => {
      expect(screen.getByText("Storage")).toBeInTheDocument();
    });
  });

  // SCIM and project-level RBAC are not built, so no pricing copy
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

// A stored plan the page has no card for (a code outside Free, Pay-as-you-go,
// Boost, Scale and Enterprise, or Custom without custom pricing) is named as
// it is, never as another plan, and no tier is marked Current for it. The page
// says once that the plan isn't listed, and every button that would replace it
// is disabled and described by that line.
describe("Plans & pricing names an unlisted plan as it is", () => {
  const NOT_LISTED =
    "This plan isn't listed here. Contact support to change it.";

  const withPlan = (overrides) => ({
    data: { result: { ...MOCK_PLANS_RESPONSE.data.result, ...overrides } },
  });

  beforeEach(() => {
    vi.clearAllMocks();
    mockGet.mockResolvedValue(MOCK_PLANS_RESPONSE);
    mockPost.mockResolvedValue({ data: { result: {} } });
  });

  const UNLISTED = [
    ["an unknown plan code", { current_plan: "legacy_team" }, "legacy_team"],
    [
      "Custom without custom pricing",
      { current_plan: "custom", isCustomPricing: false },
      "Custom",
    ],
  ];

  it.each(UNLISTED)(
    "names %s as it is, marks no tier Current and says once that the plan isn't listed here",
    async (_case, overrides, name) => {
      mockGet.mockResolvedValue(withPlan(overrides));
      const { default: PricingPage } = await import("../PricingPage");
      renderWithQuery(<PricingPage />);
      await screen.findByText("Choose your tier");

      expect(screen.queryByText("Current: Pay-as-you-go")).toBeNull();
      expect(screen.getByText(`Current: ${name}`)).toBeInTheDocument();
      expect(screen.getAllByText(NOT_LISTED)).toHaveLength(1);
      // No tier or add-on claims to be the plan.
      expect(screen.queryByText("Current")).toBeNull();
      expect(screen.queryByRole("button", { name: "Current plan" })).toBeNull();
      expect(screen.queryByText("Active")).toBeNull();
      expect(
        screen.queryByRole("button", { name: "Included in your plan" }),
      ).toBeNull();
    },
  );

  it.each(UNLISTED)(
    "disables Upgrade to PAYG, Switch to Free and every Add button for %s, each described by the line that the plan isn't listed, and sends no request",
    async (_case, overrides) => {
      mockGet.mockResolvedValue(withPlan(overrides));
      const { default: PricingPage } = await import("../PricingPage");
      renderWithQuery(<PricingPage />);
      await screen.findByText("Choose your tier");

      const buttons = [
        "Upgrade to PAYG",
        "Switch to Free",
        "Add Boost",
        "Add Scale",
        "Add Enterprise",
      ].map((label) => screen.getByRole("button", { name: label }));
      for (const button of buttons) {
        expect(button).toBeDisabled();
        expect(button).toHaveAccessibleDescription(NOT_LISTED);
        fireEvent.click(button);
      }
      expect(mockPost).not.toHaveBeenCalled();
      expect(screen.queryByRole("dialog")).toBeNull();
    },
  );

  it.each([
    ["free", { current_plan: "free" }, null, ["Upgrade to PAYG"]],
    [
      "payg",
      {},
      "Current: Pay-as-you-go",
      ["Switch to Free", "Add Boost", "Add Scale", "Add Enterprise"],
    ],
    [
      "boost",
      { current_plan: "boost" },
      "Current: Boost",
      ["Remove add-on first", "Remove add-on", "Add Scale", "Add Enterprise"],
    ],
    [
      "custom with custom pricing",
      { current_plan: "custom", isCustomPricing: true },
      null,
      [],
    ],
  ])(
    "reads %s exactly as before, with no line that the plan isn't listed",
    async (plan, overrides, header, enabled) => {
      mockGet.mockResolvedValue(withPlan(overrides));
      const { default: PricingPage } = await import("../PricingPage");
      const { container } = renderWithQuery(<PricingPage />);
      // The title also shows while plans load, so wait for the plans instead.
      await waitFor(() =>
        expect(container.querySelector(".MuiSkeleton-root")).toBeNull(),
      );

      expect(screen.queryByText(NOT_LISTED)).toBeNull();
      if (header) {
        expect(screen.getByText(header)).toBeInTheDocument();
      } else {
        expect(screen.queryByText(/^Current: /)).toBeNull();
      }
      const tiersShown = plan !== "custom with custom pricing";
      expect(screen.queryAllByText("Current")).toHaveLength(tiersShown ? 1 : 0);
      expect(
        screen.queryAllByRole("button", { name: "Current plan" }),
      ).toHaveLength(tiersShown ? 1 : 0);
      for (const label of enabled) {
        const button = screen.getByRole("button", { name: label });
        expect(button).toBeEnabled();
        expect(button).not.toHaveAccessibleDescription();
      }
    },
  );
});
