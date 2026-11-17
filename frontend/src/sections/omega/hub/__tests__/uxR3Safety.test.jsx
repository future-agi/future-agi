import React from "react";
import {
  act,
  fireEvent,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import HubPublishersReviewPage from "../HubPublishersReviewPage";
import DecisionForm from "../components/DecisionForm";
import ModerateMenu from "../components/ModerateMenu";
import official from "../__fixtures__/entry-official.json";
import review from "../__fixtures__/review-submission.json";
import rules from "../__fixtures__/rules.json";
import me from "../__fixtures__/me-maintainer.json";
import axios, { calls, respond } from "./mockAxios";
import { renderAt } from "./render";

vi.mock("src/utils/axios", () => import("./mockAxios"));

const SUSPEND = "/omega-hub/v1/review/publishers/test-publisher/suspend";
const DECISION = `/omega-hub/v1/review/submissions/${review.id}/decision`;
const YANK = "/omega-hub/v1/review/versions/future-agi/first-agent/1.0.0/yank";
const suspension =
  "The publisher can no longer publish. All their entries are removed and all their publishing tokens are revoked.";
const yankConsequence =
  "This version is marked as yanked and excluded from latest and new installs by default. It remains available by its exact version. Your reason is shown publicly and emailed to the publisher.";
const posted = (path) =>
  calls.filter((c) => c.method === "post" && c.path === path);

async function pendingConfirmation(dialog, button, path) {
  const original = axios.post.getMockImplementation();
  let finish;
  const delayed = vi.spyOn(axios, "post").mockImplementation((url, ...args) =>
    url === path
      ? new Promise((resolve, reject) => {
          finish = () => original(url, ...args).then(resolve, reject);
        })
      : original(url, ...args),
  );
  try {
    fireEvent.click(button);
    await waitFor(() => expect(button).toBeDisabled());
    expect(finish).toBeTypeOf("function");
    const cancel = within(dialog).getByRole("button", { name: "Cancel" });
    expect(cancel).toBeDisabled();
    const close = within(dialog).queryByRole("button", {
      name: "Close dialog",
    });
    if (close) expect(close).toBeDisabled();
    fireEvent.click(cancel);
    fireEvent.keyDown(dialog, { key: "Escape" });
    expect(screen.getByRole("dialog")).toBeInTheDocument();
    await act(async () => finish());
    await waitFor(() =>
      expect(screen.queryByRole("dialog")).not.toBeInTheDocument(),
    );
    expect(posted(path)).toHaveLength(1);
  } finally {
    if (finish && posted(path).length === 0) await act(async () => finish());
    delayed.mockImplementation(original);
  }
}

describe("Publisher note visibility and named confirmations", () => {
  it.each([
    [
      "community",
      false,
      "Verify",
      "verify",
      "Message to the publisher",
      "Sent to the publisher by email",
    ],
    [
      "verified",
      false,
      "Remove verification",
      "unverify",
      "Message to the publisher",
      "Sent to the publisher by email",
    ],
    [
      "community",
      true,
      "Unsuspend",
      "unsuspend",
      "Note (not saved)",
      "Required to unsuspend. This note is not saved or sent to the publisher.",
    ],
  ])(
    "discloses where the %s / %s / %s note goes",
    async (tier, suspended, verb, action, label, helper) => {
      const path = `/omega-hub/v1/review/publishers/test-publisher/${action}`;
      respond([
        ["get", "/omega-hub/v1/me", me],
        [
          "get",
          "/omega-hub/v1/review/publishers",
          [
            {
              handle: "test-publisher",
              displayName: "Test publisher",
              tier,
              suspended,
            },
          ],
        ],
        ["post", path, {}],
      ]);
      renderAt(<HubPublishersReviewPage />);
      const row = await screen.findByTestId("hub-publisher-review-row");
      const input = within(row).getByLabelText(label);
      expect(input).toHaveAccessibleDescription(helper);
      const button = within(row).getByRole("button", {
        name: verb,
        exact: true,
      });
      expect(button).toBeDisabled();
      if (!suspended) {
        fireEvent.change(within(row).getByLabelText("Note (private)"), {
          target: { value: "Private suspension context" },
        });
        expect(button).toBeDisabled();
      }
      fireEvent.change(input, { target: { value: " Action-specific note. " } });
      fireEvent.click(button);
      if (action === "unverify") {
        fireEvent.click(
          within(screen.getByRole("dialog")).getByRole("button", {
            name: "Remove verification",
          }),
        );
      }
      await waitFor(() => expect(posted(path)).toHaveLength(1));
      expect(posted(path)[0].data).toEqual({ note: "Action-specific note." });
    },
  );

  it("clears a failed verification before opening the suspension confirmation", async () => {
    respond([
      ["get", "/omega-hub/v1/me", me],
      [
        "get",
        "/omega-hub/v1/review/publishers",
        [
          {
            handle: "test-publisher",
            displayName: "Test publisher",
            tier: "community",
            suspended: false,
          },
        ],
      ],
      [
        "post",
        "/omega-hub/v1/review/publishers/test-publisher/verify",
        { error: { message: "Verification failed" } },
        400,
      ],
    ]);
    renderAt(<HubPublishersReviewPage />);
    const row = await screen.findByTestId("hub-publisher-review-row");
    // Reach the stale-error regression on both the old shared field and the
    // new action-specific fields; separate tests pin the new labels/helpers.
    const message =
      within(row).queryByLabelText("Message to the publisher") ||
      within(row).getByLabelText("Note");
    fireEvent.change(message, { target: { value: "Verification feedback." } });
    fireEvent.click(
      within(row).getByRole("button", { name: "Verify", exact: true }),
    );
    await within(row).findByText("Verification failed");
    const note =
      within(row).queryByLabelText("Note (private)") ||
      within(row).getByLabelText("Note");
    fireEvent.change(note, {
      target: { value: "Private suspension context." },
    });
    fireEvent.click(
      within(row).getByRole("button", { name: "Suspend", exact: true }),
    );
    expect(
      within(screen.getByRole("dialog")).queryByRole("alert"),
    ).not.toBeInTheDocument();
  });

  it.each(["Unrank", "Remove"])(
    "names the entry in %s and clears a prior confirmation error",
    async (verb) => {
      const path = `/omega-hub/v1/review/entries/future-agi/first-agent/${verb.toLowerCase()}`;
      respond([
        ["post", path, { error: { message: "Previous action failed" } }, 400],
      ]);
      renderAt(<ModerateMenu entry={official} version="1.0.0" />);
      const open = () => {
        fireEvent.click(screen.getByTestId("hub-moderate-button"));
        fireEvent.click(
          screen.getByRole("menuitem", { name: verb, exact: true }),
        );
      };
      open();
      let dialog = screen.getByRole("dialog");
      expect(within(dialog).getByRole("heading")).toHaveTextContent(
        `${verb} future-agi/first-agent?`,
      );
      fireEvent.change(within(dialog).getByLabelText("Note (private)"), {
        target: { value: "Reviewed moderation." },
      });
      fireEvent.click(
        within(dialog).getByRole("button", { name: verb, exact: true }),
      );
      expect(await within(dialog).findByRole("alert")).toHaveTextContent(
        "Previous action failed",
      );
      fireEvent.click(within(dialog).getByRole("button", { name: "Cancel" }));
      await waitFor(() =>
        expect(screen.queryByRole("dialog")).not.toBeInTheDocument(),
      );
      open();
      dialog = screen.getByRole("dialog");
      expect(within(dialog).queryByRole("alert")).not.toBeInTheDocument();
    },
  );
});

describe("Round-three destructive-action safety", () => {
  it("names the publisher, confirms suspension, and retains a failed confirmation for retry", async () => {
    const routes = (status = 200) => [
      ["get", "/omega-hub/v1/me", me],
      [
        "get",
        "/omega-hub/v1/review/publishers",
        [
          {
            handle: "test-publisher",
            displayName: "Test publisher",
            tier: "community",
            suspended: false,
          },
        ],
      ],
      [
        "post",
        SUSPEND,
        status === 200 ? {} : { error: { message: "Suspension failed" } },
        status,
      ],
    ];
    respond(routes(400));
    renderAt(<HubPublishersReviewPage />);
    const row = await screen.findByTestId("hub-publisher-review-row");
    const trigger = within(row).getByRole("button", {
      name: "Suspend",
      exact: true,
    });
    expect(trigger).toBeDisabled();
    const note = within(row).getByLabelText("Note (private)");
    expect(note).toHaveAccessibleDescription(
      "Not sent to the publisher or shown publicly",
    );
    fireEvent.change(within(row).getByLabelText("Message to the publisher"), {
      target: { value: "Verification feedback for the publisher." },
    });
    expect(trigger).toBeDisabled();
    fireEvent.change(note, {
      target: { value: " Reviewed suspension. " },
    });
    expect(trigger).toHaveClass("MuiButton-outlinedInherit");
    fireEvent.click(trigger);
    let dialog = screen.getByRole("dialog");
    expect(within(dialog).getByRole("heading")).toHaveTextContent(
      "Suspend publisher @test-publisher?",
    );
    expect(dialog).toHaveTextContent(suspension);
    expect(posted(SUSPEND)).toHaveLength(0);
    fireEvent.click(within(dialog).getByRole("button", { name: "Cancel" }));
    await waitFor(() =>
      expect(screen.queryByRole("dialog")).not.toBeInTheDocument(),
    );
    expect(posted(SUSPEND)).toHaveLength(0);
    fireEvent.click(trigger);
    dialog = screen.getByRole("dialog");
    const confirm = within(dialog).getByRole("button", {
      name: "Suspend publisher",
      exact: true,
    });
    expect(confirm).toHaveClass("MuiButton-containedError");
    fireEvent.click(confirm);
    expect(await within(dialog).findByRole("alert")).toHaveTextContent(
      "Suspension failed",
    );
    expect(posted(SUSPEND)).toHaveLength(1);
    expect(posted(SUSPEND)[0].data).toEqual({ note: "Reviewed suspension." });
    fireEvent.click(within(dialog).getByRole("button", { name: "Cancel" }));
    await waitFor(() =>
      expect(screen.queryByRole("dialog")).not.toBeInTheDocument(),
    );
    fireEvent.click(trigger);
    dialog = screen.getByRole("dialog");
    expect(within(dialog).queryByRole("alert")).not.toBeInTheDocument();
    respond(routes());
    await pendingConfirmation(
      dialog,
      within(dialog).getByRole("button", { name: "Suspend publisher" }),
      SUSPEND,
    );
  });

  it("confirms terminal rejection with the submission reference and preserves errors for retry", async () => {
    const routes = (status = 200) => [
      ["get", "/omega-hub/v1/rules", rules],
      [
        "post",
        DECISION,
        status === 200
          ? { status: "rejected" }
          : { error: { message: "Decision failed" } },
        status,
      ],
    ];
    respond(routes(400));
    const onDecided = vi.fn();
    renderAt(<DecisionForm submission={review} onDecided={onDecided} />);
    await screen.findAllByTestId("hub-checklist-item");
    fireEvent.click(screen.getByTestId("hub-decision-reject-tab"));
    const trigger = screen.getByTestId("hub-decision-submit");
    expect(trigger).toBeDisabled();
    fireEvent.change(screen.getByTestId("hub-decision-reason-input"), {
      target: { value: "This submission does not meet the admission rules." },
    });
    expect(trigger).toHaveClass("MuiButton-outlinedInherit");
    fireEvent.click(trigger);
    let dialog = screen.getByRole("dialog");
    expect(within(dialog).getByRole("heading")).toHaveTextContent(
      `Reject ${review.publisher}/${review.name}@${review.version}?`,
    );
    expect(dialog).toHaveTextContent(
      "This submission cannot be reopened. The submitter receives your reason by email.",
    );
    expect(posted(DECISION)).toHaveLength(0);
    expect(onDecided).not.toHaveBeenCalled();
    fireEvent.click(within(dialog).getByRole("button", { name: "Cancel" }));
    await waitFor(() =>
      expect(screen.queryByRole("dialog")).not.toBeInTheDocument(),
    );
    expect(posted(DECISION)).toHaveLength(0);
    fireEvent.click(trigger);
    dialog = screen.getByRole("dialog");
    const confirm = within(dialog).getByRole("button", {
      name: "Reject",
      exact: true,
    });
    expect(confirm).toHaveClass("MuiButton-containedError");
    fireEvent.click(confirm);
    expect(await within(dialog).findByRole("alert")).toHaveTextContent(
      "Decision failed",
    );
    expect(onDecided).not.toHaveBeenCalled();
    expect(posted(DECISION)[0].data).toMatchObject({
      decision: "reject",
      reason: "This submission does not meet the admission rules.",
      rulesVersion: rules.version,
    });
    fireEvent.click(within(dialog).getByRole("button", { name: "Cancel" }));
    await waitFor(() =>
      expect(screen.queryByRole("dialog")).not.toBeInTheDocument(),
    );
    fireEvent.click(trigger);
    dialog = screen.getByRole("dialog");
    expect(within(dialog).queryByRole("alert")).not.toBeInTheDocument();
    respond(routes());
    await pendingConfirmation(
      dialog,
      within(dialog).getByRole("button", { name: "Reject", exact: true }),
      DECISION,
    );
    expect(onDecided).toHaveBeenCalledWith({ status: "rejected" }, "reject");
  });

  it("labels the Moderate-menu yank reason public and cannot close while pending", async () => {
    respond([["post", YANK, {}]]);
    renderAt(<ModerateMenu entry={official} version="1.0.0" />);
    fireEvent.click(screen.getByTestId("hub-moderate-button"));
    fireEvent.click(
      screen.getByRole("menuitem", { name: "Yank version 1.0.0" }),
    );
    const dialog = screen.getByRole("dialog");
    const input = within(dialog).getByLabelText("Public reason");
    expect(input).toHaveAccessibleDescription(
      "Shown publicly on the entry page and on downloads of this version, and emailed to the publisher.",
    );
    expect(dialog).toHaveTextContent(yankConsequence);
    expect(dialog).not.toHaveTextContent("The note stays private.");
    expect(
      within(dialog).queryByLabelText("Note (private)"),
    ).not.toBeInTheDocument();
    fireEvent.change(input, {
      target: { value: "This version has a security issue." },
    });
    await pendingConfirmation(
      dialog,
      within(dialog).getByRole("button", { name: "Yank", exact: true }),
      YANK,
    );
    expect(posted(YANK)[0].data).toEqual({
      reason: "This version has a security issue.",
    });
  });
});
