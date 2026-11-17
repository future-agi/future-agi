import React from "react";
import { screen, waitFor } from "@testing-library/react";
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
import { NO_CHECKER_RUNNING, NO_CHECKER_STATUS } from "../lib/uploadsChecked";
import { calls, respond } from "./mockAxios";
import { renderAt } from "./render";

vi.mock("src/utils/axios", () => import("./mockAxios"));
// A server confirmed self-hosted, so a page that took the submission for one
// that cannot move would name an administrator as its next step.
vi.mock("src/hooks/useDeploymentMode", () => ({
  useDeploymentMode: () => ({
    mode: "oss",
    isCloud: false,
    isOSS: true,
    isEE: false,
    isLoading: false,
    isSuccess: true,
    isConfirmed: true,
  }),
}));

// A server whose Hub checker is configured (OMEGA_PART HUB_PUBLISH allowed)
// but not running answers a received or checking submission with one of its
// not-running sentences and checksStopped: true. The status chip beside that
// sentence read "Checks running" for a checking one. It now reads "Received",
// as it does where the server runs no checker, and keeps the server's state
// in data-status. Only the chip changes: the server's sentence shows as sent,
// the next step stays the checks, and the page keeps updating on its own, so
// it moves on by itself once a checker runs. With checksStopped false, or
// left out as a server without the field answers, the chip is as before.

// The server's sentences for a received or checking submission whose checks
// are not running (checksStopped: true), and for one whose checks run.
const STOPPED_TEXT = {
  received:
    "Received. The checks are not running right now. This upload is checked when they start.",
  checking:
    "The checks stopped because no Hub checker is running. They start again when one is running.",
};
const RUNNING_TEXT = {
  received: "Received. Automated checks start in a moment.",
  checking:
    "Automated checks are running: manifest, licence, secrets, dependencies, static scan, sandbox run, demo.",
};

const CHECKER = [OMEGA_PART.HUB_PUBLISH];
const CAPABILITIES = "/api/capabilities/";
const SUBMISSION = `/omega-hub/v1/submissions/${review.id}`;
const REVIEW = `/omega-hub/v1/review/submissions/${review.id}`;

// The answer's checksStopped: true or false as the server sends it, or no
// field at all for `undefined`, as a server without it answers.
const checksStopped = (stopped) =>
  stopped === undefined ? {} : { checksStopped: stopped };

const serverText = (status, stopped) =>
  stopped ? STOPPED_TEXT[status] : RUNNING_TEXT[status];

const submitterRow = (status, stopped) => ({
  ...review,
  status,
  statusText: serverText(status, stopped),
  report: null,
  checkSummary: null,
  decision: null,
  confirmedDisclosure: null,
  published: null,
  ...checksStopped(stopped),
});

const reviewRow = (status, stopped) => ({
  ...review,
  status,
  statusText: serverText(status, stopped),
  decision: null,
  published: null,
  reviewer: null,
  reviewStartedAt: null,
  previousVersion: { version: "0.9.0" },
  draftDisclosure: review.confirmedDisclosure,
  ...checksStopped(stopped),
});

const openSubmission = (status, stopped) => {
  respond([
    ["get", CAPABILITIES, capabilitiesAnswer({ parts: CHECKER })],
    ["get", "/omega-hub/v1/me", meUser],
    ["get", SUBMISSION, submitterRow(status, stopped)],
  ]);
  renderAt(<HubSubmissionPage />, {
    path: "/dashboard/hub/submissions/:id",
    at: `/dashboard/hub/submissions/${review.id}`,
  });
};

// A received or checking row waits on someone else, so My submissions lists
// it under Waiting.
const openSubmissions = (status, stopped) => {
  respond([
    ["get", CAPABILITIES, capabilitiesAnswer({ parts: CHECKER })],
    ["get", "/omega-hub/v1/me", meUser],
    ["get", "/omega-hub/v1/submissions", [submitterRow(status, stopped)]],
  ]);
  renderAt(<HubSubmissionsPage />, {
    at: "/dashboard/hub/submissions?view=waiting",
  });
};

