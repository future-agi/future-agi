import { describe, it, expect } from "vitest";
import { render, screen } from "src/utils/test-utils";
import Markdown from "../parts/Markdown";

describe("Markdown", () => {
  it("renders a paragraph", () => {
    render(<Markdown text="the agent takes orders" />);
    expect(screen.getByText("the agent takes orders")).toBeInTheDocument();
  });

  it("renders bold and inline code without showing the markers", () => {
    const { container } = render(<Markdown text="call **place_order** with `item`" />);
    expect(container.querySelector("strong")).toHaveTextContent("place_order");
    expect(container.querySelector("code")).toHaveTextContent("item");
    expect(container.textContent).not.toContain("**");
  });

  it("renders a heading", () => {
    render(<Markdown text="## What it does" />);
    expect(screen.getByText("What it does")).toBeInTheDocument();
  });

  it("renders a bullet list as list items", () => {
    const { container } = render(<Markdown text={"- first\n- second"} />);
    expect(container.querySelectorAll("li")).toHaveLength(2);
  });

  it("renders a table rather than a wall of pipes", () => {
    const { container } = render(
      <Markdown text={"| tool | args |\n| --- | --- |\n| place_order | item |"} />
    );
    expect(container.querySelectorAll("th")).toHaveLength(2);
    expect(container.querySelectorAll("td")).toHaveLength(2);
    expect(screen.getByText("place_order")).toBeInTheDocument();
  });

  it("renders a fenced code block", () => {
    const { container } = render(<Markdown text={"```\ndef go():\n    pass\n```"} />);
    expect(container.querySelector("pre")).toHaveTextContent("def go()");
  });

  it("leaves plain text alone when there is no markup", () => {
    render(<Markdown text="nothing special here" />);
    expect(screen.getByText("nothing special here")).toBeInTheDocument();
  });
});
