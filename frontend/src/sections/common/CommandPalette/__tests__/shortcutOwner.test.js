import { afterEach, describe, expect, it } from "vitest";
import { dsxOwnsCommandShortcut } from "../shortcutOwner";

afterEach(() => { document.body.replaceChildren(); });

describe("command shortcut ownership", () => {
  it.each(["/dashboard/develop", "/dashboard/develop/dataset-1", "/dashboard/gateway", "/dashboard/settings/billing", "/dashboard/observe", "/dashboard/studio"])(
    "keeps global and ordinary form focus with Falcon on %s", (path) => {
      window.history.replaceState(null, "", path);
      const input = document.createElement("input");
      document.body.append(input);
      expect(dsxOwnsCommandShortcut({ target: document.body })).toBe(false);
      expect(dsxOwnsCommandShortcut({ target: input })).toBe(false);
    },
  );
  it.each([['id', 'fa-data-grid-shell'], ['data-results-grid', ''], ['data-dsx-command-palette', '']])(
    "assigns focused descendants of %s to the DSX palette only", (attribute, value) => {
      const grid = document.createElement("div");
      grid.setAttribute(attribute, value);
      const cell = document.createElement("button");
      grid.append(cell);
      document.body.append(grid);
      const palette = dsxOwnsCommandShortcut({ target: cell });
      const falcon = !dsxOwnsCommandShortcut({ target: cell });
      expect(palette).toBe(true);
      expect(falcon).toBe(false);
      expect(Number(palette) + Number(falcon)).toBe(1);
    },
  );
  it("does not claim unrelated grids or missing event targets", () => {
    const grid = document.createElement("div");
    grid.setAttribute("role", "grid");
    document.body.append(grid);
    expect(dsxOwnsCommandShortcut({ target: grid })).toBe(false);
    expect(dsxOwnsCommandShortcut({ target: window })).toBe(false);
    expect(dsxOwnsCommandShortcut(null)).toBe(false);
  });
});
