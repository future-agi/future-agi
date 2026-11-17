import React from "react";
import { fireEvent, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import HubReviewPage from "../HubReviewPage";
import review from "../__fixtures__/review-submission.json";
import rules from "../__fixtures__/rules.json";
import me from "../__fixtures__/me-maintainer.json";
import { respond } from "./mockAxios";
import { renderAt } from "./render";

vi.mock("src/utils/axios", () => import("./mockAxios"));

// A review item read from top to bottom: the header, the check report's
// summary, the flags, then the evidence tabs (the submission's files and
// logs), which open on Files with the whole check report one tab away, so
// the report shows once and the evidence comes before the decision it
// informs. The disclosure, the checklist and the Decision card follow, with
// the publisher after them. Below the lg breakpoint (a 1024 window with the
// sidebar) the page ends with the sticky decision bar; a wider page puts the
// decision and the publisher in a side column. Each case renders the page at
// one width and pins the order a reviewer reads it in, or how a flagged line
// shows in Files.

const BASE = `/omega-hub/v1/review/submissions/${review.id}`;
const current = {
  ...review,
  status: "in_review",
  previousVersion: null,
  draftDisclosure: review.confirmedDisclosure,
};
const routes = (row = current, file = "x = 1\n") => [
  ["get", "/omega-hub/v1/me", me],
  ["get", "/omega-hub/v1/rules", rules],
  ["get", BASE, row],
  ["get", `${BASE}/diff`, { diff: null }],
  ["get", new RegExp(`^${BASE}/files/`), file],
];
const openReview = () =>
  renderAt(<HubReviewPage />, {
    path: "/dashboard/hub/review/:id",
    at: `/dashboard/hub/review/${review.id}`,
  });

const STEPS = review.report.steps.length;
const FINDINGS = review.report.steps.reduce(
  (count, step) => count + (step.findings || []).length,
  0,
);

// Narrower than the lg breakpoint, where the review keeps a sticky decision
// bar. Returns the undo.
function narrowWindow() {
  const original = window.matchMedia.getMockImplementation();
  window.matchMedia.mockImplementation((query) => ({
    ...original(query),
    matches: /max-width/.test(query),
  }));
  return () => window.matchMedia.mockImplementation(original);
}

let restore = null;
afterEach(() => {
  restore?.();
  restore = null;
});

// -1 when `a` comes before `b` in the page, else 1.
const before = (a, b) =>
  a.compareDocumentPosition(b) & Node.DOCUMENT_POSITION_FOLLOWING ? -1 : 1;

// The names of `parts` ([name, node] pairs) in the order the page holds
// them, which is the order a reader, a screen reader and Tab meet them.
const readingOrder = (parts) =>
  [...parts].sort(([, a], [, b]) => before(a, b)).map(([name]) => name);

const evidence = () =>
  screen.getByRole("region", { name: "Submission details" });

describe("Review item: reading order", () => {
  it("on a narrow page, reads the header, the check report, the flags, the evidence tabs, the disclosure, the checklist, then the decision", async () => {
    restore = narrowWindow();
    respond(routes());
    openReview();
    await screen.findAllByTestId("hub-checklist-item");
    const parts = [
      ["header", screen.getByTestId("hub-review-header")],
      ["check report", screen.getByTestId("hub-check-summary")],
      ["flags", screen.getByTestId("hub-flag-groups")],
      ["evidence tabs", evidence()],
      ["disclosure", screen.getByTestId("hub-review-disclosure")],
      ["checklist", screen.getByTestId("hub-review-checklist")],
      ["decision", screen.getByTestId("hub-decision-form")],
      ["publisher", screen.getByTestId("hub-review-publisher")],
      ["decision bar", screen.getByTestId("hub-decision-bar")],
    ];
    expect(readingOrder(parts)).toEqual(parts.map(([name]) => name));
    // The evidence tabs are no part of the decision's side column.
    const side = screen.getByLabelText("Decision and publisher");
    expect(side.tagName).toBe("ASIDE");
    expect(side).toContainElement(screen.getByTestId("hub-decision-form"));
    expect(side).not.toContainElement(evidence());
  });

  it("on a narrow page, shows the check report once: its summary at the top, the evidence tabs open on Files, and the whole report is one tab away", async () => {
    restore = narrowWindow();
    respond(routes());
    openReview();
    await screen.findAllByTestId("hub-checklist-item");
    expect(screen.getAllByTestId("hub-check-summary")).toHaveLength(1);
    const summary = screen.getByTestId("hub-check-summary");
    const steps = within(summary).getByRole("list", { name: "Check steps" });
    expect(within(steps).getAllByRole("listitem")).toHaveLength(STEPS);
    // The report is not drawn a second time under the flags.
    expect(screen.queryByTestId("hub-check-report")).toBeNull();
    const reportTab = screen.getByTestId("hub-review-tab-report");
    expect(reportTab.textContent).toBe("Check report (4 flags)");
    expect(reportTab).toHaveAttribute("aria-selected", "false");
    expect(screen.getByTestId("hub-review-tab-files")).toHaveAttribute(
      "aria-selected",
      "true",
    );
    expect(
      within(evidence()).getByTestId("hub-review-files"),
    ).toBeInTheDocument();
    // The Check report tab still holds the whole report: every step, each
    // with its findings.
    fireEvent.click(reportTab);
    expect(reportTab).toHaveAttribute("aria-selected", "true");
    const whole = within(evidence()).getByTestId("hub-check-report");
    expect(whole.querySelectorAll("[data-step]")).toHaveLength(STEPS);
    expect(within(whole).getAllByTestId("hub-finding")).toHaveLength(FINDINGS);
  });

  it("on a narrow page, a decided item reads the check report, the flags and the evidence tabs before its decision, and opens them on Files", async () => {
    restore = narrowWindow();
    respond(routes(review));
    openReview();
    const status = await screen.findByTestId("hub-review-status-text");
    const parts = [
      ["header", screen.getByTestId("hub-review-header")],
      ["check report", screen.getByTestId("hub-check-summary")],
      ["flags", screen.getByTestId("hub-flag-groups")],
      ["evidence tabs", evidence()],
      ["decision", status],
      ["publisher", screen.getByTestId("hub-review-publisher")],
    ];
    expect(readingOrder(parts)).toEqual(parts.map(([name]) => name));
    expect(screen.queryByTestId("hub-check-report")).toBeNull();
    expect(screen.getByTestId("hub-review-tab-files")).toHaveAttribute(
      "aria-selected",
      "true",
    );
  });

  it("on a wide page, reads the check report, the flags, the evidence tabs, the disclosure and the checklist, with the decision and the publisher beside them", async () => {
    respond(routes());
    openReview();
    await screen.findAllByTestId("hub-checklist-item");
    expect(screen.queryByTestId("hub-decision-bar")).toBeNull();
    const parts = [
      ["header", screen.getByTestId("hub-review-header")],
      ["check report", screen.getByTestId("hub-check-summary")],
      ["flags", screen.getByTestId("hub-flag-groups")],
      ["evidence tabs", evidence()],
      ["disclosure", screen.getByTestId("hub-review-disclosure")],
      ["checklist", screen.getByTestId("hub-review-checklist")],
      ["decision", screen.getByTestId("hub-decision-form")],
      ["publisher", screen.getByTestId("hub-review-publisher")],
    ];
    expect(readingOrder(parts)).toEqual(parts.map(([name]) => name));
    const side = screen.getByLabelText("Decision and publisher");
    expect(side.tagName).toBe("ASIDE");
    expect(side).toContainElement(screen.getByTestId("hub-decision-form"));
    expect(side).toContainElement(screen.getByTestId("hub-review-publisher"));
    expect(side).not.toContainElement(evidence());
  });

  it("on a wide page, shows the check report once: its summary at the top, the evidence tabs open on Files, and the whole report is one tab away", async () => {
    respond(routes());
    openReview();
    await screen.findAllByTestId("hub-checklist-item");
    expect(screen.getAllByTestId("hub-check-summary")).toHaveLength(1);
    expect(screen.queryByTestId("hub-check-report")).toBeNull();
    const reportTab = screen.getByTestId("hub-review-tab-report");
    expect(reportTab).toHaveAttribute("aria-selected", "false");
    expect(screen.getByTestId("hub-review-tab-files")).toHaveAttribute(
      "aria-selected",
      "true",
    );
    expect(
      within(evidence()).getByTestId("hub-review-files"),
    ).toBeInTheDocument();
    fireEvent.click(reportTab);
    expect(reportTab).toHaveAttribute("aria-selected", "true");
    const whole = within(evidence()).getByTestId("hub-check-report");
    expect(whole.querySelectorAll("[data-step]")).toHaveLength(STEPS);
    expect(within(whole).getAllByTestId("hub-finding")).toHaveLength(FINDINGS);
  });
});

describe("Review item: the flagged line in Files", () => {
  it("wraps a flagged line inside the code box, so the whole flagged call shows without scrolling, and keeps every other line as written", async () => {
    restore = narrowWindow();
    // lib.mjs is the first file with a flag, at its line 39.
    const flagged =
      "appendFileSync(join(process.env.OMEGA_LIVE_SPEND_LOG_DIR, 'spend.jsonl'), JSON.stringify({ time, label, model, tokens }));";
    const lines = Array.from({ length: 38 }, (_, i) => `// line ${i + 1}`);
    respond(routes(current, [...lines, flagged].join("\n")));
    openReview();
    const viewer = await screen.findByTestId("hub-file-viewer");
    expect(viewer).toHaveAttribute("data-path", "lib.mjs");
    const line = await within(viewer).findByText(flagged);
    expect(getComputedStyle(line).whiteSpace).toBe("pre-wrap");
    expect(getComputedStyle(line).overflowWrap).toBe("anywhere");
    // The flag's note sits right under the line it marks.
    expect(line.parentElement.nextElementSibling).toHaveTextContent(
      "writes-code: appendFileSync() writes a computed path",
    );
    const plain = within(viewer).getByText("// line 1");
    expect(getComputedStyle(plain).whiteSpace).toBe("pre");
  });
});
