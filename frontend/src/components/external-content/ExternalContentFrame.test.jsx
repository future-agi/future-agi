import React from "react";
import { describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";
import ExternalContentFrame from "./ExternalContentFrame";

describe("external content consent", () => {
  it("mounts no iframe or thumbnail until the labelled action is clicked", () => {
    const onRequestLoad = vi.fn();
    const { container } = render(
      <ExternalContentFrame
        src="https://video.example.test/embed/one"
        title="Help tutorial"
        onRequestLoad={onRequestLoad}
        style={{ opacity: 0, visibility: "hidden", height: 200 }}
      />,
    );
    expect(container.querySelector("iframe,img,video,link")).toBeNull();
    expect(screen.getByRole("group")).toBeVisible();
    expect(
      screen.getByText(/External content from video.example.test/),
    ).toBeVisible();
    fireEvent.click(
      screen.getByRole("button", { name: "Load external content" }),
    );
    expect(onRequestLoad).toHaveBeenCalledTimes(1);
    expect(container.querySelector("iframe").getAttribute("src")).toBe(
      "https://video.example.test/embed/one",
    );
    expect(
      container.querySelector("iframe").getAttribute("referrerpolicy"),
    ).toBe("no-referrer");
  });
  it("requires a new click when the document or help destination changes", () => {
    const { container, rerender } = render(
      <ExternalContentFrame
        src="https://video.example.test/embed/one"
        title="Tutorial"
      />,
    );
    fireEvent.click(screen.getByRole("button"));
    rerender(
      <ExternalContentFrame
        src="https://video.example.test/embed/two"
        title="Tutorial two"
      />,
    );
    expect(container.querySelector("iframe")).toBeNull();
    expect(
      screen.getByRole("button", { name: "Load external content" }),
    ).toBeVisible();
    rerender(
      <ExternalContentFrame
        src="https://video.example.test/embed/one"
        title="Tutorial"
      />,
    );
    expect(container.querySelector("iframe")).toBeNull();
  });
});
