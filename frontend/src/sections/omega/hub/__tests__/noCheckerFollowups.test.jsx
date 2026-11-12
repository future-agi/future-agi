import React from "react";
import { screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { OMEGA_PART } from "src/sections/omega/shell/rollout";
import { capabilitiesAnswer } from "src/sections/omega/shell/__tests__/rollout-answer";

import review from "../__fixtures__/review-submission.json";
import rules from "../__fixtures__/rules.json";
import meMaintainer from "../__fixtures__/me-maintainer.json";
import meUser from "../__fixtures__/me-user.json";
import * as queries from "../api/queries";
import {
  POLL_FAST_MS,
  POLL_SLOW_MS,
  submissionPollInterval,
} from "../api/queries-me";
import HubReviewPage from "../HubReviewPage";
import HubSubmissionPage from "../HubSubmissionPage";
import HubSubmissionsPage from "../HubSubmissionsPage";
import { StatusChip } from "../components/StatusTimeline";
import {
  NO_CHECKER_STATUS,
  stalledWithoutChecker,
} from "../lib/uploadsChecked";
import { calls, respond } from "./mockAxios";
import { renderAt } from "./render";

vi.mock("src/utils/axios", () => import("./mockAxios"));
// The pages' own hooks, watched so a case can read the options a page passes.
vi.mock("../api/queries", async (importOriginal) => {
  const actual = await importOriginal();
  return {
    ...actual,
    useSubmission: vi.fn(actual.useSubmission),
    useMySubmissions: vi.fn(actual.useMySubmissions),
  };
});

// EE3 FA-HUB-RECHECK-NO-CHECKER follow-ups (BRIEF-FA-HUB-FOLLOWUPS-r1,
// UX-NOCHK-2/3/4, NC-1/2/4). With no Hub checker on the server
// (OMEGA_PART HUB_PUBLISH denied) nothing claims a check job, so:
// a check_error submission reads the same "can't run until an administrator
// starts the Hub checker" line as a received or checking one, never the
// server's "a maintainer will run them again"; a checking one's chip does
// not say "Checks running"; a stalled submission is not asked for every 3 s
// and the page does not say it updates on its own; and the sentence lives
// in one module. With a checker running, nothing changes.

const STALLED_LINE =
  "The automated checks can't run on this server until an administrator starts the Hub checker.";
// The server's words (omega_hub/api/serializers/submissions.py STATUS_TEXT).
const SERVER_TEXT = {
  received: "Received. Automated checks start in a moment.",
  checking:
    "Automated checks are running: manifest, licence, secrets, dependencies, static scan, sandbox run, demo.",
  check_error:
    "We could not run the checks. A maintainer has been told and will run them again.",
  in_review:
    "Checks passed. Waiting for a Future AGI maintainer. Usually within 2 business days.",
};

const NO_CHECKER = [];
const CHECKER = [OMEGA_PART.HUB_PUBLISH];
const CAPABILITIES = "/api/capabilities/";
const REVIEW = `/omega-hub/v1/review/submissions/${review.id}`;
const SUBMISSION = `/omega-hub/v1/submissions/${review.id}`;

const reviewRow = (status) => ({
  ...review,
  status,
  statusText: SERVER_TEXT[status],
  decision: null,
  published: null,
  reviewer: null,
  reviewStartedAt: null,
  previousVersion: { version: "0.9.0" },
  draftDisclosure: review.confirmedDisclosure,
});

const submitterRow = (status, id = review.id) => ({
  ...review,
  id,
  status,
  statusText: SERVER_TEXT[status],
  report: null,
  checkSummary: null,
  decision: null,
  confirmedDisclosure: null,
  published: null,
});

function serveSubmission(status, parts) {
  respond([
    ["get", CAPABILITIES, capabilitiesAnswer({ parts })],
    ["get", "/omega-hub/v1/me", meUser],
    ["get", SUBMISSION, submitterRow(status)],
  ]);
}

function serveList(rows, parts) {
  respond([
    ["get", CAPABILITIES, capabilitiesAnswer({ parts })],
    ["get", "/omega-hub/v1/me", meUser],
    ["get", "/omega-hub/v1/submissions", rows],
  ]);
}

const openSubmission = () =>
  renderAt(<HubSubmissionPage />, {
    path: "/dashboard/hub/submissions/:id",
    at: `/dashboard/hub/submissions/${review.id}`,
  });

const openReview = () =>
  renderAt(<HubReviewPage />, {
    path: "/dashboard/hub/review/:id",
    at: `/dashboard/hub/review/${review.id}`,
  });

// Let the capabilities answer arrive, so a page that would change has had its chance.
async function settle() {
  await waitFor(
    () => expect(calls.some((call) => call.path === CAPABILITIES)).toBe(true),
    { timeout: 1000 },
  ).catch(() => {});
  await new Promise((resolve) => setTimeout(resolve, 50));
}

const lastOptions = (hook) => hook.mock.calls.at(-1)?.at(-1);

beforeEach(() => {
  vi.stubEnv("VITE_OMEGA_CONSOLE", "1");
  queries.useSubmission.mockClear();
  queries.useMySubmissions.mockClear();
});
afterEach(() => vi.unstubAllEnvs());

describe("One sentence, one module (NC-2)", () => {
  it("lib/uploadsChecked owns the stalled sentence and the rule", () => {
    expect(NO_CHECKER_STATUS).toBe(STALLED_LINE);
    for (const status of ["received", "checking", "check_error"]) {
      expect(stalledWithoutChecker("unchecked", status)).toBe(true);
      expect(stalledWithoutChecker("checked", status)).toBe(false);
      expect(stalledWithoutChecker("loading", status)).toBe(false);
    }
    for (const status of ["in_review", "checks_failed", "approved"]) {
      expect(stalledWithoutChecker("unchecked", status)).toBe(false);
    }
  });
});

describe("A check_error submission with no Hub checker (UX-NOCHK-2)", () => {
  it("My submission says the checks can't run, not that a maintainer will run them again", async () => {
    serveSubmission("check_error", NO_CHECKER);
    openSubmission();
    const text = await screen.findByTestId("hub-status-text");
    await waitFor(() => expect(text).toHaveTextContent(STALLED_LINE));
    expect(document.body).not.toHaveTextContent(SERVER_TEXT.check_error);
  });

  it("its My submissions row says the same", async () => {
    serveList(
      [submitterRow("check_error"), submitterRow("in_review", "second-row")],
      NO_CHECKER,
    );
    renderAt(<HubSubmissionsPage />);
    const rows = await screen.findAllByTestId("hub-submission-row");
    await waitFor(() => expect(rows[0]).toHaveTextContent(STALLED_LINE));
    expect(rows[1]).toHaveTextContent(SERVER_TEXT.in_review);
    expect(document.body).not.toHaveTextContent(SERVER_TEXT.check_error);
  });

  it("the Review page's Decision panel says the same", async () => {
    respond([
      ["get", CAPABILITIES, capabilitiesAnswer({ parts: NO_CHECKER })],
      ["get", "/omega-hub/v1/me", meMaintainer],
      ["get", "/omega-hub/v1/rules", rules],
      ["get", REVIEW, reviewRow("check_error")],
      ["get", `${REVIEW}/diff`, { diff: null }],
    ]);
    openReview();
    const panel = await screen.findByTestId("hub-review-status-text");
    await waitFor(() => expect(panel).toHaveTextContent(STALLED_LINE));
    expect(document.body).not.toHaveTextContent(SERVER_TEXT.check_error);
  });
});

describe("The status chip of a stalled checking submission (UX-NOCHK-3)", () => {
  it("reads Received, never Checks running, and keeps the server's state", () => {
    renderAt(<StatusChip status="checking" stalled />);
    const chip = screen.getByTestId("hub-status-chip");
    expect(chip).toHaveTextContent("Received");
    expect(chip).not.toHaveTextContent("Checks running");
    expect(chip).toHaveAttribute("data-status", "checking");
  });

  it("My submission's header chip does the same with no checker", async () => {
    serveSubmission("checking", NO_CHECKER);
    openSubmission();
    const text = await screen.findByTestId("hub-status-text");
    await waitFor(() => expect(text).toHaveTextContent(STALLED_LINE));
    expect(screen.getByTestId("hub-status-chip")).toHaveTextContent("Received");
    expect(document.body).not.toHaveTextContent("Checks running");
  });

  it("a My submissions row does the same with no checker", async () => {
    serveList([submitterRow("checking")], NO_CHECKER);
    renderAt(<HubSubmissionsPage />);
    const [row] = await screen.findAllByTestId("hub-submission-row");
    await waitFor(() => expect(row).toHaveTextContent(STALLED_LINE));
    expect(within(row).getByTestId("hub-status-chip")).toHaveTextContent(
      "Received",
    );
  });
});

describe("No 3 s polling for a submission that cannot move (NC-1)", () => {
  it("the poll interval is off for a stalled received or checking submission, and unchanged otherwise", () => {
    for (const status of ["received", "checking"]) {
      expect(submissionPollInterval(status, false)).toBe(false);
      expect(submissionPollInterval(status, true)).toBe(POLL_FAST_MS);
      expect(submissionPollInterval(status)).toBe(POLL_FAST_MS);
    }
    expect(submissionPollInterval("in_review", false)).toBe(POLL_SLOW_MS);
    expect(submissionPollInterval("approved", false)).toBe(false);
  });

  it("My submission asks for no polling and does not say it updates on its own", async () => {
    serveSubmission("received", NO_CHECKER);
    openSubmission();
    const text = await screen.findByTestId("hub-status-text");
    await waitFor(() => expect(text).toHaveTextContent(STALLED_LINE));
    expect(lastOptions(queries.useSubmission)).toEqual({ live: false });
    expect(screen.queryByText("This page updates on its own.")).toBeNull();
  });

  it("My submissions asks for no polling of stalled rows", async () => {
    serveList([submitterRow("received")], NO_CHECKER);
    renderAt(<HubSubmissionsPage />);
    const [row] = await screen.findAllByTestId("hub-submission-row");
    await waitFor(() => expect(row).toHaveTextContent(STALLED_LINE));
    expect(lastOptions(queries.useMySubmissions)).toEqual({ live: false });
  });
});

describe("With a Hub checker running, nothing changes (NC-4 and guards)", () => {
  it.each(["received", "checking"])(
    "a %s My submissions row keeps the server's words and chip",
    async (status) => {
      serveList([submitterRow(status)], CHECKER);
      renderAt(<HubSubmissionsPage />);
      const [row] = await screen.findAllByTestId("hub-submission-row");
      await settle();
      expect(row).toHaveTextContent(SERVER_TEXT[status]);
      expect(row).not.toHaveTextContent(STALLED_LINE);
      expect(within(row).getByTestId("hub-status-chip")).toHaveTextContent(
        status === "checking" ? "Checks running" : "Received",
      );
      expect(lastOptions(queries.useMySubmissions)).toEqual({ live: true });
    },
  );

  it("a check_error submission keeps the server's words", async () => {
    serveSubmission("check_error", CHECKER);
    openSubmission();
    const text = await screen.findByTestId("hub-status-text");
    await settle();
    expect(text).toHaveTextContent(SERVER_TEXT.check_error);
    expect(screen.queryByText(STALLED_LINE)).toBeNull();
  });

  it("a received submission keeps polling and says so", async () => {
    serveSubmission("received", CHECKER);
    openSubmission();
    await screen.findByTestId("hub-status-text");
    await settle();
    expect(lastOptions(queries.useSubmission)).toEqual({ live: true });
    expect(
      screen.getByText("This page updates on its own."),
    ).toBeInTheDocument();
  });
});
