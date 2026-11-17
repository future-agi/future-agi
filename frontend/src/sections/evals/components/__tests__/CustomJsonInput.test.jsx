import { describe, it, expect, vi } from "vitest";
import { useState } from "react";
import PropTypes from "prop-types";
import { render, screen, waitFor } from "src/utils/test-utils";
import { fireEvent } from "@testing-library/react";
import { CustomJsonInput } from "../TestPlayground";

const mocks = vi.hoisted(() => ({ post: vi.fn(), enqueueSnackbar: vi.fn() }));

// Monaco doesn't run in jsdom — swap it for a plain textarea that mirrors the
// value prop and forwards edits, so we can drive handleJsonChange directly.
vi.mock("../CodeEditor", () => ({
  default: ({ value, onChange }) => (
    <textarea
      data-testid="json-editor"
      value={value}
      onChange={(e) => onChange(e.target.value)}
    />
  ),
}));

vi.mock("src/utils/axios", async (importOriginal) => ({
  ...(await importOriginal()),
  default: { post: mocks.post },
}));

vi.mock("notistack", async (importOriginal) => ({
  ...(await importOriginal()),
  useSnackbar: () => ({ enqueueSnackbar: mocks.enqueueSnackbar }),
}));

// Controlled harness so inputValues round-trips through onInputChange like the
// real parent does.
function Harness({ variables }) {
  const [inputValues, setInputValues] = useState({});
  return (
    <CustomJsonInput
      variables={variables}
      inputValues={inputValues}
      onInputChange={(k, v) => setInputValues((p) => ({ ...p, [k]: v }))}
    />
  );
}

Harness.propTypes = {
  variables: PropTypes.arrayOf(PropTypes.string).isRequired,
};

const editor = () => screen.getByTestId("json-editor");

describe("CustomJsonInput — scaffold sync", () => {
  it("does not rewrite the editor on a jsonText-only change that drops a variable key", () => {
    render(<Harness variables={["conversation"]} />);
    // Mount scaffolds the variable in.
    expect(editor().value).toContain("conversation");

    // User deletes the key; JSON stays valid, variable set unchanged.
    fireEvent.change(editor(), { target: { value: "{}" } });

    // The key must NOT be re-injected (the regression this fix removes).
    expect(editor().value).toBe("{}");
  });

  it("back-fills a variable added while the JSON was invalid, once it parses again", () => {
    const { rerender } = render(<Harness variables={["conversation"]} />);

    // Make the JSON invalid mid-edit.
    fireEvent.change(editor(), { target: { value: "{bad" } });
    expect(editor().value).toBe("{bad");

    // A new variable appears while the JSON can't be parsed — deferred.
    rerender(<Harness variables={["conversation", "topic"]} />);
    expect(editor().value).toBe("{bad");

    // JSON becomes valid again → the deferred add replays.
    fireEvent.change(editor(), { target: { value: '{"conversation":""}' } });
    expect(editor().value).toContain("topic");
  });
});

// The AI eval writer's limit: past a budget the writer answers 429 with code
// "throttled" and one sentence saying which budget ran out and when to try
// again (the axios interceptor spreads the response body into the rejection).
const LIMIT_SENTENCE =
  "You've used the AI eval writer a lot in the last minute. Try again in 42 seconds.";

describe("CustomJsonInput — the AI eval writer's limit", () => {
  it("shows the server's sentence instead of the generic failure, and keeps the JSON", async () => {
    mocks.enqueueSnackbar.mockClear();
    mocks.post.mockReset();
    mocks.post.mockRejectedValue({
      status: false,
      type: "rate_limit",
      code: "throttled",
      detail: LIMIT_SENTENCE,
      message: LIMIT_SENTENCE,
      error: LIMIT_SENTENCE,
      result: LIMIT_SENTENCE,
      statusCode: 429,
    });
    render(<Harness variables={["conversation"]} />);
    const before = editor().value;

    fireEvent.click(
      screen.getByRole("button", { name: "Generate test data with AI" }),
    );
    const ask = screen.getByPlaceholderText(
      "Describe the test data you need - e.g. 'generate a failing case'",
    );
    fireEvent.change(ask, { target: { value: "a polite reply" } });
    fireEvent.keyDown(ask, { key: "Enter" });

    await waitFor(() =>
      expect(mocks.enqueueSnackbar).toHaveBeenCalledWith(
        LIMIT_SENTENCE,
        expect.objectContaining({ variant: "error" }),
      ),
    );
    expect(mocks.enqueueSnackbar).not.toHaveBeenCalledWith(
      "Couldn't generate test data. Please try again.",
      expect.anything(),
    );
    expect(mocks.post).toHaveBeenCalledTimes(1);
    expect(editor().value).toBe(before);
  });
});
