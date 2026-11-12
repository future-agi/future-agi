import { afterEach, describe, expect, it } from "vitest";
import {
  activePaletteHost,
  dsxOwnsCommandShortcut,
  registerPaletteHost,
} from "../shortcutOwner";

const cleanups = [];
const mountHost = (id) => {
  const off = registerPaletteHost(id);
  cleanups.push(off);
  return off;
};

afterEach(() => {
  cleanups.splice(0).forEach((off) => off());
  document.body.replaceChildren();
});

describe("command shortcut ownership: one palette for the whole product", () => {
  it.each([
    "/dashboard/develop",
    "/dashboard/develop/dataset-1",
    "/dashboard/gateway",
    "/dashboard/settings/billing",
    "/dashboard/observe",
    "/dashboard/console/chat",
  ])("gives Cmd/Ctrl+K to the app palette for every target on %s", (path) => {
    window.history.replaceState(null, "", path);
    mountHost("dashboard");
    const input = document.createElement("input");
    const grid = document.createElement("div");
    grid.id = "fa-data-grid-shell";
    const cell = document.createElement("button");
    grid.append(cell);
    document.body.append(input, grid);
    for (const target of [document.body, input, cell, window]) {
      expect(dsxOwnsCommandShortcut({ target })).toBe(true);
    }
    expect(dsxOwnsCommandShortcut(null)).toBe(true);
  });

  it("leaves the key to the Falcon launcher only where no palette is mounted", () => {
    expect(dsxOwnsCommandShortcut({ target: document.body })).toBe(false);
    const off = mountHost("dashboard");
    expect(dsxOwnsCommandShortcut({ target: document.body })).toBe(true);
    off();
    expect(dsxOwnsCommandShortcut({ target: document.body })).toBe(false);
  });

  it("names one active host: the first mounted, then the next when it leaves", () => {
    const offDashboard = mountHost("dashboard");
    mountHost("console");
    expect(activePaletteHost()).toBe("dashboard");
    offDashboard();
    expect(activePaletteHost()).toBe("console");
  });
});
