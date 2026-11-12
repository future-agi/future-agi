import React from "react";
import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import HubReviewPage from "../HubReviewPage";
import HubReviewQueuePage from "../HubReviewQueuePage";
import { summaryLine } from "../components/CheckReport";
import review from "../__fixtures__/review-submission.json";
import rules from "../__fixtures__/rules.json";
import me from "../__fixtures__/me-maintainer.json";
import { calls, respond } from "./mockAxios";
import { renderAt } from "./render";

vi.mock("src/utils/axios", () => import("./mockAxios"));

// The maintainer's queue and review page, as a maintainer reads them: every
// filter says what it holds, the queue is a sortable table, and a review starts
// from the check report with the flags grouped by rule, one Yes or No each.
// Approval stays gated exactly as before.

const QUEUE = "/omega-hub/v1/review/queue";
const ago = (minutes) =>
  new Date(Date.now() - minutes * 60 * 1000).toISOString();
const STAMP = /^\d{1,2} \w{3,4} \d{4},? (at )?\d{2}:\d{2} \S+/;

const ID = {
  first: "3a3a3a3a-0000-4000-8000-000000000003",
  coding: "1a1a1a1a-0000-4000-8000-000000000001",
  csv: "2a2a2a2a-0000-4000-8000-000000000002",
};

// Built per test, so each test's times are measured from its own start.
function queued() {
  return [
    {
      id: ID.first,
      ref: "future-agi/first-agent",
      version: "1.0.0",
      kind: "agent",
      tier: "official",
      channel: "github",
      status: "in_review",
      isUpdate: false,
      flags: 3,
      createdAt: ago(60),
      reviewer: null,
      reviewStartedAt: null,
    },
    {
      id: ID.coding,
      ref: "future-agi/coding-agent",
      version: "1.0.0",
      kind: "agent",
      tier: "official",
      channel: "github",
      status: "in_review",
      isUpdate: false,
      flags: 7,
      createdAt: ago(43),
      reviewer: null,
      reviewStartedAt: null,
    },
    {
      id: ID.csv,
      ref: "c-test/csv-answers",
      version: "0.2.0",
      kind: "app",
      tier: "community",
      channel: "web",
      status: "in_review",
      isUpdate: true,
      flags: 0,
      createdAt: ago(25),
      reviewer: me.user.email,
      reviewStartedAt: ago(2),
    },
  ];
}

function serveQueue(rows = queued()) {
  respond([
    ["get", "/omega-hub/v1/me", me],
    [
      "get",
      QUEUE,
      ({ config }) => {
        const kind = config?.params?.kind;
        const results = rows.filter((row) => !kind || row.kind === kind);
        return { count: results.length, results };
      },
    ],
  ]);
  return rows;
}

const refs = () =>
  screen
    .getAllByTestId("hub-queue-row")
    .map((row) => row.getAttribute("data-ref"));

