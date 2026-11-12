import React from "react";
import {
  act,
  fireEvent,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { useQueryClient } from "@tanstack/react-query";

import HubReviewPage from "../HubReviewPage";
import review from "../__fixtures__/review-submission.json";
import rules from "../__fixtures__/rules.json";
import me from "../__fixtures__/me-maintainer.json";
import axios, { calls, respond } from "./mockAxios";
import { renderAt } from "./render";

vi.mock("src/utils/axios", () => import("./mockAxios"));

const BASE = `/omega-hub/v1/review/submissions/${review.id}`;
const DIFF = `${BASE}/diff`;
const error = { error: { message: "Diff unavailable" } };
const current = {
  ...review,
  status: "in_review",
  previousVersion: { version: "0.9.0" },
  draftDisclosure: review.confirmedDisclosure,
};
const routes = (diff = { diff: null }, status = 200) => [
  ["get", "/omega-hub/v1/me", me],
  ["get", "/omega-hub/v1/rules", rules],
  ["get", BASE, current],
  ["get", DIFF, diff, status],
];
const approve = () => screen.getByTestId("hub-decision-submit");
let client;
function QueryProbe() {
  client = useQueryClient();
  return null;
}
const page = () =>
  renderAt(
    <>
      <HubReviewPage />
      <QueryProbe />
    </>,
    {
      path: "/dashboard/hub/review/:id",
      at: `/dashboard/hub/review/${review.id}`,
    },
  );
async function completeChecklist() {
  await screen.findAllByTestId("hub-checklist-item");
  for (const box of screen.getAllByTestId("hub-flag-ack")) fireEvent.click(box);
  for (const box of screen.getAllByTestId("hub-checklist-item"))
    fireEvent.click(box);
}

beforeEach(() => respond(routes()));

describe("HUB-UX-06 missing diff is not an empty diff", () => {
  it("loads the diff before opening Changes and blocks approval until it succeeds", async () => {
    const original = axios.get.getMockImplementation();
    let finish;
    const delayed = vi.spyOn(axios, "get").mockImplementation((url, config) =>
      url === DIFF
        ? new Promise((resolve) => {
            finish = resolve;
          })
        : original(url, config),
    );
    try {
      page();
      await completeChecklist();
      expect(approve()).toBeDisabled();
      expect(
        within(screen.getByTestId("hub-decision-form")).getByText(
          "The changes are still loading.",
        ),
      ).toBeInTheDocument();
      const blockers = screen.getByTestId("hub-approve-blockers");
      expect(blockers).toHaveTextContent("To approve: the changes must load.");
      expect(blockers.nextElementSibling).toBe(approve());
      expect(approve()).toHaveAccessibleDescription(
        "To approve: the changes must load.",
      );
      expect(finish).toBeTypeOf("function");
      fireEvent.click(screen.getByTestId("hub-review-tab-diff"));
      expect(screen.getByTestId("hub-loading")).toBeInTheDocument();
      expect(
        screen.queryByText(/No changes were recorded/),
      ).not.toBeInTheDocument();
      await act(async () => finish({ data: { diff: null }, status: 200 }));
      await waitFor(() => expect(approve()).toBeEnabled());
      expect(screen.getByText(/No changes were recorded/)).toBeInTheDocument();
    } finally {
      delayed.mockImplementation(original);
    }
  });

  it("shows compact error and retries, preserving the approval gate when switching tabs", async () => {
    respond(routes(error, 400));
    page();
    await completeChecklist();
    fireEvent.click(screen.getByTestId("hub-review-tab-diff"));
    expect(await screen.findByTestId("hub-error")).toHaveTextContent(
      "The changes did not load",
    );
    expect(
      screen.queryByText(/No changes were recorded/),
    ).not.toBeInTheDocument();
    expect(approve()).toBeDisabled();
    fireEvent.click(screen.getByTestId("hub-review-tab-report"));
    expect(approve()).toBeDisabled();
    const panel = screen.getByTestId("hub-decision-form");
    const warning = within(panel).getByRole("alert");
    expect(warning).toHaveClass("MuiAlert-standardWarning");
    expect(warning).toHaveTextContent(
      "The changes didn't load. Try again before approving.",
    );
    expect(
      within(panel).queryByText("The changes are still loading."),
    ).not.toBeInTheDocument();
    const blockers = within(panel).getByTestId("hub-approve-blockers");
    expect(blockers).toHaveTextContent("To approve: the changes must load.");
    expect(blockers.nextElementSibling).toBe(approve());
    expect(approve()).toHaveAccessibleDescription(
      "To approve: the changes must load.",
    );
    fireEvent.click(approve());
    expect(calls.filter((call) => call.method === "post")).toHaveLength(0);
    respond(routes());
    fireEvent.click(within(warning).getByRole("button", { name: "Try again" }));
    await waitFor(() => expect(approve()).toBeEnabled());
    expect(calls.some((call) => call.path === DIFF)).toBe(true);
    expect(screen.queryByTestId("hub-error")).not.toBeInTheDocument();
  });

  it.each(["decision", "changes"])(
    "replaces a stale diff error with focused loading feedback in %s",
    async (surface) => {
      page();
      await completeChecklist();
      await waitFor(() => expect(approve()).toBeEnabled());
      // A failed background refresh retains cached data. React Query keeps its
      // error status while the next refetch is fetching, unlike an initial miss.
      respond(routes(error, 400));
      await act(async () => client.refetchQueries());
      if (surface === "changes")
        fireEvent.click(screen.getByTestId("hub-review-tab-diff"));
      const panel =
        surface === "decision"
          ? screen.getByTestId("hub-decision-form")
          : (await screen.findByTestId("hub-error")).parentElement;
      const retry = await within(panel).findByRole("button", {
        name: "Try again",
      });
      const original = axios.get.getMockImplementation();
      let finish;
      const delayed = vi
        .spyOn(axios, "get")
        .mockImplementation((url, config) =>
          url === DIFF
            ? new Promise((resolve) => {
                finish = resolve;
              })
            : original(url, config),
        );
      try {
        retry.focus();
        fireEvent.click(retry);
        await waitFor(() => expect(finish).toBeTypeOf("function"));
        expect(
          await within(panel).findByText("The changes are still loading."),
        ).toBeInTheDocument();
        const status =
          surface === "decision" ? within(panel).getByRole("status") : panel;
        expect(status).toHaveAttribute("role", "status");
        expect(status).toHaveFocus();
        expect(within(panel).queryByRole("alert")).not.toBeInTheDocument();
        expect(
          within(panel).queryByRole("button", { name: "Try again" }),
        ).not.toBeInTheDocument();
        expect(approve()).toBeDisabled();
        expect(screen.getByTestId("hub-approve-blockers")).toHaveTextContent(
          "the changes must load",
        );
        await act(async () => finish({ data: { diff: null }, status: 200 }));
        await waitFor(() => expect(approve()).toBeEnabled());
        expect(
          within(panel).queryByTestId("hub-approve-blockers"),
        ).not.toBeInTheDocument();
      } finally {
        if (finish)
          await act(async () => finish({ data: { diff: null }, status: 200 }));
        delayed.mockImplementation(original);
      }
    },
  );

  it("keeps approval enabled during a background diff refetch with cached data", async () => {
    page();
    await completeChecklist();
    await waitFor(() => expect(approve()).toBeEnabled());
    const original = axios.get.getMockImplementation();
    let finish;
    const delayed = vi.spyOn(axios, "get").mockImplementation((url, config) =>
      url === DIFF
        ? new Promise((resolve) => {
            finish = resolve;
          })
        : original(url, config),
    );
    try {
      act(() => {
        void client.refetchQueries();
      });
      await waitFor(() => expect(finish).toBeTypeOf("function"));
      await waitFor(() => expect(client.isFetching()).toBeGreaterThan(0));
      expect(approve()).toBeEnabled();
      expect(
        screen.queryByTestId("hub-approve-blockers"),
      ).not.toBeInTheDocument();
    } finally {
      if (finish)
        await act(async () => finish({ data: { diff: null }, status: 200 }));
      delayed.mockImplementation(original);
    }
  });

  it.each(["reject", "changes"])(
    "does not disable %s for a diff failure",
    async (decision) => {
      respond(routes(error, 400));
      page();
      await completeChecklist();
      fireEvent.click(screen.getByTestId("hub-review-tab-diff"));
      await screen.findByText("The changes did not load");
      fireEvent.click(screen.getByTestId(`hub-decision-${decision}-tab`));
      fireEvent.change(screen.getByTestId("hub-decision-reason-input"), {
        target: { value: "The disclosure does not describe this code." },
      });
      expect(approve()).toBeEnabled();
    },
  );
});
