import React from "react";
import {
  act,
  fireEvent,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import { useQueryClient } from "@tanstack/react-query";
import { describe, expect, it, vi } from "vitest";

import DecisionForm from "../components/DecisionForm";
import review from "../__fixtures__/review-submission.json";
import rules from "../__fixtures__/rules.json";
import axios, { calls, respond } from "./mockAxios";
import { renderAt } from "./render";

vi.mock("src/utils/axios", () => import("./mockAxios"));

const RULES = "/omega-hub/v1/rules";
const ready = { ...review, draftDisclosure: review.confirmedDisclosure };
const submit = () => screen.getByTestId("hub-decision-submit");
const errorRoutes = [
  ["get", RULES, { error: { message: "Rules unavailable" } }, 400],
];
let client;
function QueryProbe() {
  client = useQueryClient();
  return null;
}
const form = () =>
  renderAt(
    <>
      <DecisionForm submission={ready} />
      <QueryProbe />
    </>,
  );
async function completeChecklist() {
  await screen.findAllByTestId("hub-checklist-item");
  for (const box of screen.getAllByTestId("hub-flag-ack")) fireEvent.click(box);
  for (const box of screen.getAllByTestId("hub-checklist-item"))
    fireEvent.click(box);
}

describe("Round-five review rules recovery", () => {
  it.each(["approve", "reject", "changes"])(
    "explains the missing rules next to %s and never posts",
    async (decision) => {
      respond(errorRoutes);
      form();
      const alert = await screen.findByRole("alert");
      expect(alert).toHaveClass("MuiAlert-standardError");
      expect(alert).toHaveTextContent(
        "The review rules did not load. Try again before making a decision.",
      );
      expect(
        within(alert).getByRole("button", { name: "Try again" }),
      ).toBeEnabled();
      fireEvent.click(screen.getByTestId(`hub-decision-${decision}-tab`));
      if (decision !== "approve") {
        fireEvent.change(screen.getByTestId("hub-decision-reason-input"), {
          target: { value: "The disclosure needs to describe this code." },
        });
      }
      expect(submit()).toBeDisabled();
      const blocker = screen.getByTestId(
        decision === "approve"
          ? "hub-approve-blockers"
          : "hub-decision-rules-blocker",
      );
      expect(blocker).toHaveTextContent(/the review rules must load/i);
      expect(blocker.nextElementSibling).toBe(submit());
      expect(submit()).toHaveAccessibleDescription(blocker.textContent);
      fireEvent.click(submit());
      expect(calls.filter((call) => call.method === "post")).toHaveLength(0);
    },
  );

  it.each([false, true])(
    "shows busy feedback and preserves focus while retrying rules (cached=%s)",
    async (cached) => {
      respond(cached ? [["get", RULES, rules]] : errorRoutes);
      form();
      if (cached) {
        await completeChecklist();
        await waitFor(() => expect(submit()).toBeEnabled());
        respond(errorRoutes);
        await act(async () => client.refetchQueries());
      }
      const alert = await screen.findByRole("alert");
      const retry = within(alert).getByRole("button", { name: "Try again" });
      expect(submit()).toBeDisabled();
      const original = axios.get.getMockImplementation();
      let finish;
      const delayed = vi
        .spyOn(axios, "get")
        .mockImplementation((url, config) =>
          url === RULES
            ? new Promise((resolve) => {
                finish = resolve;
              })
            : original(url, config),
        );
      try {
        retry.focus();
        fireEvent.click(retry);
        await waitFor(() => expect(finish).toBeTypeOf("function"));
        const status = await screen.findByRole("status");
        expect(status).toHaveTextContent("Loading the review rules…");
        expect(status).toHaveFocus();
        expect(
          screen.queryByRole("button", { name: "Try again" }),
        ).not.toBeInTheDocument();
        expect(screen.queryByRole("alert")).not.toBeInTheDocument();
        expect(submit()).toBeDisabled();
        await act(async () => finish({ data: rules, status: 200 }));
        await screen.findAllByTestId("hub-checklist-item");
        if (!cached) await completeChecklist();
        await waitFor(() => expect(submit()).toBeEnabled());
        expect(
          screen.queryByTestId("hub-approve-blockers"),
        ).not.toBeInTheDocument();
      } finally {
        if (finish) await act(async () => finish({ data: rules, status: 200 }));
        delayed.mockImplementation(original);
      }
    },
  );

  it("does not block healthy cached rules or steal focus on a background refresh", async () => {
    respond([["get", RULES, rules]]);
    form();
    await completeChecklist();
    await waitFor(() => expect(submit()).toBeEnabled());
    const original = axios.get.getMockImplementation();
    let finish;
    const delayed = vi.spyOn(axios, "get").mockImplementation((url, config) =>
      url === RULES
        ? new Promise((resolve) => {
            finish = resolve;
          })
        : original(url, config),
    );
    try {
      submit().focus();
      act(() => {
        void client.refetchQueries();
      });
      await waitFor(() => expect(finish).toBeTypeOf("function"));
      expect(submit()).toBeEnabled();
      expect(submit()).toHaveFocus();
      expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    } finally {
      if (finish) await act(async () => finish({ data: rules, status: 200 }));
      delayed.mockImplementation(original);
    }
  });
});
