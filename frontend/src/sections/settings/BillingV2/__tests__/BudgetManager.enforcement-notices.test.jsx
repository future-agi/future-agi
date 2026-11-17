import React from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import {
  act,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "src/utils/test-utils";

import BudgetManager from "../BudgetManager";

// Billing > Usage budgets says so on a budget when the installed billing
// package does not do what the budget asks: a total-spend budget that is not
// acted on when reached, and a notify, warn or pause budget that lists no
// recipients of its own while budget e-mails reach no one. The probe answers
// both; where it says the package does the work, every row reads exactly as
// before. Where the probe gives no answer, no budget claims either way and the
// section says once that this could not be checked. In the budget dialog a
// total-spend budget always says, under Threshold, what it counts, and that
// line describes the Threshold input.

const PROBE_URL = "/api/deployment-info/";
const BUDGETS_URL = "/usage/v2/budgets/";

const NOT_PAUSED =
  "Not enforced: usage is not paused when this budget is reached.";
const NOT_RAISED =
  "Not enforced: no notice or warning is raised when this budget is reached.";
const NOT_SENT = "Email notices are not sent for this budget.";
const NOT_CHECKED =
  "Couldn't check whether budgets are enforced. Reload the page.";
const COUNTS =
  "Total spend is this billing period's usage charges so far, AI Credits usage included: the same figure as Usage charges (MTD) on the Usage summary page. Your platform fee and AI Gateway model spend paid with Future AGI credits are not part of it; limit AI Gateway model spend in AI Gateway → Budgets.";
const THRESHOLD_INVALID = "Enter a positive number";
// Every Scope option other than Total Spend, as the dialog names them.
const OTHER_SCOPES = [
  "AI Credits",
  "Storage",
  "Gateway Requests",
  "Cache Hits",
  "Text Sim Tokens",
  "Voice Sim Minutes",
  "Tracing Events",
];

const BOTH = {
  total_spend_budgets_enforced: true,
  budget_notices_delivered: true,
};
const SPEND_OFF = {
  total_spend_budgets_enforced: false,
  budget_notices_delivered: true,
};
const MAIL_OFF = {
  total_spend_budgets_enforced: true,
  budget_notices_delivered: false,
};
const NEITHER = {
  total_spend_budgets_enforced: false,
  budget_notices_delivered: false,
};

const SPEND_PAUSE = {
  id: "budget-spend",
  name: "Total spend hard stop",
  scope: "total_spend",
  threshold_value: "2500.0000",
  action: "pause",
  last_triggered_period: "2026-10",
};
const SPEND_NOTIFY = {
  id: "budget-spend-notify",
  name: "Total spend notice",
  scope: "total_spend",
  threshold_value: "2500.0000",
  action: "notify",
  last_triggered_period: "2026-10",
};
const SPEND_WARN = {
  id: "budget-spend-warn",
  name: "Total spend warning",
  scope: "total_spend",
  threshold_value: "2500.0000",
  action: "warn",
  last_triggered_period: "2026-10",
};
const AI_NOTIFY = {
  id: "budget-ai",
  name: "Monthly AI credits cap",
  scope: "ai_credits",
  threshold_value: "500",
  action: "notify",
  notify_emails: [],
  last_triggered_period: "2026-10",
};
// No recipient list in the response at all: no recipients of its own.
const AI_NOTIFY_QUIET = {
  id: "budget-ai-quiet",
  name: "AI credits early notice",
  scope: "ai_credits",
  threshold_value: "100",
  action: "notify",
  last_triggered_period: null,
};
const STORAGE_WARN = {
  id: "budget-storage",
  name: "Storage warning",
  scope: "storage",
  threshold_value: "50",
  action: "warn",
  notify_emails: [],
  last_triggered_period: "2026-10",
};
const AI_NOTIFY_LISTED = {
  id: "budget-ai-listed",
  name: "AI credits finance notice",
  scope: "ai_credits",
  threshold_value: "800",
  action: "notify",
  notify_emails: ["finance@example.com"],
  last_triggered_period: "2026-10",
};
const STORAGE_WARN_LISTED = {
  id: "budget-storage-listed",
  name: "Storage finance warning",
  scope: "storage",
  threshold_value: "80",
  action: "warn",
  notify_emails: ["finance@example.com"],
  last_triggered_period: "2026-10",
};
const AI_PAUSE = {
  id: "budget-ai-pause",
  name: "AI credits hard stop",
  scope: "ai_credits",
  threshold_value: "750",
  action: "pause",
  notify_emails: [],
  last_triggered_period: "2026-10",
};
const AI_PAUSE_LISTED = {
  id: "budget-ai-pause-listed",
  name: "AI credits finance stop",
  scope: "ai_credits",
  threshold_value: "900",
  action: "pause",
  notify_emails: ["finance@example.com"],
  last_triggered_period: "2026-10",
};

// Each row's text today: name, threshold line, action chip, Triggered chip.
const SPEND_PAUSE_TODAY =
  "Total spend hard stopTotal Spend: $2,500.00 → Pause UsagePause UsageTriggered Oct 2026";
const SPEND_NOTIFY_TODAY =
  "Total spend noticeTotal Spend: $2,500.00 → NotifyNotifyTriggered Oct 2026";
const SPEND_WARN_TODAY =
  "Total spend warningTotal Spend: $2,500.00 → WarnWarnTriggered Oct 2026";
const AI_NOTIFY_TODAY =
  "Monthly AI credits capAI Credits: 500 credits → NotifyNotifyTriggered Oct 2026";
const STORAGE_WARN_TODAY =
  "Storage warningStorage: 50 GB → WarnWarnTriggered Oct 2026";
const AI_NOTIFY_LISTED_TODAY =
  "AI credits finance noticeAI Credits: 800 credits → NotifyNotifyTriggered Oct 2026";
const STORAGE_WARN_LISTED_TODAY =
  "Storage finance warningStorage: 80 GB → WarnWarnTriggered Oct 2026";
const AI_PAUSE_TODAY =
  "AI credits hard stopAI Credits: 750 credits → Pause UsagePause UsageTriggered Oct 2026";
const AI_PAUSE_LISTED_TODAY =
  "AI credits finance stopAI Credits: 900 credits → Pause UsagePause UsageTriggered Oct 2026";

// The Action menu's options in the budget dialog.
const ACTION_OPTIONS = { notify: /^Notify/, warn: /^Warn/, pause: /^Pause/ };

const state = vi.hoisted(() => ({
  get: vi.fn(),
  post: vi.fn(),
  put: vi.fn(),
  budgets: [],
  probe: {},
  probeFails: false,
}));

// An organization Owner, so the budget dialogs open.
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
    post: (...args) => state.post(...args),
    put: (...args) => state.put(...args),
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

// Picks an option from one of the dialog's drop-downs (Scope is the first,
// Action the second) and waits for its menu to close: while a menu is open
// the dialog is hidden from role queries.
async function choose(dialog, index, option) {
  fireEvent.mouseDown(within(dialog).getAllByRole("combobox")[index]);
  fireEvent.click(
    within(await screen.findByRole("listbox")).getByRole("option", {
      name: option,
    }),
  );
  await waitFor(() => expect(screen.queryByRole("listbox")).toBeNull());
}

const chooseTotalSpend = (dialog) => choose(dialog, 0, "Total Spend");
const chooseAction = (dialog, action) =>
  choose(dialog, 1, ACTION_OPTIONS[action]);

// The total-spend sentence inside `container`: the expected one, led by its
// warning icon, and not the other; or, with none expected, neither of them
// and no warning icon at all.
async function expectSpendLine(container, expected) {
  if (expected) {
    const line = await within(container).findByText(expected);
    expect(line).toBeVisible();
    expect(
      within(line.parentElement).getByRole("img", { name: "Warning" }),
    ).toBeInTheDocument();
  } else {
    expect(
      within(container).queryByRole("img", { name: "Warning" }),
    ).toBeNull();
  }
  for (const other of [NOT_PAUSED, NOT_RAISED]) {
    if (other !== expected) {
      expect(within(container).queryByText(other)).toBeNull();
    }
  }
}

// `second` comes after `first` in the page.
const follows = (first, second) =>
  Boolean(
    first.compareDocumentPosition(second) & Node.DOCUMENT_POSITION_FOLLOWING,
  );

// The line that says what a total-spend budget counts: plain, with no
// warning icon of its own, under Threshold and above Action, and the
// description a screen reader gives the Threshold input. The line that
// total spend is not enforced, when expected, shows below it, still above
// Action, and carries the dialog's one warning icon.
async function expectCountsLine(dialog, notEnforced) {
  const line = await within(dialog).findByText(COUNTS);
  expect(line).toBeVisible();
  const threshold = within(dialog).getByLabelText("Threshold");
  const action = within(dialog).getAllByRole("combobox")[1];
  expect(follows(threshold, line)).toBe(true);
  expect(follows(line, action)).toBe(true);
  expect(threshold).toHaveAccessibleDescription(COUNTS);
  await expectSpendLine(dialog, notEnforced);
  if (notEnforced) {
    const sentence = within(dialog).getByText(notEnforced);
    expect(follows(line, sentence)).toBe(true);
    expect(follows(sentence, action)).toBe(true);
    expect(
      within(dialog).getAllByRole("img", { name: "Warning" }),
    ).toHaveLength(1);
  }
}

// Any other scope shows no such line, and a valid or empty Threshold is
// described by nothing.
function expectNoCountsLine(dialog) {
  expect(within(dialog).queryByText(COUNTS)).toBeNull();
  expect(
    within(dialog).getByLabelText("Threshold"),
  ).not.toHaveAccessibleDescription();
}

beforeEach(() => {
  vi.clearAllMocks();
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
  state.post.mockResolvedValue({ data: { result: {} } });
  state.put.mockResolvedValue({ data: { result: {} } });
});

describe("A total-spend budget says what reaching it does not do", () => {
  it.each([
    ["pause", SPEND_PAUSE, SPEND_PAUSE_TODAY],
    ["notify", SPEND_NOTIFY, SPEND_NOTIFY_TODAY],
    ["warn", SPEND_WARN, SPEND_WARN_TODAY],
  ])(
    "reads a %s budget exactly as before when the billing package acts on total-spend budgets",
    async (_action, budget, today) => {
      state.budgets = [budget];
      mount();
      const row = await rowOf(budget.name);
      await probeAnswered();

      expect(row.textContent).toBe(today);
      expect(within(row).queryByRole("img", { name: "Warning" })).toBeNull();
    },
  );

  it.each([
    ["pause", "usage is not paused", SPEND_PAUSE, NOT_PAUSED, "Pause Usage"],
    ["notify", "no notice is raised", SPEND_NOTIFY, NOT_RAISED, "Notify"],
    ["warn", "no warning is raised", SPEND_WARN, NOT_RAISED, "Warn"],
  ])(
    "says on a %s budget that %s, with a warning icon, and keeps everything else on the row",
    async (_action, _what, budget, sentence, label) => {
      state.probe = SPEND_OFF;
      state.budgets = [budget];
      mount();
      const row = await rowOf(budget.name);
      await probeAnswered();

      await expectSpendLine(row, sentence);
      expect(
        within(row).getByText(`Total Spend: $2,500.00 → ${label}`),
      ).toBeVisible();
      expect(within(row).getByText(label)).toBeVisible();
      // The budget's e-mails are sent, so its Triggered chip stays.
      expect(within(row).getByText("Triggered Oct 2026")).toBeVisible();
      expect(within(row).queryByText(NOT_SENT)).toBeNull();
      expect(within(row).getByTitle("Edit budget")).not.toBeDisabled();
      expect(within(row).getByTitle("Delete budget")).not.toBeDisabled();
    },
  );

  it.each([
    [
      "names what reaching it does not do",
      "does not act on",
      SPEND_OFF,
      { notify: NOT_RAISED, warn: NOT_RAISED, pause: NOT_PAUSED },
    ],
    [
      "shows no such line",
      "acts on",
      BOTH,
      { notify: null, warn: null, pause: null },
    ],
  ])(
    "in Add Usage Budget for Total Spend, %s for each action when the billing package %s total-spend budgets, before saving, and the budget still saves",
    async (_outcome, _packageState, probe, expected) => {
      state.probe = probe;
      state.budgets = [];
      mount();
      await probeAnswered();
      fireEvent.click(
        await screen.findByRole("button", { name: "Add Budget" }),
      );
      const dialog = await screen.findByRole("dialog", {
        name: "Add Usage Budget",
      });
      await expectSpendLine(dialog, null);

      await chooseTotalSpend(dialog);
      // Action starts at Notify.
      await expectSpendLine(dialog, expected.notify);
      await chooseAction(dialog, "warn");
      await expectSpendLine(dialog, expected.warn);
      await chooseAction(dialog, "pause");
      await expectSpendLine(dialog, expected.pause);

      fireEvent.change(within(dialog).getByLabelText("Budget Name"), {
        target: { value: "Spend cap" },
      });
      fireEvent.change(within(dialog).getByLabelText("Threshold"), {
        target: { value: "2500" },
      });
      const create = within(dialog).getByRole("button", {
        name: "Create Budget",
      });
      expect(create).not.toBeDisabled();
      fireEvent.click(create);
      await waitFor(() =>
        expect(state.post).toHaveBeenCalledWith(BUDGETS_URL, {
          name: "Spend cap",
          scope: "total_spend",
          threshold_value: "2500",
          action: "pause",
        }),
      );
    },
  );

  it.each([
    [
      "pause",
      "does not act on",
      "says usage is not paused",
      SPEND_PAUSE,
      SPEND_OFF,
      NOT_PAUSED,
    ],
    [
      "warn",
      "does not act on",
      "says no notice or warning is raised",
      SPEND_WARN,
      SPEND_OFF,
      NOT_RAISED,
    ],
    ["pause", "acts on", "shows no such line", SPEND_PAUSE, BOTH, null],
    ["warn", "acts on", "shows no such line", SPEND_WARN, BOTH, null],
  ])(
    "in Edit Budget for a saved total-spend %s budget, when the billing package %s total-spend budgets, %s and Save Changes stays available",
    async (_action, _packageState, _outcome, budget, probe, expected) => {
      state.probe = probe;
      state.budgets = [budget];
      mount();
      const row = await rowOf(budget.name);
      await probeAnswered();
      fireEvent.click(within(row).getByTitle("Edit budget"));
      const dialog = await screen.findByRole("dialog", {
        name: "Edit Budget",
      });

      await expectSpendLine(dialog, expected);
      expect(
        within(dialog).getByRole("button", { name: "Save Changes" }),
      ).not.toBeDisabled();
    },
  );
});

describe("A total-spend budget says what it counts", () => {
  it.each([
    [
      "the billing package acts on total-spend budgets",
      { probe: BOTH },
      null,
      null,
    ],
    [
      "the billing package does not act on total-spend budgets",
      { probe: SPEND_OFF },
      NOT_RAISED,
      NOT_PAUSED,
    ],
    ["the probe gives no budget answers", { probe: {} }, null, null],
    ["the probe cannot be read", { probeFails: true }, null, null],
  ])(
    "in Add Usage Budget, says under Threshold what total spend counts for Total Spend only, whichever action, when %s",
    async (_when, setup, notifyLine, pauseLine) => {
      Object.assign(state, setup);
      state.budgets = [];
      mount();
      await probeAnswered();
      fireEvent.click(
        await screen.findByRole("button", { name: "Add Budget" }),
      );
      const dialog = await screen.findByRole("dialog", {
        name: "Add Usage Budget",
      });
      // Scope starts at AI Credits.
      expectNoCountsLine(dialog);

      await chooseTotalSpend(dialog);
      // Action starts at Notify.
      await expectCountsLine(dialog, notifyLine);
      await chooseAction(dialog, "pause");
      await expectCountsLine(dialog, pauseLine);

      for (const scope of OTHER_SCOPES) {
        await choose(dialog, 0, scope);
        expectNoCountsLine(dialog);
      }
    },
  );

  it("in Add Usage Budget for Total Spend, describes an invalid Threshold by its error and then by what total spend counts", async () => {
    state.budgets = [];
    mount();
    await probeAnswered();
    fireEvent.click(await screen.findByRole("button", { name: "Add Budget" }));
    const dialog = await screen.findByRole("dialog", {
      name: "Add Usage Budget",
    });
    await chooseTotalSpend(dialog);
    const threshold = within(dialog).getByLabelText("Threshold");

    fireEvent.change(threshold, { target: { value: "1e5" } });
    expect(within(dialog).getByText(THRESHOLD_INVALID)).toBeVisible();
    expect(threshold).toHaveAccessibleDescription(
      `${THRESHOLD_INVALID} ${COUNTS}`,
    );

    fireEvent.change(threshold, { target: { value: "2500" } });
    expect(within(dialog).queryByText(THRESHOLD_INVALID)).toBeNull();
    expect(threshold).toHaveAccessibleDescription(COUNTS);
  });

  it.each([
    ["the billing package acts on total-spend budgets", BOTH, null],
    [
      "the billing package does not act on total-spend budgets",
      SPEND_OFF,
      NOT_PAUSED,
    ],
    ["the probe gives no budget answers", {}, null],
  ])(
    "in Edit Budget, says under Threshold what a saved total-spend budget counts, and nothing of the kind for an AI credits budget, when %s",
    async (_when, probe, notEnforced) => {
      state.probe = probe;
      state.budgets = [SPEND_PAUSE, AI_PAUSE];
      mount();
      const spend = await rowOf(SPEND_PAUSE.name);
      const credits = await rowOf(AI_PAUSE.name);
      await probeAnswered();

      fireEvent.click(within(spend).getByTitle("Edit budget"));
      let dialog = await screen.findByRole("dialog", { name: "Edit Budget" });
      await expectCountsLine(dialog, notEnforced);
      fireEvent.click(within(dialog).getByRole("button", { name: "Cancel" }));
      await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());

      fireEvent.click(within(credits).getByTitle("Edit budget"));
      dialog = await screen.findByRole("dialog", { name: "Edit Budget" });
      expect(within(dialog).getByLabelText("Threshold")).toHaveValue("750");
      expectNoCountsLine(dialog);
    },
  );
});

