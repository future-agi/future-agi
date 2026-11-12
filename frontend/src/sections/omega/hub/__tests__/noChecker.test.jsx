import React from "react";
import { fireEvent, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { OMEGA_PART } from "src/sections/omega/shell/rollout";
import { capabilitiesAnswer } from "src/sections/omega/shell/__tests__/rollout-answer";

import review from "../__fixtures__/review-submission.json";
import rules from "../__fixtures__/rules.json";
import meMaintainer from "../__fixtures__/me-maintainer.json";
import meUser from "../__fixtures__/me-user.json";
import HubReviewPage from "../HubReviewPage";
import HubSubmissionPage from "../HubSubmissionPage";
import HubSubmissionsPage from "../HubSubmissionsPage";
import axios, { calls, respond } from "./mockAxios";
import { renderAt } from "./render";

vi.mock("src/utils/axios", () => import("./mockAxios"));

// FA-HUB-RECHECK-NO-CHECKER. With no Hub checker on the server
// (omega_hub/conf.py checker_token() None, so OMEGA_PART HUB_PUBLISH is
// denied: the answer the Publish page reads), nothing ever claims a check job.
// A maintainer's "Run the checks again" answered 200, moved the submission to
// Received and took the review lock and the decision form with it, and every
// received or checking submission said "Automated checks start in a moment."
// for good. Now the button stays in view but off, with one line saying why;
// the review controls stay; and those submissions say plainly why they wait.
// With a checker running, nothing changes.

const RECHECK_LINE =
  "The checks can't run on this server because no Hub checker is running.";
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
const RECHECK = `${REVIEW}/recheck`;
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

function serveReview(status, parts, extra = []) {
  respond([
    ["get", CAPABILITIES, capabilitiesAnswer({ parts })],
    ["get", "/omega-hub/v1/me", meMaintainer],
    ["get", "/omega-hub/v1/rules", rules],
    ["get", REVIEW, reviewRow(status)],
    ["get", `${REVIEW}/diff`, { diff: null }],
    ...extra,
  ]);
}

function serveSubmission(status, parts) {
  respond([
    ["get", CAPABILITIES, capabilitiesAnswer({ parts })],
    ["get", "/omega-hub/v1/me", meUser],
    ["get", SUBMISSION, submitterRow(status)],
  ]);
}

const openReview = () =>
  renderAt(<HubReviewPage />, {
    path: "/dashboard/hub/review/:id",
    at: `/dashboard/hub/review/${review.id}`,
  });

const openSubmission = () =>
  renderAt(<HubSubmissionPage />, {
    path: "/dashboard/hub/submissions/:id",
    at: `/dashboard/hub/submissions/${review.id}`,
  });

const recheckButton = () =>
  screen.getByRole("button", { name: "Run the checks again" });

// Let the capabilities answer arrive, so a page that would change has had its chance.
async function settle() {
  // A page that reads capabilities gets its answer first; a page that never asks (as before this change) is not
  // made to fail here, so the checker-running cases guard the old behaviour on both sides of the change.
  await waitFor(
    () => expect(calls.some((call) => call.path === CAPABILITIES)).toBe(true),
    { timeout: 1000 },
  ).catch(() => {});
  await new Promise((resolve) => setTimeout(resolve, 50));
}

beforeEach(() => vi.stubEnv("VITE_OMEGA_CONSOLE", "1"));
afterEach(() => vi.unstubAllEnvs());

describe("Review page with no Hub checker running", () => {
  it("keeps 'Run the checks again' in view but off, says why, and keeps the review controls", async () => {
    serveReview("in_review", NO_CHECKER);
    openReview();
    expect(await screen.findByText(RECHECK_LINE)).toBeInTheDocument();
    const button = recheckButton();
    expect(button).toBeDisabled();
    expect(button).toHaveAccessibleDescription(RECHECK_LINE);
    // The review lock and the decision form stay.
    expect(screen.getByTestId("hub-start-review")).toBeEnabled();
    expect(screen.getByTestId("hub-decision-form")).toBeInTheDocument();
    fireEvent.click(button);
    await settle();
    expect(calls.filter((call) => call.method === "post")).toEqual([]);
    expect(screen.getByTestId("hub-decision-form")).toBeInTheDocument();
  });

  it("keeps it off with the same line for a submission whose checks could not run", async () => {
    serveReview("check_error", NO_CHECKER);
    openReview();
    expect(await screen.findByText(RECHECK_LINE)).toBeInTheDocument();
    expect(recheckButton()).toBeDisabled();
    // UX-NOCHK-2 (follow-up): its status says the checks can't run, never the
    // server's "a maintainer will run them again" (noCheckerFollowups.test.jsx).
    expect(screen.getByTestId("hub-review-status-text")).toHaveTextContent(
      STALLED_LINE,
    );
    expect(document.body).not.toHaveTextContent(SERVER_TEXT.check_error);
  });

  it.each(["received", "checking"])(
    "says plainly why a %s submission waits, never 'in a moment'",
    async (status) => {
      serveReview(status, NO_CHECKER);
      openReview();
      expect(await screen.findByText(STALLED_LINE)).toBeInTheDocument();
      expect(document.body).not.toHaveTextContent(SERVER_TEXT[status]);
      expect(document.body).not.toHaveTextContent(/in a moment/i);
    },
  );
});

describe("My submission with no Hub checker running", () => {
  it.each(["received", "checking"])(
    "a %s submission says its checks can't run until an administrator starts the Hub checker",
    async (status) => {
      serveSubmission(status, NO_CHECKER);
      openSubmission();
      const text = await screen.findByTestId("hub-status-text");
      await waitFor(() => expect(text).toHaveTextContent(STALLED_LINE));
      expect(document.body).not.toHaveTextContent(SERVER_TEXT[status]);
      expect(document.body).not.toHaveTextContent(/in a moment/i);
      // Withdrawing stays the way out.
      expect(screen.getByTestId("hub-withdraw")).toBeEnabled();
    },
  );

  it("a received row in My submissions says the same, and other rows keep the server's words", async () => {
    respond([
      ["get", CAPABILITIES, capabilitiesAnswer({ parts: NO_CHECKER })],
      ["get", "/omega-hub/v1/me", meUser],
      [
        "get",
        "/omega-hub/v1/submissions",
        [submitterRow("received"), submitterRow("in_review", "second-row")],
      ],
    ]);
    renderAt(<HubSubmissionsPage />);
    const rows = await screen.findAllByTestId("hub-submission-row");
    await waitFor(() => expect(rows[0]).toHaveTextContent(STALLED_LINE));
    expect(rows[1]).toHaveTextContent(SERVER_TEXT.in_review);
    expect(document.body).not.toHaveTextContent(/in a moment/i);
  });

  it("never says the checks can't run while the server's answer is on its way", async () => {
    serveSubmission("received", NO_CHECKER);
    const answer = axios.get.getMockImplementation();
    // The capabilities answer never arrives; every other call is answered.
    axios.get.mockImplementation((url, ...rest) =>
      String(url).includes(CAPABILITIES)
        ? new Promise(() => {})
        : answer(url, ...rest),
    );
    try {
      openSubmission();
      expect(await screen.findByTestId("hub-status-text")).toHaveTextContent(
        SERVER_TEXT.received,
      );
      expect(screen.queryByText(STALLED_LINE)).toBeNull();
    } finally {
      axios.get.mockImplementation(answer);
    }
  });
});

describe("With a Hub checker running, nothing changes", () => {
  it("'Run the checks again' is on, has no line, and asks the server", async () => {
    serveReview("in_review", CHECKER, [
      ["post", RECHECK, reviewRow("received")],
    ]);
    openReview();
    const button = await screen.findByRole("button", {
      name: "Run the checks again",
    });
    await waitFor(() => expect(button).toBeEnabled());
    expect(screen.queryByText(RECHECK_LINE)).toBeNull();
    fireEvent.click(button);
    await waitFor(() =>
      expect(
        calls.some((call) => call.method === "post" && call.path === RECHECK),
      ).toBe(true),
    );
  });

  it("the review page keeps the server's words for a received submission", async () => {
    serveReview("received", CHECKER);
    openReview();
    expect(await screen.findByText(SERVER_TEXT.received)).toBeInTheDocument();
    await settle();
    expect(screen.getByText(SERVER_TEXT.received)).toBeInTheDocument();
    expect(screen.queryByText(STALLED_LINE)).toBeNull();
  });

  it.each(["received", "checking"])(
    "a %s submission keeps the server's words",
    async (status) => {
      serveSubmission(status, CHECKER);
      openSubmission();
      expect(await screen.findByTestId("hub-status-text")).toHaveTextContent(
        SERVER_TEXT[status],
      );
      await settle();
      expect(screen.getByTestId("hub-status-text")).toHaveTextContent(
        SERVER_TEXT[status],
      );
      expect(screen.queryByText(STALLED_LINE)).toBeNull();
    },
  );
});

describe("A recheck the server refuses", () => {
  it("shows the server's message instead of failing silently", async () => {
    const refused = {
      error: {
        code: "OMEGA_HUB_BAD_STATE",
        message: "The submission is not in a state that allows this.",
        fix: "Reload the submission and check its status.",
      },
    };
    serveReview("in_review", CHECKER, [["post", RECHECK, refused, 409]]);
    openReview();
    const button = await screen.findByRole("button", {
      name: "Run the checks again",
    });
    await waitFor(() => expect(button).toBeEnabled());
    fireEvent.click(button);
    const alert = await screen.findByText(refused.error.message, {
      exact: false,
    });
    expect(alert).toHaveTextContent(refused.error.fix);
  });
});
