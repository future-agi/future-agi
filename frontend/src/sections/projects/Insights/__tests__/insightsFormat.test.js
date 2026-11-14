import { describe, expect, it, vi } from "vitest";

vi.mock("src/utils/axios", () => ({
  default: { get: vi.fn(), post: vi.fn() },
  readQuery: vi.fn(),
  endpoints: { project: {} },
}));

import { insightsErrorMessage } from "../insightsApi";
import {
  estimateMinutes,
  formatCount,
  formatLatency,
  formatPercent,
  formatSpan,
  humanizeKey,
  relativeTime,
  runOutcome,
  runReadAny,
} from "../insightsFormat";
import { attributeError, facetChips } from "../insightsLenses";
import { sessionTracesLink, unitLink, userPageLink } from "../insightsLinks";

describe("a run that ended without a report", () => {
  it("accounts for every unit it read", () => {
    const run = {
      analysed_count: 11,
      excluded_count: 4,
      failed_count: 1,
      skipped_count: 2,
    };
    expect(runOutcome(run, "failures")).toBe(
      "11 analysed (4 had no failure) · 1 couldn't be read · 2 skipped",
    );
    expect(runOutcome(run, "custom")).toBe(
      "11 analysed (4 not relevant to the question) · 1 couldn't be read · 2 skipped",
    );
    expect(runOutcome({ analysed_count: 3, skipped_count: 0 })).toBe(
      "3 analysed · 0 skipped",
    );
  });

  it("has read something when the lens left all it read out", () => {
    expect(runReadAny({ analysed_count: 0, excluded_count: 5 })).toBe(true);
    expect(runReadAny({ analysed_count: 0, failed_count: 5 })).toBe(false);
    expect(runReadAny({ analysed_count: 2 })).toBe(true);
  });
});

describe("formatting", () => {
  it("formats counts, shares and latency", () => {
    expect(formatCount(12345)).toBe("12,345");
    expect(formatPercent(0.3888)).toBe("39%");
    expect(formatPercent(0.002)).toBe("<1%");
    expect(formatPercent(0)).toBe("0%");
    expect(formatLatency(1873)).toBe("1.9 s");
    expect(formatLatency(640)).toBe("640 ms");
  });

  it("formats elapsed time and estimates", () => {
    const now = new Date("2026-10-15T12:00:00Z");
    expect(relativeTime("2026-10-15T10:00:00Z", now)).toBe("2h ago");
    expect(relativeTime("2026-10-15T11:59:40Z", now)).toBe("just now");
    expect(formatSpan(165)).toBe("2m 45s");
    expect(estimateMinutes(108)).toBe("about 3 minutes");
    expect(estimateMinutes(10)).toBe("about 2 minutes");
    expect(humanizeKey("attr:asked_for_human")).toBe("Asked for human");
  });
});

