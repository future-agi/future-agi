import React from "react";
import { afterEach, describe, expect, it } from "vitest";
import { fireEvent, render, screen } from "src/utils/test-utils";
import ShortcutSheetComponent from "src/sections/common/RowPanel/ShortcutSheet";
import { searchGlossary } from "src/sections/common/ux/glossary";
import {
  glossaryShownIn,
  setLaunchOverrideForTest,
  surfaceShownIn,
} from "../launchSwitch";

// The component's JSDoc omits the parent props object, so TypeScript infers
// its argument as boolean. Use its actual propTypes contract for this test;
// this is the same component, not a wrapper or a mock.
const ShortcutSheet =
  /** @type {React.ComponentType<import("prop-types").InferProps<typeof ShortcutSheetComponent.propTypes>>} */ (
    /** @type {unknown} */ (ShortcutSheetComponent)
  );

// The glossary (the `?` sheet's Glossary tab and the palette's glossary
// results) is the palette.glossary claim. Once it shows, each term follows
// the surface of the feature it explains, so a launch build never lists a
// word for a page it hides.

const state = (madeReal, experimentsBasic = false, launch = true) => ({
  launch,
  experimentsBasic,
  madeReal: new Set(madeReal),
});
const listed = (s) =>
  searchGlossary("")
    .map(({ key }) => key)
    .filter((key) => glossaryShownIn(key, s));

const GRID_TERMS = ["wrapLines", "matchedByMeaning"];

afterEach(() => setLaunchOverrideForTest(null));

describe("glossary terms follow their features", () => {
  it("a claimed glossary lists only the terms of shown features", () => {
    expect(listed(state(["palette.glossary"]))).toEqual(GRID_TERMS);
  });

  it("experimentsBasic adds the wizard's term, not the results' terms", () => {
    const basic = state(["palette.glossary"], true);
    expect(listed(basic)).toEqual(["expectedAnswers", ...GRID_TERMS]);
    ["successRate", "baseline", "flaky", "passHatK", "matrix"].forEach((k) =>
      expect(glossaryShownIn(k, basic), k).toBe(false),
    );
  });

  it("a results view's claim shows its own terms and no other view's", () => {
    const paths = state(["palette.glossary", "exp.results.paths"], true);
    ["paths", "pathShare", "graphDiff", "graphQuality", "differ"].forEach((k) =>
      expect(glossaryShownIn(k, paths), k).toBe(true),
    );
    ["matrix", "successRate", "costPerSolved", "runBar"].forEach((k) =>
      expect(glossaryShownIn(k, paths), k).toBe(false),
    );
    // The same view without experimentsBasic hides its terms with it.
    const off = state(["palette.glossary", "exp.results.paths"], false);
    expect(glossaryShownIn("pathShare", off)).toBe(false);
  });

  it.each(["exp.results.matrix", "exp.results.summary", "exp.compare"])(
    "%s alone never lists pairwise or blind review terms",
    (results) => {
      const current = state(["palette.glossary", results], true);
      // Positive control: this is a shown results host, not an OFF-only test.
      expect(surfaceShownIn(results, current)).toBe(true);
      expect(surfaceShownIn("dataset.collab.reviews", current)).toBe(false);
      expect(glossaryShownIn("baseline", current)).toBe(true);
      expect(glossaryShownIn("pairwise", current)).toBe(false);
      expect(glossaryShownIn("blind", current)).toBe(false);
    },
  );

  it.each([false, true])(
    "review terms follow the review capability (experimentsBasic=%s)",
    (basic) => {
      const current = state(
        ["palette.glossary", "dataset.collab.reviews"],
        basic,
      );
      expect(surfaceShownIn("dataset.collab.reviews", current)).toBe(true);
      expect(surfaceShownIn("exp.results.matrix", current)).toBe(false);
      expect(glossaryShownIn("pairwise", current)).toBe(true);
      expect(glossaryShownIn("blind", current)).toBe(true);
      expect(glossaryShownIn("matrix", current)).toBe(false);
    },
  );

  it("unknown terms fail closed only in a claimed launch glossary", () => {
    expect(glossaryShownIn("unknown-term", state(["palette.glossary"]))).toBe(
      false,
    );
    expect(glossaryShownIn("unknown-term", state([], false, false))).toBe(true);
  });

  it("an unclaimed glossary keeps the legacy terms even with sibling claims", () => {
    const current = state(
      ["exp.results.matrix", "dataset.collab.reviews"],
      true,
    );
    expect(listed(current)).toEqual(GRID_TERMS);
  });

  it("an unclaimed glossary keeps its two grid terms; a prototype keeps all", () => {
    const unclaimed = state([]);
    expect(listed(unclaimed)).toEqual(GRID_TERMS);
    expect(glossaryShownIn("baseline", unclaimed)).toBe(false);
    const prototype = state([], false, false);
    expect(glossaryShownIn("baseline", prototype)).toBe(true);
    expect(glossaryShownIn("pathShare", prototype)).toBe(true);
  });
});

