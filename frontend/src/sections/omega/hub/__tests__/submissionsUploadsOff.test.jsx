import React from "react";
import { screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { OMEGA_PART } from "src/sections/omega/shell/rollout";
import { capabilitiesAnswer } from "src/sections/omega/shell/__tests__/rollout-answer";

import review from "../__fixtures__/review-submission.json";
import meUser from "../__fixtures__/me-user.json";
import HubSubmissionsPage from "../HubSubmissionsPage";
import { UPLOADS_OFF_LINE } from "../components/UploadsUnavailable";
import { respond } from "./mockAxios";
import { renderAt } from "./render";

vi.mock("src/utils/axios", () => import("./mockAxios"));
// A Cloud server, where everyone reads the short line alone. The words for a
// self-hosted server, and while its mode is not known, are in
// uploadsOffReaders.test.jsx.
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

// While this server runs no Hub checker, Publish takes no upload. My
// submissions says so once, in one notice above the list, in the shared
// short line, instead of the same line on every row whose turn it is. Each
// such row keeps its Upload a new version link, and the link carries the
// notice's sentence as its description, so a screen reader user who reaches
// it hears why it leads to a page that cannot take an upload yet. A view
// with no rows, and a list with nothing in it yet, keep the notice. A server
// that can check uploads shows none.

const LIST = "/dashboard/hub/submissions";
const CHECKER = [OMEGA_PART.HUB_PUBLISH];
const NO_CHECKER = [];

// The server's words (omega_hub/api/serializers/submissions.py STATUS_TEXT).
const SERVER_TEXT = {
  checks_failed:
    "The automated checks found problems. Fix each finding and publish again.",
  changes_requested:
    "A maintainer asked for changes. Answer by publishing a new upload.",
  in_review:
    "Checks passed. Waiting for a Future AGI maintainer. Usually within 2 business days.",
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

function serveList(rows, parts) {
  respond([
    ["get", "/api/capabilities/", capabilitiesAnswer({ parts })],
    ["get", "/omega-hub/v1/me", meUser],
    ["get", "/omega-hub/v1/submissions", rows],
  ]);
}

const openList = (search = "") =>
  renderAt(<HubSubmissionsPage />, { at: `${LIST}${search}` });

const notice = () => screen.findByTestId("hub-submissions-uploads-off");

const follows = (a, b) =>
  Boolean(a.compareDocumentPosition(b) & Node.DOCUMENT_POSITION_FOLLOWING);

// Let /api/capabilities/ answer, so a notice that would show has had its
// chance.
const settle = () => new Promise((done) => setTimeout(done, 50));

const YOURS = [
  row("checks_failed", "first-entry"),
  row("changes_requested", "second-entry"),
];

beforeEach(() => vi.stubEnv("VITE_OMEGA_CONSOLE", "1"));
afterEach(() => vi.unstubAllEnvs());

describe("My submissions with no Hub checker: one notice", () => {
  it("says once, above the list, that uploads are off, and not again on each row", async () => {
    serveList([...YOURS, row("in_review", "third-entry")], NO_CHECKER);
    openList("?view=all");
    const off = await notice();
    expect(off.textContent.trim()).toBe(UPLOADS_OFF_LINE);
    expect(off).toHaveAttribute("role", "status");
    expect(screen.getAllByText(UPLOADS_OFF_LINE)).toHaveLength(1);
    const list = screen.getByTestId("hub-submissions-list");
    const views = screen.getByTestId("hub-submissions-views");
    expect(follows(off, views)).toBe(true);
    expect(follows(off, list)).toBe(true);
    const rows = within(list).getAllByTestId("hub-submission-row");
    expect(rows).toHaveLength(3);
    for (const item of rows) expect(item).not.toHaveTextContent(/Uploads/);
  });

  it("keeps Upload a new version on each row whose turn it is, each link carrying the notice's sentence", async () => {
    serveList(YOURS, NO_CHECKER);
    openList();
    await screen.findAllByTestId("hub-submission-row");
    for (const name of ["first-entry", "second-entry"]) {
      const link = screen.getByRole("link", {
        name: `Upload a new version of c-test/${name}`,
      });
      expect(link).toHaveTextContent("Upload a new version");
      expect(link).toHaveAttribute("href", "/dashboard/hub/publish");
      await waitFor(() =>
        expect(link).toHaveAccessibleDescription(UPLOADS_OFF_LINE),
      );
    }
  });

  it("keeps the notice above a view with no rows in it", async () => {
    serveList([row("in_review", "third-entry")], NO_CHECKER);
    openList();
    const off = await notice();
    const empty = await screen.findByText("Nothing needs you right now");
    expect(follows(off, empty)).toBe(true);
  });

  it("keeps the notice above a list with nothing in it yet", async () => {
    serveList([], NO_CHECKER);
    openList();
    const off = await notice();
    const empty = await screen.findByText("Nothing submitted yet");
    expect(follows(off, empty)).toBe(true);
  });

  it("shows no notice where this server can check uploads", async () => {
    serveList(YOURS, CHECKER);
    openList();
    await screen.findAllByTestId("hub-submission-row");
    await settle();
    expect(screen.queryByTestId("hub-submissions-uploads-off")).toBeNull();
    expect(screen.queryByText(UPLOADS_OFF_LINE)).toBeNull();
    const link = screen.getByRole("link", {
      name: "Upload a new version of c-test/first-entry",
    });
    expect(link).not.toHaveAttribute("aria-describedby");
  });
});
