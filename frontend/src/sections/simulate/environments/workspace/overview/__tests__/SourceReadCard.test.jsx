import { describe, it, expect } from "vitest";
import { render, screen } from "src/utils/test-utils";

import SourceReadCard from "../SourceReadCard";

const LONG_RULE =
  "When calling a tool, use only approved neutral fillers ('One moment.', 'Let me check.', 'Just a second.', 'Give me a moment.', 'Let me see.', 'Let me look into that.') and never repeat the same filler twice in a row.";

describe("SourceReadCard", () => {
  it("wraps a long rule instead of cutting it off", () => {
    render(<SourceReadCard rules={[LONG_RULE]} />);
    const rule = screen.getByText(LONG_RULE);
    const style = window.getComputedStyle(rule);
    expect(style.whiteSpace).not.toBe("nowrap");
    expect(style.textOverflow).not.toBe("ellipsis");
  });

  it("renders tools and rules from strings and objects", () => {
    render(
      <SourceReadCard
        tools={[{ name: "lookup_order" }]}
        rules={[{ text: "Start in English." }]}
      />,
    );
    expect(screen.getByText("lookup_order")).toBeInTheDocument();
    expect(screen.getByText("Start in English.")).toBeInTheDocument();
  });
});
