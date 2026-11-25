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

  it("renders bare formatting tags with basicHtml and keeps any other HTML as text", () => {
    const text =
      '<b>bold</b> <img src="x" onerror="alert(1)"> <script>alert(1)</script> <front desk>';
    const { container } = render(<ChatMarkdown text={text} basicHtml />);
    expect(container.querySelector("b")).toHaveTextContent("bold");
    expect(container.querySelector("img, script")).toBeNull();
    expect(container).toHaveTextContent(
      '<img src="x" onerror="alert(1)"> <script>alert(1)</script> <front desk>',
    );

    const { container: plain } = render(<ChatMarkdown text="<b>bold</b>" />);
    expect(plain.querySelector("b")).toBeNull();
    expect(plain).toHaveTextContent("<b>bold</b>");
  });
});
