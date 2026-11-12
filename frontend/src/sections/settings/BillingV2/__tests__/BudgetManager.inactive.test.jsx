import React from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, render, screen, waitFor, within } from "src/utils/test-utils";

import BudgetManager from "../BudgetManager";

// Billing > Usage budgets: a budget that is turned off does nothing when it is
// reached, so its row says so. It carries an Inactive chip beside its action
// chip and, under its threshold line, a warning line that reaching it does
// nothing. It makes none of the claims about what reaching it does or does not
// do (total spend not enforced, e-mail notices not sent). Its Triggered chip,
// if it has one, stays: that already happened. A budget that is on reads
// exactly as before.

const PROBE_URL = "/api/deployment-info/";
const BUDGETS_URL = "/usage/v2/budgets/";

const OFF_LINE = "This budget is off. Reaching it does nothing.";
const NOT_PAUSED =
  "Not enforced: usage is not paused when this budget is reached.";
const NOT_RAISED =
  "Not enforced: no notice or warning is raised when this budget is reached.";
const NOT_SENT = "Email notices are not sent for this budget.";

// The billing package acts on total-spend budgets and sends budget e-mails.
const BOTH = {
  total_spend_budgets_enforced: true,
  budget_notices_delivered: true,
};
// It does neither.
const NEITHER = {
  total_spend_budgets_enforced: false,
  budget_notices_delivered: false,
};

// Turned off.
const AI_NOTIFY_OFF = {
  id: "budget-ai-off",
  name: "Old AI credits notice",
  scope: "ai_credits",
  threshold_value: "500",
  action: "notify",
  notify_emails: [],
  is_active: false,
  last_triggered_period: "2026-10",
};
const STORAGE_WARN_OFF = {
  id: "budget-storage-off",
  name: "Old storage warning",
  scope: "storage",
  threshold_value: "50",
  action: "warn",
  notify_emails: [],
  is_active: false,
  last_triggered_period: null,
};
// No recipient list in the response at all: no recipients of its own.
const SPEND_PAUSE_OFF = {
  id: "budget-spend-off",
  name: "Old total spend stop",
  scope: "total_spend",
  threshold_value: "2500.0000",
  action: "pause",
  is_active: false,
  last_triggered_period: "2026-10",
};

// On: one with no is_active in the response, one with is_active true.
const SPEND_PAUSE = {
  id: "budget-spend",
  name: "Total spend hard stop",
  scope: "total_spend",
  threshold_value: "2500.0000",
  action: "pause",
  last_triggered_period: "2026-10",
};
const AI_PAUSE_ON = {
  id: "budget-ai-pause",
  name: "AI credits hard stop",
  scope: "ai_credits",
  threshold_value: "750",
  action: "pause",
  notify_emails: ["finance@example.com"],
  is_active: true,
  last_triggered_period: "2026-10",
};

// Each row's text: name, threshold line, any line under it, then its chips.
const AI_NOTIFY_OFF_TEXT = `Old AI credits noticeAI Credits: 500 credits → Notify${OFF_LINE}NotifyInactiveTriggered Oct 2026`;
const STORAGE_WARN_OFF_TEXT = `Old storage warningStorage: 50 GB → Warn${OFF_LINE}WarnInactive`;
const SPEND_PAUSE_OFF_TEXT = `Old total spend stopTotal Spend: $2,500.00 → Pause Usage${OFF_LINE}Pause UsageInactiveTriggered Oct 2026`;
const SPEND_PAUSE_TODAY =
  "Total spend hard stopTotal Spend: $2,500.00 → Pause UsagePause UsageTriggered Oct 2026";
const AI_PAUSE_ON_TODAY =
  "AI credits hard stopAI Credits: 750 credits → Pause UsagePause UsageTriggered Oct 2026";

const state = vi.hoisted(() => ({
  get: vi.fn(),
  budgets: [],
  probe: {},
}));

// An organization Owner, so every budget control is enabled.
vi.mock("src/contexts/OrganizationContext", () => ({
  useOrganization: () => ({ orgLevel: 15 }),
}));
vi.mock("src/auth/hooks", () => ({
  useAuthContext: () => ({
    role: "workspace_admin",
    user: { organization_role: "Owner" },
  }),
}));
vi.mock("notistack", () => ({ enqueueSnackbar: vi.fn() }));
vi.mock("src/utils/axios", () => ({
  default: {
    get: (...args) => state.get(...args),
    post: vi.fn(),
    put: vi.fn(),
    delete: vi.fn(),
  },
  endpoints: {
    settings: {
      v2: {
        budgets: "/usage/v2/budgets/",
        budgetDetail: (id) => `/usage/v2/budgets/${id}/`,
        deploymentInfo: "/api/deployment-info/",
      },
    },
  },
}));

function mount() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={client}>
      <BudgetManager />
    </QueryClientProvider>,
  );
}

// Lets a request that is under way finish and renders what follows it.
const settle = () =>
  act(() => new Promise((resolve) => setTimeout(resolve, 0)));

// The screen has asked the probe and drawn its answer.
async function probeAnswered() {
  await waitFor(() => expect(state.get).toHaveBeenCalledWith(PROBE_URL));
  await settle();
}

async function rowOf(name) {
  const title = await screen.findByText(name);
  return title.closest('[data-testid="usage-budget-row"]');
}

