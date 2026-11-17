// The AI eval writer's limit: past a budget the writer call answers 429 with
// code "throttled" and one sentence saying which budget ran out and when to
// try again (the axios interceptor spreads the response body into the
// rejection). The LLM-as-a-Judge prompt editor shows that sentence and leaves
// the messages as they were.
import React from "react";
import { describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "src/utils/test-utils";
import LLMPromptEditor from "../LLMPromptEditor";

const mocks = vi.hoisted(() => ({ post: vi.fn(), enqueueSnackbar: vi.fn() }));

// Quill does not run in jsdom.
vi.mock("../MessageEditorBlock", () => ({ default: () => null }));
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

describe("LLMPromptEditor: the AI eval writer's limit", () => {
  it("shows the server's sentence when the writer answers 429, and keeps the messages", async () => {
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
    const onMessagesChange = vi.fn();
    render(
      <LLMPromptEditor
        messages={[{ role: "system", content: "" }]}
        onMessagesChange={onMessagesChange}
        templateFormat="mustache"
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: "Generate with AI" }));
    const ask = screen.getByPlaceholderText(
      "Describe your eval — e.g. 'judge if chatbot responses are helpful and accurate'",
    );
    fireEvent.change(ask, { target: { value: "judge politeness" } });
    fireEvent.keyDown(ask, { key: "Enter" });

    await waitFor(() =>
      expect(mocks.enqueueSnackbar).toHaveBeenCalledWith(
        SENTENCE,
        expect.objectContaining({ variant: "error" }),
      ),
    );
    expect(mocks.post).toHaveBeenCalledTimes(1);
    expect(onMessagesChange).not.toHaveBeenCalled();
  });
});
