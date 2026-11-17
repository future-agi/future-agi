import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, render, screen, waitFor, within } from "src/utils/test-utils";

import BudgetManager from "../BudgetManager";

// Billing > Usage budgets: where the installed billing package acts on
// total-spend budgets, a total-spend pause budget that is on and fired in the
// current billing period (the UTC month, "YYYY-MM" as the backend names it)
// holds usage paused for the whole organization. Its row says so once, under
// its threshold line after a warning icon, with what lifts it, and keeps every
// chip and line it already had. A mark from an earlier period, a budget that
// has not fired, a warn or notify budget, a dimension's pause budget and a
// budget that is turned off say nothing of a pause; nor does any budget where
// total spend is not enforced or the probe gives no answer.

const PROBE_URL = "/api/deployment-info/";
const BUDGETS_URL = "/usage/v2/budgets/";

// Mid-October 2026, UTC: "2026-10" is the current billing period.
const NOW = "2026-10-20T12:00:00Z";

const PAUSED =
  "Usage is paused for your organization because total spend reached this budget. Raise or remove the budget to continue.";
const NOT_PAUSED =
  "Not enforced: usage is not paused when this budget is reached.";
const NOT_SENT = "Email notices are not sent for this budget.";
const NOT_CHECKED =
  "Couldn't check whether budgets are enforced. Reload the page.";
const OFF_LINE = "This budget is off. Reaching it does nothing.";

// The billing package acts on total-spend budgets and sends budget e-mails.
const BOTH = {
  total_spend_budgets_enforced: true,
  budget_notices_delivered: true,
};
// It does not act on total-spend budgets.
const SPEND_OFF = {
  total_spend_budgets_enforced: false,
  budget_notices_delivered: true,
};
// It acts on total-spend budgets but sends no budget e-mails.
const MAIL_OFF = {
  total_spend_budgets_enforced: true,
  budget_notices_delivered: false,
};

// Fired this period. No is_active and no recipient list in the response: on,
// with no recipients of its own.
const SPEND_PAUSE = {
  id: "budget-spend",
  name: "Total spend hard stop",
  scope: "total_spend",
  threshold_value: "2500.0000",
  action: "pause",
  last_triggered_period: "2026-10",
};
// The same budget, on, with recipients of its own.
const SPEND_PAUSE_ON = {
  ...SPEND_PAUSE,
  is_active: true,
  notify_emails: ["finance@example.com"],
};
// Fired last period.
const LAST_MONTH = {
  id: "budget-spend-september",
  name: "Total spend September stop",
  scope: "total_spend",
  threshold_value: "2500.0000",
  action: "pause",
  last_triggered_period: "2026-09",
};
// Not fired yet.
const UNFIRED = {
  id: "budget-spend-unfired",
  name: "Total spend second stop",
  scope: "total_spend",
  threshold_value: "2500.0000",
  action: "pause",
  last_triggered_period: null,
};
// Fired this period, but they do not pause usage.
const SPEND_WARN = {
  id: "budget-spend-warn",
  name: "Total spend warning",
  scope: "total_spend",
  threshold_value: "2500.0000",
  action: "warn",
  last_triggered_period: "2026-10",
};
const SPEND_NOTIFY = {
  id: "budget-spend-notify",
  name: "Total spend notice",
  scope: "total_spend",
  threshold_value: "2500.0000",
  action: "notify",
  notify_emails: ["finance@example.com"],
  last_triggered_period: "2026-10",
};
// A dimension's pause budget, fired this period.
const AI_PAUSE = {
  id: "budget-ai-pause",
  name: "AI credits hard stop",
  scope: "ai_credits",
  threshold_value: "750",
  action: "pause",
  last_triggered_period: "2026-10",
};
// Fired this period, then turned off.
const SPEND_PAUSE_OFF = {
  id: "budget-spend-off",
  name: "Old total spend stop",
  scope: "total_spend",
  threshold_value: "2500.0000",
  action: "pause",
  is_active: false,
  last_triggered_period: "2026-10",
};
// Fired in November 2026.
const NOVEMBER = {
  id: "budget-spend-november",
  name: "Total spend November stop",
  scope: "total_spend",
  threshold_value: "2500.0000",
  action: "pause",
  last_triggered_period: "2026-11",
};

