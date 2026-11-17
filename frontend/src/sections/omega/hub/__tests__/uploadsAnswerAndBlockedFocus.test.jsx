import React from "react";
import {
  act,
  fireEvent,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { OMEGA_PART } from "src/sections/omega/shell/rollout";
import { capabilitiesAnswer } from "src/sections/omega/shell/__tests__/rollout-answer";

import review from "../__fixtures__/review-submission.json";
import rules from "../__fixtures__/rules.json";
import meMaintainer from "../__fixtures__/me-maintainer.json";
import meUser from "../__fixtures__/me-user.json";
import HubReviewPage from "../HubReviewPage";
import HubSubmissionsPage from "../HubSubmissionsPage";
import axios, { calls, respond } from "./mockAxios";
import { renderAt } from "./render";

vi.mock("src/utils/axios", () => import("./mockAxios"));
// A server confirmed as Cloud: while uploads are off, the notice above My
// submissions is the one sentence that says so.
vi.mock("src/hooks/useDeploymentMode", () => ({
  useDeploymentMode: () => ({
    mode: "cloud",
    isCloud: true,
    isOSS: false,
    isEE: false,
    isLoading: false,
    isSuccess: true,
    isConfirmed: true,
  }),
}));

// My submissions with nothing submitted yet: the list's line depends on
// whether this server can check uploads, and that answer can come after the
// list. The line now waits for it, so a server with no checker never shows,
// not even for one render, the line that offers an upload; nothing else on
// the empty list waits. While uploads are off, the empty list's Publish is
// the outlined button, still a link to the Publish page with the notice's
// sentence as its description; with uploads on it stays the contained one.
//
// The Review page: a natively disabled button leaves the tab order, so its
// reason is never heard. Approve and publish, in the Decision card and in a
// narrow page's decision bar, now stays in the tab order while something
// blocks it: off (aria-disabled), with the blockers line as its description,
// and a click, Enter or Space sends nothing. Once nothing blocks it, a click
// sends the approval as before. Run the checks again keeps keyboard focus
// while a recheck is on its way, and a second press sends nothing.

const CAPABILITIES = "/api/capabilities/";
const CHECKER = [OMEGA_PART.HUB_PUBLISH];
const NO_CHECKER = [];

const LIST = "/dashboard/hub/submissions";
const PUBLISH = "/dashboard/hub/publish";
const OFF = "Uploads are off on this server.";
// The empty list's line while uploads are off, and the one it gives where
// this server can check uploads (the CLI not released).
const FOLLOW = "You will follow your uploads here through checks and review.";
const UPLOAD_LINE =
  "Upload a folder. You will follow it here through checks and review.";

const REVIEW = `/omega-hub/v1/review/submissions/${review.id}`;
const DIFF = `${REVIEW}/diff`;
const DECISION = `${REVIEW}/decision`;
const RECHECK = `${REVIEW}/recheck`;
// What is left to approve while the changes have not loaded: the Decision
// card's line ends with a full stop, the decision bar's does not.
const HELD_BACK = "To approve: the changes must load";

// A submission the submitter's list shows under Published, so the default
// view (Needs you) is empty and ends in Publish.
const approved = {
  ...review,
  id: "approved-one",
  publisher: "c-test",
  status: "approved",
  statusText: "Published.",
  name: "csv-answers",
  version: "1.0.0",
  hints: { name: "csv-answers", version: "1.0.0" },
  published: null,
};

const inReview = {
  ...review,
  status: "in_review",
  decision: null,
  published: null,
  reviewer: null,
  reviewStartedAt: null,
  previousVersion: { version: "0.9.0" },
  draftDisclosure: review.confirmedDisclosure,
};

function serveList(rows, parts) {
  respond([
    ["get", CAPABILITIES, capabilitiesAnswer({ parts })],
    ["get", "/omega-hub/v1/me", meUser],
    ["get", "/omega-hub/v1/submissions", rows],
  ]);
}

function serveReview() {
  respond([
    ["get", CAPABILITIES, capabilitiesAnswer({ parts: CHECKER })],
    ["get", "/omega-hub/v1/me", meMaintainer],
    ["get", "/omega-hub/v1/rules", rules],
    ["get", REVIEW, inReview],
    ["get", DIFF, { diff: null }],
    ["get", new RegExp(`^${REVIEW}/files/`), "x = 1\n"],
    ["post", DECISION, { status: "approved" }],
  ]);
}

const openList = () => renderAt(<HubSubmissionsPage />, { at: LIST });

const openReview = () =>
  renderAt(<HubReviewPage />, {
    path: "/dashboard/hub/review/:id",
    at: `/dashboard/hub/review/${review.id}`,
  });

const location = () => screen.getByTestId("location").textContent;

const posts = (path) =>
  calls.filter((call) => call.method === "post" && call.path === path);

// Long enough for an answer, or a request a press would send, to arrive.
const settle = () => new Promise((resolve) => setTimeout(resolve, 50));

// Holds every /api/capabilities/ answer until `release`.
function holdCapabilities() {
  const answer = axios.get.getMockImplementation();
  let open;
  const held = new Promise((resolve) => {
    open = resolve;
  });
  axios.get.mockImplementation((url, ...rest) =>
    String(url).includes(CAPABILITIES)
      ? held.then(() => answer(url, ...rest))
      : answer(url, ...rest),
  );
  return {
    release: () => act(async () => open()),
    restore: () => axios.get.mockImplementation(answer),
  };
}

// Holds the changes' answer until `release`, so the changes are all that is
// left to approve.
function holdChanges() {
  const answer = axios.get.getMockImplementation();
  let finish;
  axios.get.mockImplementation((url, ...rest) =>
    String(url) === DIFF
      ? new Promise((resolve) => {
          finish = resolve;
        })
      : answer(url, ...rest),
  );
  return {
    release: async () => {
      await waitFor(() => expect(finish).toBeTypeOf("function"));
      await act(async () => finish({ data: { diff: null }, status: 200 }));
    },
    restore: () => axios.get.mockImplementation(answer),
  };
}

// Every text matching `pattern` the page held while the test ran, from the
// moment this is called until the returned stop: a line drawn for one render
// and then replaced still counts.
function watchFor(pattern) {
  const seen = [];
  const note = (text) => {
    if (text && pattern.test(text)) seen.push(text);
  };
  const read = (records) => {
    for (const record of records) {
      note(record.oldValue);
      note(record.target.textContent);
      record.addedNodes.forEach((node) => note(node.textContent));
      record.removedNodes.forEach((node) => note(node.textContent));
    }
  };
  const observer = new MutationObserver(read);
  observer.observe(document.body, {
    subtree: true,
    childList: true,
    characterData: true,
    characterDataOldValue: true,
  });
  return () => {
    read(observer.takeRecords());
    observer.disconnect();
    return seen;
  };
}

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

// Answers every flag group Yes, ticks every checklist item and chooses
// Approve and publish: with the changes held back, they are all that is left
// to approve.
async function everythingButTheChanges() {
  await screen.findAllByTestId("hub-checklist-item");
  for (const box of screen.getAllByTestId("hub-flag-ack")) fireEvent.click(box);
  for (const box of screen.getAllByTestId("hub-checklist-item"))
    fireEvent.click(box);
  fireEvent.click(screen.getByTestId("hub-decision-approve-tab"));
}

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

beforeEach(() => vi.stubEnv("VITE_OMEGA_CONSOLE", "1"));
afterEach(() => vi.unstubAllEnvs());

describe("My submissions with nothing submitted yet: the line waits for the uploads answer", () => {
  it("never shows 'Upload a folder.' on a server with no checker, not even before the answer, then gives the line that offers no upload", async () => {
    serveList([], NO_CHECKER);
    const stop = watchFor(/Upload a folder\./);
    const capabilities = holdCapabilities();
    let seen;
    try {
      openList();
      const empty = await screen.findByTestId("hub-empty");
      // Nothing else on the empty list waits: its title and Publish are there.
      expect(empty).toHaveTextContent("Nothing submitted yet");
      expect(
        within(empty).getByRole("link", { name: "Publish" }),
      ).toHaveAttribute("href", PUBLISH);
      await settle();
      // No line yet: neither the one that offers an upload nor the other.
      expect(empty).not.toHaveTextContent(/upload a folder/i);
      expect(empty).not.toHaveTextContent(FOLLOW);
      await capabilities.release();
      await waitFor(() =>
        expect(screen.getByTestId("hub-empty")).toHaveTextContent(FOLLOW),
      );
      expect(
        screen.getByTestId("hub-submissions-uploads-off"),
      ).toBeInTheDocument();
    } finally {
      capabilities.restore();
      seen = stop();
    }
    expect(seen).toEqual([]);
  });

  it("draws no line before the answer where this server can check uploads, then names the way to upload", async () => {
    serveList([], CHECKER);
    const capabilities = holdCapabilities();
    try {
      openList();
      const empty = await screen.findByTestId("hub-empty");
      expect(empty).toHaveTextContent("Nothing submitted yet");
      expect(
        within(empty).getByRole("link", { name: "Publish" }),
      ).toHaveAttribute("href", PUBLISH);
      await settle();
      expect(empty).not.toHaveTextContent(/upload a folder/i);
      await capabilities.release();
      expect(
        await within(screen.getByTestId("hub-empty")).findByText(UPLOAD_LINE),
      ).toBeInTheDocument();
      expect(screen.queryByTestId("hub-submissions-uploads-off")).toBeNull();
    } finally {
      capabilities.restore();
    }
  });
});

describe("The empty list's Publish while uploads are off", () => {
  it("is the outlined button, still a link to the Publish page, with the notice's sentence as its description", async () => {
    serveList([], NO_CHECKER);
    openList();
    await screen.findByTestId("hub-submissions-uploads-off");
    const empty = await screen.findByTestId("hub-empty");
    const publish = within(empty).getByRole("link", { name: "Publish" });
    expect(publish).toHaveClass("MuiButton-outlined");
    expect(publish).not.toHaveClass("MuiButton-contained");
    expect(publish).toHaveAttribute("href", PUBLISH);
    expect(publish).not.toHaveAttribute("aria-disabled");
    expect(publish).toHaveAccessibleDescription(OFF);
    fireEvent.click(publish);
    await waitFor(() => expect(location()).toBe(PUBLISH));
  });

  it("is outlined under an empty view too, with the same description", async () => {
    serveList([approved], NO_CHECKER);
    openList();
    await screen.findByTestId("hub-submissions-uploads-off");
    const empty = await screen.findByTestId("hub-empty");
    expect(empty).toHaveTextContent("Nothing needs you right now");
    const publish = within(empty).getByRole("link", { name: "Publish" });
    expect(publish).toHaveClass("MuiButton-outlined");
    expect(publish).not.toHaveClass("MuiButton-contained");
    expect(publish).toHaveAttribute("href", PUBLISH);
    expect(publish).toHaveAccessibleDescription(OFF);
  });

  it("stays the contained button, with no description, where this server can check uploads", async () => {
    serveList([], CHECKER);
    openList();
    const empty = await screen.findByTestId("hub-empty");
    await within(empty).findByText(UPLOAD_LINE);
    const publish = within(empty).getByRole("link", { name: "Publish" });
    expect(publish).toHaveClass("MuiButton-contained");
    expect(publish).not.toHaveClass("MuiButton-outlined");
    expect(publish).toHaveAttribute("href", PUBLISH);
    expect(publish).not.toHaveAttribute("aria-describedby");
  });
});

describe("Approve and publish while something blocks it", () => {
  it("stays in the tab order, off, with the blockers line as its description, and sends nothing; once nothing blocks it, a click sends the approval", async () => {
    const user = userEvent.setup();
    serveReview();
    const changes = holdChanges();
    try {
      openReview();
      await everythingButTheChanges();
      const approve = screen.getByTestId("hub-decision-submit");
      expect(approve).toHaveTextContent("Approve and publish");
      // Off for assistive technology, but not taken out of the tab order.
      expect(approve).toHaveAttribute("aria-disabled", "true");
      expect(approve).not.toHaveAttribute("disabled");
      expect(approve).toHaveAccessibleDescription(`${HELD_BACK}.`);
      // A keyboard user reaches it, and hears the blockers as its description.
      await tabTo(user, approve);
      expect(approve).toHaveFocus();
      // Enter, Space and a click send nothing.
      await user.keyboard("{Enter}");
      await user.keyboard(" ");
      fireEvent.click(approve);
      await settle();
      expect(posts(DECISION)).toEqual([]);
      expect(approve).toHaveFocus();
      expect(approve).toHaveAttribute("aria-disabled", "true");
      // The changes load: nothing blocks it, and a click sends the approval.
      await changes.release();
      await waitFor(() => expect(approve).not.toHaveAttribute("aria-disabled"));
      expect(approve).toBeEnabled();
      expect(approve).not.toHaveAttribute("aria-describedby");
      fireEvent.click(approve);
      await waitFor(() => expect(posts(DECISION)).toHaveLength(1));
      expect(posts(DECISION)[0].data.decision).toBe("approve");
    } finally {
      changes.restore();
    }
  });

  it("on a narrow page, the decision bar's Approve and publish stays in the tab order, off, with the bar's line as its description, and sends nothing until nothing blocks it", async () => {
    const user = userEvent.setup();
    const restore = narrowWindow();
    serveReview();
    const changes = holdChanges();
    try {
      openReview();
      await everythingButTheChanges();
      const bar = screen.getByTestId("hub-decision-bar");
      await waitFor(() => expect(bar).toHaveTextContent(HELD_BACK));
      const approve = within(bar).getByRole("button", {
        name: "Approve and publish",
      });
      expect(approve).toHaveAttribute("aria-disabled", "true");
      expect(approve).not.toHaveAttribute("disabled");
      expect(approve).toHaveAccessibleDescription(HELD_BACK);
      await tabTo(user, approve);
      expect(approve).toHaveFocus();
      await user.keyboard("{Enter}");
      await user.keyboard(" ");
      fireEvent.click(approve);
      await settle();
      expect(posts(DECISION)).toEqual([]);
      expect(approve).toHaveFocus();
      expect(approve).toHaveAttribute("aria-disabled", "true");
      await changes.release();
      await waitFor(() => expect(bar).toHaveTextContent("Ready to approve"));
      expect(approve).not.toHaveAttribute("aria-disabled");
      expect(approve).toBeEnabled();
      fireEvent.click(approve);
      await waitFor(() => expect(posts(DECISION)).toHaveLength(1));
      expect(posts(DECISION)[0].data.decision).toBe("approve");
    } finally {
      changes.restore();
      restore();
    }
  });
});

describe("Run the checks again while a recheck is on its way", () => {
  it("keeps keyboard focus on the button, off, and a second press sends nothing", async () => {
    const user = userEvent.setup();
    serveReview();
    const answer = axios.post.getMockImplementation();
    const sent = [];
    // The recheck never answers, so it stays on its way.
    axios.post.mockImplementation((url) => {
      sent.push(String(url));
      return new Promise(() => {});
    });
    try {
      openReview();
      const button = await screen.findByRole("button", {
        name: "Run the checks again",
      });
      await waitFor(() => expect(button).toBeEnabled());
      expect(button).not.toHaveAttribute("aria-disabled");
      await tabTo(user, button);
      expect(button).toHaveFocus();
      await user.keyboard("{Enter}");
      await waitFor(() => expect(sent).toEqual([RECHECK]));
      // On its way: off, but still where the keyboard user left it.
      await waitFor(() =>
        expect(button).toHaveAttribute("aria-disabled", "true"),
      );
      expect(button).not.toHaveAttribute("disabled");
      expect(button).not.toHaveAttribute("aria-describedby");
      expect(document.activeElement).toBe(button);
      // A second press, by Enter, Space or a click, sends nothing.
      await user.keyboard("{Enter}");
      await user.keyboard(" ");
      fireEvent.click(button);
      await settle();
      expect(sent).toEqual([RECHECK]);
      expect(document.activeElement).toBe(button);
    } finally {
      axios.post.mockImplementation(answer);
    }
  });
});