const openReview = (status, stopped) => {
  respond([
    ["get", CAPABILITIES, capabilitiesAnswer({ parts: CHECKER })],
    ["get", "/omega-hub/v1/me", meMaintainer],
    ["get", "/omega-hub/v1/rules", rules],
    ["get", REVIEW, reviewRow(status, stopped)],
    ["get", `${REVIEW}/diff`, { diff: null }],
  ]);
  renderAt(<HubReviewPage />, {
    path: "/dashboard/hub/review/:id",
    at: `/dashboard/hub/review/${review.id}`,
  });
};

// The three places the chip sits beside the submission's status sentence:
// how to open each, and the element that holds the sentence.
const PAGES = [
  {
    page: "My submission",
    open: openSubmission,
    sentence: () => screen.findByTestId("hub-status-text"),
  },
  {
    page: "My submissions",
    open: openSubmissions,
    sentence: () => screen.findByTestId("hub-submission-row"),
  },
  {
    page: "The Review page",
    open: openReview,
    sentence: () => screen.findByTestId("hub-review-status-text"),
  },
];

// Let the capabilities answer arrive, so a page that would change has had
// its chance.
async function settle() {
  await waitFor(
    () => expect(calls.some((call) => call.path === CAPABILITIES)).toBe(true),
    { timeout: 1000 },
  ).catch(() => {});
  await new Promise((resolve) => setTimeout(resolve, 50));
}

// Each page has one submission, so one chip.
const chip = () => screen.getByTestId("hub-status-chip");

beforeEach(() => vi.stubEnv("VITE_OMEGA_CONSOLE", "1"));
afterEach(() => vi.unstubAllEnvs());

describe.each(PAGES)("$page", ({ open, sentence }) => {
  it("reads Received for a checking submission whose checks stopped, beside the server's sentence", async () => {
    open("checking", true);
    const text = await sentence();
    await waitFor(() => expect(text).toHaveTextContent(STOPPED_TEXT.checking));
    await settle();
    expect(chip()).toHaveTextContent("Received");
    expect(chip()).not.toHaveTextContent("Checks running");
    expect(chip()).toHaveAttribute("data-status", "checking");
    // The server's sentence, as sent, never the page's own.
    expect(await sentence()).toHaveTextContent(STOPPED_TEXT.checking);
    expect(document.body).not.toHaveTextContent(NO_CHECKER_STATUS);
    expect(document.body).not.toHaveTextContent(NO_CHECKER_RUNNING);
  });

  it.each([
    { sent: "false", stopped: false },
    { sent: "left out", stopped: undefined },
  ])(
    "reads Checks running for a checking submission when checksStopped is $sent",
    async ({ stopped }) => {
      open("checking", stopped);
      const text = await sentence();
      await waitFor(() =>
        expect(text).toHaveTextContent(RUNNING_TEXT.checking),
      );
      await settle();
      expect(chip()).toHaveTextContent("Checks running");
      expect(chip()).toHaveAttribute("data-status", "checking");
      expect(await sentence()).toHaveTextContent(RUNNING_TEXT.checking);
    },
  );

  it.each([
    { sent: "true", stopped: true },
    { sent: "false", stopped: false },
    { sent: "left out", stopped: undefined },
  ])(
    "reads Received for a received submission when checksStopped is $sent",
    async ({ stopped }) => {
      open("received", stopped);
      const text = await sentence();
      await waitFor(() =>
        expect(text).toHaveTextContent(serverText("received", stopped)),
      );
      await settle();
      expect(chip()).toHaveTextContent("Received");
      expect(chip()).toHaveAttribute("data-status", "received");
    },
  );
});

describe("Only the chip reads the stopped checks", () => {
  it("My submission still waits on the checks and keeps updating on its own", async () => {
    openSubmission("checking", true);
    const text = await screen.findByTestId("hub-status-text");
    await waitFor(() => expect(text).toHaveTextContent(STOPPED_TEXT.checking));
    await settle();
    expect(screen.getByTestId("hub-next-step")).toHaveTextContent(
      "Waiting on the checks",
    );
    expect(
      screen.getByText("This page updates on its own."),
    ).toBeInTheDocument();
  });

  it("a My submissions row still waits on the checks", async () => {
    openSubmissions("checking", true);
    const row = await screen.findByTestId("hub-submission-row");
    await waitFor(() => expect(row).toHaveTextContent(STOPPED_TEXT.checking));
    await settle();
    expect(screen.getByTestId("hub-submission-next")).toHaveTextContent(
      "Waiting on the checks",
    );
  });
});