describe("Review queue", () => {
  it("shows every filter's current value, so no filter reads as blank", async () => {
    serveQueue();
    renderAt(<HubReviewQueuePage />, { at: "/dashboard/hub/review" });
    await screen.findAllByTestId("hub-queue-row");
    const filters = screen.getByRole("group", { name: "Filter the queue" });
    expect(
      within(filters)
        .getAllByRole("combobox")
        .map((box) => box.textContent),
    ).toEqual([
      "In review",
      "Any kind",
      "Any tier",
      "Any type",
      "Any flags",
      "Any channel",
    ]);
    for (const name of ["Status", "Kind", "Tier", "Type", "Flags", "Channel"])
      expect(
        within(filters).getByRole("combobox", { name: new RegExp(`^${name}`) }),
      ).toBeInTheDocument();
  });

  it("marks a narrowed filter, counts what matches, and clears every filter in one step", async () => {
    serveQueue();
    renderAt(<HubReviewQueuePage />, {
      at: "/dashboard/hub/review?kind=agent",
    });
    await screen.findAllByTestId("hub-queue-row");
    expect(screen.getByRole("combobox", { name: /^Kind/ })).toHaveTextContent(
      "Kind: Agents",
    );
    expect(screen.getByTestId("hub-queue-count")).toHaveTextContent(
      "2 waiting with these filters",
    );
    fireEvent.click(screen.getByRole("button", { name: "Clear all filters" }));
    await waitFor(() =>
      expect(screen.getByTestId("location")).toHaveTextContent(
        /^\/dashboard\/hub\/review$/,
      ),
    );
    expect(screen.getByRole("combobox", { name: /^Status/ })).toHaveFocus();
    expect(
      screen.queryByRole("button", { name: "Clear all filters" }),
    ).not.toBeInTheDocument();
    await waitFor(() =>
      expect(screen.getByTestId("hub-queue-count")).toHaveTextContent(
        /^3 waiting$/,
      ),
    );
  });

  it("is a table, oldest first, that sorts by its column headers", async () => {
    serveQueue();
    renderAt(<HubReviewQueuePage />, { at: "/dashboard/hub/review" });
    const table = await screen.findByRole("table", { name: "Review queue" });
    expect(
      within(table)
        .getAllByRole("columnheader")
        .map((cell) => cell.textContent.replace(/sorted (a|de)scending/, "")),
    ).toEqual(["Entry", "Kind", "Tier", "Waiting", "Checks", "Reviewer"]);
    expect(refs()).toEqual([
      "future-agi/first-agent",
      "future-agi/coding-agent",
      "c-test/csv-answers",
    ]);
    expect(
      within(table).getByRole("columnheader", { name: /Waiting/ }),
    ).toHaveAttribute("aria-sort", "descending");
    fireEvent.click(within(table).getByRole("button", { name: /^Checks/ }));
    expect(refs()).toEqual([
      "future-agi/coding-agent",
      "future-agi/first-agent",
      "c-test/csv-answers",
    ]);
    expect(
      within(table).getByRole("columnheader", { name: /Checks/ }),
    ).toHaveAttribute("aria-sort", "descending");
    expect(
      within(table).getByRole("columnheader", { name: /Waiting/ }),
    ).not.toHaveAttribute("aria-sort");
  });

  it("sorts by tier and by who holds the review", async () => {
    serveQueue();
    renderAt(<HubReviewQueuePage />, { at: "/dashboard/hub/review" });
    const table = await screen.findByRole("table", { name: "Review queue" });
    fireEvent.click(within(table).getByRole("button", { name: /^Tier/ }));
    expect(refs()).toEqual([
      "future-agi/first-agent",
      "future-agi/coding-agent",
      "c-test/csv-answers",
    ]);
    expect(screen.getByText("Official first")).toBeInTheDocument();
    fireEvent.click(within(table).getByRole("button", { name: /^Tier/ }));
    expect(refs()).toEqual([
      "c-test/csv-answers",
      "future-agi/first-agent",
      "future-agi/coding-agent",
    ]);
    expect(screen.getByText("Community first")).toBeInTheDocument();
    fireEvent.click(within(table).getByRole("button", { name: /^Reviewer/ }));
    expect(refs()).toEqual([
      "c-test/csv-answers",
      "future-agi/first-agent",
      "future-agi/coding-agent",
    ]);
    expect(
      within(table).getByRole("columnheader", { name: /Reviewer/ }),
    ).toHaveAttribute("aria-sort", "ascending");
  });

  it("names the ref as the row's link and opens the review from anywhere on the row", async () => {
    serveQueue();
    renderAt(<HubReviewQueuePage />, { at: "/dashboard/hub/review" });
    const [row] = await screen.findAllByTestId("hub-queue-row");
    expect(
      within(row).getByRole("link", { name: "future-agi/first-agent@1.0.0" }),
    ).toHaveAttribute("href", `/dashboard/hub/review/${ID.first}`);
    fireEvent.click(within(row).getByText("1 h"));
    expect(screen.getByTestId("location")).toHaveTextContent(
      `/dashboard/hub/review/${ID.first}`,
    );
  });

  it("opens the review in a new tab on a Cmd, Ctrl or middle click on the row", async () => {
    serveQueue();
    const open = vi.spyOn(window, "open").mockImplementation(() => null);
    try {
      renderAt(<HubReviewQueuePage />, { at: "/dashboard/hub/review" });
      const [row] = await screen.findAllByTestId("hub-queue-row");
      const waiting = within(row).getByText("1 h");
      fireEvent.click(waiting, { metaKey: true });
      fireEvent.click(waiting, { ctrlKey: true });
      fireEvent(
        waiting,
        new MouseEvent("auxclick", { bubbles: true, button: 1 }),
      );
      const href = `/dashboard/hub/review/${ID.first}`;
      expect(open.mock.calls).toEqual([
        [href, "_blank", "noopener"],
        [href, "_blank", "noopener"],
        [href, "_blank", "noopener"],
      ]);
      expect(screen.getByTestId("location")).toHaveTextContent(
        /^\/dashboard\/hub\/review$/,
      );
    } finally {
      open.mockRestore();
    }
  });

  it("shows how long each entry waits, with the submission time and its zone on hover, and who reviews it", async () => {
    const served = serveQueue();
    renderAt(<HubReviewQueuePage />, { at: "/dashboard/hub/review" });
    const rows = await screen.findAllByTestId("hub-queue-row");
    const waiting = within(rows[1]).getByText("43 min");
    expect(waiting.tagName).toBe("TIME");
    expect(waiting).toHaveAttribute("dateTime", served[1].createdAt);
    const title = waiting.getAttribute("title");
    expect(title).toMatch(/^Submitted /);
    expect(title.replace(/^Submitted /, "")).toMatch(STAMP);
    expect(rows[1]).toHaveTextContent("7 flags");
    expect(rows[2]).toHaveTextContent("You, started 2 min ago");
    expect(rows[2]).toHaveTextContent("Update · via Web upload");
  });

  it("keeps each row's tier under its ref, where a phone still shows it", async () => {
    serveQueue();
    renderAt(<HubReviewQueuePage />, { at: "/dashboard/hub/review" });
    const rows = await screen.findAllByTestId("hub-queue-row");
    // A phone hides the Tier column, so the entry cell carries the tier too.
    const entry = within(rows[2]).getAllByRole("cell")[0];
    expect(within(entry).getByTestId("hub-tier-badge")).toHaveAttribute(
      "data-tier",
      "community",
    );
  });

  it("says when nothing matches the filters and offers the whole queue", async () => {
    serveQueue([]);
    renderAt(<HubReviewQueuePage />, {
      at: "/dashboard/hub/review?tier=verified",
    });
    expect(
      await screen.findByText("Nothing matches these filters"),
    ).toBeInTheDocument();
    fireEvent.click(
      screen.getByRole("button", { name: "Show the whole queue" }),
    );
    await waitFor(() =>
      expect(screen.getByTestId("location")).toHaveTextContent(
        /^\/dashboard\/hub\/review$/,
      ),
    );
    expect(await screen.findByText("The queue is empty")).toBeInTheDocument();
  });
});

