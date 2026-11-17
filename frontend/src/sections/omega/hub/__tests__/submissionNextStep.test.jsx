import React from "react";
import {
  act,
  fireEvent,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { OMEGA_PART } from "src/sections/omega/shell/rollout";
import { capabilitiesAnswer } from "src/sections/omega/shell/__tests__/rollout-answer";
import { palette } from "src/theme/palette";

import review from "../__fixtures__/review-submission.json";
import rules from "../__fixtures__/rules.json";
import meUser from "../__fixtures__/me-user.json";
import HubPublishPage from "../HubPublishPage";
import HubSubmissionPage from "../HubSubmissionPage";
import HubSubmissionsPage from "../HubSubmissionsPage";
import { formatStamp } from "../lib/decision";
import axios, { respond } from "./mockAxios";
import { renderAt } from "./render";

vi.mock("src/utils/axios", () => import("./mockAxios"));
// The server's mode as useDeploymentMode() answers it: confirmed self-hosted,
// where an administrator starts the Hub checker, unless a case says Cloud.
// On Cloud, and while the server's mode is not known, an upload the checks
// cannot reach names no next step (uploadsOffReaders.test.jsx).
const { server, SELF_HOSTED } = vi.hoisted(() => {
  const selfHosted = {
    mode: "oss",
    isCloud: false,
    isOSS: true,
    isEE: false,
    isLoading: false,
    isSuccess: true,
    isConfirmed: true,
  };
  return { server: { deployment: selfHosted }, SELF_HOSTED: selfHosted };
});
vi.mock("src/hooks/useDeploymentMode", () => ({
  useDeploymentMode: () => server.deployment,
}));
const CLOUD = { ...SELF_HOSTED, mode: "cloud", isCloud: true, isOSS: false };

// A publisher follows an upload the way a pull request's checks or a
// deployment's status are followed. Each row of My submissions says who moves
// it next: "Your turn" with a link to upload a new version, or who it waits
// on (the checks, a maintainer, or, on a self-hosted server, an administrator
// who has to start the Hub checker). That link is drawn in the app's link
// colour and underlined, and while this server runs no Hub checker it carries
// the page's one notice, above the list, that uploads are off. The rows are
// divided by the theme's divider line. The reference is the row's link, and
// its last update shows the exact moment with its time zone on hover and on
// keyboard focus. The list loads as rows, and a list that did not load says
// which. A submission's page puts the next action at the top right, lists
// each automated check with its state and time (a failed one links down to
// its findings and their fixes), gives its history with each moment's time
// zone and the time since the step before, and offers its whole id to copy
// on a row of facts that wraps rather than cutting it. A failed or
// change-requested upload
// that a newer upload of the same entry answered says so on its page, as its
// row does, links to that upload, and offers no action at the top right;
// until My submissions arrives the page does not yet say whose turn it is.
// The Publish page says, under what happens after a submit, where an upload
// is followed.

const LIST = "/dashboard/hub/submissions";
const CHECKER = [OMEGA_PART.HUB_PUBLISH];
const NO_CHECKER = [];

// The server's words (omega_hub/api/serializers/submissions.py STATUS_TEXT).
const SERVER_TEXT = {
  received: "Received. Automated checks start in a moment.",
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
};

// A submission as the submitter's API sends it: the full report only with a
// failed upload, the public check summary otherwise.
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
    published: status === "approved" ? `c-test/${entry}@1.0.0` : null,
  };
};

function serveList(rows, parts = CHECKER) {
  respond([
    ["get", "/api/capabilities/", capabilitiesAnswer({ parts })],
    ["get", "/omega-hub/v1/me", meUser],
    ["get", "/omega-hub/v1/submissions", rows],
  ]);
}

function serveOne(submission, parts = CHECKER) {
  respond([
    ["get", "/api/capabilities/", capabilitiesAnswer({ parts })],
    ["get", "/omega-hub/v1/me", meUser],
    ["get", `/omega-hub/v1/submissions/${submission.id}`, submission],
  ]);
}

