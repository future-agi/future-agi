import React from "react";
import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { OMEGA_PART } from "src/sections/omega/shell/rollout";
import { capabilitiesAnswer } from "src/sections/omega/shell/__tests__/rollout-answer";

import review from "../__fixtures__/review-submission.json";
import meUser from "../__fixtures__/me-user.json";
import HubSubmissionPage from "../HubSubmissionPage";
import HubSubmissionsPage from "../HubSubmissionsPage";
import { respond } from "./mockAxios";
import { renderAt } from "./render";

// Who is reading, and on which server: the person's organization role and
// what useDeploymentMode() answered.
const reader = vi.hoisted(() => ({ role: "Member", deployment: null }));

vi.mock("src/utils/axios", () => import("./mockAxios"));
vi.mock("src/auth/hooks/use-auth-context", () => ({
  useSafeAuthContext: () => ({ role: reader.role }),
}));
vi.mock("src/hooks/useDeploymentMode", () => ({
  useDeploymentMode: () => reader.deployment,
}));

// While this server runs no Hub checker, Publish takes no upload, and My
// submissions says so once, in the notice above the list. Every Publish on
// it stays a link to the Publish page, which says the same, and nothing on
// it is turned off: the tab row's, at every width, with no description, as
// on every Hub page; the empty list's when nothing is submitted yet, and an
// empty view's, each carrying the notice's sentence as its description. The
// empty list's line then says what the page will show, not how to upload. A
// submission's own page keeps both of its Upload a new version links
// working, as each row's link on My submissions does, and each carries that
// notice's sentence as its description; the page shows the sentence once,
// beside the first of them. The sentence is the one the Publish page gives
// this person: on Cloud only that uploads are off, on a self-hosted server
// also to ask their administrator. Where this server can check uploads,
// nothing changes.

const OFF = "Uploads are off on this server.";
const ASK =
  "Uploads are off on this server. Ask your administrator to start the Hub checker.";
const LIST = "/dashboard/hub/submissions";
const PUBLISH = "/dashboard/hub/publish";
const CHECKER = [OMEGA_PART.HUB_PUBLISH];
const NO_CHECKER = [];

// The empty list's line while uploads are off, and the lines it keeps where
// this server can check uploads, before and after the CLI ships.
const FOLLOW = "You will follow your uploads here through checks and review.";
const UPLOAD_LINE =
  "Upload a folder. You will follow it here through checks and review.";
const TERMINAL_LINE =
  "Publish from your terminal or upload a folder. You will follow it here through checks and review.";

// What useDeploymentMode() answers for a confirmed mode.
const confirmed = (mode) => ({
  mode,
  isCloud: mode === "cloud",
  isOSS: mode === "oss",
  isEE: mode === "ee",
  isLoading: false,
  isSuccess: true,
  isConfirmed: true,
});
const CLOUD = confirmed("cloud");
const SELF_HOSTED = confirmed("oss");

// Each reader, and the sentence the notice above My submissions gives them.
const READERS = [
  ["a member on Cloud", CLOUD, "Member", OFF],
  ["an owner on Cloud", CLOUD, "Owner", OFF],
  ["a member of a self-hosted server", SELF_HOSTED, "Member", ASK],
  ["an owner of a self-hosted server", SELF_HOSTED, "Owner", ASK],
];

// The server's words (omega_hub/api/serializers/submissions.py STATUS_TEXT).
const SERVER_TEXT = {
  checks_failed:
    "The automated checks found problems. Fix each finding and publish again.",
  changes_requested:
    "A maintainer asked for changes. Answer by publishing a new upload.",
  in_review:
    "Checks passed. Waiting for a Future AGI maintainer. Usually within 2 business days.",
  approved: "Published.",
  withdrawn: "Withdrawn.",
};

// A submission as the submitter's API sends it.
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

// A failed upload's report: one check failed, with its finding and fix.
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
  ],
};
const FAILED_SUMMARY = {
  steps: { unpack: "pass", manifest: "fail" },
  flags: {},
  flagsReviewed: 0,
  checkerVersion: "0.1.0",
  runMs: 0,
  network: "none",
};

const failed = (options) => ({
  ...row("checks_failed", options),
  report: FAILED_REPORT,
  checkSummary: FAILED_SUMMARY,
  timeline: review.timeline.slice(0, 2),
});

const CHANGES = {
  decision: "request_changes",
  reason: "Name every host it calls in the disclosure.",
  notes: [],
  decidedAt: "2026-11-10T10:30:00+00:00",
};

// Each view of My submissions that ends in Publish when nothing is in it: its
// name, its address, a submission that leaves it empty with nothing open in
// any other view, and a reader with the sentence the notice gives them.
const EMPTY_VIEWS = [
  ["Needs you", "", "approved", CLOUD, "Member", OFF],
  ["Waiting", "?view=waiting", "approved", SELF_HOSTED, "Member", ASK],
  ["Published", "?view=published", "withdrawn", SELF_HOSTED, "Owner", ASK],
];

