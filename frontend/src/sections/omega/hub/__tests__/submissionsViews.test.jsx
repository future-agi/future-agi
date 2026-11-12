import React from "react";
import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { OMEGA_PART } from "src/sections/omega/shell/rollout";
import { capabilitiesAnswer } from "src/sections/omega/shell/__tests__/rollout-answer";

import review from "../__fixtures__/review-submission.json";
import meUser from "../__fixtures__/me-user.json";
import HubSubmissionsPage from "../HubSubmissionsPage";
import { respond } from "./mockAxios";
import { renderAt } from "./render";

vi.mock("src/utils/axios", () => import("./mockAxios"));

// Hub: My submissions opens on what needs the person. Before, My
// submissions listed every upload ever made, newest first, so the one thing a
// publisher had to act on sat among dozens of published rows. It now opens on
// what needs the person: uploads still in checks or review, and failed or
// change-requested ones no newer upload has answered. "Published" and "All"
// sit beside it in a quiet segmented control, each with its count once it is
// above zero; All keeps every submission, so nothing is removed. The view is
// URL state (?view=published, ?view=all), so a link opens it and Back
// returns to it. Each view with nothing in it says so in plain words, with
// the next thing to do.

const PAGE = "/dashboard/hub/submissions";
const CHECKER = [OMEGA_PART.HUB_PUBLISH];
const NO_CHECKER = [];
const STALLED_LINE =
  "The automated checks can't run on this server until an administrator starts the Hub checker.";

// The server's words (omega_hub/api/serializers/submissions.py STATUS_TEXT).
const SERVER_TEXT = {
  received: "Received. Automated checks start in a moment.",
  checking:
    "Automated checks are running: manifest, licence, secrets, dependencies, static scan, sandbox run, demo.",
  checks_failed:
    "The automated checks found problems. Fix each finding and publish again.",
  check_error:
    "We could not run the checks. A maintainer has been told and will run them again.",
  in_review:
    "Checks passed. Waiting for a Future AGI maintainer. Usually within 2 business days.",
  changes_requested:
    "A maintainer asked for changes. Answer by publishing a new upload.",
  rejected: "A maintainer did not accept this submission.",
  approved: "Published.",
  withdrawn: "Withdrawn.",
  superseded: "Replaced by your newer upload.",
};

const OPEN = [
  "received",
  "checking",
  "check_error",
  "in_review",
  "checks_failed",
  "changes_requested",
];
const DONE = ["approved", "rejected", "withdrawn", "superseded"];

// A submission as GET /submissions?mine=1 lists it.
const row = (status, { id, name, at = "2026-11-10T10:00:00+00:00" } = {}) => {
  const entry = name || `${status.replace(/_/g, "-")}-entry`;
  return {
    ...review,
    id: id || `${status}-${entry}`,
    publisher: "c-test",
    status,
    statusText: SERVER_TEXT[status],
    name: entry,
    version: "1.0.0",
    hints: { name: entry, version: "1.0.0" },
    createdAt: at,
    updatedAt: at,
    report: null,
    checkSummary: null,
    decision: null,
    confirmedDisclosure: null,
    published: null,
  };
};

// One of every status, each its own entry, newest first as the server sends them.
const EVERY_STATUS = [...OPEN, ...DONE].map((status, index) =>
  row(status, {
    at: new Date(Date.UTC(2026, 10, 10, 20 - index)).toISOString(),
  }),
);

function serve(rows, parts = CHECKER) {
  respond([
    ["get", "/api/capabilities/", capabilitiesAnswer({ parts })],
    ["get", "/omega-hub/v1/me", meUser],
    ["get", "/omega-hub/v1/submissions", rows],
  ]);
}

const open = (search = "") =>
  renderAt(<HubSubmissionsPage />, { at: `${PAGE}${search}` });

const shownStatuses = () =>
  screen
    .queryAllByTestId("hub-submission-row")
    .map((node) => node.getAttribute("data-status"));

const viewTab = (label) =>
  screen.getByRole("tab", { name: new RegExp(`^${label}`) });