describe("insightsErrorMessage", () => {
  it("names no edition for a refusal that doesn't say why", () => {
    expect(
      insightsErrorMessage({ statusCode: 402, code: "CAPABILITY_DENIED" }),
    ).toBe("Insights isn't available to this organization.");
  });

  it("says why a plan refused, when the server does", () => {
    // Insights is on the plan, the Turing models it runs on aren't.
    const turing =
      "Insights runs on Turing models, which aren't available on this organization's plan.";
    expect(
      insightsErrorMessage({
        status: false,
        statusCode: 402,
        code: "CAPABILITY_DENIED",
        message: turing,
        result: turing,
      }),
    ).toBe(turing);
    // A message that isn't one short line isn't shown.
    expect(
      insightsErrorMessage({
        statusCode: 402,
        code: "CAPABILITY_DENIED",
        message: "Denied\nTraceback (most recent call last)",
      }),
    ).toBe("Insights isn't available to this organization.");
    expect(
      insightsErrorMessage({ statusCode: 402, message: "x".repeat(300) }),
    ).toBe("Insights isn't available to this organization.");
  });

  it.each([
    [
      "LICENSE_MISSING",
      "Insights needs an Enterprise license that includes it.",
    ],
    [
      "LICENSE_EXPIRED",
      "Insights needs an Enterprise license; this one has expired.",
    ],
    [
      "LICENSE_FEATURE_MISSING",
      "Insights isn't included in this Enterprise license.",
    ],
    ["PLAN_FEATURE_MISSING", "Insights isn't included in your plan."],
  ])("says what unlocks Insights for a %s refusal", (code, message) => {
    // A self-hosted install was told "Insights isn't available on this plan."
    expect(insightsErrorMessage({ statusCode: 402, code, message })).toBe(
      message,
    );
  });

  it("names no edition for an older entitlement refusal", () => {
    expect(
      insightsErrorMessage({
        statusCode: 402,
        code: "ENTITLEMENT_DENIED",
        message: "This feature is not available on your current plan.",
      }),
    ).toBe("Insights isn't available to this organization.");
  });

  it("shows rate-limit and model messages as written", () => {
    expect(
      insightsErrorMessage({
        statusCode: 429,
        code: "rate_limited",
        message: "Too many reports this hour. Try again later.",
      }),
    ).toBe("Too many reports this hour. Try again later.");
    expect(
      insightsErrorMessage({
        statusCode: 503,
        code: "model_unavailable",
        message: "The analysis model is unavailable right now.",
      }),
    ).toBe("The analysis model is unavailable right now.");
  });

  it("says a start was refused for the person's role as the server does", () => {
    // A Workspace Viewer's run, as the API refuses it.
    const viewer = {
      type: "permission_error",
      code: "permission_denied",
      detail: "Write access denied to this workspace",
      statusCode: 403,
    };
    expect(
      insightsErrorMessage(viewer, "The report could not be started."),
    ).toBe("Write access denied to this workspace");
    expect(
      insightsErrorMessage(
        { ...viewer, detail: "Denied\nTraceback (most recent call last)" },
        "The report could not be started.",
      ),
    ).toBe("The report could not be started.");
  });

  it("says a fault on the server's side is worth retrying, not the input's", () => {
    const retry = "Couldn't start right now. Try again in a moment.";
    for (const error of [
      { statusCode: 503, message: "Database temporarily unavailable" },
      { statusCode: 500, message: "Traceback: boom" },
      { message: "Network Error", transportCode: "ERR_NETWORK" },
    ])
      expect(insightsErrorMessage(error, "Could not start.", retry)).toBe(
        retry,
      );
    expect(insightsErrorMessage({ statusCode: 502 })).toBe(
      "Couldn't do that right now. Try again in a moment.",
    );
  });

  it("keeps a refusal's own message, else the caller's words", () => {
    expect(
      insightsErrorMessage(
        {
          status: false,
          code: "bad_request",
          message: "Choose a date range for the report.",
          result: "Choose a date range for the report.",
          statusCode: 400,
        },
        "Could not start.",
      ),
    ).toBe("Choose a date range for the report.");
    expect(insightsErrorMessage({ statusCode: 400 }, "Could not start.")).toBe(
      "Could not start.",
    );
  });
});

