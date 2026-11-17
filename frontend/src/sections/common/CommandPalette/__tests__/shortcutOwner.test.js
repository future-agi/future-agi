import { afterEach, describe, expect, it } from "vitest";
import {
  activePaletteHost,
  appPaletteOwnsCommandShortcut,
  assistantOwnsCommandShortcut,
  registerAssistantLauncher,
  registerPaletteHost,
} from "../shortcutOwner";

const cleanups = [];
const mountHost = (id) => {
  const off = registerPaletteHost(id);
  cleanups.push(off);
  return off;
};
const mountLauncher = (id) => {
  const off = registerAssistantLauncher(id);
  cleanups.push(off);
  return off;
};

afterEach(() => {
  cleanups.splice(0).forEach((off) => off());
  document.body.replaceChildren();
});

describe("command shortcut ownership", () => {
  it.each([
    "/dashboard/develop",
    "/dashboard/develop/dataset-1",
    "/dashboard/gateway",
    "/dashboard/settings/billing",
    "/dashboard/observe",
  ])("gives Cmd/Ctrl+K to the assistant for every target on %s", (path) => {
    window.history.replaceState(null, "", path);
    mountHost("dashboard");
    mountLauncher("falcon");
    const input = document.createElement("input");
    const grid = document.createElement("div");
    grid.id = "fa-data-grid-shell";
    const cell = document.createElement("button");
    grid.append(cell);
    document.body.append(input, grid);
    expect(assistantOwnsCommandShortcut()).toBe(true);
    for (const target of [document.body, input, cell, window]) {
      expect(appPaletteOwnsCommandShortcut({ target })).toBe(false);
    }
    expect(appPaletteOwnsCommandShortcut()).toBe(false);
  });

  it("leaves the key to the palette only where no assistant launcher is mounted", () => {
    // The Console tab: a palette and no launcher.
    window.history.replaceState(null, "", "/dashboard/console/chat");
    expect(appPaletteOwnsCommandShortcut()).toBe(false);
    mountHost("console");
    expect(appPaletteOwnsCommandShortcut()).toBe(true);
    const off = mountLauncher("falcon");
    expect(appPaletteOwnsCommandShortcut()).toBe(false);
    off();
    expect(appPaletteOwnsCommandShortcut()).toBe(true);
  });

  it("names one active host: the first mounted, then the next when it leaves", () => {
    const offDashboard = mountHost("dashboard");
    mountHost("console");
    expect(activePaletteHost()).toBe("dashboard");
    offDashboard();
    expect(activePaletteHost()).toBe("console");
  });
});
