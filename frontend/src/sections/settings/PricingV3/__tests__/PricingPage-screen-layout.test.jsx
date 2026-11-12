import React from "react";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { createTheme } from "@mui/material/styles";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "src/utils/test-utils";
import { palette } from "src/theme/palette";
import { typography } from "src/theme/typography";
import { shadows } from "src/theme/shadows";
import { customShadows } from "src/theme/custom-shadows";
import { dsShape } from "src/theme/ds-tokens";
import { componentsOverrides } from "src/theme/overrides";

import PricingPage from "../PricingPage";

// Screen-pass layout checks for Settings > Plans & pricing, under the app
// theme.

const mockGet = vi.fn();

vi.mock("src/utils/axios", () => ({
  default: {
    get: (...args) => mockGet(...args),
    post: vi.fn(),
    put: vi.fn(),
  },
  endpoints: {
    settings: {
      v2: {
        plansAndAddons: "/usage/v2/plans-and-addons/",
        upgradeToPayg: "/usage/v2/upgrade-to-payg/",
        addAddon: "/usage/v2/add-addon/",
        removeAddon: "/usage/v2/remove-addon/",
        reinstateAddon: "/usage/v2/reinstate-addon/",
        downgradeToFree: "/usage/v2/downgrade-to-free/",
      },
    },
  },
}));

vi.mock("notistack", () => ({
  enqueueSnackbar: vi.fn(),
}));

vi.mock("src/pages/dashboard/settings/stripeVariables", () => ({
  stripePromise: Promise.resolve({}),
}));

const features = (overrides) => ({
  monitors: 3,
  has_knowledge_base: false,
  has_review_workflow: false,
  has_scim: false,
  has_audit_logs: false,
  has_data_masking: false,
  has_optimization: false,
  has_agentic_eval: false,
  retention_traces_days: 30,
  ...overrides,
});

const PLANS = {
  data: {
    result: {
      current_plan: "payg",
      billing_interval: "monthly",
      tiers: [
        {
          key: "free",
          display_name: "Free",
          platform_fee_monthly: 0,
          features: features(),
        },
        {
          key: "payg",
          display_name: "Pay-as-you-go",
          platform_fee_monthly: 0,
          features: features(),
        },
      ],
      addons: [
        {
          key: "boost",
          display_name: "Boost",
          platform_fee_monthly: 250,
          features: features({
            monitors: 15,
            has_knowledge_base: true,
            has_review_workflow: true,
            retention_traces_days: 90,
          }),
        },
        {
          key: "scale",
          display_name: "Scale",
          platform_fee_monthly: 750,
          features: features({
            monitors: -1,
            has_knowledge_base: true,
            has_review_workflow: true,
            has_optimization: true,
            retention_traces_days: 365,
          }),
        },
        {
          key: "enterprise",
          display_name: "Enterprise",
          platform_fee_monthly: 2000,
          features: features({
            monitors: -1,
            has_knowledge_base: true,
            has_review_workflow: true,
            has_data_masking: true,
            has_optimization: true,
            retention_traces_days: 2555,
          }),
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

const SIDES = ["Top", "Right", "Bottom", "Left"];

function appTheme(mode) {
  const theme = createTheme({
    palette: palette(mode),
    customShadows: customShadows(mode),
    shadows: shadows(mode),
    shape: dsShape,
    typography,
  });
  theme.components = componentsOverrides(theme);
  return theme;
}

function renderPricing(mode = "light") {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <PricingPage />
    </QueryClientProvider>,
    { theme: appTheme(mode) },
  );
}

function paddings(element) {
  const style = getComputedStyle(element);
  return SIDES.map((side) => style[`padding${side}`]);
}

async function loadedPagePadding() {
  mockGet.mockResolvedValue(PLANS);
  const loaded = renderPricing();
  expect(await screen.findByText("Choose your tier")).toBeInTheDocument();
  const padding = paddings(loaded.container.firstChild);
  loaded.unmount();
  return padding;
}

describe("Plans & pricing screen layout", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockGet.mockResolvedValue(PLANS);
  });

  // Finding rank 5: the loading skeleton and the load-failed card had their
  // own 24px inset, so the content jumped left when the plans loaded.
  it("keeps the Plans loading skeleton on the loaded page's edges", async () => {
    const loadedPadding = await loadedPagePadding();

    mockGet.mockReturnValue(new Promise(() => {}));
    const { container } = renderPricing();
    expect(container.querySelector(".MuiSkeleton-root")).toBeInTheDocument();
    expect(screen.queryByText("Choose your tier")).not.toBeInTheDocument();
    expect(paddings(container.firstChild)).toEqual(loadedPadding);
  });

  it("keeps Plans couldn't be loaded on the loaded page's edges", async () => {
    const loadedPadding = await loadedPagePadding();

    mockGet.mockRejectedValue(new Error("Not found"));
    const { container } = renderPricing();
    expect(
      await screen.findByText("Plans couldn't be loaded"),
    ).toBeInTheDocument();
    expect(paddings(container.firstChild)).toEqual(loadedPadding);
  });

  // Finding rank 7: the add-on card body (Section's inner box) was a plain
  // block, so the list's flexGrow and the button row's mt: auto did nothing
  // and Add Boost sat higher than Add Scale and Add Enterprise.
  it.each(["light", "dark"])(
    "makes each add-on card body a full-height column so the Add buttons share a baseline (%s)",
    async (mode) => {
      renderPricing(mode);
      expect(await screen.findByText("Add Boost")).toBeInTheDocument();

      ["Boost", "Scale", "Enterprise"].forEach((name) => {
        const button = screen.getByText(`Add ${name}`).closest("button");
        const actionRow = button.parentElement;
        const body = actionRow.parentElement;
        const card = body.parentElement;

        expect(body.querySelector("ul")).not.toBeNull();
        expect(card.lastElementChild).toBe(body);
        expect(getComputedStyle(card).display).toBe("flex");
        expect(getComputedStyle(card).flexDirection).toBe("column");

        const bodyStyle = getComputedStyle(body);
        expect(bodyStyle.display).toBe("flex");
        expect(bodyStyle.flexDirection).toBe("column");
        expect(bodyStyle.flexGrow).toBe("1");
      });
    },
  );
});