const chipOf = (element) => element.closest(".MuiChip-root");

beforeEach(() => {
  vi.clearAllMocks();
  state.budgets = [];
  state.probe = BOTH;
  state.get.mockImplementation(async (url) => {
    if (url === PROBE_URL) {
      return {
        data: { status: true, result: { mode: "cloud", ...state.probe } },
      };
    }
    if (url === BUDGETS_URL) {
      return { data: { result: { budgets: state.budgets } } };
    }
    throw new Error(`unexpected request ${url}`);
  });
});

describe("A budget that is turned off says so on its row", () => {
  it.each([
    [
      "notify",
      AI_NOTIFY_OFF,
      "AI Credits: 500 credits → Notify",
      "Notify",
      AI_NOTIFY_OFF_TEXT,
    ],
    [
      "warn",
      STORAGE_WARN_OFF,
      "Storage: 50 GB → Warn",
      "Warn",
      STORAGE_WARN_OFF_TEXT,
    ],
    [
      "pause",
      SPEND_PAUSE_OFF,
      "Total Spend: $2,500.00 → Pause Usage",
      "Pause Usage",
      SPEND_PAUSE_OFF_TEXT,
    ],
  ])(
    "shows an outlined Inactive chip beside the action chip of a %s budget that is turned off, and says under its threshold line, after a warning icon, that reaching it does nothing",
    async (_action, budget, caption, label, expected) => {
      state.budgets = [budget];
      mount();
      const row = await rowOf(budget.name);
      await probeAnswered();

      expect(row.textContent).toBe(expected);

      const line = within(row).getByText(OFF_LINE);
      expect(line).toBeVisible();
      expect(
        within(line.parentElement).getByRole("img", { name: "Warning" }),
      ).toBeInTheDocument();
      // In the left column with the threshold line, not beside the chips.
      expect(within(row).getByText(caption).parentElement).toContainElement(
        line,
      );

      const inactive = chipOf(within(row).getByText("Inactive"));
      expect(inactive).toBeVisible();
      expect(inactive).toHaveClass("MuiChip-outlined");
      // The default colour: no status colour, which would claim a state.
      for (const colour of [
        "MuiChip-colorPrimary",
        "MuiChip-colorSecondary",
        "MuiChip-colorError",
        "MuiChip-colorWarning",
        "MuiChip-colorInfo",
        "MuiChip-colorSuccess",
      ]) {
        expect(inactive).not.toHaveClass(colour);
      }
      expect(chipOf(within(row).getByText(label)).nextElementSibling).toBe(
        inactive,
      );

      // The budget can still be edited or deleted.
      expect(within(row).getByTitle("Edit budget")).not.toBeDisabled();
      expect(within(row).getByTitle("Delete budget")).not.toBeDisabled();
    },
  );

  it("makes no claim about what reaching a budget that is turned off does or does not do, and keeps its Triggered chip, when the billing package neither acts on total spend nor sends budget e-mails", async () => {
    state.probe = NEITHER;
    state.budgets = [SPEND_PAUSE, SPEND_PAUSE_OFF, AI_NOTIFY_OFF];
    mount();
    const on = await rowOf("Total spend hard stop");
    const spendOff = await rowOf("Old total spend stop");
    const notifyOff = await rowOf("Old AI credits notice");
    await probeAnswered();
    // The answer has been drawn: the budget that is on says what reaching it
    // does not do.
    await within(on).findByText(NOT_SENT);

    expect(spendOff.textContent).toBe(SPEND_PAUSE_OFF_TEXT);
    expect(notifyOff.textContent).toBe(AI_NOTIFY_OFF_TEXT);
    for (const row of [spendOff, notifyOff]) {
      for (const sentence of [NOT_PAUSED, NOT_RAISED, NOT_SENT]) {
        expect(within(row).queryByText(sentence)).toBeNull();
      }
      // The only warning on the row is the line that it is off.
      expect(within(row).getAllByRole("img", { name: "Warning" })).toHaveLength(
        1,
      );
      expect(within(row).getByText(OFF_LINE)).toBeVisible();
      // What it did while it was on stays on the row.
      expect(within(row).getByText("Triggered Oct 2026")).toBeVisible();
    }

    // The budget that is on still says both, as before.
    expect(on.textContent).toBe(
      `Total spend hard stopTotal Spend: $2,500.00 → Pause Usage${NOT_PAUSED}${NOT_SENT}Pause UsageTriggered Oct 2026`,
    );
  });

  it("reads a budget that is on exactly as before, with or without is_active in the response, beside one that is turned off", async () => {
    state.budgets = [SPEND_PAUSE, AI_PAUSE_ON, AI_NOTIFY_OFF];
    mount();
    const missing = await rowOf("Total spend hard stop");
    const on = await rowOf("AI credits hard stop");
    await rowOf("Old AI credits notice");
    await probeAnswered();

    expect(missing.textContent).toBe(SPEND_PAUSE_TODAY);
    expect(on.textContent).toBe(AI_PAUSE_ON_TODAY);
    for (const row of [missing, on]) {
      expect(within(row).queryByText("Inactive")).toBeNull();
      expect(within(row).queryByText(OFF_LINE)).toBeNull();
      expect(within(row).queryByRole("img", { name: "Warning" })).toBeNull();
    }
  });
});
