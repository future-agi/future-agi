import React from "react";
import { fireEvent, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { OMEGA_PART } from "src/sections/omega/shell/rollout";
import { capabilitiesAnswer } from "src/sections/omega/shell/__tests__/rollout-answer";

import review from "../__fixtures__/review-submission.json";
import rules from "../__fixtures__/rules.json";
import meMaintainer from "../__fixtures__/me-maintainer.json";
import HubReviewPage from "../HubReviewPage";
import axios, { calls, respond } from "./mockAxios";
import { renderAt } from "./render";

vi.mock("src/utils/axios", () => import("./mockAxios"));
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

// With no Hub checker running, "Run the checks again" cannot run, and the
// line beside it says why. A natively disabled button leaves the tab order,
// so a keyboard or screen-reader user never reached it and never heard that
// line. It now stays in the tab order, off (aria-disabled), with the line as
// its description, and a click, Enter or Space sends nothing. While a
// recheck is on its way it is off the same way, with no line (there is no
// reason to hear), so focus stays on it and a second press sends nothing
// (uploadsAnswerAndBlockedFocus.test.jsx follows the keyboard). With a
// checker running and no recheck on its way, nothing changes.

const RECHECK_LINE =
  "The checks can't run on this server because no Hub checker is running.";

const NO_CHECKER = [];
const CHECKER = [OMEGA_PART.HUB_PUBLISH];
const CAPABILITIES = "/api/capabilities/";

const REVIEW = `/omega-hub/v1/review/submissions/${review.id}`;
const RECHECK = `${REVIEW}/recheck`;

const reviewRow = (status) => ({
  ...review,
  status,
  decision: null,
  published: null,
  reviewer: null,
  reviewStartedAt: null,
  previousVersion: { version: "0.9.0" },
  draftDisclosure: review.confirmedDisclosure,
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

const openReview = () =>
  renderAt(<HubReviewPage />, {
    path: "/dashboard/hub/review/:id",
    at: `/dashboard/hub/review/${review.id}`,
  });

const findRecheck = () =>
  screen.findByRole("button", { name: "Run the checks again" });

const posts = () => calls.filter((call) => call.method === "post");

// Long enough for a request a press would send to go out.
const settle = () => new Promise((resolve) => setTimeout(resolve, 50));

// Everything Tab could stop on, an upper bound on the page's stops.
const STOPS = "a[href], button, input, select, textarea, [tabindex]";

// Presses Tab from the top of the page, the way a keyboard user moves, until
// focus lands on `target` or every stop on the page has had its turn.
async function tabTo(user, target) {
  const stops = document.querySelectorAll(STOPS).length;
  document.activeElement?.blur();
  for (let i = 0; i < stops && document.activeElement !== target; i += 1) {
    await user.tab();
  }
}

beforeEach(() => vi.stubEnv("VITE_OMEGA_CONSOLE", "1"));
afterEach(() => vi.unstubAllEnvs());

describe("Review page with no Hub checker running", () => {
  it.each(["in_review", "check_error"])(
    "keeps 'Run the checks again' in the tab order for a %s submission, says why on focus, and sends nothing",
    async (status) => {
      const user = userEvent.setup();
      serveReview(status, NO_CHECKER);
      openReview();
      expect(await screen.findByText(RECHECK_LINE)).toBeInTheDocument();
      const button = await findRecheck();
      // Off for assistive technology, but not taken out of the tab order.
      expect(button).toHaveAttribute("aria-disabled", "true");
      expect(button).not.toHaveAttribute("disabled");
      expect(button).toHaveAccessibleDescription(RECHECK_LINE);
      // A keyboard user reaches it, and hears the line as its description.
      await tabTo(user, button);
      expect(button).toHaveFocus();
      // Enter, Space and a click do nothing.
      await user.keyboard("{Enter}");
      await user.keyboard(" ");
      fireEvent.click(button);
      await settle();
      expect(posts()).toEqual([]);
      expect(button).toHaveFocus();
      expect(button).toHaveAttribute("aria-disabled", "true");
    },
  );
});

describe("With a Hub checker running", () => {
  it("'Run the checks again' is on, has no line, and a click asks the server", async () => {
    serveReview("in_review", CHECKER, [
      ["post", RECHECK, reviewRow("received")],
    ]);
    openReview();
    const button = await findRecheck();
    await waitFor(() => expect(button).toBeEnabled());
    expect(button).not.toHaveAttribute("aria-disabled");
    expect(button).not.toHaveAttribute("aria-describedby");
    expect(screen.queryByText(RECHECK_LINE)).toBeNull();
    fireEvent.click(button);
    await waitFor(() => expect(posts()).toHaveLength(1));
    expect(posts()[0].path).toBe(RECHECK);
  });

  it("is off but stays in the tab order while a recheck is on its way, with no line, and a second click sends nothing", async () => {
    serveReview("in_review", CHECKER);
    const answer = axios.post.getMockImplementation();
    // The recheck never answers, so it stays on its way.
    axios.post.mockImplementation(() => new Promise(() => {}));
    try {
      openReview();
      const button = await findRecheck();
      await waitFor(() => expect(button).toBeEnabled());
      expect(button).not.toHaveAttribute("aria-disabled");
      fireEvent.click(button);
      await waitFor(() =>
        expect(button).toHaveAttribute("aria-disabled", "true"),
      );
      expect(button).not.toHaveAttribute("disabled");
      expect(axios.post.mock.lastCall?.[0]).toBe(RECHECK);
      const sent = axios.post.mock.calls.length;
      expect(button).not.toHaveAttribute("aria-describedby");
      expect(screen.queryByText(RECHECK_LINE)).toBeNull();
      fireEvent.click(button);
      await settle();
      expect(axios.post.mock.calls).toHaveLength(sent);
    } finally {
      axios.post.mockImplementation(answer);
    }
  });
});
