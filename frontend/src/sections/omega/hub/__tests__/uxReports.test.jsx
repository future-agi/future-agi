import React from "react";
import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import HubReportsPage from "../HubReportsPage";
import me from "../__fixtures__/me-maintainer.json";
import { calls, respond } from "./mockAxios";
import { renderAt } from "./render";

vi.mock("src/utils/axios", () => import("./mockAxios"));

const report = {
  id: "report-1",
  entry: "test-publisher/test-entry",
  version: "1.0.0",
  reason: "security",
  details: "Please review this version.",
  status: "open",
  reporter: "reporter@example.test",
  createdAt: "2026-10-30T12:00:00Z",
};
const RESOLVE = "/omega-hub/v1/review/reports/report-1/resolve";
const routes = (status = 200, version = report.version) => [
  ["get", "/omega-hub/v1/me", me],
  ["get", "/omega-hub/v1/review/reports", [{ ...report, version }]],
  [
    "post",
    RESOLVE,
    status === 200 ? {} : { error: { message: "Resolution failed" } },
    status,
  ],
];
const posted = () =>
  calls.filter((call) => call.method === "post" && call.path === RESOLVE);

beforeEach(() => respond(routes()));

async function choose(label) {
  renderAt(<HubReportsPage />);
  const row = await screen.findByTestId("hub-report-row");
  fireEvent.mouseDown(within(row).getByLabelText("Action"));
  fireEvent.click(screen.getByRole("option", { name: label, exact: true }));
  const noteLabel =
    {
      "Yank a version": "Public reason",
      "Ask the publisher": "Message to the publisher",
    }[label] || "Note (private)";
  const input = within(row).getByLabelText(noteLabel);
  const helper =
    {
      "Yank a version":
        "Shown publicly on the entry page and on downloads of this version, and emailed to the publisher.",
      "Ask the publisher": "Sent to the publisher by email",
    }[label] || "Not sent to the publisher or shown publicly";
  expect(input).toHaveAccessibleDescription(helper);
  if (noteLabel !== "Note (private)")
    expect(
      within(row).queryByLabelText("Note (private)"),
    ).not.toBeInTheDocument();
  fireEvent.change(input, {
    target: { value: "Reviewed the report." },
  });
  if (label === "Remove the entry" || label === "Unrank the entry") {
    fireEvent.mouseDown(
      within(row).getByLabelText(
        label === "Remove the entry" ? "Category" : "Reason",
      ),
    );
    fireEvent.click(screen.getAllByRole("option")[0]);
  }
  return row;
}

