import React from "react";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { createTheme } from "@mui/material/styles";
import {
  QueryCache,
  QueryClient,
  QueryClientProvider,
} from "@tanstack/react-query";
import { enqueueSnackbar } from "notistack";
import {
  render,
  renderHook,
  screen,
  waitFor,
  within,
} from "src/utils/test-utils";
import { palette } from "src/theme/palette";
import { typography } from "src/theme/typography";
import { shadows } from "src/theme/shadows";
import { customShadows } from "src/theme/custom-shadows";
import { dsShape } from "src/theme/ds-tokens";
import { componentsOverrides } from "src/theme/overrides";
import { handleError } from "src/utils/queryErrorHandler";
import { usePlansAndAddons } from "src/hooks/use-plans-and-addons";

import PricingPage from "../PricingPage";

// Settings > Plans & pricing: the add-on cards
// agree with the Feature comparison, a failed read is reported once, the title
// stays put while plans load, one filled button per view, and the tables use
// the theme's header type, keep each unit next to its price and drop rows that
// are a dash for every plan. Real money formatter, app theme.

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
  has_audit_logs: false,
  has_data_masking: false,
  has_optimization: false,
  has_agentic_eval: false,
  retention_traces_days: 30,
  ...overrides,
});

// Sample plans: Boost already carries data masking and optimization, and
// no add-on carries custom roles (the comparison crosses it out).
const BOOST_FLAGS = {
  has_knowledge_base: true,
  has_review_workflow: true,
  has_audit_logs: true,
  has_data_masking: true,
  has_optimization: true,
  has_agentic_eval: true,
};

const SAMPLE_ADDONS = [
  {
    key: "boost",
    display_name: "Boost",
    platform_fee_monthly: 250,
    features: features({
      ...BOOST_FLAGS,
      monitors: 15,
      retention_traces_days: 90,
    }),
  },
  {
    key: "scale",
    display_name: "Scale",
    platform_fee_monthly: 750,
    features: features({
      ...BOOST_FLAGS,
      monitors: -1,
      retention_traces_days: 365,
    }),
  },
  {
    key: "enterprise",
    display_name: "Enterprise",
    platform_fee_monthly: 2000,
    features: features({
      ...BOOST_FLAGS,
      monitors: -1,
      retention_traces_days: 2555,
      gateway_webhooks: 5,
    }),
  },
];

// Each add-on brings its own flag: custom roles and optimization arrive with
// Scale, data masking with Enterprise.
const STEP_ADDONS = [
  {
    ...SAMPLE_ADDONS[0],
    features: features({
      has_knowledge_base: true,
      has_review_workflow: true,
      monitors: 15,
      retention_traces_days: 90,
    }),
  },
  {
    ...SAMPLE_ADDONS[1],
    features: features({
      has_knowledge_base: true,
      has_review_workflow: true,
      has_custom_roles: true,
      has_optimization: true,
      monitors: -1,
      retention_traces_days: 365,
    }),
  },
  {
    ...SAMPLE_ADDONS[2],
    features: features({
      has_knowledge_base: true,
      has_review_workflow: true,
      has_custom_roles: true,
      has_optimization: true,
      has_data_masking: true,
      monitors: -1,
      retention_traces_days: 2555,
    }),
  },
];