describe("A notify, warn or pause budget says when its e-mail notices are not sent", () => {
  it("reads exactly as before, Triggered chips kept, when the billing package sends budget e-mails", async () => {
    state.budgets = [AI_NOTIFY, STORAGE_WARN];
    mount();
    const notify = await rowOf("Monthly AI credits cap");
    const warn = await rowOf("Storage warning");
    await probeAnswered();

    expect(notify.textContent).toBe(AI_NOTIFY_TODAY);
    expect(warn.textContent).toBe(STORAGE_WARN_TODAY);
    expect(screen.queryByRole("img", { name: "Warning" })).toBeNull();
  });

  it("shows the sentence in place of the Triggered chip on a notify budget with no recipients of its own, triggered or not", async () => {
    state.probe = MAIL_OFF;
    state.budgets = [AI_NOTIFY, AI_NOTIFY_QUIET];
    mount();
    const triggered = await rowOf("Monthly AI credits cap");
    const quiet = await rowOf("AI credits early notice");
    await probeAnswered();

    for (const row of [triggered, quiet]) {
      const line = await within(row).findByText(NOT_SENT);
      expect(line).toBeVisible();
      expect(
        within(line.parentElement).getByRole("img", { name: "Warning" }),
      ).toBeInTheDocument();
      expect(within(row).getByText("Notify")).toBeVisible();
      expect(within(row).queryByText(/^Triggered/)).toBeNull();
      expect(within(row).queryByText(NOT_PAUSED)).toBeNull();
      expect(within(row).queryByText(NOT_RAISED)).toBeNull();
    }
  });

  it("says a warn budget with no recipients of its own sends no e-mail notices and keeps its Triggered chip", async () => {
    state.probe = MAIL_OFF;
    state.budgets = [STORAGE_WARN];
    mount();
    const warn = await rowOf("Storage warning");
    await probeAnswered();

    const line = await within(warn).findByText(NOT_SENT);
    expect(line).toBeVisible();
    expect(
      within(line.parentElement).getByRole("img", { name: "Warning" }),
    ).toBeInTheDocument();
    expect(within(warn).getByText("Warn")).toBeVisible();
    // The warn flag and banner still happen, so the chip stays true.
    expect(within(warn).getByText("Triggered Oct 2026")).toBeVisible();
  });

  it("leaves a notify or warn budget that lists its own recipients exactly as before, Triggered chip kept", async () => {
    state.probe = MAIL_OFF;
    state.budgets = [AI_NOTIFY, AI_NOTIFY_LISTED, STORAGE_WARN_LISTED];
    mount();
    const unlisted = await rowOf("Monthly AI credits cap");
    const notify = await rowOf("AI credits finance notice");
    const warn = await rowOf("Storage finance warning");
    await probeAnswered();
    // The answer has been drawn: a budget with no recipients of its own
    // says its notices are not sent.
    await within(unlisted).findByText(NOT_SENT);

    expect(notify.textContent).toBe(AI_NOTIFY_LISTED_TODAY);
    expect(warn.textContent).toBe(STORAGE_WARN_LISTED_TODAY);
    for (const row of [notify, warn]) {
      expect(within(row).queryByRole("img", { name: "Warning" })).toBeNull();
    }
  });

  // A pause budget is e-mailed like any other. With no recipients of its own
  // it says so under its threshold line, or under the line that total spend
  // is not enforced when there is one, before its chips.
  it.each([
    [
      "an AI credits",
      AI_PAUSE,
      "AI Credits: 750 credits → Pause Usage",
      `AI credits hard stopAI Credits: 750 credits → Pause Usage${NOT_SENT}Pause UsageTriggered Oct 2026`,
    ],
    [
      "a total-spend",
      SPEND_PAUSE,
      "Total Spend: $2,500.00 → Pause Usage",
      `Total spend hard stopTotal Spend: $2,500.00 → Pause Usage${NOT_SENT}Pause UsageTriggered Oct 2026`,
    ],
  ])(
    "says once under its threshold line, after a warning icon, that %s pause budget with no recipients of its own sends no e-mail notices, and keeps its Triggered chip",
    async (_kind, budget, caption, expected) => {
      state.probe = MAIL_OFF;
      state.budgets = [budget];
      mount();
      const row = await rowOf(budget.name);
      await probeAnswered();

      const line = await within(row).findByText(NOT_SENT);
      expect(line).toBeVisible();
      expect(
        within(line.parentElement).getByRole("img", { name: "Warning" }),
      ).toBeInTheDocument();
      // In the left column with the threshold line, not beside the chips.
      expect(within(row).getByText(caption).parentElement).toContainElement(
        line,
      );
      expect(row.textContent).toBe(expected);
      // Reaching it still pauses usage, so the chip stays true.
      expect(within(row).getByText("Pause Usage")).toBeVisible();
      expect(within(row).getByText("Triggered Oct 2026")).toBeVisible();
    },
  );

  it("says a total-spend pause budget with no recipients of its own sends no e-mail notices after its line that total spend is not enforced", async () => {
    state.probe = NEITHER;
    state.budgets = [SPEND_PAUSE];
    mount();
    const row = await rowOf("Total spend hard stop");
    await probeAnswered();

    const line = await within(row).findByText(NOT_SENT);
    expect(line).toBeVisible();
    expect(
      within(line.parentElement).getByRole("img", { name: "Warning" }),
    ).toBeInTheDocument();
    expect(within(row).getAllByRole("img", { name: "Warning" })).toHaveLength(
      2,
    );
    expect(row.textContent).toBe(
      `Total spend hard stopTotal Spend: $2,500.00 → Pause Usage${NOT_PAUSED}${NOT_SENT}Pause UsageTriggered Oct 2026`,
    );
    expect(within(row).getByText("Triggered Oct 2026")).toBeVisible();
  });

  it.each([
    [
      "it lists recipients of its own",
      MAIL_OFF,
      [AI_PAUSE_LISTED, AI_NOTIFY],
      AI_PAUSE_LISTED,
      AI_PAUSE_LISTED_TODAY,
      // The answer has been drawn: a budget with no recipients of its own
      // says its notices are not sent.
      async () =>
        within(await rowOf("Monthly AI credits cap")).findByText(NOT_SENT),
    ],
    [
      "the billing package sends budget e-mails",
      BOTH,
      [AI_PAUSE],
      AI_PAUSE,
      AI_PAUSE_TODAY,
      async () => {},
    ],
    [
      "the probe gives no answer",
      {},
      [AI_PAUSE],
      AI_PAUSE,
      AI_PAUSE_TODAY,
      () => screen.findByText(NOT_CHECKED),
    ],
  ])(
    "leaves a pause budget exactly as before, with no e-mail sentence, when %s",
    async (_when, probe, budgets, budget, today, answerDrawn) => {
      state.probe = probe;
      state.budgets = budgets;
      mount();
      const row = await rowOf(budget.name);
      await probeAnswered();
      await answerDrawn();

      expect(row.textContent).toBe(today);
      expect(within(row).queryByText(NOT_SENT)).toBeNull();
      expect(within(row).queryByRole("img", { name: "Warning" })).toBeNull();
    },
  );
});