function as(deployment, role) {
  reader.deployment = deployment;
  reader.role = role;
}

function serveList(rows, parts) {
  respond([
    ["get", "/api/capabilities/", capabilitiesAnswer({ parts })],
    ["get", "/omega-hub/v1/me", meUser],
    ["get", "/omega-hub/v1/submissions", rows],
  ]);
}

// One submission, and My submissions beside it.
function serveOne(submission, rows, parts) {
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
    at: `${LIST}/${submission.id}`,
  });

// The tab row's Publish: the outlined one from a tablet up, the icon one on a
// phone.
const navPublish = () => {
  const nav = screen.getByTestId("hub-nav-main");
  return [
    within(nav).getByTestId("hub-nav-publish"),
    within(nav).getByLabelText("Publish"),
  ];
};

// A Publish that is a working link to the Publish page, not turned off.
function expectPublishLink(publish) {
  expect(publish).toHaveAttribute("href", PUBLISH);
  expect(publish).toBeEnabled();
  expect(publish).not.toHaveAttribute("aria-disabled");
}

// A submission page's Upload a new version: at the top right, and where the
// findings or a maintainer's reasons are.
const topAction = () => screen.getByTestId("hub-next-action");
const resubmit = () => screen.getByTestId("hub-resubmit");

const location = () => screen.getByTestId("location").textContent;

const follows = (a, b) =>
  Boolean(a.compareDocumentPosition(b) & Node.DOCUMENT_POSITION_FOLLOWING);

// Let /api/capabilities/ answer, so a description that would show has had
// its chance.
const settle = () => new Promise((done) => setTimeout(done, 50));

beforeEach(() => {
  vi.stubEnv("VITE_OMEGA_CONSOLE", "1");
  as(SELF_HOSTED, "Member");
});
afterEach(() => vi.unstubAllEnvs());

describe("My submissions with nothing submitted yet, while uploads are off", () => {
  it.each(READERS)(
    "links Publish for %s to the Publish page, with the notice's sentence as its description, under a line that offers no upload",
    async (_, deployment, role, sentence) => {
      as(deployment, role);
      serveList([], NO_CHECKER);
      openList();
      const notice = await screen.findByTestId("hub-submissions-uploads-off");
      const empty = await screen.findByTestId("hub-empty");
      expect(empty).toHaveTextContent("Nothing submitted yet");
      expect(empty).toHaveTextContent(FOLLOW);
      expect(empty).not.toHaveTextContent(/upload a folder/i);
      const publish = within(empty).getByRole("link", { name: "Publish" });
      expectPublishLink(publish);
      expect(publish).toHaveAccessibleDescription(sentence);
      // Its description is the notice above the list, said once.
      expect(follows(notice, publish)).toBe(true);
      expect(screen.getAllByText(sentence)).toHaveLength(1);
      // It leads to the Publish page, which says why.
      fireEvent.click(publish);
      await waitFor(() => expect(location()).toBe(PUBLISH));
    },
  );

  it("gives the same line once the CLI ships, naming neither the terminal nor a folder", async () => {
    as(CLOUD, "Member");
    serveList([], [OMEGA_PART.HUB_CLI]);
    openList();
    await screen.findByTestId("hub-submissions-uploads-off");
    const empty = await screen.findByTestId("hub-empty");
    expect(empty).toHaveTextContent(FOLLOW);
    expect(empty).not.toHaveTextContent(/terminal/i);
    expect(empty).not.toHaveTextContent(/upload a folder/i);
    const publish = within(empty).getByRole("link", { name: "Publish" });
    expectPublishLink(publish);
    expect(publish).toHaveAccessibleDescription(OFF);
  });

  it.each(READERS)(
    "keeps the tab row's Publish for %s a link to the Publish page at every width, with no description",
    async (_, deployment, role) => {
      as(deployment, role);
      serveList([], NO_CHECKER);
      openList();
      await screen.findByTestId("hub-submissions-uploads-off");
      await screen.findByTestId("hub-empty");
      for (const publish of navPublish()) {
        expectPublishLink(publish);
        expect(publish).not.toHaveAttribute("aria-describedby");
      }
    },
  );

  it.each([
    ["without the CLI", CHECKER, UPLOAD_LINE],
    ["once the CLI ships", [...CHECKER, OMEGA_PART.HUB_CLI], TERMINAL_LINE],
  ])(
    "names the ways to publish %s, with every Publish a link and no description, where this server can check uploads",
    async (_, parts, line) => {
      serveList([], parts);
      openList();
      await screen.findByTestId("hub-empty");
      await settle();
      const empty = screen.getByTestId("hub-empty");
      expect(within(empty).getByText(line)).toBeInTheDocument();
      const publishes = [
        within(empty).getByRole("link", { name: "Publish" }),
        ...navPublish(),
      ];
      for (const publish of publishes) {
        expectPublishLink(publish);
        expect(publish).not.toHaveAttribute("aria-describedby");
      }
      expect(screen.queryByTestId("hub-submissions-uploads-off")).toBeNull();
    },
  );
});

