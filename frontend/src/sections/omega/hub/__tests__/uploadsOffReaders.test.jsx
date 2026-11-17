import React from "react";
import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { OMEGA_PART } from "src/sections/omega/shell/rollout";
import { capabilitiesAnswer } from "src/sections/omega/shell/__tests__/rollout-answer";

import review from "../__fixtures__/review-submission.json";
import rules from "../__fixtures__/rules.json";
import meMaintainer from "../__fixtures__/me-maintainer.json";
import meUser from "../__fixtures__/me-user.json";
import HubPublishPage from "../HubPublishPage";
import HubReviewPage from "../HubReviewPage";
import HubSubmissionPage from "../HubSubmissionPage";
import HubSubmissionsPage from "../HubSubmissionsPage";
import { respond } from "./mockAxios";
import { renderAt } from "./render";

// Who is reading, and on which server: the person's organization role and
// what useDeploymentMode() answered.
const reader = vi.hoisted(() => ({ role: "Owner", deployment: null }));

vi.mock("src/utils/axios", () => import("./mockAxios"));
vi.mock("src/auth/hooks/use-auth-context", () => ({
  useSafeAuthContext: () => ({ role: reader.role }),
}));
vi.mock("src/hooks/useDeploymentMode", () => ({
  useDeploymentMode: () => reader.deployment,
}));

// Uploads are off while this server runs no Hub checker, and who reads what
// depends on the server and the person. On a server confirmed self-hosted,
// an organization owner or admin reads the setup steps under "For
// administrators", ending with the self-host docs; anyone else there reads
// that uploads are off and is asked to send their administrator. On Cloud,
// uploads are off by design: everyone reads that uploads are off and nothing
// more, and nothing sends them to an administrator. While the server's mode
// is on its way, or after its answer failed, the app stands in "oss" for it;
// everyone then reads the Cloud sentence, so no one is shown the steps, or
// sent to an administrator, on a guess. The Publish page, the one notice
// above My submissions, an upload the server refuses because no checker can
// check it, and a submission that cannot move (on My submissions, on its own
// page and on the Review page) all say the same.

const OFF = "Uploads are off on this server.";
const ASK =
  "Uploads are off on this server. Ask your administrator to start the Hub checker.";
const STALLED_SELF_HOSTED =
  "The automated checks can't run on this server until an administrator starts the Hub checker.";
const STALLED =
  "The checks can't run on this server because no Hub checker is running.";
const SELF_HOST_DOCS = "https://docs.futureagi.com/docs/self-hosting";
const CODE = "OMEGA_HUB_CHECKER_NOT_CONFIGURED";
const TOKEN = "OMEGA_HUB_CHECKER_TOKEN";
// Every fact of the setup, as an owner or admin reads it.
const FACTS = [
  "Until it runs, the server refuses every upload and stores nothing.",
  "Make sure OMEGA_HUB_CHECKER_TOKEN is set on the server.",
  "The Hub checker uses the same token.",
  "On Docker Compose, make sure hub is in COMPOSE_PROFILES in .env.",
  "The server does not read a --profile flag; it reads only that setting.",
  "Restart the server. Uploads work once it runs with both settings.",
];

// What useDeploymentMode() answers: a confirmed mode, or "oss" standing in
// while the answer is on its way or after it failed.
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
const ENTERPRISE = confirmed("ee");
const LOADING = {
  ...SELF_HOSTED,
  isLoading: true,
  isSuccess: false,
  isConfirmed: false,
};
const FAILED = { ...SELF_HOSTED, isSuccess: false, isConfirmed: false };

// An owner or admin of a server confirmed self-hosted reads the setup steps.
const STEPS_READERS = [
  ["an owner of a self-hosted server", SELF_HOSTED, "Owner"],
  ["an admin of a self-hosted server", SELF_HOSTED, "Admin"],
  ["an owner of a self-hosted Enterprise server", ENTERPRISE, "Owner"],
];
// Anyone else on a server confirmed self-hosted is asked to send their
// administrator.
const ASK_READERS = [
  ["a member of a self-hosted server", SELF_HOSTED, "Member"],
  ["a member of a self-hosted Enterprise server", ENTERPRISE, "Member"],
];
// Everyone on Cloud, and everyone while the server's mode is not known, reads
// that uploads are off and nothing more.
const OFF_READERS = [
  ["an owner on Cloud", CLOUD, "Owner"],
  ["an admin on Cloud", CLOUD, "Admin"],
  ["a member on Cloud", CLOUD, "Member"],
  ["an owner while the server's mode is on its way", LOADING, "Owner"],
  ["a member while the server's mode is on its way", LOADING, "Member"],
  ["an admin when the server's mode did not load", FAILED, "Admin"],
];