// One submission, and My submissions beside it.
function serveOneWithList(submission, rows, parts = CHECKER) {
  respond([
    ["get", "/api/capabilities/", capabilitiesAnswer({ parts })],
    ["get", "/omega-hub/v1/me", meUser],
    ["get", `/omega-hub/v1/submissions/${submission.id}`, submission],
    ["get", "/omega-hub/v1/submissions", rows],
  ]);
}

const openList = (search = "") =>
  renderAt(<HubSubmissionsPage />, { at: `${LIST}${search}` });

const openOne = (submission) =>
  renderAt(<HubSubmissionPage />, {
    path: "/dashboard/hub/submissions/:id",
    at: `/dashboard/hub/submissions/${submission.id}`,
  });

const rowOf = (status) =>
  screen
    .getAllByTestId("hub-submission-row")
    .find((node) => node.getAttribute("data-status") === status);

const nextOf = (status) =>
  within(rowOf(status)).getByTestId("hub-submission-next");

const cssRgb = (hex) => {
  const [r, g, b] = hex.match(/[a-f\d]{2}/gi).map((n) => parseInt(n, 16));
  return `rgb(${r}, ${g}, ${b})`;
};

const writeText = vi.fn(() => Promise.resolve());

beforeEach(() => {
  vi.stubEnv("VITE_OMEGA_CONSOLE", "1");
  server.deployment = SELF_HOSTED;
  writeText.mockClear();
  Object.defineProperty(navigator, "clipboard", {
    value: { writeText },
    configurable: true,
  });
});
afterEach(() => vi.unstubAllEnvs());

describe("The Publish page says where an upload is followed", () => {
  it("under what happens after you submit: the upload's own page, and My submissions", async () => {
    respond([
      ["get", "/api/capabilities/", capabilitiesAnswer({ parts: CHECKER })],
      ["get", "/omega-hub/v1/rules", rules],
      ["get", "/omega-hub/v1/me", meUser],
    ]);
    renderAt(<HubPublishPage />);
    const follow = await screen.findByTestId("hub-publish-follow");
    expect(follow).toHaveTextContent(
      "After you submit, the upload's own page follows it through each step and says what to do next. My submissions lists every upload.",
    );
    expect(
      within(follow).getByRole("link", { name: "My submissions" }),
    ).toHaveAttribute("href", "/dashboard/hub/submissions");
    const pipeline = screen.getByTestId("hub-publish-pipeline");
    expect(
      pipeline.compareDocumentPosition(follow) &
        Node.DOCUMENT_POSITION_FOLLOWING,
    ).toBeTruthy();
  });
});