const plans = ({ currentPlan = "payg", addons = SAMPLE_ADDONS } = {}) => ({
  data: {
    result: {
      current_plan: currentPlan,
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
      addons,
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
});

// What the API client rejects with when the plans read answers 503.
const UNAVAILABLE = {
  status: false,
  statusCode: 503,
  result: "Service unavailable",
  message: "Service unavailable",
};

function appTheme(mode = "light") {
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

// The app's QueryClient reports a failed read with a toast unless the reader
// says it shows the failure itself (src/app.jsx).
function appQueryClient() {
  return new QueryClient({
    queryCache: new QueryCache({ onError: handleError }),
    defaultOptions: { queries: { retry: false } },
  });
}

function renderPricing(theme = appTheme()) {
  return render(
    <QueryClientProvider client={appQueryClient()}>
      <PricingPage />
    </QueryClientProvider>,
    { theme },
  );
}

// The bullets of one add-on card: its Add button sits in the action row, the
// last child of the card body that also holds the list.
function cardBullets(name) {
  const button = screen.getByRole("button", { name: `Add ${name}` });
  const body = button.parentElement.parentElement;
  return Array.from(body.querySelectorAll("li"), (li) => li.textContent);
}

// The theme gives a size in px (a number) or as a CSS string ("0.75rem").
const cssSize = (value) => (typeof value === "number" ? `${value}px` : value);

// A theme colour as getComputedStyle reports it ("transparent" reads
// rgba(0, 0, 0, 0)).
function computedBackground(color) {
  const probe = document.createElement("div");
  probe.style.backgroundColor = color;
  document.body.appendChild(probe);
  const value = getComputedStyle(probe).backgroundColor;
  probe.remove();
  return value;
}

// Style rules a table row hands down to its cells (sx "& th"). The browser
// puts them over the cells' own theme padding.
function rulesFromRow(row) {
  const prefixes = Array.from(row.classList, (name) => `.${name} `);
  return Array.from(document.styleSheets)
    .flatMap((sheet) => Array.from(sheet.cssRules))
    .filter((rule) =>
      prefixes.some((prefix) => rule.selectorText?.startsWith(prefix)),
    );
}

// The upgrade dialog the app always mounts after the routed page: closed, it
// still reads the same plans and shows when they are loading.
function ClosedUpgradeDialogPlans() {
  const { isLoading } = usePlansAndAddons(false);
  return <div aria-busy={isLoading} />;
}

describe("Plans & pricing", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockGet.mockResolvedValue(plans());
  });

  // the card bullets were hard-coded, so Scale offered "Custom roles"
  // that the comparison crosses out for +Scale and +Enterprise, and
  // Enterprise offered "Data masking" that the comparison already ticks for
  // +Boost.
  it.each([
    [
      "Boost already carries the advanced flags",
      SAMPLE_ADDONS,
      {
        Boost: [
          "Knowledge bases",
          "Review workflows",
          "15 monitors & alerts",
          "90-day data retention",
        ],
        Scale: [
          "Everything in Boost",
          "Unlimited monitors & alerts",
          "1-year data retention",
        ],
        Enterprise: [
          "Everything in Scale",
          "Dedicated support",
          "7-year data retention",
          "Custom pricing",
        ],
      },
    ],
    [
      "each add-on brings its own flag",
      STEP_ADDONS,
      {
        Boost: [
          "Knowledge bases",
          "Review workflows",
          "15 monitors & alerts",
          "90-day data retention",
        ],
        Scale: [
          "Everything in Boost",
          "Unlimited monitors & alerts",
          "Custom roles",
          "Optimization",
          "1-year data retention",
        ],
        Enterprise: [
          "Everything in Scale",
          "Data masking",
          "Dedicated support",
          "7-year data retention",
          "Custom pricing",
        ],
      },
    ],
  ])(
    "lists on each add-on card only what its flags add over the plan below it (%s)",
    async (_case, addons, expected) => {
      mockGet.mockResolvedValue(plans({ addons }));
      renderPricing();
      expect(await screen.findByText("Feature comparison")).toBeInTheDocument();

      expect({
        Boost: cardBullets("Boost"),
        Scale: cardBullets("Scale"),
        Enterprise: cardBullets("Enterprise"),
      }).toEqual(expected);
    },
  );

  // Commercial terms with no entitlement behind them stay on the Enterprise
  // card as written, whatever the flags say.
  it("keeps Custom pricing and Dedicated support on the Enterprise card", async () => {
    renderPricing();
    expect(await screen.findByText("Feature comparison")).toBeInTheDocument();

    expect(cardBullets("Enterprise")).toEqual(
      expect.arrayContaining(["Dedicated support", "Custom pricing"]),
    );
  });

  // the plans read had no errorHandled meta, so the app toasted a vague
  // "Something went wrong" on top of "Plans couldn't be loaded".
  it("reports a failed plans read once, on the page, with no toast", async () => {
    mockGet.mockRejectedValue(UNAVAILABLE);
    renderPricing();

    expect(
      await screen.findByText("Plans couldn't be loaded"),
    ).toBeInTheDocument();
    expect(enqueueSnackbar).not.toHaveBeenCalled();
  });

  // The app always mounts the upgrade dialog after the routed page, and the
  // closed dialog reads the same plans. The query keeps the options of the
  // last reader that rendered, so the dialog's read must not bring the toast
  // back once it re-renders while the page's read is still in flight.
  it("adds no toast to a failed plans read while the closed upgrade dialog also reads plans", async () => {
    mockGet.mockImplementation(
      () =>
        new Promise((_resolve, reject) => {
          setTimeout(() => reject(UNAVAILABLE), 50);
        }),
    );
    render(
      <QueryClientProvider client={appQueryClient()}>
        <PricingPage />
        <ClosedUpgradeDialogPlans />
      </QueryClientProvider>,
      { theme: appTheme() },
    );

    expect(
      await screen.findByText("Plans couldn't be loaded"),
    ).toBeInTheDocument();
    expect(enqueueSnackbar).not.toHaveBeenCalled();
  });

  // Guard: the upgrade dialog reads the same plans and shows no failure of
  // its own, so its read keeps the app's toast.
  it("keeps the toast for a plans read that shows no failure of its own", async () => {
    mockGet.mockRejectedValue(UNAVAILABLE);
    const client = appQueryClient();
    const wrapper = ({ children }) => (
      <QueryClientProvider client={client}>{children}</QueryClientProvider>
    );
    const { result } = renderHook(() => usePlansAndAddons(), { wrapper });

    await waitFor(() => expect(result.current.isError).toBe(true));
    expect(enqueueSnackbar).toHaveBeenCalledTimes(1);
    expect(enqueueSnackbar).toHaveBeenCalledWith(expect.any(String), {
      variant: "error",
    });
  });

  // the title appeared only once plans arrived, so it popped in.
  it("shows the Plans & pricing title above the loading skeleton", () => {
    mockGet.mockReturnValue(new Promise(() => {}));
    const { container } = renderPricing();

    expect(container.querySelector(".MuiSkeleton-root")).toBeInTheDocument();
    expect(
      screen.getByRole("heading", { name: "Plans & pricing" }),
    ).toBeInTheDocument();
    expect(screen.queryByText("Choose your tier")).not.toBeInTheDocument();
  });

  it("shows the Plans & pricing title above Plans couldn't be loaded", async () => {
    mockGet.mockRejectedValue(UNAVAILABLE);
    renderPricing();

    expect(
      await screen.findByText("Plans couldn't be loaded"),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("heading", { name: "Plans & pricing" }),
    ).toBeInTheDocument();
  });

  // Add Boost, Add Scale and Add Enterprise were three filled primary
  // buttons side by side.
  it.each([
    ["payg", []],
    ["free", ["Upgrade to PAYG"]],
  ])(
    "keeps at most one filled button in the %s view",
    async (currentPlan, filled) => {
      mockGet.mockResolvedValue(plans({ currentPlan }));
      const { container } = renderPricing();
      expect(
        await screen.findByRole("button", { name: "Add Boost" }),
      ).toBeInTheDocument();

      const contained = Array.from(
        container.querySelectorAll(".MuiButton-contained"),
        (button) => button.textContent,
      );
      expect(contained).toEqual(filled);
    },
  );

  // both table header rows were set at 1rem, above the 13 px section
  // headings, in a heavier weight than the theme's header, and the
  // comparison header row had a fill the theme's header does not.
  it("sets the comparison and usage table headers in the theme's header type", async () => {
    const theme = appTheme();
    const { head } = theme.components.MuiTableCell.styleOverrides;
    renderPricing(theme);
    expect(await screen.findByText("Feature comparison")).toBeInTheDocument();

    const headers = ["Feature", "+Boost", "+Enterprise", "Dimension"];
    [...headers, "Price per Unit"].forEach((name) => {
      const header = screen.getByRole("columnheader", { name });
      expect(getComputedStyle(header).fontSize).toBe(cssSize(head.fontSize));
      expect(getComputedStyle(header).fontWeight).toBe(String(head.fontWeight));
    });

    const comparisonHead = screen
      .getByRole("columnheader", { name: "Feature" })
      .closest("tr");
    expect(getComputedStyle(comparisonHead).backgroundColor).toBe(
      computedBackground(head.backgroundColor),
    );
  });

  // A custom-pricing organization's "Your pricing tiers" header row has the
  // theme's header height and weight, like the page's other tables.
  it("keeps the theme's header height on Your pricing tiers", async () => {
    mockGet.mockResolvedValue({
      data: {
        result: {
          ...plans().data.result,
          current_plan: "custom",
          isCustomPricing: true,
          customDetails: {
            platform_fee: 12000,
            platform_fee_billing_cycle: 12,
            per_charge_amount: 12000,
            features: { monitors: 100 },
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
    renderPricing();
    expect(await screen.findByText("Your pricing tiers")).toBeInTheDocument();

    const tiersHead = screen
      .getByRole("columnheader", { name: "Dimension" })
      .closest("tr");
    const padding = rulesFromRow(tiersHead).flatMap((rule) =>
      ["padding-top", "padding-bottom"]
        .map((property) => rule.style.getPropertyValue(property))
        .filter(Boolean),
    );
    expect(padding).toEqual([]);
    const { head } = appTheme().components.MuiTableCell.styleOverrides;
    for (const name of ["Dimension", "Tier Range", "Price per Unit"]) {
      expect(
        getComputedStyle(screen.getByRole("columnheader", { name })).fontWeight,
      ).toBe(String(head.fontWeight));
    }
  });

  // "per GB" sat at the far right of the price cell.
  it("puts the unit right after each price in Usage-based pricing", async () => {
    renderPricing();
    expect(await screen.findByText("Usage-based pricing")).toBeInTheDocument();

    const units = screen.getAllByText("per GB");
    const prices = units.map(
      (unit) => unit.previousElementSibling?.textContent,
    );
    expect(prices).toEqual(["$2.00", "$1.50"]);
    units.forEach((unit) => {
      expect(getComputedStyle(unit.parentElement).justifyContent).not.toBe(
        "space-between",
      );
    });
  });

  // Every comparison row renders, including a row that is a dash for every
  // plan: the page states what each plan includes and hides nothing.
  it("keeps every comparison row, including rows that are a dash for every plan", async () => {
    renderPricing();
    expect(await screen.findByText("Feature comparison")).toBeInTheDocument();

    [
      "Alert Rules",
      "Turing Evals (Managed AI)",
      "Gateway Email Alerts",
      "Limits & Rates",
      "Annotation Queues",
      "Shadow Experiments",
      "Automation Rules",
      "Max Concurrency",
      "API Rate Limit",
      "Gateway Log Retention",
    ].forEach((label) => {
      expect(screen.getByText(label)).toBeInTheDocument();
    });

    // Rows with values, and their groups, render too.
    [
      "Monitors & Alerts",
      "Gateway",
      "Gateway Webhooks",
      "Data Retention",
      "Trace Retention",
      "Custom Roles",
    ].forEach((label) => {
      expect(screen.getByText(label)).toBeInTheDocument();
    });
    const webhooks = screen.getByText("Gateway Webhooks").closest("tr");
    expect(within(webhooks).getByText("5")).toBeInTheDocument();
  });

  // prices were quoted with no or one decimal ($250, $1.5 per
  // GB) while the credits card uses $25.00.
  it("quotes every Plans price with cents", async () => {
    renderPricing();
    expect(await screen.findByText("Usage-based pricing")).toBeInTheDocument();

    ["$250.00", "$750.00", "$2,000.00", "$2.00", "$1.50"].forEach((price) => {
      expect(screen.getByText(price)).toBeInTheDocument();
    });
  });
});
