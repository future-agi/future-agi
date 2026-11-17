import { beforeEach, describe, expect, it, vi } from "vitest";
import { catalogEntry, entriesFor, isOffered } from "../catalog";
import { pickCard, pickEntry, pickerFromParams } from "../sourceModel";

const flags = vi.hoisted(() => ({ launch: true, running: false, kinds: true }));
vi.mock("src/launch/launchSwitch", () => ({
  isSurfaceShown: (id) =>
    !flags.launch ||
    (id === "exp.new.runningSources" ? flags.running : flags.kinds),
}));

beforeEach(() => {
  Object.assign(flags, { launch: true, running: false, kinds: true });
});

const hiddenLinks = [
  "src=running",
  "source=running",
  "source=http",
  "src=http",
  "entry=http",
  "entry=a2a",
  "entry=mcp_tool",
  "src=running&entry=http",
  "source=http&entry=a2a&mode=remote&form=connect",
];

describe("hidden running sources are not selectable", () => {
  it.each(hiddenLinks)("refuses new_experiment=1&%s", (query) => {
    const params = new URLSearchParams(`new_experiment=1&${query}`);
    for (const input of [params, Object.fromEntries(params)]) {
      expect(pickerFromParams(input)).toMatchObject({
        level: 1,
        card: null,
        entryId: null,
        mode: null,
      });
    }
  });

  it.each(["http", "a2a", "mcp_tool"])(
    "retains %s lookup metadata but refuses selection",
    (id) => {
      expect(catalogEntry(id)).not.toBeNull();
      expect(isOffered(id)).toBe(false);
      const state = { level: 1, card: null, entryId: null };
      expect(pickEntry(state, id)).toEqual(state);
    },
  );

  it("refuses direct hidden-card selection and empty-query catalog enumeration", () => {
    const state = { level: 1, card: null, entryId: null };
    expect(pickCard(state, "running")).toEqual(state);
    for (const kind of ["running", "http", "a2a", "mcp_tool"]) {
      expect(entriesFor(kind)).toEqual([]);
    }
  });

  it.each(["prompt", "agent"])("keeps the qualified %s path", (card) => {
    expect(
      pickerFromParams({ new_experiment: "1", source: card }),
    ).toMatchObject({
      card,
      level: 1,
    });
    expect(pickCard({ level: 1 }, card)).toMatchObject({ card, level: 2 });
  });

  it.each(["non-launch", "all-shown"])(
    "keeps %s running selections",
    (mode) => {
      flags.launch = mode !== "non-launch";
      flags.running = mode === "all-shown";
      expect(pickerFromParams({ src: "running" })).toMatchObject({
        level: 2,
        card: "running",
      });
      expect(pickCard({ level: 1 }, "running").card).toBe("running");
      for (const id of ["http", "a2a", "mcp_tool"]) {
        expect(isOffered(id)).toBe(true);
        expect(entriesFor("running").some((entry) => entry.id === id)).toBe(
          true,
        );
      }
    },
  );
});