const location = () => screen.getByTestId("location").textContent;

beforeEach(() => vi.stubEnv("VITE_OMEGA_CONSOLE", "1"));
afterEach(() => vi.unstubAllEnvs());

describe("My submissions opens on what needs you", () => {
  it("shows only open submissions, in the server's order, under a selected Needs you", async () => {
    serve(EVERY_STATUS);
    open();
    await screen.findAllByTestId("hub-submission-row");
    expect(shownStatuses()).toEqual(OPEN);
    expect(viewTab("Needs you")).toHaveAttribute("aria-selected", "true");
    expect(location()).toBe(PAGE);
  });

  it("a quiet segmented control offers Needs you, Published and All, each with its count", async () => {
    serve(EVERY_STATUS);
    open();
    const control = await screen.findByRole("tablist", { name: "Show" });
    const tabs = within(control).getAllByRole("tab");
    expect(
      tabs.map((tab) => tab.textContent.replace(/\s+/g, " ").trim()),
    ).toEqual(["Needs you 6", "Published 1", "All 10"]);
    expect(viewTab("Needs you")).toHaveAccessibleName("Needs you 6");
  });

  it("a view with nothing in it shows its name alone, never a 0", async () => {
    serve([row("approved")]);
    open();
    await screen.findByRole("tablist", { name: "Show" });
    expect(viewTab("Needs you").textContent.trim()).toBe("Needs you");
    expect(viewTab("Published").textContent).toMatch(/^Published\s*1$/);
  });
});

describe("Published and All", () => {
  it("Published shows only published submissions, and the address says so", async () => {
    serve(EVERY_STATUS);
    open();
    await screen.findAllByTestId("hub-submission-row");
    fireEvent.click(viewTab("Published"));
    await waitFor(() => expect(shownStatuses()).toEqual(["approved"]));
    expect(viewTab("Published")).toHaveAttribute("aria-selected", "true");
    expect(location()).toBe(`${PAGE}?view=published`);
  });

  it("All shows every submission: nothing is removed", async () => {
    serve(EVERY_STATUS);
    open("?view=all");
    await screen.findAllByTestId("hub-submission-row");
    expect(shownStatuses()).toEqual([...OPEN, ...DONE]);
    expect(viewTab("All")).toHaveAttribute("aria-selected", "true");
  });

  it("a ?view=published link opens Published; Needs you takes the view out of the address", async () => {
    serve(EVERY_STATUS);
    open("?view=published");
    await screen.findAllByTestId("hub-submission-row");
    expect(shownStatuses()).toEqual(["approved"]);
    fireEvent.click(viewTab("Needs you"));
    await waitFor(() => expect(shownStatuses()).toEqual(OPEN));
    expect(location()).toBe(PAGE);
  });

  it("an unknown ?view reads Needs you", async () => {
    serve(EVERY_STATUS);
    open("?view=everything");
    await screen.findAllByTestId("hub-submission-row");
    expect(viewTab("Needs you")).toHaveAttribute("aria-selected", "true");
    expect(shownStatuses()).toEqual(OPEN);
  });
});

describe("A failed or change-requested upload a newer upload answered", () => {
  const T1 = "2026-11-10T09:00:00+00:00";
  const T2 = "2026-11-10T10:00:00.000500+00:00";
  const T3 = "2026-11-10T11:00:00+00:00";

  it("leaves Needs you, and stays under All", async () => {
    serve([
      row("approved", { id: "third", name: "csv-answers", at: T3 }),
      row("changes_requested", { id: "second", name: "csv-answers", at: T2 }),
      row("checks_failed", { id: "first", name: "csv-answers", at: T1 }),
      row("changes_requested", { id: "other", name: "other-entry", at: T1 }),
    ]);
    open();
    const rows = await screen.findAllByTestId("hub-submission-row");
    expect(rows).toHaveLength(1);
    expect(rows[0]).toHaveTextContent("c-test/other-entry@1.0.0");
    expect(rows[0]).toHaveAttribute("data-status", "changes_requested");
    fireEvent.click(viewTab("All"));
    await waitFor(() =>
      expect(shownStatuses()).toEqual([
        "approved",
        "changes_requested",
        "checks_failed",
        "changes_requested",
      ]),
    );
  });

  it("a newer upload still in checks answers it too: only the newer one needs you", async () => {
    serve([
      row("in_review", { id: "second", name: "csv-answers", at: T2 }),
      row("checks_failed", { id: "first", name: "csv-answers", at: T1 }),
    ]);
    open();
    await screen.findAllByTestId("hub-submission-row");
    expect(shownStatuses()).toEqual(["in_review"]);
  });

  it("a withdrawn newer upload answers nothing", async () => {
    serve([
      row("withdrawn", { id: "second", name: "csv-answers", at: T3 }),
      row("changes_requested", { id: "first", name: "csv-answers", at: T1 }),
    ]);
    open();
    await screen.findAllByTestId("hub-submission-row");
    expect(shownStatuses()).toEqual(["changes_requested"]);
  });
});