describe("Each row of My submissions says who moves it next", () => {
  const EVERY = [
    "checks_failed",
    "changes_requested",
    "received",
    "check_error",
    "in_review",
    "approved",
    "rejected",
  ].map((status, index) =>
    row(status, {
      at: new Date(Date.UTC(2026, 10, 10, 20 - index)).toISOString(),
    }),
  );

  it("an upload you can act on says it is your turn, with a link to upload a new version", async () => {
    serveList(EVERY);
    openList("?view=all");
    await screen.findAllByTestId("hub-submission-row");
    for (const status of ["checks_failed", "changes_requested"]) {
      const next = nextOf(status);
      expect(next).toHaveTextContent("Your turn");
      const entry = `c-test/${status.replace(/_/g, "-")}-entry`;
      expect(
        within(next).getByRole("link", {
          name: `Upload a new version of ${entry}`,
        }),
      ).toHaveAttribute("href", "/dashboard/hub/publish");
    }
  });

  it("an upload in checks or review names who it waits on, with nothing for you to do", async () => {
    serveList(EVERY);
    openList("?view=all");
    await screen.findAllByTestId("hub-submission-row");
    await waitFor(() =>
      expect(nextOf("received")).toHaveTextContent("Waiting on the checks"),
    );
    expect(nextOf("in_review")).toHaveTextContent("Waiting on a maintainer");
    // A maintainer runs checks that could not run again (the server's words).
    expect(nextOf("check_error")).toHaveTextContent("Waiting on a maintainer");
    for (const status of ["received", "in_review", "check_error"])
      expect(within(nextOf(status)).queryByRole("link")).toBeNull();
  });

  it("with no Hub checker on a self-hosted server, an upload the checks cannot reach waits on an administrator", async () => {
    serveList(EVERY, NO_CHECKER);
    openList("?view=all");
    await screen.findAllByTestId("hub-submission-row");
    await waitFor(() =>
      expect(nextOf("received")).toHaveTextContent(
        "Waiting on an administrator",
      ),
    );
    expect(nextOf("check_error")).toHaveTextContent(
      "Waiting on an administrator",
    );
    expect(nextOf("in_review")).toHaveTextContent("Waiting on a maintainer");
  });

  it("a finished upload has nothing to do; a published one links to its entry", async () => {
    serveList(EVERY);
    openList("?view=all");
    await screen.findAllByTestId("hub-submission-row");
    expect(nextOf("approved")).toHaveTextContent("Nothing to do");
    expect(
      within(nextOf("approved")).getByRole("link", {
        name: "Open c-test/approved-entry on the Hub",
      }),
    ).toHaveAttribute("href", "/dashboard/hub/c-test/approved-entry");
    expect(nextOf("rejected")).toHaveTextContent("Nothing to do");
    expect(within(nextOf("rejected")).queryByRole("link")).toBeNull();
  });

  it("a failed upload a newer upload answered says so, and offers no upload", async () => {
    serveList([
      row("in_review", {
        id: "newer",
        name: "csv-answers",
        at: "2026-11-10T11:00:00+00:00",
      }),
      row("checks_failed", {
        id: "older",
        name: "csv-answers",
        at: "2026-11-10T09:00:00+00:00",
      }),
    ]);
    openList("?view=all");
    await screen.findAllByTestId("hub-submission-row");
    expect(nextOf("checks_failed")).toHaveTextContent(
      "Answered by a newer upload",
    );
    expect(within(nextOf("checks_failed")).queryByRole("link")).toBeNull();
  });
});

describe("My submissions in the theme's own colours", () => {
  const UPLOAD = "Upload a new version of c-test/checks-failed-entry";
  const OFF = "Uploads are off on this server.";

  it("divides the rows with the theme's divider line, the colour of the list's own frame, never the text colour", async () => {
    serveList([
      row("checks_failed", { id: "first", name: "first-entry" }),
      row("changes_requested", { id: "second", name: "second-entry" }),
    ]);
    openList();
    const [first, second] = await screen.findAllByTestId("hub-submission-row");
    const { divider, text } = palette("light");
    const frame = getComputedStyle(screen.getByTestId("hub-submissions-list"));
    const line = getComputedStyle(second);
    expect(line.borderTopStyle).toBe("solid");
    expect(line.borderTopWidth).toBe("1px");
    expect(line.borderTopColor).toBe(cssRgb(divider));
    expect(line.borderTopColor).toBe(frame.borderTopColor);
    expect(line.borderTopColor).not.toBe(cssRgb(text.primary));
    // The list's frame is the first row's line.
    expect(getComputedStyle(first).borderTopWidth).toMatch(/^0(px)?$/);
  });

  it("draws Upload a new version in the app's link colour, underlined, so it reads as a link and not as body text", async () => {
    serveList([row("checks_failed", { id: "one" })]);
    openList();
    await screen.findAllByTestId("hub-submission-row");
    const link = within(nextOf("checks_failed")).getByRole("link", {
      name: UPLOAD,
    });
    const style = getComputedStyle(link);
    expect(style.color).toBe(cssRgb(palette("light").text.link));
    expect(style.textDecorationLine || style.textDecoration).toMatch(
      /underline/,
    );
  });

  it("with no Hub checker on this server (Cloud), says once above the list that uploads are off, and Upload a new version carries that sentence", async () => {
    server.deployment = CLOUD;
    serveList([row("checks_failed", { id: "one" })], NO_CHECKER);
    openList();
    await screen.findAllByTestId("hub-submission-row");
    const next = nextOf("checks_failed");
    const off = await screen.findByText(OFF);
    const link = within(next).getByRole("link", { name: UPLOAD });
    expect(link).toHaveAttribute("href", "/dashboard/hub/publish");
    expect(link).toHaveAccessibleDescription(OFF);
    // One notice, before the list, and not again in the row.
    expect(screen.getAllByText(OFF)).toHaveLength(1);
    expect(next).not.toHaveTextContent(OFF);
    const list = screen.getByTestId("hub-submissions-list");
    expect(
      off.compareDocumentPosition(list) & Node.DOCUMENT_POSITION_FOLLOWING,
    ).toBeTruthy();
  });

  it("says nothing about uploads where this server can check them", async () => {
    serveList([row("checks_failed", { id: "one" })]);
    openList();
    await screen.findAllByTestId("hub-submission-row");
    const link = within(nextOf("checks_failed")).getByRole("link", {
      name: UPLOAD,
    });
    expect(nextOf("checks_failed")).not.toHaveTextContent(OFF);
    expect(screen.queryByText(OFF)).toBeNull();
    expect(link).not.toHaveAttribute("aria-describedby");
  });
});

