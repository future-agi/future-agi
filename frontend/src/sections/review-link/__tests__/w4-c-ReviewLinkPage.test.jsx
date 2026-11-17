import React from "react";
import { describe, it, expect, vi, beforeEach } from "vitest";
import {
  act,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { HelmetProvider } from "react-helmet-async";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import ReviewLinkPage from "src/pages/review-link/ReviewLinkPage";

// W4 C3: the outside reviewer's public page, one sampled item at a time.
vi.mock("src/components/iconify", () => ({
  default: ({ icon }) => <span data-icon={icon} />,
}));

const LABEL = {
  id: "l-ok",
  name: "Correct?",
  type: "thumbs_up_down",
  settings: {},
};
const VIEW = {
  org_name: "Acme AI",
  dataset_name: "Support agent - golden set",
  queue: {
    name: "Quality · #4A",
    instructions: "Judge the reply.",
    labels: [LABEL],
  },
  invited_by: { name: "Maya Chen" },
  progress: { done: 0, total: 2 },
  next_item_id: "i1",
  item_ids: ["i1", "i2"],
  expires_at: "2026-10-29T12:00:00Z",
  corrections: { column_name: "expected_reply" },
};
const itemOf = (id, index) => ({
  item_id: id,
  index,
  total: 2,
  labels: [LABEL],
  my_answer: null,
  content: {
    mode: "rubric",
    input: { customer_message: `Question ${index}` },
    candidates: [{ slot: 1, status: "completed", output: `Answer ${index}` }],
    expected: { expected_reply: `Expected ${index}` },
  },
});

let view;
let viewError;
const submit = vi.fn();
vi.mock("src/api/develop/review-links", () => ({
  reviewLinkKeys: {
    view: (t) => ["rl", t],
    item: (t, i) => ["rl", t, i],
  },
  useReviewLink: () =>
    viewError
      ? { isLoading: false, isError: true, error: viewError, data: undefined }
      : { isLoading: false, isError: false, data: view },
  useReviewLinkItem: (_t, id) => ({
    isError: false,
    data: id ? itemOf(id, id === "i1" ? 1 : 2) : undefined,
  }),
  submitReviewLinkItem: (...a) => submit(...a),
  errorOf: (e) => ({
    status: e?.statusCode ?? null,
    message: e?.result ?? null,
  }),
}));

const renderAt = (path) =>
  render(
    <HelmetProvider>
      <QueryClientProvider client={new QueryClient()}>
        <MemoryRouter initialEntries={[path]}>
          <Routes>
            <Route path="/review/:token" element={<ReviewLinkPage />} />
          </Routes>
        </MemoryRouter>
      </QueryClientProvider>
    </HelmetProvider>,
  );

describe("ReviewLinkPage", () => {
  beforeEach(() => {
    view = VIEW;
    viewError = null;
    submit.mockReset();
  });

  it("shows the header, the instructions and the first sampled item", () => {
    renderAt("/review/tok1");
    expect(screen.getByTestId("review-link-title").textContent).toBe(
      "Review for Acme AI · Support agent - golden set",
    );
    expect(
      screen.getByText(/^Invited by Maya Chen · 2 items · expires /),
    ).toBeTruthy();
    expect(screen.getByText("Judge the reply.")).toBeTruthy();
    expect(screen.getByTestId("review-link-position").textContent).toBe(
      "1 of 2",
    );
    expect(screen.getByText("Question 1")).toBeTruthy();
    expect(screen.getByText("Answer 1")).toBeTruthy();
    expect(screen.getByText("Expected 1")).toBeTruthy();
  });

  it("refuses an unanswered label, then key 1 answers it and Submit moves on", async () => {
    submit.mockResolvedValue({
      saved: true,
      progress: { done: 1, total: 2 },
      next_item_id: "i2",
    });
    renderAt("/review/tok1");
    fireEvent.click(screen.getByTestId("review-link-submit"));
    expect(await screen.findByText("Answer “Correct?” first.")).toBeTruthy();
    expect(submit).not.toHaveBeenCalled();
    act(() => {
      fireEvent.keyDown(window, { key: "1" });
    });
    fireEvent.click(screen.getByTestId("review-link-submit"));
    await waitFor(() => expect(submit).toHaveBeenCalledTimes(1));
    expect(submit).toHaveBeenCalledWith("tok1", "i1", {
      values: [{ label_id: "l-ok", value: { value: "up" } }],
    });
    await waitFor(() =>
      expect(screen.getByTestId("review-link-position").textContent).toBe(
        "2 of 2",
      ),
    );
  });

  it("sends a corrected expected answer, and the last item ends on the done screen", async () => {
    submit.mockResolvedValue({
      saved: true,
      progress: { done: 2, total: 2 },
      next_item_id: null,
    });
    renderAt("/review/tok1?item=i2");
    fireEvent.click(screen.getByRole("button", { name: "Correct" }));
    fireEvent.change(screen.getByLabelText("Corrected expected_reply"), {
      target: { value: "A better expected answer" },
    });
    act(() => {
      fireEvent.keyDown(window, { key: "2" });
    });
    fireEvent.click(screen.getByTestId("review-link-submit"));
    await waitFor(() => expect(submit).toHaveBeenCalledTimes(1));
    expect(submit.mock.calls[0][1]).toBe("i2");
    expect(submit.mock.calls[0][2]).toEqual({
      values: [{ label_id: "l-ok", value: { value: "down" } }],
      corrected_expected: { value: "A better expected answer" },
    });
    expect((await screen.findByTestId("review-link-done")).textContent).toMatch(
      /Thanks, you reviewed \d+ items?\. You can close this tab\./,
    );
  });

  it("J and K step through the sample", () => {
    renderAt("/review/tok1");
    act(() => {
      fireEvent.keyDown(window, { key: "j" });
    });
    expect(screen.getByTestId("review-link-position").textContent).toBe(
      "2 of 2",
    );
    act(() => {
      fireEvent.keyDown(window, { key: "k" });
    });
    expect(screen.getByTestId("review-link-position").textContent).toBe(
      "1 of 2",
    );
  });

  it.each([
    [{ statusCode: 404, result: "Link not found" }, "Link not found"],
    [{ statusCode: 410 }, "Link expired"],
    [
      { statusCode: 403 },
      "This review link is waiting for an admin to approve it.",
    ],
  ])("shows the link error %#", (error, copy) => {
    viewError = error;
    renderAt("/review/tok1");
    expect(screen.getByTestId("review-link-error").textContent).toContain(copy);
  });
});
