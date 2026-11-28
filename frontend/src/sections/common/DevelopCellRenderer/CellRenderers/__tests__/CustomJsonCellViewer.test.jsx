import React from "react";
import { afterEach, describe, expect, it } from "vitest";
import { fireEvent, render, screen } from "src/utils/test-utils";
import CustomJsonCellViewer from "../CustomJsonCellViewer";

// JsonViewer renders data-testid="data-key-pair{keypath}" for each entry
const getKeyPair = (key) => screen.queryByTestId(`data-key-pair${key}`);

describe("CustomJsonCellViewer media previews", () => {
  afterEach(() => {
    delete window.__jsonViewerClick;
  });

  it("opens an image URL in a full-size preview instead of the row drawer", () => {
    const src = "https://cdn.example.com/photos/cat.webp";
    render(<CustomJsonCellViewer object={{ image: src }} />);

    fireEvent.click(screen.getByRole("button", { name: "Open image preview" }));

    // The dataset grids skip opening the row drawer when this flag is set.
    expect(window.__jsonViewerClick).toBe(true);
    expect(
      screen.getByRole("img", { name: "Full size image" }),
    ).toHaveAttribute("src", src);
  });

  it("opens an audio URL in a player", () => {
    const src = "https://cdn.example.com/calls/call-42.wav?sig=abc";
    render(<CustomJsonCellViewer object={{ recording_url: src }} />);

    fireEvent.click(screen.getByRole("button", { name: "Play audio" }));

    expect(window.__jsonViewerClick).toBe(true);
    expect(document.querySelector("audio")).toHaveAttribute("src", src);
  });

  it("keeps a string that is not media as plain text", () => {
    render(
      <CustomJsonCellViewer object={{ note: "https://example.com/report" }} />,
    );

    expect(getKeyPair("note")).toHaveTextContent("https://example.com/report");
    expect(
      screen.queryByRole("button", { name: "Open image preview" }),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "Play audio" }),
    ).not.toBeInTheDocument();
  });
});