describe("A row's link and its last update", () => {
  it("the reference is the row's link, and the last update shows the exact moment with its time zone on hover and on keyboard focus", async () => {
    const at = new Date(Date.now() - 2 * 60 * 60 * 1000).toISOString();
    serveList([row("checks_failed", { id: "one", at })]);
    openList();
    const [item] = await screen.findAllByTestId("hub-submission-row");
    const link = within(item).getByRole("link", {
      name: "c-test/checks-failed-entry@1.0.0",
    });
    expect(link).toHaveAttribute("href", "/dashboard/hub/submissions/one");
    const time = within(item).getByText("2 h ago");
    expect(link).not.toContainElement(time);
    const stamp = formatStamp(at);
    expect(stamp).toMatch(/\d{4},? (at )?\d{2}:\d{2} \S+/);
    expect(time).toHaveAttribute("tabindex", "0");
    expect(time).toHaveAccessibleName(`Updated 2 h ago, ${stamp}`);
    fireEvent.mouseOver(time);
    expect((await screen.findByRole("tooltip")).textContent).toBe(
      `Updated ${stamp}`,
    );
    fireEvent.mouseLeave(time);
    await waitFor(() => expect(screen.queryByRole("tooltip")).toBeNull());
    act(() => time.focus());
    expect((await screen.findByRole("tooltip")).textContent).toBe(
      `Updated ${stamp}`,
    );
  });
});

describe("My submissions while it loads, and when it does not", () => {
  it("shows the list's own rows while it loads, not cards", async () => {
    serveList([]);
    const answer = axios.get.getMockImplementation();
    // The list never arrives; every other call is answered.
    axios.get.mockImplementation((url, ...rest) =>
      String(url).includes("/omega-hub/v1/submissions")
        ? new Promise(() => {})
        : answer(url, ...rest),
    );
    try {
      openList();
      const placeholders = await screen.findAllByTestId(
        "hub-submission-row-loading",
      );
      expect(placeholders).toHaveLength(4);
      expect(screen.getByTestId("hub-loading")).toHaveAttribute(
        "aria-busy",
        "true",
      );
    } finally {
      axios.get.mockImplementation(answer);
    }
  });

  it("a list that did not load says it was your submissions, with Try again", async () => {
    respond([
      ["get", "/api/capabilities/", capabilitiesAnswer({ parts: CHECKER })],
      ["get", "/omega-hub/v1/me", meUser],
      ["get", "/omega-hub/v1/submissions", {}, 502],
    ]);
    openList();
    // A gateway error is asked once more before the page says so.
    const error = await screen.findByTestId("hub-error", {}, { timeout: 5000 });
    expect(error).toHaveTextContent("Your submissions did not load");
    expect(
      within(error).getByRole("button", { name: "Try again" }),
    ).toBeInTheDocument();
  });
});