// Each row's text: name, threshold line, any line under it, then its chips.
const SPEND_CAPTION = "Total Spend: $2,500.00 → Pause Usage";
const SPEND_PAUSE_TODAY =
  "Total spend hard stopTotal Spend: $2,500.00 → Pause UsagePause UsageTriggered Oct 2026";
const SPEND_PAUSE_PAUSED = `Total spend hard stopTotal Spend: $2,500.00 → Pause Usage${PAUSED}Pause UsageTriggered Oct 2026`;
const LAST_MONTH_TODAY =
  "Total spend September stopTotal Spend: $2,500.00 → Pause UsagePause UsageTriggered Sep 2026";
const UNFIRED_TODAY =
  "Total spend second stopTotal Spend: $2,500.00 → Pause UsagePause Usage";
const SPEND_WARN_TODAY =
  "Total spend warningTotal Spend: $2,500.00 → WarnWarnTriggered Oct 2026";
const SPEND_NOTIFY_TODAY =
  "Total spend noticeTotal Spend: $2,500.00 → NotifyNotifyTriggered Oct 2026";
const AI_PAUSE_TODAY =
  "AI credits hard stopAI Credits: 750 credits → Pause UsagePause UsageTriggered Oct 2026";
const SPEND_PAUSE_OFF_TEXT = `Old total spend stopTotal Spend: $2,500.00 → Pause Usage${OFF_LINE}Pause UsageInactiveTriggered Oct 2026`;

