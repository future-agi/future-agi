// Five parts the launch registry lists are not offered by a deployment yet.
// Where the registry does not offer one, it says the shared line every
// turned-off part says (NOT_IN_RELEASE), whatever the show-unreleased switch
// shows; prototype builds and the unit-test profile offer every part.
import { afterEach, describe, expect, it } from "vitest";
import { NOT_IN_RELEASE } from "src/components/NotInRelease/notInRelease";
import { setCollabOfferForTest } from "../collabOffer";
import * as notOffered from "../notOffered";

const { notOfferedLine } = notOffered;

const PARTS = [
  "list.organize.metadata",
  "list.organize.agents",
  "dataset.collab.translate",
  "dataset.rowExtras.runs",
  "evals.remote",
];

afterEach(() => setCollabOfferForTest(null));

describe("the five parts' words", () => {
  it("the module spells no sentence of its own", () => {
    const spelled = Object.entries(notOffered).filter(
      ([, value]) => typeof value === "string",
    );
    expect(spelled).toEqual([]);
  });
});

describe("which part says the line", () => {
  it("each part a deployment does not offer says the shared line", () => {
    setCollabOfferForTest({ all: false, madeReal: [] });
    PARTS.forEach((surface) =>
      expect([surface, notOfferedLine(surface)]).toEqual([
        surface,
        NOT_IN_RELEASE,
      ]),
    );
  });

  it("a part the registry makes real says nothing; the others still say the shared line", () => {
    setCollabOfferForTest({ all: false, madeReal: ["dataset.rowExtras.runs"] });
    expect(notOfferedLine("dataset.rowExtras.runs")).toBeNull();
    expect(notOfferedLine("dataset.collab.translate")).toBe(NOT_IN_RELEASE);
  });

  it("this build makes runs across experiments real; the other four still say the shared line", () => {
    setCollabOfferForTest({ all: false });
    expect(notOfferedLine("dataset.rowExtras.runs")).toBeNull();
    const others = PARTS.filter((s) => s !== "dataset.rowExtras.runs");
    others.forEach((surface) =>
      expect([surface, notOfferedLine(surface)]).toEqual([
        surface,
        NOT_IN_RELEASE,
      ]),
    );
  });

  it("prototype builds and the unit-test profile offer every part", () => {
    PARTS.forEach((surface) =>
      expect([surface, notOfferedLine(surface)]).toEqual([surface, null]),
    );
  });

  it("a part outside the five says nothing", () => {
    setCollabOfferForTest({ all: false });
    expect(notOfferedLine("list.organize.folders")).toBeNull();
  });
});