const BASE = `/omega-hub/v1/review/submissions/${review.id}`;
const DECISION = `${BASE}/decision`;
const DRAFTED =
  "Child processes (main.test.mjs:3): say why the entry needs it, or remove it.";
const current = {
  ...review,
  status: "in_review",
  channel: "github",
  previousVersion: null,
  draftDisclosure: review.confirmedDisclosure,
};
const routes = (row = current, extra = []) => [
  ["get", "/omega-hub/v1/me", me],
  ["get", "/omega-hub/v1/rules", rules],
  ["get", BASE, row],
  ["get", `${BASE}/diff`, { diff: null }],
  ...extra,
];
const openReview = () =>
  renderAt(<HubReviewPage />, {
    path: "/dashboard/hub/review/:id",
    at: `/dashboard/hub/review/${review.id}`,
  });
const submit = () => screen.getByTestId("hub-decision-submit");
const reasonInput = () => screen.getByTestId("hub-decision-reason-input");
const answerAll = (name) => {
  for (const radio of screen.getAllByRole("radio", { name }))
    fireEvent.click(radio);
};
// Answers the Child processes group, the second in report order.
const answerShell = (name) => {
  const shell = screen.getAllByTestId("hub-flag-group")[1];
  fireEvent.click(within(shell).getByRole("radio", { name }));
};
const tickChecklist = () => {
  for (const box of screen.getAllByTestId("hub-checklist-item"))
    fireEvent.click(box);
};
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

