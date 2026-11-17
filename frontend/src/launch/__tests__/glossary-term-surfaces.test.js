import { describe, expect, it } from "vitest";
import { GLOSSARY as PREVIEW_GLOSSARY } from "src/test-fixtures/build-data/glossary";
import { GLOSSARY_KEYS } from "src/sections/common/ux/glossary";
import { LAUNCH_GLOSSARY_KEEP, SURFACE_BY_ID } from "../launchSurfaces";
import { GLOSSARY_TERM_SURFACES } from "../glossarySurfaces";

// Every public glossary term names the surfaces of the feature it explains;
// a term without one would never show in a launch build, so a new term has
// to choose its surface here.

const PUBLIC_KEYS = GLOSSARY_KEYS.filter((k) => !(k in PREVIEW_GLOSSARY));

describe("glossary term surfaces", () => {
  it("maps every public term, and only those", () => {
    expect(Object.keys(GLOSSARY_TERM_SURFACES).sort()).toEqual(
      [...PUBLIC_KEYS].sort(),
    );
  });

  it("names only registered surfaces, at least one per term", () => {
    Object.entries(GLOSSARY_TERM_SURFACES).forEach(([key, ids]) => {
      expect(ids.length, key).toBeGreaterThan(0);
      ids.forEach((id) =>
        expect(SURFACE_BY_ID[id], `${key}: ${id}`).toBeTruthy(),
      );
      expect(Object.isFrozen(ids), key).toBe(true);
    });
  });

  it.each(["pairwise", "blind"])(
    "ties %s to review capability, not results",
    (key) => {
      expect(GLOSSARY_TERM_SURFACES[key]).toEqual(["dataset.collab.reviews"]);
    },
  );

  it("keeps the grid terms on a shown surface once the glossary is claimed", () => {
    LAUNCH_GLOSSARY_KEEP.forEach((key) =>
      expect(
        GLOSSARY_TERM_SURFACES[key].some(
          (id) => SURFACE_BY_ID[id].ships === "shown",
        ),
        key,
      ).toBe(true),
    );
  });
});
