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
import me from "../__fixtures__/me-maintainer.json";
import axios, { calls, respond } from "./mockAxios";
import { renderAt } from "./render";

vi.mock("src/utils/axios", () => import("./mockAxios"));

const BASE = "/omega-hub/v1/review/publishers";
const publisher = {
  handle: "test-publisher",
  displayName: "Test publisher",
  tier: "community",
  suspended: false,
};
const posted = () => calls.filter((call) => call.method === "post");

async function openAll() {
  renderAt(<HubPublishersReviewPage />);
  await screen.findByTestId("hub-publisher-review-row");
  fireEvent.click(screen.getByRole("tab", { name: "All publishers" }));
  return screen.findByTestId("hub-publisher-review-row");
}

const confirm = (name) =>
  within(screen.getByRole("dialog")).getByRole("button", { name, exact: true });

const closeDialog = async () => {
  fireEvent.click(confirm("Cancel"));
  await waitFor(() =>
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument(),
  );
};

describe("Round-five publisher action state", () => {
  it.each([
    [
      "verify",
      "Verify",
      "community",
      false,
      "Remove verification",
      "Message to the publisher",
      "Note (private)",
    ],
    [
      "unverify",
      "Remove verification",
      "verified",
      false,
      "Verify",
      "Message to the publisher",
      "Note (private)",
    ],
    [
      "suspend",
      "Suspend",
      "community",
      false,
      "Unsuspend",
      "Note (private)",
      "Message to the publisher",
    ],
    [
      "unsuspend",
      "Unsuspend",
      "community",
      true,
      "Suspend",
      "Note (not saved)",
      "Message to the publisher",
    ],
  ])(
    "clears only the owning field after %s succeeds on a retained row",
    async (action, verb, tier, suspended, nextVerb, label, otherLabel) => {
      let current = { ...publisher, tier, suspended };
      respond([
        ["get", "/omega-hub/v1/me", me],
        ["get", BASE, () => [current]],
        [
          "post",
          `${BASE}/${publisher.handle}/${action}`,
          () => {
            current = {
              ...current,
              ...(action === "verify" ? { tier: "verified" } : {}),
              ...(action === "unverify" ? { tier: "community" } : {}),
              ...(action === "suspend" ? { suspended: true } : {}),
              ...(action === "unsuspend" ? { suspended: false } : {}),
            };
            return current;
          },
        ],
      ]);
      const row = await openAll();
      const input = within(row).getByLabelText(label);
      const other = within(row).getByLabelText(otherLabel);
      fireEvent.change(input, { target: { value: " This action's text. " } });
      fireEvent.change(other, { target: { value: "Other field draft" } });
      fireEvent.click(
        within(row).getByRole("button", { name: verb, exact: true }),
      );
      if (action === "suspend" || action === "unverify") {
        fireEvent.click(
          confirm(action === "suspend" ? "Suspend publisher" : verb),
        );
      }
      await waitFor(() => expect(posted()).toHaveLength(1));
      await waitFor(() => expect(input).toHaveValue(""));
      await waitFor(() =>
        expect(
          within(row).getByRole("button", { name: nextVerb, exact: true }),
        ).toBeDisabled(),
      );
      expect(other).toHaveValue("Other field draft");
      expect(posted()[0].data).toEqual({ note: "This action's text." });
      expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    },
  );

  it("confirms removal by name, cancels without posting, retains failures, and blocks close while pending", async () => {
    let fail = true;
    const path = `${BASE}/${publisher.handle}/unverify`;
    respond([
      ["get", "/omega-hub/v1/me", me],
      ["get", BASE, [{ ...publisher, tier: "verified" }]],
      [
        "post",
        path,
        () => {
          if (fail)
            throw Object.assign(new Error("Verification removal failed"), {
              error: { message: "Verification removal failed" },
              statusCode: 400,
            });
          return {};
        },
      ],
    ]);
    const row = await openAll();
    const input = within(row).getByLabelText("Message to the publisher");
    fireEvent.change(input, {
      target: { value: "Please submit new evidence." },
    });
    const trigger = within(row).getByRole("button", {
      name: "Remove verification",
    });
    fireEvent.click(trigger);
    let dialog = screen.getByRole("dialog");
    expect(within(dialog).getByRole("heading")).toHaveTextContent(
      "Remove verification from @test-publisher?",
    );
    expect(dialog).toHaveTextContent(
      "The publisher returns to the Community tier without a verified badge, and your message is emailed to them.",
    );
    expect(confirm("Remove verification")).toHaveClass(
      "MuiButton-containedError",
    );
    expect(posted()).toHaveLength(0);
    await closeDialog();
    expect(posted()).toHaveLength(0);
    fireEvent.click(trigger);
    fireEvent.click(confirm("Remove verification"));
    dialog = screen.getByRole("dialog");
    expect(await within(dialog).findByRole("alert")).toHaveTextContent(
      "Verification removal failed",
    );
    expect(input).toHaveValue("Please submit new evidence.");
    await closeDialog();
    fireEvent.click(trigger);
    dialog = screen.getByRole("dialog");
    expect(within(dialog).queryByRole("alert")).not.toBeInTheDocument();
    fail = false;
    const original = axios.post.getMockImplementation();
    let finish;
    const delayed = vi
      .spyOn(axios, "post")
      .mockImplementation((url, ...args) =>
        url === path
          ? new Promise((resolve, reject) => {
              finish = () => original(url, ...args).then(resolve, reject);
            })
          : original(url, ...args),
      );
    try {
      fireEvent.click(confirm("Remove verification"));
      await waitFor(() =>
        expect(confirm("Remove verification")).toBeDisabled(),
      );
      expect(confirm("Cancel")).toBeDisabled();
      const close = within(dialog).queryByRole("button", {
        name: "Close dialog",
      });
      if (close) expect(close).toBeDisabled();
      fireEvent.click(confirm("Cancel"));
      fireEvent.keyDown(dialog, { key: "Escape" });
      expect(screen.getByRole("dialog")).toBeInTheDocument();
      await act(async () => finish());
      await waitFor(() =>
        expect(screen.queryByRole("dialog")).not.toBeInTheDocument(),
      );
      expect(input).toHaveValue("");
      expect(trigger).toBeDisabled();
      expect(posted()).toHaveLength(2);
    } finally {
      delayed.mockImplementation(original);
    }
  });
});
