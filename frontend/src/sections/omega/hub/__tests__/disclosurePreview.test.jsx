import React from "react";
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import DisclosureText from "../components/DisclosureText";

afterEach(cleanup);

// A disclosure part longer than this shows only its opening words.
const PREVIEW_LENGTH = 300;

// A part whose first 300 characters end with `windowEnd`, followed by
// `after`. Short words fill the start, so the preview window ends exactly
// where the case needs it to.
function part(windowEnd, after) {
  const room = PREVIEW_LENGTH - windowEnd.length;
  const lead = "a ".repeat(Math.floor(room / 2)).padEnd(room, " ");
  return `${lead}${windowEnd}${after}`;
}

// The shortened text as shown, without the Show all button after it.
const previewOf = (node) => node.textContent.replace(/Show all$/, "");

const renderPart = (text) =>
  render(
    <div data-testid="part">
      <DisclosureText text={text} label="Credentials" />
    </div>,
  );

describe("Disclosure preview", () => {
  // The preview used to end in ".…" or ",…" when the last whole word in the
  // window closed a sentence or a clause.
  it.each([
    [
      "a stop, cut on the space after it",
      "Uses OMEGA_LIVE_MODEL. ",
      "Then calls the host.",
      "Uses OMEGA_LIVE_MODEL…",
    ],
    [
      "a stop, cut inside the next word",
      "Uses OMEGA_LIVE_MODEL. Th",
      "en calls the host.",
      "Uses OMEGA_LIVE_MODEL…",
    ],
    [
      "a comma",
      "The clock holds the time, ",
      "then stops.",
      "The clock holds the time…",
    ],
    [
      "a semicolon",
      "No key is stored; ",
      "nothing else is kept.",
      "No key is stored…",
    ],
    [
      "a colon",
      "Reads the first of these: ",
      "OMEGA_VERTEX_CREDS.",
      "Reads the first of these…",
    ],
  ])("drops %s before the ellipsis", (_case, windowEnd, after, shownEnd) => {
    const text = part(windowEnd, after);
    expect(text.length).toBeGreaterThan(PREVIEW_LENGTH);
    renderPart(text);

    const preview = previewOf(screen.getByTestId("part"));
    expect(preview.slice(-shownEnd.length)).toBe(shownEnd);
    expect(preview).not.toMatch(/[\s.,;:]…$/);
    // Show all is still there to open the whole part.
    expect(
      screen.getByRole("button", { name: "Show all credentials disclosure" }),
    ).toBeInTheDocument();
  });

  // Show all sat about 8 px in from the start of the text above it.
  it("lines Show all up with the text: no side padding, no minimum width", () => {
    renderPart(part("Uses OMEGA_LIVE_MODEL. ", "Then calls the host."));

    const toggle = screen.getByRole("button", {
      name: "Show all credentials disclosure",
    });
    const style = getComputedStyle(toggle);
    expect(parseFloat(style.paddingLeft)).toBe(0);
    expect(parseFloat(style.paddingRight)).toBe(0);
    expect(parseFloat(style.minWidth)).toBe(0);
  });
});