describe("When the billing package cannot be checked, budgets claim neither way", () => {
  it.each([
    ["the probe cannot be read", { probeFails: true }],
    ["the probe does not carry the budget answers", { probe: {} }],
  ])(
    "when %s, every row reads as before and the section says once that budgets couldn't be checked",
    async (_label, setup) => {
      Object.assign(state, setup);
      state.budgets = [SPEND_PAUSE, SPEND_NOTIFY, AI_NOTIFY, STORAGE_WARN];
      mount();
      const rows = [
        [await rowOf("Total spend hard stop"), SPEND_PAUSE_TODAY],
        [await rowOf("Total spend notice"), SPEND_NOTIFY_TODAY],
        [await rowOf("Monthly AI credits cap"), AI_NOTIFY_TODAY],
        [await rowOf("Storage warning"), STORAGE_WARN_TODAY],
      ];
      await probeAnswered();

      const line = await screen.findByText(NOT_CHECKED);
      expect(line).toBeVisible();
      expect(screen.getAllByText(NOT_CHECKED)).toHaveLength(1);
      expect(
        within(line.parentElement).getByRole("img", { name: "Warning" }),
      ).toBeInTheDocument();
      expect(line.closest('[data-testid="usage-budget-row"]')).toBeNull();

      for (const [row, today] of rows) {
        expect(row.textContent).toBe(today);
        expect(within(row).queryByRole("img", { name: "Warning" })).toBeNull();
      }
    },
  );
});