const ME = {
  ...meUser,
  publishers: [
    {
      handle: "c-test",
      displayName: "C Test",
      tier: "community",
      kind: "user",
    },
  ],
};

// The server's refusal of an upload no checker could check
// (omega_hub/services/intake.py).
const REFUSED = {
  error: {
    code: CODE,
    message:
      "This Hub cannot check uploads: its sandboxed checker is not configured. Nothing was stored.",
    fix: "Ask the Hub's administrator to run the checker with OMEGA_HUB_CHECKER_TOKEN set, then upload again.",
  },
};

// The server's words (omega_hub/api/serializers/submissions.py STATUS_TEXT).
const SERVER_TEXT = {
  received: "Received. Automated checks start in a moment.",
  check_error:
    "We could not run the checks. A maintainer has been told and will run them again.",
  checks_failed:
    "The automated checks found problems. Fix each finding and publish again.",
  changes_requested:
    "A maintainer asked for changes. Answer by publishing a new upload.",
};

// A submission as the submitter's API sends it.
const row = (status, name) => ({
  ...review,
  id: `${status}-${name}`,
  publisher: "c-test",
  status,
  statusText: SERVER_TEXT[status],
  name,
  version: "1.0.0",
  hints: { name, version: "1.0.0" },
  report: null,
  checkSummary: null,
  decision: null,
  confirmedDisclosure: null,
  published: null,
});

const YOURS = [
  row("checks_failed", "first-entry"),
  row("changes_requested", "second-entry"),
];
const STUCK = [
  row("received", "third-entry"),
  row("check_error", "fourth-entry"),
];

const REVIEW = `/omega-hub/v1/review/submissions/${review.id}`;

// A submission as the Review page's API sends it to a maintainer.
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

function as(deployment, role) {
  reader.deployment = deployment;
  reader.role = role;
}

function servePublish(parts) {
  respond([
    ["get", "/api/capabilities/", capabilitiesAnswer({ parts })],
    ["get", "/omega-hub/v1/rules", rules],
    ["get", "/omega-hub/v1/me", ME],
    ["post", "/omega-hub/v1/submissions", REFUSED, 503],
  ]);
}

function serveList(rows) {
  respond([
    ["get", "/api/capabilities/", capabilitiesAnswer({ parts: [] })],
    ["get", "/omega-hub/v1/me", meUser],
    ["get", "/omega-hub/v1/submissions", rows],
  ]);
}

function serveOne(submission) {
  respond([
    ["get", "/api/capabilities/", capabilitiesAnswer({ parts: [] })],
    ["get", "/omega-hub/v1/me", meUser],
    ["get", `/omega-hub/v1/submissions/${submission.id}`, submission],
  ]);
}

function serveReview(submission) {
  respond([
    ["get", "/api/capabilities/", capabilitiesAnswer({ parts: [] })],
    ["get", "/omega-hub/v1/me", meMaintainer],
    ["get", "/omega-hub/v1/rules", rules],
    ["get", REVIEW, submission],
    ["get", `${REVIEW}/diff`, { diff: null }],
    ["get", new RegExp(`^${REVIEW}/files/`), "x = 1\n"],
  ]);
}

const openList = (search = "") =>
  renderAt(<HubSubmissionsPage />, {
    at: `/dashboard/hub/submissions${search}`,
  });

const openOne = (submission) =>
  renderAt(<HubSubmissionPage />, {
    path: "/dashboard/hub/submissions/:id",
    at: `/dashboard/hub/submissions/${submission.id}`,
  });

const openReview = () =>
  renderAt(<HubReviewPage />, {
    path: "/dashboard/hub/review/:id",
    at: `/dashboard/hub/review/${review.id}`,
  });

const follows = (a, b) =>
  Boolean(a.compareDocumentPosition(b) & Node.DOCUMENT_POSITION_FOLLOWING);