describe("A submission's page: when, and what happened", () => {
  // Published through the Omega command line, with the public check summary.
  const published = { ...review, report: null };

  it("says when it was submitted, with the time zone", async () => {
    serveOne(published);
    openOne(published);
    const page = await screen.findByTestId("hub-submission");
    expect(page).toHaveTextContent(
      `Submitted ${formatStamp(review.createdAt)}`,
    );
  });

  it("gives each event of its history with its moment, its time zone and the time since the step before", async () => {
    serveOne(published);
    openOne(published);
    const timeline = await screen.findByTestId("hub-timeline");
    const events = within(timeline).getAllByRole("listitem");
    expect(events).toHaveLength(review.timeline.length);
    review.timeline.forEach((event, index) =>
      expect(events[index]).toHaveTextContent(formatStamp(event.at)),
    );
    expect(events[0]).toHaveTextContent("Received");
    expect(events[1]).toHaveTextContent("1 s later");
    expect(events[2]).toHaveTextContent("Automated checks passed");
    expect(events[2]).toHaveTextContent("7 s later");
    expect(events[3]).toHaveTextContent("Approved and published");
    expect(events[3]).toHaveTextContent("21 min later");
  });

  it("lists each automated check with its state, and the sandbox run with its time", async () => {
    serveOne(published);
    openOne(published);
    const section = await screen.findByTestId("hub-check-summary");
    const steps = within(section).getAllByTestId("hub-check-step");
    expect(steps).toHaveLength(10);
    expect(section).toHaveTextContent(
      "10 checks: 2 flagged for the reviewer, 8 passed.",
    );
    const run = steps.find(
      (step) => step.getAttribute("data-step") === "sandbox-run",
    );
    expect(run).toHaveTextContent("Sandbox run");
    expect(run).toHaveTextContent("Passed");
    expect(run).toHaveTextContent("6.6 s");
    const scan = steps.find(
      (step) => step.getAttribute("data-step") === "static-scan",
    );
    expect(scan).toHaveTextContent("Flagged for the reviewer");
  });

  it("offers its id to copy", async () => {
    serveOne(published);
    openOne(published);
    const button = await screen.findByRole("button", {
      name: "Copy the submission id",
    });
    expect(screen.getByTestId("hub-submission-status")).toHaveTextContent(
      review.id,
    );
    fireEvent.click(button);
    await waitFor(() => expect(writeText).toHaveBeenCalledWith(review.id));
  });

  it("shows the whole id, never cut, on a row of facts that wraps", async () => {
    serveOne(published);
    openOne(published);
    const status = await screen.findByTestId("hub-submission-status");
    const id = within(status).getByText(review.id);
    expect(id).toHaveAttribute("title", review.id);
    const style = getComputedStyle(id);
    expect(style.textOverflow).not.toBe("ellipsis");
    expect(style.whiteSpace).not.toBe("nowrap");
    expect(style.overflow).not.toBe("hidden");
    // Only a phone-width page wraps it.
    expect(style.overflowWrap).toBe("anywhere");
    const facts = getComputedStyle(id.closest("dl"));
    expect(facts.display).toBe("flex");
    expect(facts.flexWrap).toBe("wrap");
  });
});

describe("A submission's page before its manifest is read", () => {
  // The server sends no name or version for it, and the upload gave no
  // hints either. Whether or not a checker runs, its title says only what
  // is true: the manifest is not read yet. Nothing claims it is being read.
  const unread = {
    ...row("received", { id: "unread-one" }),
    name: null,
    version: null,
    hints: { name: null, version: null },
  };

  it.each([
    ["runs a Hub checker", CHECKER, "Waiting on the checks"],
    ["runs no Hub checker", NO_CHECKER, "Waiting on an administrator"],
  ])(
    "on a server that %s, its title names the publisher and says the manifest is not read yet",
    async (_, parts, waitsOn) => {
      serveOne(unread, parts);
      openOne(unread);
      const next = await screen.findByTestId("hub-next-step");
      await waitFor(() => expect(next).toHaveTextContent(waitsOn));
      const heading = screen.getByRole("heading", { level: 1 });
      expect(heading.textContent).toBe("c-test/… (manifest not read yet)");
      expect(document.body).not.toHaveTextContent(/reading the manifest/i);
    },
  );

  it("once its checks have answered without reading it, its title names the publisher and promises no read", async () => {
    const failed = {
      ...row("checks_failed", { id: "unread-failed" }),
      name: null,
      version: null,
      hints: { name: null, version: null },
    };
    serveOne(failed, CHECKER);
    openOne(failed);
    await waitFor(() =>
      expect(screen.getByRole("heading", { level: 1 }).textContent).toBe(
        "c-test/…",
      ),
    );
    expect(document.body).not.toHaveTextContent(/manifest not read/i);
  });
});