describe("A view of My submissions with nothing in it, while uploads are off", () => {
  it.each(EMPTY_VIEWS)(
    "links Publish under %s to the Publish page, with the notice's sentence as its description",
    async (_, search, status, deployment, role, sentence) => {
      as(deployment, role);
      serveList([row(status)], NO_CHECKER);
      openList(search);
      const notice = await screen.findByTestId("hub-submissions-uploads-off");
      const empty = await screen.findByTestId("hub-empty");
      const publish = within(empty).getByRole("link", { name: "Publish" });
      expectPublishLink(publish);
      expect(publish).toHaveAccessibleDescription(sentence);
      expect(follows(notice, publish)).toBe(true);
      // The tab row's Publish stays the link it is on every Hub page.
      for (const tabRowPublish of navPublish()) {
        expectPublishLink(tabRowPublish);
        expect(tabRowPublish).not.toHaveAttribute("aria-describedby");
      }
      expect(screen.getAllByText(sentence)).toHaveLength(1);
    },
  );

  it("links Publish under an empty view where this server can check uploads, with no description", async () => {
    as(CLOUD, "Member");
    serveList([row("approved")], CHECKER);
    openList();
    const empty = await screen.findByTestId("hub-empty");
    await settle();
    const publish = within(empty).getByRole("link", { name: "Publish" });
    expectPublishLink(publish);
    expect(publish).not.toHaveAttribute("aria-describedby");
  });
});

describe("A submission's own page, while uploads are off", () => {
  it.each(READERS)(
    "gives %s each Upload a new version of a failed upload the notice's sentence as its description, and shows it once beside the first",
    async (_, deployment, role, sentence) => {
      as(deployment, role);
      const upload = failed({ id: "failed-one" });
      serveOne(upload, [upload], NO_CHECKER);
      openOne(upload);
      await waitFor(() =>
        expect(topAction()).toHaveAccessibleDescription(sentence),
      );
      const top = topAction();
      const findings = screen.getByTestId("hub-findings");
      const again = within(findings).getByTestId("hub-resubmit");
      expect(again).toHaveAccessibleDescription(sentence);
      // Both stay links to the Publish page, as each row's link does.
      for (const action of [top, again]) {
        expect(action).toHaveTextContent("Upload a new version");
        expect(action).toHaveAttribute("href", PUBLISH);
        expect(action).toBeEnabled();
      }
      // Said once, beside the first of them, above the page's facts.
      expect(screen.getAllByText(sentence)).toHaveLength(1);
      const line = screen.getByText(sentence);
      const status = screen.getByTestId("hub-submission-status");
      expect(follows(top, line)).toBe(true);
      expect(follows(line, status)).toBe(true);
    },
  );

  it("gives both Upload a new version of a change-requested upload the same description", async () => {
    as(CLOUD, "Member");
    const upload = { ...row("changes_requested"), decision: CHANGES };
    serveOne(upload, [upload], NO_CHECKER);
    openOne(upload);
    await waitFor(() => expect(topAction()).toHaveAccessibleDescription(OFF));
    const decision = screen.getByTestId("hub-decision");
    const again = within(decision).getByTestId("hub-resubmit");
    expect(again).toHaveAccessibleDescription(OFF);
    expect(again).toHaveAttribute("href", PUBLISH);
    expect(screen.getAllByText(OFF)).toHaveLength(1);
  });

  it("puts the sentence beside the one Upload a new version left once a newer upload answered this one", async () => {
    as(CLOUD, "Member");
    const older = failed({
      id: "older-one",
      name: "csv-answers",
      at: "2026-11-10T09:00:00+00:00",
    });
    const newer = row("in_review", {
      id: "newer-one",
      name: "csv-answers",
      at: "2026-11-10T11:00:00+00:00",
    });
    serveOne(older, [newer, older], NO_CHECKER);
    openOne(older);
    const next = await screen.findByTestId("hub-next-step");
    await waitFor(() =>
      expect(next).toHaveTextContent("Answered by a newer upload"),
    );
    expect(screen.queryByTestId("hub-next-action")).toBeNull();
    await waitFor(() => expect(resubmit()).toHaveAccessibleDescription(OFF));
    const again = resubmit();
    expect(again).toHaveAttribute("href", PUBLISH);
    expect(screen.getAllByText(OFF)).toHaveLength(1);
    expect(follows(again, screen.getByText(OFF))).toBe(true);
  });

  it("gives neither Upload a new version a description where this server can check uploads", async () => {
    as(CLOUD, "Member");
    const upload = failed({ id: "failed-one" });
    serveOne(upload, [upload], CHECKER);
    openOne(upload);
    await screen.findByTestId("hub-next-action");
    await settle();
    for (const action of [topAction(), resubmit()]) {
      expect(action).toHaveAttribute("href", PUBLISH);
      expect(action).not.toHaveAttribute("aria-describedby");
    }
    expect(screen.queryByText(/Uploads are off/)).toBeNull();
  });
});