// The words of a notice, as one line.
const wordsOf = (node) => node.textContent.replace(/\s+/g, " ").trim();

// No setup in `node`: no For administrators, no setting to type, no docs.
function expectNoSetup(node) {
  expect(
    within(node).queryByRole("heading", { name: "For administrators" }),
  ).toBeNull();
  expect(
    within(node).queryByRole("list", { name: "For administrators" }),
  ).toBeNull();
  expect(node).not.toHaveTextContent(TOKEN);
  expect(
    within(node).queryByRole("link", { name: "Self-host docs" }),
  ).toBeNull();
}

// Choose a folder and submit it; the server refuses it.
async function uploadOnce() {
  renderAt(<HubPublishPage />);
  await screen.findByTestId("hub-upload-card");
  const file = new File(
    [new Uint8Array([0x1f, 0x8b, 0x08, 0x00])],
    "first-agent.tar.gz",
    { type: "application/gzip" },
  );
  fireEvent.change(screen.getByTestId("hub-upload-input"), {
    target: { files: [file] },
  });
  fireEvent.click(screen.getByTestId("hub-upload-submit"));
  return screen.findByTestId("hub-upload-error");
}

beforeEach(() => {
  vi.stubEnv("VITE_OMEGA_CONSOLE", "1");
  as(SELF_HOSTED, "Owner");
});
afterEach(() => vi.unstubAllEnvs());

describe("the Publish page while uploads are off", () => {
  it.each(STEPS_READERS)(
    "gives %s the sentence, then the setup steps under For administrators and the self-host docs",
    async (_, deployment, role) => {
      as(deployment, role);
      servePublish([]);
      renderAt(<HubPublishPage />);
      const notice = await screen.findByTestId("hub-uploads-unavailable");
      expect(wordsOf(within(notice).getByTestId("hub-uploads-off"))).toBe(ASK);
      expect(
        within(notice).getByRole("heading", {
          level: 3,
          name: "For administrators",
        }),
      ).toBeInTheDocument();
      const steps = within(notice).getByRole("list", {
        name: "For administrators",
      });
      expect(within(steps).getAllByRole("listitem")).toHaveLength(3);
      for (const fact of FACTS) expect(notice).toHaveTextContent(fact);
      expect(within(notice).getByText(TOKEN).tagName).toBe("CODE");
      const docs = within(notice).getByRole("link", { name: "Self-host docs" });
      expect(docs).toHaveAttribute("href", SELF_HOST_DOCS);
    },
  );

  it.each(ASK_READERS)(
    "asks %s to send their administrator, in one sentence and no setup",
    async (_, deployment, role) => {
      as(deployment, role);
      servePublish([]);
      renderAt(<HubPublishPage />);
      const notice = await screen.findByTestId("hub-uploads-unavailable");
      expect(wordsOf(within(notice).getByTestId("hub-uploads-off"))).toBe(ASK);
      expectNoSetup(notice);
    },
  );

  it.each(OFF_READERS)(
    "tells %s only that uploads are off, and sends them to no administrator",
    async (_, deployment, role) => {
      as(deployment, role);
      servePublish([]);
      renderAt(<HubPublishPage />);
      const notice = await screen.findByTestId("hub-uploads-unavailable");
      expect(wordsOf(within(notice).getByTestId("hub-uploads-off"))).toBe(OFF);
      expectNoSetup(notice);
      expect(notice).not.toHaveTextContent(/administrator/i);
      // Publish stays a page: the rules are still there.
      expect(await screen.findByTestId("hub-rules")).toBeInTheDocument();
    },
  );

  it("shows an owner no setup while the server's mode is on its way, then the steps once the server is confirmed self-hosted", async () => {
    as(LOADING, "Owner");
    servePublish([]);
    const view = renderAt(<HubPublishPage />);
    const notice = await screen.findByTestId("hub-uploads-unavailable");
    expect(wordsOf(within(notice).getByTestId("hub-uploads-off"))).toBe(OFF);
    expectNoSetup(notice);
    as(SELF_HOSTED, "Owner");
    view.rerender(<HubPublishPage />);
    expect(
      await screen.findByRole("heading", {
        level: 3,
        name: "For administrators",
      }),
    ).toBeInTheDocument();
    expect(wordsOf(screen.getByTestId("hub-uploads-off"))).toBe(ASK);
  });
});

