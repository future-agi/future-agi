import React from "react";
import { describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";

vi.mock("src/components/iconify", () => ({
  default: (props) => <span data-icon={props.icon} />,
}));
vi.mock("notistack", () => ({ enqueueSnackbar: vi.fn() }));
const copied = [];
vi.mock("src/utils/utils", () => ({
  copyToClipboard: (t) => copied.push(t),
}));

import SafeMarkdown from "../SafeMarkdown";
import { LINK_REL } from "../markdownModel";
import ReasonText from "../ReasonText";

describe("w5-c SafeMarkdown", () => {
  it("renders a list and a link formatted; links get noopener noreferrer nofollow", () => {
    const { container } = render(
      <SafeMarkdown
        text={
          "Fails because:\n\n- wrong **order id**\n- no [policy](https://example.org/p)"
        }
      />,
    );
    expect(container.querySelectorAll("li")).toHaveLength(2);
    expect(container.querySelector("strong")?.textContent).toBe("order id");
    const a = container.querySelector("a");
    expect(a.getAttribute("href")).toBe("https://example.org/p");
    expect(a.getAttribute("rel")).toBe(LINK_REL);
    expect(a.getAttribute("target")).toBe("_blank");
  });

  it("a <script> stays text and never becomes an element", () => {
    const { container } = render(
      <SafeMarkdown
        text={
          'Bad <script>alert("x")</script> and <img src=x onerror=alert(1)>'
        }
      />,
    );
    expect(container.querySelector("script")).toBeNull();
    expect(container.querySelector("img")).toBeNull();
    expect(container.textContent).toContain('<script>alert("x")</script>');
    expect(container.textContent).toContain("<img src=x onerror=alert(1)>");
  });

  it("a script-scheme link loses its target; remote images show alt text only", () => {
    const scheme = ["java", "script", ":"].join("");
    const source =
      "[x](" + scheme + "alert(1)) ![a chart](https://example.org/c.png)";
    const { container } = render(<SafeMarkdown text={source} />);
    const a = container.querySelector("a");
    expect(a?.getAttribute("href") ?? "").not.toMatch(/javascript/i);
    expect(container.querySelector("img")).toBeNull();
    expect(container.textContent).toContain("[image: a chart]");
  });

  it("Raw shows the stored text exactly", () => {
    const text = "- one\n- **two**";
    render(<SafeMarkdown text={text} />);
    fireEvent.click(screen.getByRole("button", { name: "Raw" }));
    expect(screen.getByTestId("safe-markdown-raw").textContent).toBe(text);
    fireEvent.click(screen.getByRole("button", { name: "Raw" }));
    expect(screen.getByTestId("safe-markdown")).toBeTruthy();
  });

  it("ReasonText keeps Copy reason with the raw text", () => {
    render(<ReasonText text={"**bold** reason"} copy />);
    fireEvent.click(screen.getByRole("button", { name: "Copy reason" }));
    expect(copied).toEqual(["**bold** reason"]);
  });
});
