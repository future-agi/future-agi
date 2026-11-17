// The smart filter's limit: past a budget the AI filter call answers 429 with
// code "throttled" and one sentence saying which budget ran out and when to
// try again (the axios interceptor spreads the response body into the
// rejection). The AI box shows that sentence, not its generic line.
import React from "react";
import { describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen } from "src/utils/test-utils";

import FilterPanel from "../FilterPanel";

const mocks = vi.hoisted(() => ({ post: vi.fn() }));

vi.mock("src/utils/axios", async (importOriginal) => ({
  ...(await importOriginal()),
  default: { post: mocks.post },
}));

const SENTENCE =
  "You've used the smart filter a lot in the last minute. Try again in 42 seconds.";

const FIELDS = [
  { value: "status", label: "Status", type: "enum", choices: ["OK", "ERROR"] },
];

describe("FilterPanel: the smart filter's limit", () => {
  it("shows the server's sentence when the AI filter answers 429", async () => {
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
    render(
      <FilterPanel
        anchorEl={document.body}
        open
        onClose={vi.fn()}
        filterFields={FIELDS}
        currentFilters={null}
        onApply={vi.fn()}
      />,
    );

    const ask = screen.getByPlaceholderText(
      "Ask AI — e.g. 'show me items with errors'",
    );
    fireEvent.change(ask, { target: { value: "show failed calls" } });
    fireEvent.keyDown(ask, { key: "Enter" });

    expect(await screen.findByText(SENTENCE)).toBeInTheDocument();
    expect(mocks.post).toHaveBeenCalledTimes(1);
    expect(
      screen.queryByText("AI unavailable, using local parser"),
    ).not.toBeInTheDocument();
  });
});