describe("the one notice above My submissions while uploads are off", () => {
  it.each(STEPS_READERS)(
    "gives %s the sentence, then the setup steps under For administrators, above the list",
    async (_, deployment, role) => {
      as(deployment, role);
      serveList(YOURS);
      openList();
      const off = await screen.findByTestId("hub-submissions-uploads-off");
      expect(off).toHaveAttribute("role", "status");
      expect(wordsOf(off)).toBe(ASK);
      // The page's own heading is its h1, so this one is an h2.
      const heading = await screen.findByRole("heading", {
        level: 2,
        name: "For administrators",
      });
      const steps = screen.getByRole("list", { name: "For administrators" });
      expect(within(steps).getAllByRole("listitem")).toHaveLength(3);
      for (const fact of FACTS) expect(document.body).toHaveTextContent(fact);
      expect(screen.getByText(TOKEN).tagName).toBe("CODE");
      const docs = screen.getByRole("link", { name: "Self-host docs" });
      expect(docs).toHaveAttribute("href", SELF_HOST_DOCS);
      expect(docs).toHaveAttribute("target", "_blank");
      expect(docs).toHaveAttribute("rel", "noopener noreferrer");
      // Said once, after the sentence and before the list, not on each row.
      const list = await screen.findByTestId("hub-submissions-list");
      expect(follows(off, heading)).toBe(true);
      expect(follows(docs, list)).toBe(true);
      expect(screen.getAllByText("For administrators")).toHaveLength(1);
      for (const item of within(list).getAllByTestId("hub-submission-row"))
        expect(item).not.toHaveTextContent(TOKEN);
    },
  );

  it.each(ASK_READERS)(
    "asks %s to send their administrator, and each Upload a new version carries that sentence",
    async (_, deployment, role) => {
      as(deployment, role);
      serveList(YOURS);
      openList();
      const off = await screen.findByTestId("hub-submissions-uploads-off");
      expect(wordsOf(off)).toBe(ASK);
      expectNoSetup(document.body);
      const link = await screen.findByRole("link", {
        name: "Upload a new version of c-test/first-entry",
      });
      await waitFor(() => expect(link).toHaveAccessibleDescription(ASK));
    },
  );

  it.each(OFF_READERS)(
    "tells %s only that uploads are off, and nothing on the page sends them to an administrator",
    async (_, deployment, role) => {
      as(deployment, role);
      serveList(YOURS);
      openList();
      const off = await screen.findByTestId("hub-submissions-uploads-off");
      expect(wordsOf(off)).toBe(OFF);
      const link = await screen.findByRole("link", {
        name: "Upload a new version of c-test/first-entry",
      });
      await waitFor(() => expect(link).toHaveAccessibleDescription(OFF));
      expectNoSetup(document.body);
      expect(document.body).not.toHaveTextContent(/administrator/i);
    },
  );
});

describe("an upload the server refuses because no checker can check it", () => {
  // The capability answer said uploads work; the server knew better and
  // refused the upload with its code.
  it.each(OFF_READERS)(
    "tells %s only that uploads are off, never the server's code or its setting",
    async (_, deployment, role) => {
      as(deployment, role);
      servePublish([OMEGA_PART.HUB_PUBLISH]);
      const alert = await uploadOnce();
      expect(wordsOf(alert)).toBe(OFF);
      expect(alert).not.toHaveTextContent(CODE);
      expect(alert).not.toHaveTextContent(TOKEN);
      expect(alert).not.toHaveTextContent(REFUSED.error.message);
      expect(alert).not.toHaveTextContent(/administrator/i);
    },
  );

  it.each(ASK_READERS)(
    "asks %s to send their administrator, in one sentence and never the server's code",
    async (_, deployment, role) => {
      as(deployment, role);
      servePublish([OMEGA_PART.HUB_PUBLISH]);
      const alert = await uploadOnce();
      expect(wordsOf(alert)).toBe(ASK);
      expect(alert).not.toHaveTextContent(CODE);
      expect(alert).not.toHaveTextContent(TOKEN);
    },
  );

  it.each(STEPS_READERS)(
    "gives %s the sentence, then the setup, and never the server's code",
    async (_, deployment, role) => {
      as(deployment, role);
      servePublish([OMEGA_PART.HUB_PUBLISH]);
      const alert = await uploadOnce();
      expect(wordsOf(alert).startsWith(ASK)).toBe(true);
      expect(alert).toHaveTextContent("For administrators:");
      expect(alert).toHaveTextContent(TOKEN);
      expect(alert).not.toHaveTextContent(CODE);
      expect(alert.textContent).not.toContain("`");
    },
  );
});