describe("Each view with nothing in it says so", () => {
  const NOT_PLAIN = /\b(?:yet|soon|coming|operator)\b/i;

  it("Needs you, with only published work: nothing needs you, and Publish is the next step", async () => {
    serve([row("approved"), row("withdrawn")]);
    open();
    const empty = await screen.findByTestId("hub-empty");
    expect(empty).toHaveTextContent("Nothing needs you right now");
    expect(empty).toHaveTextContent(
      "No upload is in checks or review, and no maintainer has asked you for changes.",
    );
    expect(
      within(empty).getByRole("link", { name: "Publish" }),
    ).toHaveAttribute("href", "/dashboard/hub/publish");
    expect(empty.textContent).not.toMatch(NOT_PLAIN);
    expect(screen.queryAllByTestId("hub-submission-row")).toHaveLength(0);
    // The published work is one click away.
    expect(viewTab("Published").textContent).toMatch(/^Published\s*1$/);
  });

  it("Published, with work still open: nothing published, and a way back to what needs you", async () => {
    serve([row("in_review")]);
    open("?view=published");
    const empty = await screen.findByTestId("hub-empty");
    expect(empty).toHaveTextContent("Nothing published");
    expect(empty).toHaveTextContent(
      "A submission shows here once a maintainer approves it.",
    );
    expect(empty.textContent).not.toMatch(NOT_PLAIN);
    fireEvent.click(
      within(empty).getByRole("button", { name: "See what needs you" }),
    );
    await waitFor(() => expect(shownStatuses()).toEqual(["in_review"]));
    expect(viewTab("Needs you")).toHaveAttribute("aria-selected", "true");
    expect(location()).toBe(PAGE);
  });

  it("Published, with nothing open either: Publish is the next step", async () => {
    serve([row("withdrawn")]);
    open("?view=published");
    const empty = await screen.findByTestId("hub-empty");
    expect(empty).toHaveTextContent("Nothing published");
    expect(
      within(empty).getByRole("link", { name: "Publish" }),
    ).toHaveAttribute("href", "/dashboard/hub/publish");
  });

  it("with nothing submitted at all, there is no control, only the first-upload state", async () => {
    serve([]);
    open("?view=published");
    expect(
      await screen.findByText("Nothing submitted yet"),
    ).toBeInTheDocument();
    expect(screen.queryByRole("tablist", { name: "Show" })).toBeNull();
  });
});

describe("With no Hub checker, every view keeps the no-checker line", () => {
  it.each([
    ["Needs you", ""],
    ["All", "?view=all"],
  ])("a received row under %s says why it waits", async (_, search) => {
    serve([row("received"), row("approved")], NO_CHECKER);
    open(search);
    const received = await waitFor(() => {
      const found = screen
        .getAllByTestId("hub-submission-row")
        .find((node) => node.getAttribute("data-status") === "received");
      expect(found).toHaveTextContent(STALLED_LINE);
      return found;
    });
    expect(received).not.toHaveTextContent(SERVER_TEXT.received);
    expect(within(received).getByTestId("hub-status-chip")).toHaveTextContent(
      "Received",
    );
  });
});