describe("A submission's page: the next action", () => {
  const FAILED_REPORT = {
    steps: [
      { id: "unpack", status: "pass", ms: 2, findings: [] },
      {
        id: "manifest",
        status: "fail",
        ms: 12,
        findings: [
          {
            id: "manifest-cover",
            severity: "block",
            rule: "manifest",
            file: "omega.hub.json",
            line: 4,
            message: "cover.from is not a color the Hub knows",
            fix: "pick a color from the list",
          },
        ],
      },
      { id: "licence", status: "pass", ms: 1, findings: [] },
      {
        id: "sandbox-run",
        status: "skipped",
        ms: 0,
        findings: [],
        network: "none",
        why: "an earlier step failed",
      },
    ],
  };
  const failed = {
    ...row("checks_failed", { id: "failed-one" }),
    report: FAILED_REPORT,
    checkSummary: {
      steps: {
        unpack: "pass",
        manifest: "fail",
        licence: "pass",
        "sandbox-run": "skipped",
      },
      flags: {},
      flagsReviewed: 0,
      checkerVersion: "0.1.0",
      runMs: 0,
      network: "none",
    },
    timeline: review.timeline.slice(0, 2),
  };
  const CHANGES = {
    decision: "request_changes",
    reason: "Name every host it calls in the disclosure.",
    notes: [],
    decidedAt: "2026-11-10T10:30:00+00:00",
  };

  it("a failed upload: your turn, with Upload a new version at the top right and under the findings", async () => {
    serveOne(failed);
    openOne(failed);
    const action = await screen.findByTestId("hub-next-action");
    expect(action).toHaveTextContent("Upload a new version");
    expect(action).toHaveAttribute("href", "/dashboard/hub/publish");
    expect(screen.getByTestId("hub-next-step")).toHaveTextContent("Your turn");
    const findings = screen.getByTestId("hub-findings");
    expect(within(findings).getByTestId("hub-resubmit")).toHaveAttribute(
      "href",
      "/dashboard/hub/publish",
    );
    expect(screen.getAllByTestId("hub-resubmit")).toHaveLength(1);
  });

  it("a failed upload: each check with its state and time, and a failed one links down to its findings and their fixes", async () => {
    serveOne(failed);
    openOne(failed);
    const section = await screen.findByTestId("hub-check-summary");
    expect(section).toHaveTextContent(
      "4 checks: 1 failed, 2 passed, 1 skipped.",
    );
    const steps = within(section).getAllByTestId("hub-check-step");
    expect(steps.map((step) => step.getAttribute("data-step"))).toEqual([
      "unpack",
      "manifest",
      "licence",
      "sandbox-run",
    ]);
    expect(steps[1]).toHaveTextContent("Manifest and format");
    expect(steps[1]).toHaveTextContent("Failed");
    expect(steps[1]).toHaveTextContent("12 ms");
    const link = within(steps[1]).getByRole("link", { name: "See 1 finding" });
    expect(link).toHaveAttribute("href", "#hub-check-manifest");
    const target = document.getElementById("hub-check-manifest");
    expect(screen.getByTestId("hub-findings")).toContainElement(target);
    expect(target).toHaveTextContent("Fix: pick a color from the list");
    expect(steps[3]).toHaveTextContent("Skipped: an earlier step failed");
    expect(steps[3]).not.toHaveTextContent("0 ms");
  });

  it("changes requested: your turn, with the same action at the top right and the decision's own", async () => {
    const changes = {
      ...row("changes_requested", { id: "changes-one" }),
      decision: CHANGES,
    };
    serveOne(changes);
    openOne(changes);
    const action = await screen.findByTestId("hub-next-action");
    expect(action).toHaveAttribute("href", "/dashboard/hub/publish");
    expect(screen.getByTestId("hub-next-step")).toHaveTextContent("Your turn");
    expect(
      within(screen.getByTestId("hub-decision")).getByTestId("hub-resubmit"),
    ).toHaveAttribute("href", "/dashboard/hub/publish");
    expect(screen.getAllByTestId("hub-resubmit")).toHaveLength(1);
  });

  // An earlier upload of csv-answers: a blocked try, or a change request.
  const earlier = (status) => ({
    ...row(status, {
      id: "older-one",
      name: "csv-answers",
      at: "2026-11-10T09:00:00+00:00",
    }),
    report: status === "checks_failed" ? FAILED_REPORT : null,
    checkSummary: status === "checks_failed" ? failed.checkSummary : null,
    decision: status === "changes_requested" ? CHANGES : null,
  });

  it.each([
    ["checks_failed", "in_review"],
    ["changes_requested", "approved"],
  ])(
    "a %s upload a newer %s upload of the same entry answered says so, as its row does, links to that upload and offers no action at the top right",
    async (status, newerStatus) => {
      const older = earlier(status);
      const newer = row(newerStatus, {
        id: "newer-one",
        name: "csv-answers",
        at: "2026-11-10T11:00:00+00:00",
      });
      serveOneWithList(older, [newer, older]);
      openOne(older);
      const next = await screen.findByTestId("hub-next-step");
      await waitFor(() =>
        expect(next).toHaveTextContent("Answered by a newer upload"),
      );
      expect(next).not.toHaveTextContent("Your turn");
      expect(
        within(next).getByRole("link", { name: "See the newer upload" }),
      ).toHaveAttribute("href", "/dashboard/hub/submissions/newer-one");
      expect(screen.queryByTestId("hub-next-action")).toBeNull();
      // The way to upload again stays where the findings or the reasons are.
      expect(screen.getAllByTestId("hub-resubmit")).toHaveLength(1);
    },
  );

  it("says it is your turn only once My submissions shows no newer upload answers it", async () => {
    serveOneWithList(failed, [failed]);
    const answer = axios.get.getMockImplementation();
    let release;
    const list = new Promise((resolve) => {
      release = () => resolve({ data: [failed], status: 200 });
    });
    // My submissions arrives when the case lets it; every other call is
    // answered.
    axios.get.mockImplementation((url, ...rest) =>
      String(url).split("?")[0] === "/omega-hub/v1/submissions"
        ? list
        : answer(url, ...rest),
    );
    try {
      openOne(failed);
      const next = await screen.findByTestId("hub-next-step");
      expect(next).not.toHaveTextContent("Your turn");
      expect(screen.queryByTestId("hub-next-action")).toBeNull();
      await act(async () => release());
      const action = await screen.findByTestId("hub-next-action");
      expect(action).toHaveAttribute("href", "/dashboard/hub/publish");
      expect(next).toHaveTextContent("Your turn");
    } finally {
      axios.get.mockImplementation(answer);
    }
  });

  it("an upload in review names who it waits on and offers no upload", async () => {
    const waiting = row("in_review", { id: "waiting-one" });
    serveOne(waiting);
    openOne(waiting);
    const next = await screen.findByTestId("hub-next-step");
    expect(next).toHaveTextContent("Waiting on a maintainer");
    expect(screen.queryByTestId("hub-next-action")).toBeNull();
    expect(screen.queryByTestId("hub-resubmit")).toBeNull();
  });

  it("with no Hub checker on a self-hosted server, a received upload waits on an administrator", async () => {
    const received = row("received", { id: "received-one" });
    serveOne(received, NO_CHECKER);
    openOne(received);
    const next = await screen.findByTestId("hub-next-step");
    await waitFor(() =>
      expect(next).toHaveTextContent("Waiting on an administrator"),
    );
    expect(screen.queryByTestId("hub-next-action")).toBeNull();
  });
});
