import { describe, it, expect, vi } from "vitest";
import { render, screen } from "src/utils/test-utils";
import ReactMarkdown from "react-markdown";

import ChatMarkdown from "../ChatMarkdown";

vi.mock("react-markdown", async (importOriginal) => {
  const actual = await importOriginal();
  return { default: vi.fn(actual.default) };
});

describe("ChatMarkdown", () => {
  it("parses again only when the text changes", () => {
    const { rerender } = render(<ChatMarkdown text="First" sx={{ flex: 1 }} />);
    ReactMarkdown.mockClear();

    // A composer keystroke redraws the chat with a new sx object and the same text.
    rerender(<ChatMarkdown text="First" sx={{ flex: 1 }} />);
    expect(ReactMarkdown).not.toHaveBeenCalled();

    rerender(<ChatMarkdown text="Second" sx={{ flex: 1 }} />);
    expect(ReactMarkdown).toHaveBeenCalledTimes(1);
    expect(screen.getByText("Second")).toBeInTheDocument();
  });
});
