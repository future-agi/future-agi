// The AI eval writer's limit: past a budget the writer call answers 429 with
// code "throttled" and one sentence saying which budget ran out and when to
// try again (the axios interceptor spreads the response body into the
// rejection). The instruction editor shows that sentence and leaves the
// instructions as they were.
import React, { forwardRef } from "react";
import { describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "src/utils/test-utils";
import InstructionEditor from "../InstructionEditor";

const mocks = vi.hoisted(() => ({ post: vi.fn(), enqueueSnackbar: vi.fn() }));

// Quill does not run in jsdom.
vi.mock("src/components/PromptCards/PromptEditor", () => ({
  default: forwardRef(function PromptEditor() {
    return null;
  }),
}));
vi.mock("../ModelSelector", () => ({ default: () => null }));
vi.mock("src/components/iconify", () => ({ default: () => null }));
vi.mock("src/components/svg-color", () => ({ default: () => null }));
vi.mock("src/utils/axios", async (importOriginal) => ({
  ...(await importOriginal()),
  default: { post: mocks.post },
}));
vi.mock("notistack", async (importOriginal) => ({
  ...(await importOriginal()),
  useSnackbar: () => ({ enqueueSnackbar: mocks.enqueueSnackbar }),
}));

const SENTENCE =
  "You've used the AI eval writer a lot in the last minute. Try again in 42 seconds.";

describe("InstructionEditor: the AI eval writer's limit", () => {
  it("shows the server's sentence when the writer answers 429, and keeps the instructions", async () => {
    mocks.post.mockRejectedValue({
      status: false,
      type: "rate_limit",
      code: "throttled",
      detail: SENTENCE,
      message: SENTENCE,
      error: SENTENCE,
      result: SENTENCE,
      statusCode: 429,
    });
    const onChange = vi.fn();
    render(
      <InstructionEditor
        value=""
        onChange={onChange}
        model="synthetic-model"
        onModelChange={vi.fn()}
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: "Write with AI" }));
    const ask = screen.getByPlaceholderText(
      "Describe your eval — e.g. 'evaluate SEO blog quality'",
    );
    fireEvent.change(ask, { target: { value: "check the answer is polite" } });
    fireEvent.keyDown(ask, { key: "Enter" });

    await waitFor(() =>
      expect(mocks.enqueueSnackbar).toHaveBeenCalledWith(
        SENTENCE,
        expect.objectContaining({ variant: "error" }),
      ),
    );
    expect(mocks.post).toHaveBeenCalledTimes(1);
    expect(onChange).not.toHaveBeenCalled();
  });
});