describe("HUB-UX-04 report resolution", () => {
  it.each([
    ["Remove the entry", "Remove entry", "remove_entry"],
    ["Yank a version", "Yank version", "yank_version"],
    ["Suspend the publisher", "Suspend publisher", "suspend_publisher"],
    ["Unrank the entry", "Unrank entry", "unrank_entry"],
  ])(
    "confirms %s with the target and same action verb",
    async (option, label, action) => {
      const row = await choose(option);
      if (action === "yank_version") {
        const version = within(row).getByLabelText(/^Version/);
        expect(version).toHaveValue(report.version);
        expect(version).toHaveAttribute("readonly");
        // Even a synthetic change cannot override the report-bound version.
        fireEvent.change(version, {
          target: { value: "2.0.0" },
        });
      }
      const button = within(row).getByRole("button", {
        name: label,
        exact: true,
      });
      expect(button).toHaveClass("MuiButton-outlinedInherit");
      expect(button).not.toHaveClass("MuiButton-containedError");
      fireEvent.click(button);
      const dialog = screen.getByRole("dialog");
      expect(within(dialog).getByRole("heading")).toHaveTextContent(
        action === "suspend_publisher" ? "@test-publisher" : report.entry,
      );
      const consequence = {
        remove_entry:
          "The entry is removed and its versions can no longer be downloaded.",
        yank_version:
          "This version is marked as yanked and excluded from latest and new installs by default. It remains available by its exact version. Your reason is shown publicly and emailed to the publisher.",
        suspend_publisher:
          "The publisher can no longer publish. All their entries are removed and all their publishing tokens are revoked.",
        unrank_entry:
          "The entry leaves browse lists but remains available by its exact reference.",
      }[action];
      expect(dialog).toHaveTextContent(consequence);
      if (action === "yank_version") {
        // community.resolve_report prefers report.version over the submitted version.
        const serverTarget = report.version || "2.0.0";
        expect(within(dialog).getByRole("heading")).toHaveTextContent(
          `Yank version ${report.entry}@${serverTarget}?`,
        );
      }
      if (action === "suspend_publisher")
        expect(dialog).toHaveTextContent("@test-publisher");
      expect(posted()).toHaveLength(0);
      fireEvent.click(within(dialog).getByRole("button", { name: "Cancel" }));
      await waitFor(() =>
        expect(screen.queryByRole("dialog")).not.toBeInTheDocument(),
      );
      expect(posted()).toHaveLength(0);
      fireEvent.click(button);
      const confirm = within(screen.getByRole("dialog")).getByRole("button", {
        name: label,
        exact: true,
      });
      expect(confirm).toHaveClass("MuiButton-containedError");
      fireEvent.click(confirm);
      await waitFor(() => expect(posted()).toHaveLength(1));
      expect(posted()[0].data).toMatchObject({
        action,
        note: "Reviewed the report.",
      });
      if (action === "yank_version")
        expect(posted()[0].data.version).toBe(report.version);
    },
  );

  it.each([
    ["Dismiss", "Dismiss", "dismiss"],
    ["Ask the publisher", "Ask publisher", "ask_publisher"],
    ["Restore", "Restore", "restore"],
  ])(
    "names %s and submits it without a destructive confirmation",
    async (option, label, action) => {
      const row = await choose(option);
      fireEvent.click(
        within(row).getByRole("button", { name: label, exact: true }),
      );
      await waitFor(() => expect(posted()).toHaveLength(1));
      expect(posted()[0].data.action).toBe(action);
      expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    },
  );

  it("requires an explicit version for an unbound report and confirms exactly what is sent", async () => {
    respond(routes(200, null));
    const row = await choose("Yank a version");
    const input = within(row).getByLabelText(/^Version/);
    const button = within(row).getByRole("button", { name: "Yank version" });
    expect(input).toBeRequired();
    expect(input).not.toHaveAttribute("readonly");
    expect(button).toBeDisabled();
    fireEvent.click(button);
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(posted()).toHaveLength(0);
    fireEvent.change(input, { target: { value: "   " } });
    expect(button).toBeDisabled();
    fireEvent.change(input, { target: { value: " 2.0.0 " } });
    expect(button).toBeEnabled();
    fireEvent.click(button);
    const dialog = screen.getByRole("dialog");
    expect(within(dialog).getByRole("heading")).toHaveTextContent(
      `Yank version ${report.entry}@2.0.0?`,
    );
    expect(dialog).not.toHaveTextContent(/@undefined|@\?/);
    fireEvent.click(
      within(dialog).getByRole("button", { name: "Yank version" }),
    );
    await waitFor(() => expect(posted()).toHaveLength(1));
    expect(posted()[0].data.version).toBe("2.0.0");
  });

  it("retains a failed destructive confirmation and shows its error", async () => {
    respond(routes(400));
    const row = await choose("Suspend the publisher");
    fireEvent.click(
      within(row).getByRole("button", {
        name: "Suspend publisher",
        exact: true,
      }),
    );
    const dialog = screen.getByRole("dialog");
    fireEvent.click(
      within(dialog).getByRole("button", {
        name: "Suspend publisher",
        exact: true,
      }),
    );
    expect(await within(dialog).findByRole("alert")).toHaveTextContent(
      "Resolution failed",
    );
    expect(posted()).toHaveLength(1);
    fireEvent.click(within(dialog).getByRole("button", { name: "Cancel" }));
    await waitFor(() =>
      expect(screen.queryByRole("dialog")).not.toBeInTheDocument(),
    );
    fireEvent.click(
      within(row).getByRole("button", {
        name: "Suspend publisher",
        exact: true,
      }),
    );
    expect(
      within(screen.getByRole("dialog")).queryByRole("alert"),
    ).not.toBeInTheDocument();
  });
});