describe("Review page", () => {
  it("shows who submitted it, when, from where, and with which checker", async () => {
    respond(routes());
    openReview();
    const header = await screen.findByTestId("hub-review-header");
    expect(header).toHaveTextContent("Submitted by");
    expect(header).toHaveTextContent(review.submittedBy.email);
    const at = within(header).getByText((_, el) => el?.tagName === "TIME");
    expect(at).toHaveAttribute("dateTime", review.createdAt);
    expect(at.textContent).toMatch(STAMP);
    expect(header).toHaveTextContent("SourceGitHub");
    expect(header).toHaveTextContent(`Checker${review.report.checker.version}`);
  });

  it("opens with the check report summary", async () => {
    respond(routes());
    openReview();
    const summary = await screen.findByTestId("hub-check-summary");
    expect(
      within(summary).getByRole("heading", { level: 2, name: "Check report" }),
    ).toBeInTheDocument();
    expect(summary).toHaveTextContent(
      "Checks passed. 4 flags in 4 groups need your answer. Its keyless sandbox run passed in 6.6 s with the network off.",
    );
    const steps = within(summary).getByRole("list", { name: "Check steps" });
    expect(within(steps).getAllByRole("listitem")).toHaveLength(
      review.report.steps.length,
    );
    expect(steps).toHaveTextContent("Static scan3 flags");
  });

  it("names a sandbox network other than none as on, in the checker's word", () => {
    const report = {
      steps: [
        { id: "sandbox-run", status: "pass", ms: 6628, network: "allowlist" },
      ],
    };
    expect(summaryLine(report, { flagCount: 0, groupCount: 0 }).join("")).toBe(
      "Checks passed. Nothing was flagged. Its keyless sandbox run passed in 6.6 s with the network on (allowlist).",
    );
  });

  it("groups the flags by rule, each with one fixed hint and a Yes or No", async () => {
    respond(routes());
    openReview();
    const groups = await screen.findAllByTestId("hub-flag-group");
    expect(groups.map((group) => group.getAttribute("data-rule"))).toEqual([
      "writes-code",
      "child-process",
      "env-enumeration",
      "disclosure-gap",
    ]);
    const shell = groups[1];
    expect(within(shell).getByRole("heading", { level: 3 })).toHaveTextContent(
      "Child processes",
    );
    expect(shell).toHaveTextContent("imports node:child_process");
    expect(shell).toHaveTextContent(
      "Starts another program. Check that the Shell part of the disclosure names each command and when it runs.",
    );
    const answer = within(shell).getByRole("radiogroup", {
      name: /^Expected for this entry\?/,
    });
    expect(
      within(answer)
        .getAllByRole("radio")
        .map((radio) => radio.closest("label").textContent),
    ).toEqual(["Yes, it needs this", "No, ask for a change"]);
  });

  it("shows no count and no instruction for flags when the checks flagged nothing", async () => {
    respond(routes({ ...current, flags: [] }));
    openReview();
    const flags = await screen.findByTestId("hub-flag-groups");
    const heading = within(flags).getByRole("heading", {
      level: 2,
      name: "Flags",
    });
    expect(heading.parentElement).toHaveTextContent(/^Flags$/);
    expect(flags).toHaveTextContent(
      /^Flags\s*The checks flagged nothing in this version\.$/,
    );
    expect(document.body).toHaveTextContent(
      "Confirm the disclosure and the checklist, then decide.",
    );
    expect(document.body).not.toHaveTextContent("Answer each flag group");
  });

  it("states the publisher's record in words, with no zero counts", async () => {
    respond(routes());
    openReview();
    const card = await screen.findByTestId("hub-review-publisher");
    expect(card).toHaveTextContent("@future-agi");
    expect(card).toHaveTextContent(
      "3 listed entries · No past rejections · No open reports",
    );
  });

  it("names the submission's publisher, and claims no counts, when the server sends no record", async () => {
    respond(
      routes({ ...current, publisher: "c-test", publisherHistory: undefined }),
    );
    openReview();
    const card = await screen.findByTestId("hub-review-publisher");
    expect(card).toHaveTextContent(/^Publisher@c-test$/);
  });

  it("a Yes for every group acknowledges every flag, in the same payload as before", async () => {
    let sent;
    respond(
      routes(current, [
        [
          "post",
          DECISION,
          ({ data }) => {
            sent = data;
            return { status: "approved" };
          },
        ],
      ]),
    );
    openReview();
    await screen.findAllByTestId("hub-checklist-item");
    const card = screen.getByTestId("hub-decision-form");
    expect(card).toHaveTextContent("Flag groups answered: 0 of 4");
    expect(card).toHaveTextContent(`Checklist: 0 of ${rules.checklist.length}`);
    expect(card).toHaveTextContent("Disclosure: as drafted");
    answerAll("Yes, it needs this");
    tickChecklist();
    expect(card).toHaveTextContent("Flag groups answered: 4 of 4");
    await waitFor(() => expect(submit()).toBeEnabled());
    fireEvent.click(submit());
    await waitFor(() => expect(sent).toBeDefined());
    expect(sent.decision).toBe("approve");
    expect(sent.acknowledgedFlags).toEqual([...review.flags].sort());
    expect(Object.keys(sent.checklist).sort()).toEqual(
      rules.checklist.map((item) => item.key).sort(),
    );
  });

  it("a No makes Request changes the action, drafts the request, and keeps Approve off", async () => {
    respond(routes());
    openReview();
    await screen.findAllByTestId("hub-checklist-item");
    answerAll("Yes, it needs this");
    tickChecklist();
    await waitFor(() => expect(submit()).toBeEnabled());
    answerShell("No, ask for a change");
    expect(submit()).toHaveTextContent("Request changes");
    expect(screen.getByTestId("hub-decision-changes-tab")).toBeChecked();
    expect(reasonInput()).toHaveValue(DRAFTED);
    fireEvent.click(screen.getByTestId("hub-decision-approve-tab"));
    expect(submit()).toHaveTextContent("Approve and publish");
    expect(submit()).toBeDisabled();
    expect(screen.getByTestId("hub-approve-blockers")).toHaveTextContent(
      "To approve: change 1 No answer to Yes.",
    );
    expect(calls.filter((call) => call.method === "post")).toEqual([]);
  });

  it("gives the decision back to Approve when the only No turns Yes before the request is edited", async () => {
    respond(routes());
    openReview();
    await screen.findAllByTestId("hub-checklist-item");
    answerShell("No, ask for a change");
    expect(screen.getByTestId("hub-decision-changes-tab")).toBeChecked();
    answerShell("Yes, it needs this");
    expect(screen.getByTestId("hub-decision-approve-tab")).toBeChecked();
    expect(submit()).toHaveTextContent("Approve and publish");
    expect(
      screen.queryByTestId("hub-decision-reason-input"),
    ).not.toBeInTheDocument();
  });

  it("keeps Request changes after a Yes once the reviewer has written the request", async () => {
    respond(routes());
    openReview();
    await screen.findAllByTestId("hub-checklist-item");
    answerShell("No, ask for a change");
    const words = "Say in the Shell part which command main.test.mjs runs.";
    fireEvent.change(reasonInput(), { target: { value: words } });
    answerShell("Yes, it needs this");
    expect(screen.getByTestId("hub-decision-changes-tab")).toBeChecked();
    expect(reasonInput()).toHaveValue(words);
  });

  it("keeps Request changes after a Yes when the reviewer chose it", async () => {
    respond(routes());
    openReview();
    await screen.findAllByTestId("hub-checklist-item");
    fireEvent.click(screen.getByTestId("hub-decision-changes-tab"));
    answerShell("Yes, it needs this");
    expect(screen.getByTestId("hub-decision-changes-tab")).toBeChecked();
    expect(submit()).toHaveTextContent("Request changes");
  });

  it("does not carry the drafted request into a rejection", async () => {
    respond(routes());
    openReview();
    await screen.findAllByTestId("hub-checklist-item");
    answerShell("No, ask for a change");
    expect(reasonInput()).toHaveValue(DRAFTED);
    fireEvent.click(screen.getByTestId("hub-decision-reject-tab"));
    expect(reasonInput()).toHaveValue("");
    fireEvent.click(screen.getByTestId("hub-decision-changes-tab"));
    expect(reasonInput()).toHaveValue(DRAFTED);
  });

  it("keeps Approve off for the maintainer who submitted the version", async () => {
    respond(
      routes({
        ...current,
        submittedBy: { id: me.user.id, email: me.user.email },
      }),
    );
    openReview();
    await screen.findAllByTestId("hub-checklist-item");
    answerAll("Yes, it needs this");
    tickChecklist();
    await waitFor(() =>
      expect(screen.getByTestId("hub-approve-blockers")).toHaveTextContent(
        "To approve: another maintainer must approve it, because you submitted this version.",
      ),
    );
    expect(submit()).toBeDisabled();
  });

  it("reads the disclosure as rows with an Edit each, and what the checks observed", async () => {
    respond(routes());
    openReview();
    const editor = await screen.findByRole("group", {
      name: "Disclosure, as it will be published",
    });
    for (const label of [
      "Summary",
      "Network",
      "Files",
      "Shell",
      "Keeps running",
      "Credentials",
    ])
      expect(
        within(editor).getByRole("button", { name: `Edit ${label}` }),
      ).toBeInTheDocument();
    expect(within(editor).queryByRole("textbox")).not.toBeInTheDocument();
    expect(screen.getByTestId("hub-observed-credentials")).toHaveTextContent(
      "Observed by the checks: keys: ANTHROPIC_API_KEY, OMEGA_VERTEX_CREDS, OPENAI_API_KEY; environment enumeration: main.test.mjs:43",
    );
    fireEvent.click(within(editor).getByRole("button", { name: "Edit Shell" }));
    const box = within(editor).getByRole("textbox", {
      name: "Shell disclosure",
    });
    expect(box).toHaveFocus();
    fireEvent.change(box, {
      target: { value: "Runs node main.mjs in its test only." },
    });
    fireEvent.click(
      within(editor).getByRole("button", { name: "Done editing Shell" }),
    );
    expect(within(editor).queryByRole("textbox")).not.toBeInTheDocument();
    expect(editor).toHaveTextContent("Runs node main.mjs in its test only.");
    expect(editor).toHaveTextContent("Edited in review");
    expect(
      within(editor).getByRole("button", { name: "Edit Shell" }),
    ).toHaveFocus();
    expect(screen.getByTestId("hub-decision-form")).toHaveTextContent(
      "Disclosure: 1 part edited in review",
    );
  });

  it("opens a flagged line's file in Files", async () => {
    respond(
      routes(current, [["get", new RegExp(`^${BASE}/files/`), "x = 1\n"]]),
    );
    openReview();
    const shell = (await screen.findAllByTestId("hub-flag-group"))[1];
    fireEvent.click(
      within(shell).getByRole("button", {
        name: "Open main.test.mjs:3 in Files",
      }),
    );
    expect(screen.getByTestId("hub-review-tab-files")).toHaveAttribute(
      "aria-selected",
      "true",
    );
    await waitFor(() =>
      expect(
        calls.some((call) => call.path === `${BASE}/files/main.test.mjs`),
      ).toBe(true),
    );
  });

  it("on a narrow page, a decision bar follows the same gate and turns to the request after a No", async () => {
    const restore = narrowWindow();
    try {
      respond(routes());
      openReview();
      await screen.findAllByTestId("hub-checklist-item");
      const bar = screen.getByTestId("hub-decision-bar");
      expect(bar).toHaveTextContent(
        `0 of 4 flag groups · 0 of ${rules.checklist.length} checklist items`,
      );
      expect(
        within(bar).getByRole("button", { name: "Approve and publish" }),
      ).toBeDisabled();
      answerShell("No, ask for a change");
      expect(bar).toHaveTextContent("A flag group is answered No");
      fireEvent.click(
        within(bar).getByRole("button", { name: "Go to the request" }),
      );
      expect(reasonInput()).toHaveFocus();
      expect(calls.filter((call) => call.method === "post")).toEqual([]);
    } finally {
      restore();
    }
  });

  it("on a narrow page, the decision bar says why Approve is off once every answer is in", async () => {
    const restore = narrowWindow();
    try {
      respond(
        routes({
          ...current,
          submittedBy: { id: me.user.id, email: me.user.email },
        }),
      );
      openReview();
      await screen.findAllByTestId("hub-checklist-item");
      answerAll("Yes, it needs this");
      tickChecklist();
      const bar = screen.getByTestId("hub-decision-bar");
      await waitFor(() =>
        expect(bar).toHaveTextContent(
          "To approve: another maintainer must approve it, because you submitted this version",
        ),
      );
      const approve = within(bar).getByRole("button", {
        name: "Approve and publish",
      });
      expect(approve).toBeDisabled();
      expect(approve).toHaveAccessibleDescription(
        /^To approve: another maintainer must approve it/,
      );
    } finally {
      restore();
    }
  });
});