describe("the ? sheet's Glossary tab", () => {
  const open = () =>
    render(
      <ShortcutSheet open onClose={() => {}} surface="grid" tab="glossary" />,
    );
  const rows = () =>
    [...document.querySelectorAll("[data-glossary]")].map((el) =>
      el.getAttribute("data-glossary"),
    );

  it("lists only shown terms and its search hint names only shown terms", () => {
    setLaunchOverrideForTest({
      launch: true,
      experimentsBasic: false,
      madeReal: ["palette.glossary"],
    });
    open();
    // The dialog's accessible name comes from its title row (MUI links the
    // DialogTitle), so the tab is what proves the glossary shows.
    expect(screen.getByRole("dialog")).toHaveAttribute(
      "data-fa-shortcut-sheet",
    );
    expect(screen.getByRole("tab", { name: "Glossary" })).toHaveAttribute(
      "aria-selected",
      "true",
    );
    expect(rows()).toEqual(GRID_TERMS);
    const search = screen.getByRole("textbox", { name: "Search terms" });
    expect(search).toHaveAttribute(
      "placeholder",
      "Search terms (wrap lines, matched by meaning…)",
    );
    fireEvent.change(search, { target: { value: "pass^3" } });
    expect(rows()).toEqual([]);
    expect(screen.getByText("No term matches “pass^3”.")).toBeInTheDocument();
    fireEvent.change(search, { target: { value: "meaning" } });
    expect(rows()).toEqual(["matchedByMeaning"]);
  });

  it("forces the shortcuts tab while the glossary is unclaimed", () => {
    setLaunchOverrideForTest(state([]));
    open();
    expect(
      screen.queryByRole("tab", { name: "Glossary" }),
    ).not.toBeInTheDocument();
    expect(
      screen.getByRole("textbox", { name: "Search shortcuts" }),
    ).toHaveAttribute("placeholder", "Search shortcuts (a key or a word)");
    expect(rows()).toEqual([]);
  });

  it("a Matrix-only sheet lists Matrix but neither review term", () => {
    setLaunchOverrideForTest(
      state(["palette.glossary", "exp.results.matrix"], true),
    );
    open();
    expect(rows()).toContain("matrix");
    expect(rows()).not.toContain("pairwise");
    expect(rows()).not.toContain("blind");
    for (const term of ["pairwise", "blind"]) {
      fireEvent.change(screen.getByRole("textbox", { name: "Search terms" }), {
        target: { value: term },
      });
      expect(rows()).toEqual([]);
      expect(
        screen.getByText(`No term matches “${term}”.`),
      ).toBeInTheDocument();
    }
  });

  it("keeps the full glossary and its hint in a prototype build", () => {
    setLaunchOverrideForTest({ launch: false });
    open();
    expect(
      screen.getByRole("textbox", { name: "Search terms" }),
    ).toHaveAttribute("placeholder", "Search terms (pass^3, baseline, flaky…)");
    expect(rows()).toEqual(expect.arrayContaining(["baseline", "pathShare"]));
  });
});