const state = vi.hoisted(() => ({
  get: vi.fn(),
  budgets: [],
  probe: {},
  probeFails: false,
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
  return /** @type {HTMLElement} */ (
    title.closest('[data-testid="usage-budget-row"]')
  );
}

// `second` comes after `first` in the page.
const follows = (first, second) =>
  Boolean(
    first.compareDocumentPosition(second) & Node.DOCUMENT_POSITION_FOLLOWING,
  );

// The line that usage is paused, in `row`: once, visible, after a warning
// icon, marked for tests, and in the row's left column under its threshold
// line `caption`.
async function expectPausedLine(row, caption) {
  const line = await within(row).findByText(PAUSED);
  expect(line).toBeVisible();
  expect(within(row).getAllByText(PAUSED)).toHaveLength(1);
  expect(
    within(line.parentElement).getByRole("img", { name: "Warning" }),
  ).toBeInTheDocument();
  const marked = within(row).getByTestId("usage-budget-paused");
  expect(marked).toContainElement(line);
  const threshold = within(row).getByText(caption);
  expect(threshold.parentElement).toContainElement(line);
  expect(follows(threshold, line)).toBe(true);
  return line;
}

beforeEach(() => {
  vi.clearAllMocks();
  // Only Date is set; timers stay real, so queries and waits run as usual.
  vi.setSystemTime(new Date(NOW));
  state.budgets = [SPEND_PAUSE];
  state.probe = BOTH;
  state.probeFails = false;
  state.get.mockImplementation(async (url) => {
    if (url === PROBE_URL) {
      if (state.probeFails) throw new Error("Network Error");
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

afterEach(() => {
  vi.useRealTimers();
});

describe("A total-spend pause budget that fired this billing period says usage is paused", () => {
  it.each([
    ["with no is_active or recipients in the answer", SPEND_PAUSE],
    ["that is on and lists recipients of its own", SPEND_PAUSE_ON],
  ])(
    "says once under its threshold line, after a warning icon, that usage is paused and what lifts it, and keeps its chips, for a budget %s",
    async (_what, budget) => {
      state.budgets = [budget];
      mount();
      const row = await rowOf(budget.name);
      await probeAnswered();

      await expectPausedLine(row, SPEND_CAPTION);
      expect(row.textContent).toBe(SPEND_PAUSE_PAUSED);
      expect(within(row).getByText("Pause Usage")).toBeVisible();
      expect(within(row).getByText("Triggered Oct 2026")).toBeVisible();
      expect(within(row).getByTitle("Edit budget")).not.toBeDisabled();
      expect(within(row).getByTitle("Delete budget")).not.toBeDisabled();
    },
  );

  it("says it before the line that e-mail notices are not sent, on a budget with no recipients of its own while budget e-mails reach no one", async () => {
    state.probe = MAIL_OFF;
    mount();
    const row = await rowOf(SPEND_PAUSE.name);
    await probeAnswered();

    const line = await expectPausedLine(row, SPEND_CAPTION);
    const notSent = within(row).getByText(NOT_SENT);
    expect(follows(line, notSent)).toBe(true);
    const icons = within(row).getAllByRole("img", { name: "Warning" });
    expect(icons).toHaveLength(2);
    expect(row.textContent).toBe(
      `Total spend hard stopTotal Spend: $2,500.00 → Pause Usage${PAUSED}${NOT_SENT}Pause UsageTriggered Oct 2026`,
    );
  });
});

describe("Only a total-spend pause budget that is on and fired this billing period says usage is paused", () => {
  it.each([
    ["fired in an earlier billing period", LAST_MONTH, LAST_MONTH_TODAY],
    ["has not fired", UNFIRED, UNFIRED_TODAY],
    ["is a total-spend warn budget", SPEND_WARN, SPEND_WARN_TODAY],
    ["is a total-spend notify budget", SPEND_NOTIFY, SPEND_NOTIFY_TODAY],
    ["is an AI credits pause budget", AI_PAUSE, AI_PAUSE_TODAY],
    ["is turned off", SPEND_PAUSE_OFF, SPEND_PAUSE_OFF_TEXT],
  ])(
    "says nothing of a pause on a budget that %s, which reads exactly as before, beside a budget that says it",
    async (_what, budget, today) => {
      state.budgets = [SPEND_PAUSE, budget];
      mount();
      const holding = await rowOf(SPEND_PAUSE.name);
      const row = await rowOf(budget.name);
      await probeAnswered();
      // The answer has been drawn: the budget that holds the pause says so.
      await expectPausedLine(holding, SPEND_CAPTION);

      expect(row.textContent).toBe(today);
      expect(within(row).queryByText(PAUSED)).toBeNull();
      expect(within(row).queryByTestId("usage-budget-paused")).toBeNull();
      expect(screen.getAllByText(PAUSED)).toHaveLength(1);
    },
  );
});

describe("The billing period is the UTC month", () => {
  it("says usage is paused in the last minutes of the UTC month the budget fired in", async () => {
    vi.setSystemTime(new Date("2026-10-31T23:55:00Z"));
    mount();
    const row = await rowOf(SPEND_PAUSE.name);
    await probeAnswered();

    await expectPausedLine(row, SPEND_CAPTION);
    expect(row.textContent).toBe(SPEND_PAUSE_PAUSED);
  });

  it("stops saying it once the UTC month has turned, while a budget that fired in the new month says it", async () => {
    vi.setSystemTime(new Date("2026-11-01T00:05:00Z"));
    state.budgets = [SPEND_PAUSE, NOVEMBER];
    mount();
    const october = await rowOf(SPEND_PAUSE.name);
    const november = await rowOf(NOVEMBER.name);
    await probeAnswered();

    await expectPausedLine(november, SPEND_CAPTION);
    expect(october.textContent).toBe(SPEND_PAUSE_TODAY);
    expect(within(october).queryByText(PAUSED)).toBeNull();
  });
});

describe("Where total spend is not enforced or can't be checked, no budget says usage is paused", () => {
  it("says only that usage is not paused where the billing package does not act on total-spend budgets", async () => {
    state.probe = SPEND_OFF;
    mount();
    const row = await rowOf(SPEND_PAUSE.name);
    await probeAnswered();

    expect(await within(row).findByText(NOT_PAUSED)).toBeVisible();
    expect(within(row).queryByText(PAUSED)).toBeNull();
    expect(row.textContent).toBe(
      `Total spend hard stopTotal Spend: $2,500.00 → Pause Usage${NOT_PAUSED}Pause UsageTriggered Oct 2026`,
    );
  });

  it.each([
    ["the probe cannot be read", { probeFails: true }],
    ["the probe does not carry the budget answers", { probe: {} }],
  ])(
    "reads exactly as before when %s, and the section says once that budgets couldn't be checked",
    async (_when, setup) => {
      Object.assign(state, setup);
      mount();
      const row = await rowOf(SPEND_PAUSE.name);
      await probeAnswered();

      expect(await screen.findByText(NOT_CHECKED)).toBeVisible();
      expect(row.textContent).toBe(SPEND_PAUSE_TODAY);
      expect(screen.queryByText(PAUSED)).toBeNull();
    },
  );
});
