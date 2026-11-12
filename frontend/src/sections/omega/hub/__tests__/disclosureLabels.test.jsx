import React from "react";
import {
  cleanup,
  fireEvent,
  render,
  screen,
  within,
} from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import DisclosureEditor from "../components/DisclosureEditor";
import { DISCLOSURE_PARTS } from "../lib/disclosure";

afterEach(cleanup);

// The review page's disclosure text boxes need names a
// screen reader can tell apart. MUI already ties each visible box to its
// floating label ("Network", "Files", …), which the review page also uses for
// its tabs and other fields; the editor is now one named group and each box
// says it is part of the disclosure, with its visible label first (label in
// name). MUI's hidden sizing copy of each box stays out of the accessibility
// tree (aria-hidden) and is not counted. The disclosure now reads as six rows,
// each with its own Edit; a box opens for the part being edited, and from the
// start for a part that needs the reviewer's words.

const value = {
  summary: "Reads a CSV and answers with a chart.",
  network: "None.",
  files: "None.",
  shell: "None.",
  background: "None.",
  credentials: "None.",
};

const LABELS = ["Summary", ...DISCLOSURE_PARTS.map((part) => part.label)];
const NAMES = LABELS.map((label) => `${label} disclosure`);

const editAll = () => {
  for (const label of LABELS)
    fireEvent.click(screen.getByRole("button", { name: `Edit ${label}` }));
};

describe("Review page disclosure fields", () => {
  it("sit in one group named for the disclosure, each part with its own Edit", () => {
    render(<DisclosureEditor value={value} onChange={() => {}} />);
    const group = screen.getByRole("group", {
      name: "Disclosure, as it will be published",
    });
    expect(group).toHaveAttribute("data-testid", "hub-disclosure-editor");
    expect(
      within(group)
        .getAllByRole("button", { name: /^Edit / })
        .map((button) => button.getAttribute("aria-label")),
    ).toEqual(LABELS.map((label) => `Edit ${label}`));
    expect(within(group).queryByRole("textbox")).not.toBeInTheDocument();
    editAll();
    expect(
      within(group)
        .getAllByRole("textbox")
        .map((box) => box.getAttribute("aria-label")),
    ).toEqual(NAMES);
  });

  it("give every open text box a name with its visible label in it, and a label element", () => {
    render(<DisclosureEditor value={value} onChange={() => {}} />);
    editAll();
    const visible = [
      ...screen
        .getByTestId("hub-disclosure-editor")
        .querySelectorAll("textarea"),
    ].filter((box) => box.getAttribute("aria-hidden") !== "true");
    expect(visible).toHaveLength(NAMES.length);
    for (const box of visible) {
      expect(box.labels.length).toBeGreaterThan(0);
      const shown = box.labels[0].textContent.replace(/\s*\*$/, "").trim();
      expect(box).toHaveAccessibleName(`${shown} disclosure`);
    }
    for (const name of NAMES) {
      expect(screen.getByRole("textbox", { name })).toBeInTheDocument();
    }
  });

  it("opens a part the checks observed facts for while the draft says None.", () => {
    render(
      <DisclosureEditor
        value={value}
        observed={{ shell: ["main.mjs:3"] }}
        onChange={() => {}}
      />,
    );
    expect(
      screen
        .getAllByRole("textbox")
        .map((box) => box.getAttribute("aria-label")),
    ).toEqual(["Shell disclosure"]);
    expect(screen.getByTestId("hub-observed-shell")).toHaveTextContent(
      "Observed by the checks: shell: main.mjs:3",
    );
  });
});
