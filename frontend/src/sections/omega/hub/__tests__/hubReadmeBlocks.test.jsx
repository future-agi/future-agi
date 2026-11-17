import React from "react";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import ReadmeView from "../components/ReadmeView";

// A README on the Hub reads like one on a repository or a model card: each
// code block, not only a shell command, has a copy button that copies its
// text exactly, and the README's own top-level sections are ruled off under
// their headings while its smaller headings are not.

// Defined, not assigned: user-event's setup() leaves a getter-only stub.
function clipboard() {
  const writeText = vi.fn().mockResolvedValue(undefined);
  Object.defineProperty(navigator, "clipboard", {
    value: { writeText },
    configurable: true,
    writable: true,
  });
  return writeText;
}

const README = [
  "It prints:",
  "",
  "```text",
  "answer: In Lisbon today: sunny, 24 degrees.",
  'tool get_weather ok=true -> {"city":"Lisbon"}',
  "```",
  "",
  "```json",
  '{ "maxCalls": 2 }',
  "```",
  "",
  "```bash",
  "node main.mjs",
  "```",
].join("\n");

describe("README code blocks", () => {
  it("gives every block that is not a shell command a copy button that copies its text exactly", async () => {
    const writeText = clipboard();
    render(<ReadmeView markdown={README} />);
    // One button for each block that is not a shell command, named for it.
    expect(screen.getAllByRole("button", { name: / block$/ })).toHaveLength(2);
    const buttons = [
      screen.getByRole("button", { name: "Copy the text block" }),
      screen.getByRole("button", { name: "Copy the json block" }),
    ];
    fireEvent.click(buttons[0]);
    await waitFor(() =>
      expect(writeText).toHaveBeenCalledWith(
        'answer: In Lisbon today: sunny, 24 degrees.\ntool get_weather ok=true -> {"city":"Lisbon"}',
      ),
    );
    fireEvent.click(buttons[1]);
    await waitFor(() =>
      expect(writeText).toHaveBeenLastCalledWith('{ "maxCalls": 2 }'),
    );
    // The shell command keeps its own copy button, which copies the command.
    expect(
      screen.getByRole("button", { name: "Copy node main.mjs" }),
    ).toBeInTheDocument();
  });

  it("keeps each block's text in place as preformatted text", () => {
    const { container } = render(<ReadmeView markdown={README} />);
    const blocks = [...container.querySelectorAll("pre")];
    expect(blocks.map((block) => block.textContent.trim())).toEqual([
      'answer: In Lisbon today: sunny, 24 degrees.\ntool get_weather ok=true -> {"city":"Lisbon"}',
      '{ "maxCalls": 2 }',
    ]);
  });
});

describe("README headings", () => {
  it("rules off the README's own top-level sections under their headings, and not its smaller headings", () => {
    render(
      <ReadmeView
        markdown={"## Run it\n\nText.\n\n### In detail\n\nMore text."}
        headingOffset={1}
      />,
    );
    const section = screen.getByRole("heading", { name: "Run it" });
    expect(section.tagName).toBe("H3");
    expect(getComputedStyle(section).borderBottomStyle).toBe("solid");
    const smaller = screen.getByRole("heading", { name: "In detail" });
    expect(getComputedStyle(smaller).borderBottomStyle).not.toBe("solid");
  });
});
