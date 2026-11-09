import { describe, expect, it } from "vitest";
import inventory from "../__fixtures__/inventory-classes.json";
import laneAdditions from "../__fixtures__/registry-lane-additions-705.json";
import { SURFACE_BY_ID, surfacesOfOption } from "../launchSurfaces";
import { surfaceShownIn } from "../launchSwitch";
import { madeRealSurfaceIds } from "../madeReal";

const PAGE = "shell.notFound.page";
const BUNDLE = "shell.notFound";
// The experiment page hosts experiment-detail/ExperimentNotFound.jsx.
// J09 REVISED: the existing not-found page follows the restored classic route.
const EXPERIMENT_HOST = "exp.classic";
const REST = [
  "header:moved-banner",
  "url:owned-elsewhere",
  "url:pages",
  "url:grid-filters",
];
const state = (experimentsBasic, ids = []) => ({
  launch: true,
  experimentsBasic,
  madeReal: new Set(ids),
});
const shown = (option, current) =>
  surfacesOfOption(option).every((s) => surfaceShownIn(s.id, current));

describe("not-found page scope", () => {
  it("gives the not-found state its own hidden surface", () => {
    expect(SURFACE_BY_ID[PAGE]).toMatchObject({
      ships: "hidden",
      madeRealBy: "EVAL",
      options: ["state:not-found"],
    });
    expect(SURFACE_BY_ID[PAGE].parent).toBeUndefined();
    expect(SURFACE_BY_ID[PAGE].experiments).toBeUndefined();
    expect(SURFACE_BY_ID[BUNDLE].options).toEqual(REST);
    expect(surfacesOfOption("state:not-found").map((s) => s.id)).toEqual([
      PAGE,
    ]);
    expect(laneAdditions).toContainEqual({
      id: PAGE,
      slot: "EVAL",
      ships: "hidden",
      splitFrom: BUNDLE,
      options: ["state:not-found"],
    });
  });

  it("describes only the dataset page; the experiment page stays with its host", () => {
    const { label, gate, why } = SURFACE_BY_ID[PAGE];
    expect(label).toMatch(/\bdataset\b/);
    expect(label).not.toMatch(/experiment/i);
    expect(gate).toMatch(
      /DataTab\/DatasetNotFound\.jsx renders in every build/,
    );
    expect(gate).not.toMatch(/ExperimentNotFound\.jsx[^.]* in every build/);
    expect(gate).toContain(
      `ExperimentNotFound.jsx renders only on ${EXPERIMENT_HOST} routes`,
    );
    expect(why).not.toMatch(/where experiments show/);
    expect(why).toContain(EXPERIMENT_HOST);
    expect(SURFACE_BY_ID[EXPERIMENT_HOST].ships).toBe("shown");
    for (const basic of [false, true])
      expect(surfaceShownIn(EXPERIMENT_HOST, state(basic, [PAGE]))).toBe(
        surfaceShownIn(EXPERIMENT_HOST, state(basic)),
      );
    expect(surfaceShownIn(EXPERIMENT_HOST, state(false, [PAGE]))).toBe(true);
  });

  it.each([false, true])(
    "the page claim reveals only the not-found state (experimentsBasic=%s)",
    (basic) => {
      const revealed = Object.keys(inventory.classes).filter(
        (option) =>
          !shown(option, state(basic)) && shown(option, state(basic, [PAGE])),
      );
      expect(revealed).toEqual(["state:not-found"]);
      for (const option of REST)
        expect(shown(option, state(basic, [PAGE]))).toBe(false);
    },
  );

  it.each([false, true])(
    "without F77 claims, sibling claims keep the page and bundle hidden (experimentsBasic=%s)",
    (basic) => {
      // Simulate OFF independently of either later flag-only ON commit.
      const claims = [...madeRealSurfaceIds()].filter(
        (id) => id !== PAGE && id !== "palette.glossary",
      );
      const live = state(basic, claims);
      expect(surfaceShownIn(PAGE, live)).toBe(false);
      expect(surfaceShownIn(BUNDLE, live)).toBe(false);
      expect(shown("state:not-found", live)).toBe(false);
      for (const option of REST) expect(shown(option, live)).toBe(false);
    },
  );
});