describe("unit facets and links", () => {
  it("tones facets by what they mean", () => {
    expect(
      facetChips({
        outcome: "unresolved",
        sentiment: 2,
        failure_mode: "re-asked for order number",
        broke_at_turn: 1,
        language: "Spanish",
      }).map((c) => [c.key, c.tone]),
    ).toEqual([
      ["outcome", "bad"],
      ["sentiment", "bad"],
      ["failure_mode", "bad"],
      ["broke_at_turn", "bad"],
      ["language", "neutral"],
    ]);
  });

  it("shows no failure for 'none' or one the unit no longer counts", () => {
    const keys = (facets) => facetChips(facets).map((c) => c.key);
    expect(keys({ failure_mode: "none", failure_present: false })).toEqual([]);
    expect(keys({ failure_mode: "None" })).toEqual([]);
    // The reading named a failure nothing in the conversation backed.
    expect(
      keys({ failure_mode: "made-up delivery time", failure_present: false }),
    ).toEqual([]);
    expect(
      keys({ failure_mode: "false refund claim", failure_present: true }),
    ).toEqual(["failure_mode"]);
  });

  it("checks attribute keys", () => {
    expect(attributeError({ key: "asked_for_human" }, [])).toBe("");
    expect(attributeError({ key: "Asked" }, [])).toMatch(/Lowercase/);
    const dup = [{ key: "a" }, { key: "a" }];
    expect(attributeError(dup[0], dup)).toMatch(/own key/);
  });

  it("opens each unit where the app shows it", () => {
    expect(unitLink("p1", "traces", { unit_id: "t1" }).href).toBe(
      "/dashboard/observe/p1/trace/t1",
    );
    expect(
      unitLink("p1", "users", { unit_id: "uuid", user_id: "cust 1" }).href,
    ).toBe("/dashboard/users/cust%201?userTab=sessions");
    const session = new URL(
      sessionTracesLink("p1", "s1", "2026-10-09T10:31:29Z"),
      "https://app.test",
    );
    expect(session.pathname).toBe("/dashboard/observe/p1/llm-tracing");
    expect(JSON.parse(session.searchParams.get("observeLinkFilter"))).toEqual([
      {
        column_id: "session_id",
        filter_config: {
          filter_type: "text",
          filter_op: "equals",
          filter_value: "s1",
        },
      },
    ]);
    expect(session.searchParams.get("primaryTraceDateFilter")).toContain(
      "Custom",
    );
    expect(unitLink("p1", "users", { unit_id: "uuid" })).toBeNull();
  });

  // The user page's Sessions list opens on Today unless the link says
  // otherwise, and shows "No Rows To Show" for an older example.
  const userWindow = (href) => {
    const url = new URL(href, "https://app.test");
    const dates = JSON.parse(url.searchParams.get("sessionDateFilter"));
    expect(url.searchParams.get("userTab")).toBe("sessions");
    expect(dates.dateOption).toBe("Custom");
    return dates.dateFilter.map((value) => new Date(value).getTime());
  };
  const REPORT = ["2026-10-22T00:00:00+00:00", "2026-10-23T23:59:59+00:00"];

  it("opens a user on the report's days", () => {
    expect(
      userWindow(
        userPageLink("cust_009", {
          window: REPORT,
          time: "2026-10-22T18:51:20Z",
        }),
      ),
    ).toEqual(REPORT.map((value) => new Date(value).getTime()));
    const link = unitLink(
      "p1",
      "users",
      { unit_id: "uuid", user_id: "cust_009", time: "2026-10-23T09:00:00Z" },
      { window: REPORT },
    );
    expect(new URL(link.href, "https://app.test").pathname).toBe(
      "/dashboard/users/cust_009",
    );
    expect(userWindow(link.href)).toEqual(
      REPORT.map((value) => new Date(value).getTime()),
    );
  });

  it("opens a user on a window that holds the example", () => {
    const at = new Date("2026-10-22T18:51:20Z").getTime();
    // No report window: a day either side of the example.
    expect(
      userWindow(userPageLink("cust_009", { time: "2026-10-22T18:51:20Z" })),
    ).toEqual([at - 24 * 3600 * 1000, at + 24 * 3600 * 1000]);
    // An example outside the window stretches it.
    const [from, to] = userWindow(
      userPageLink("cust_009", {
        window: ["2026-10-23T00:00:00Z", "2026-10-24T00:00:00Z"],
        time: "2026-10-22T18:51:20Z",
      }),
    );
    expect(from).toBe(at);
    expect(to).toBe(new Date("2026-10-24T00:00:00Z").getTime());
    // Nothing to go on: the page's own default.
    expect(userPageLink("cust_009")).toBe(
      "/dashboard/users/cust_009?userTab=sessions",
    );
  });
});
