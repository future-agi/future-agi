// The AI eval writer's limit: past a budget the writer call answers 429 with
// code "throttled" and one sentence saying which budget ran out and when to
// try again (the axios interceptor spreads the response body into the
// rejection). The editor shows that sentence and leaves the code as it was,
// instead of writing a local template.
import React from "react";
import { describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "src/utils/test-utils";
import CodeEvalEditor from "../CodeEvalEditor";

const mocks = vi.hoisted(() => ({ post: vi.fn(), enqueueSnackbar: vi.fn() }));

// Monaco does not run in jsdom.
vi.mock("../CodeEditor", () => ({ default: () => null }));
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

describe("CodeEvalEditor: the AI eval writer's limit", () => {
  it("shows the server's sentence when the writer answers 429, and keeps the code", async () => {
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
    const setCode = vi.fn();
    render(
      <CodeEvalEditor
        code=""
        setCode={setCode}
        codeLanguage="python"
        setCodeLanguage={vi.fn()}
      />,
    );

    fireEvent.click(
      screen.getByRole("button", { name: "Generate eval code with AI" }),
    );
    const ask = screen.getByPlaceholderText(
      "Describe what to evaluate — e.g. 'check response has valid JSON'",
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
    expect(setCode).not.toHaveBeenCalled();
  });
});
