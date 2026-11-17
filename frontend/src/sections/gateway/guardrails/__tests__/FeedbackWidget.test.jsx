import React from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { renderPage } from "./helpers";
import FeedbackWidget from "../FeedbackWidget";

const { get, post, patch } = vi.hoisted(() => ({
  get: vi.fn(),
  post: vi.fn(),
  patch: vi.fn(),
}));
vi.mock("src/utils/axios", async (importOriginal) => {
  const actual = await importOriginal();
  return { ...actual, default: { ...actual.default, get, post, patch } };
});
vi.mock("notistack", () => ({ enqueueSnackbar: vi.fn() }));

const SAVED = {
  id: "fb-1",
  request_log: "log-1",
  check_name: "keyword-blocklist",
  feedback: "false_positive",
  comment: "Order email, should not block",
};

// An edit used to POST a second review and the widget never loaded
// the saved one, so one reviewer's change counted twice.
describe("guardrail feedback on a matched request", () => {
  beforeEach(() => {
    get.mockReset();
    post.mockReset();
    patch.mockReset();
    post.mockResolvedValue({ data: { result: { id: "fb-2" } } });
    patch.mockResolvedValue({ data: { result: { id: "fb-1" } } });
  });

  it("loads this request's saved review and edits it in place", async () => {
    const user = userEvent.setup();
    get.mockResolvedValue({ data: { result: [SAVED] } });
    renderPage(
      <FeedbackWidget requestLogId="log-1" checkName="keyword-blocklist" />,
    );
    expect(await screen.findByText(/False positive/)).toBeInTheDocument();
    const [url, config] = get.mock.calls[0];
    expect(url).toBe("/agentcc/guardrail-feedback/");
    // Only this reviewer's answer: only its author may change a review.
    expect(config.params).toEqual({
      request_log_id: "log-1",
      check_name: "keyword-blocklist",
      mine: "true",
    });

    await user.click(screen.getByRole("button", { name: "Edit" }));
    await user.click(screen.getByRole("button", { name: "Correct" }));
    await user.click(screen.getByRole("button", { name: "Save review" }));
    await waitFor(() => expect(patch).toHaveBeenCalledTimes(1));
    expect(patch.mock.calls[0][0]).toBe("/agentcc/guardrail-feedback/fb-1/");
    expect(patch.mock.calls[0][1]).toEqual({
      feedback: "correct",
      comment: "Order email, should not block",
    });
    expect(post).not.toHaveBeenCalled();
  });

  it("creates a review when there is none yet", async () => {
    const user = userEvent.setup();
    get.mockResolvedValue({ data: { result: [] } });
    renderPage(
      <FeedbackWidget requestLogId="log-2" checkName="pii-detection" />,
    );
    await user.click(await screen.findByRole("button", { name: "Correct" }));
    await user.click(screen.getByRole("button", { name: "Save review" }));
    await waitFor(() => expect(post).toHaveBeenCalledTimes(1));
    expect(post.mock.calls[0][1]).toEqual({
      request_log: "log-2",
      check_name: "pii-detection",
      feedback: "correct",
      comment: "",
    });
    expect(patch).not.toHaveBeenCalled();
  });
});