describe("a submission that cannot move while no checker runs", () => {
  it.each(OFF_READERS)(
    "tells %s on My submissions why it waits, names no next step, and sends them to no administrator",
    async (_, deployment, role) => {
      as(deployment, role);
      serveList(STUCK);
      openList("?view=waiting");
      const rows = await screen.findAllByTestId("hub-submission-row");
      expect(rows).toHaveLength(2);
      await waitFor(() => {
        for (const item of rows) expect(item).toHaveTextContent(STALLED);
      });
      for (const item of rows) {
        expect(within(item).queryByTestId("hub-submission-next")).toBeNull();
      }
      expect(document.body).not.toHaveTextContent(/Waiting on/);
      expect(document.body).not.toHaveTextContent(/administrator/i);
      expect(document.body).not.toHaveTextContent(SERVER_TEXT.received);
      expect(document.body).not.toHaveTextContent(SERVER_TEXT.check_error);
    },
  );

  it.each(OFF_READERS)(
    "tells %s on its own page why it waits, names no next step, and sends them to no administrator",
    async (_, deployment, role) => {
      as(deployment, role);
      const received = row("received", "third-entry");
      serveOne(received);
      openOne(received);
      const text = await screen.findByTestId("hub-status-text");
      await waitFor(() => expect(text).toHaveTextContent(STALLED));
      expect(screen.queryByTestId("hub-next-step")).toBeNull();
      expect(screen.queryByText("Next step")).toBeNull();
      expect(document.body).not.toHaveTextContent(/administrator/i);
      expect(document.body).not.toHaveTextContent(/in a moment/i);
    },
  );

  it.each(OFF_READERS)(
    "tells %s on the Review page why it waits, and sends them to no administrator",
    async (_, deployment, role) => {
      as(deployment, role);
      serveReview(reviewRow("check_error"));
      openReview();
      const status = await screen.findByTestId("hub-review-status-text");
      await waitFor(() => expect(status).toHaveTextContent(STALLED));
      // The recheck that cannot run says the same beside it.
      expect(screen.getByTestId("hub-recheck-no-checker")).toHaveTextContent(
        STALLED,
      );
      expect(document.body).not.toHaveTextContent(/administrator/i);
      expect(document.body).not.toHaveTextContent(SERVER_TEXT.check_error);
    },
  );

  it("on a self-hosted server, says on My submissions that an administrator starts the checker, and the row waits on one", async () => {
    as(SELF_HOSTED, "Member");
    serveList(STUCK);
    openList("?view=waiting");
    const rows = await screen.findAllByTestId("hub-submission-row");
    await waitFor(() => {
      for (const item of rows)
        expect(item).toHaveTextContent(STALLED_SELF_HOSTED);
    });
    for (const item of rows) {
      expect(within(item).getByTestId("hub-submission-next")).toHaveTextContent(
        "Waiting on an administrator",
      );
    }
  });

  it("on a self-hosted server, says on its own page that an administrator starts the checker, and it waits on one", async () => {
    as(SELF_HOSTED, "Owner");
    const received = row("received", "third-entry");
    serveOne(received);
    openOne(received);
    const text = await screen.findByTestId("hub-status-text");
    await waitFor(() => expect(text).toHaveTextContent(STALLED_SELF_HOSTED));
    expect(screen.getByTestId("hub-next-step")).toHaveTextContent(
      "Waiting on an administrator",
    );
  });

  it("on a self-hosted server, says on the Review page that an administrator starts the checker", async () => {
    as(SELF_HOSTED, "Admin");
    serveReview(reviewRow("check_error"));
    openReview();
    const status = await screen.findByTestId("hub-review-status-text");
    await waitFor(() => expect(status).toHaveTextContent(STALLED_SELF_HOSTED));
    expect(screen.getByTestId("hub-recheck-no-checker")).toHaveTextContent(
      STALLED,
    );
  });
});
