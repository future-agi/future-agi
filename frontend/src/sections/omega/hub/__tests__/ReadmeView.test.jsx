import React from "react";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import ReadmeView, {
  isRemoteSrc,
  pinReadmeCommands,
  splitReadme,
} from "../components/ReadmeView";

describe("README rendering (SPEC §1.2: remote images render as links)", () => {
  it("turns a remote image into a link and loads nothing", () => {
    const { container } = render(
      <ReadmeView
        markdown={"# Title\n\n![tracker](https://tracker.example/p.png)"}
      />,
    );
    expect(container.querySelector("img")).toBeNull();
    const link = screen.getByTestId("hub-readme-image-link");
    expect(link).toHaveAttribute("href", "https://tracker.example/p.png");
    expect(link).toHaveAttribute("rel", "nofollow noopener noreferrer");
    expect(link).toHaveTextContent("Image: tracker");
  });

  it("treats protocol-relative and data: sources as remote too", () => {
    expect(isRemoteSrc("//cdn.example/x.png")).toBe(true);
    expect(isRemoteSrc("data:image/png;base64,AAAA")).toBe(true);
    expect(isRemoteSrc("docs/shot.png")).toBe(false);
    const { container } = render(
      <ReadmeView
        markdown={"![x](//cdn.example/x.png)"}
        resolveImage={() => "/local.png"}
      />,
    );
    expect(container.querySelector("img")).toBeNull();
  });

  it("shows a bundle image only through the registry's own file URL", () => {
    const { container } = render(
      <ReadmeView
        markdown={"![shot](docs/shot.png)"}
        resolveImage={(src) =>
          `/omega-hub/v1/entries/a/b/versions/1.0.0/files/${src}`
        }
      />,
    );
    expect(container.querySelector("img")).toHaveAttribute(
      "src",
      "/omega-hub/v1/entries/a/b/versions/1.0.0/files/docs/shot.png",
    );
  });

  it("never renders raw HTML from the README", () => {
    const { container } = render(
      <ReadmeView
        markdown={
          '<img src="https://x.example/a.png"><script>window.bad=1</script>\n\ntext'
        }
      />,
    );
    expect(container.querySelector("img")).toBeNull();
    expect(container.querySelector("script")).toBeNull();
  });
});

// The entry page shows "What it does" first and the rest of the README in
// its Readme tab, with the README's own commands pinned to the version shown.
const README = [
  "## What it does",
  "",
  "Answers from one tool.",
  "",
  "```text",
  "## not a heading inside a fence",
  "```",
  "",
  "## Run it with no key",
  "",
  "```bash",
  "omega hub run first-agent",
  "```",
  "",
  "```bash",
  "export OPENAI_API_KEY=...",
  "omega hub run first-agent --live openai:gpt-4o-mini",
  "omega hub run other-entry",
  "omega hub check first-agent --record",
  "```",
].join("\n");

const PINNED = {
  names: ["first-agent"],
  run: "omega hub run hub:future-agi/first-agent@1.0.0 --registry https://r.example.test/omega-hub/v1",
  add: "omega hub add hub:future-agi/first-agent@1.0.0 --registry https://r.example.test/omega-hub/v1",
};

describe("README parts on the entry page", () => {
  it("splits out What it does, skipping headings inside code fences", () => {
    const { what, rest } = splitReadme(README);
    expect(what).toBe(
      "Answers from one tool.\n\n```text\n## not a heading inside a fence\n```",
    );
    expect(rest.startsWith("## Run it with no key")).toBe(true);
    expect(rest).not.toContain("What it does");
  });

  it("leaves a README without that section whole", () => {
    expect(splitReadme("## Usage\n\nRun it.")).toEqual({
      what: null,
      rest: "## Usage\n\nRun it.",
    });
  });

  it("pins this entry's run commands, keeps the rest of each line, and leaves other commands alone", () => {
    const pinned = pinReadmeCommands(README, PINNED);
    expect(pinned).toContain(`\n${PINNED.run}\n`);
    expect(pinned).toContain(`\n${PINNED.run} --live openai:gpt-4o-mini\n`);
    expect(pinned).toContain("\nomega hub run other-entry\n");
    expect(pinned).toContain("\nomega hub check first-agent --record");
    expect(pinned).not.toMatch(/omega hub run first-agent\b/);
    expect(pinReadmeCommands(README, null)).toBe(README);
  });

  it("shows a shell block as a whole command with a copy button that copies it exactly", async () => {
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.defineProperty(navigator, "clipboard", {
      value: { writeText },
      configurable: true,
      writable: true,
    });
    render(
      <ReadmeView markdown={splitReadme(README).rest} commands={PINNED} />,
    );
    const live = `export OPENAI_API_KEY=...\n${PINNED.run} --live openai:gpt-4o-mini\nomega hub run other-entry\nomega hub check first-agent --record`;
    const copy = screen.getByRole("button", {
      name: /^Copy export OPENAI_API_KEY=\.\.\./,
    });
    fireEvent.click(copy);
    await waitFor(() => expect(writeText).toHaveBeenCalledWith(live));
  });

  it("moves headings down under a page heading, sized by the README's own level", () => {
    render(
      <ReadmeView markdown={"## Make it yours\n\nText."} headingOffset={1} />,
    );
    const heading = screen.getByRole("heading", { name: "Make it yours" });
    expect(heading.tagName).toBe("H3");
    expect(heading).toHaveClass("hub-md-h2");
  });
});
