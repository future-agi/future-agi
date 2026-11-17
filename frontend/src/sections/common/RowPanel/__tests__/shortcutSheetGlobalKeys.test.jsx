// The "?" sheet's Global section: the command palette's row names its key,
// Cmd/Ctrl+Shift+P. The Cmd/Ctrl+K row names what that key opens on the
// page the sheet is opened on: Falcon AI where its launcher is mounted
// (every dashboard page but the Hub), the command palette where none is (the
// Hub). Where the browser keeps the palette's key for itself (Firefox: a
// private window), the palette row stays and names no key.
import React from "react";
import { afterEach, describe, expect, it } from "vitest";
import { screen, within } from "@testing-library/react";
import { render } from "src/utils/test-utils";
import { MOD_KEY_LABEL } from "src/utils/keymap";
import {
  registerAssistantLauncher,
  registerPaletteHost,
} from "src/sections/common/CommandPalette/shortcutOwner";
import ShortcutSheet from "../ShortcutSheet";

const ON_MAC = MOD_KEY_LABEL === "⌘";
const PALETTE_CAP = ON_MAC ? "⌘⇧P" : "Ctrl+Shift+P";
const COMMAND_K_CAP = ON_MAC ? "⌘K" : "Ctrl+K";
const FIREFOX =
  "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:132.0) Gecko/20100101 Firefox/132.0";
const PALETTE_ROW = "Command palette: go to, search, run a command";

const setUserAgent = (userAgent) =>
  Object.defineProperty(window.navigator, "userAgent", {
    value: userAgent,
    configurable: true,
  });

const cleanups = [];
// A dashboard page: the layout mounts the palette and the Falcon AI
// launcher. The Hub: the palette only.
const onDashboardPage = () =>
  cleanups.push(
    registerPaletteHost("dashboard"),
    registerAssistantLauncher("falcon"),
  );
const onHub = () => cleanups.push(registerPaletteHost("dashboard"));

const row = (action) => document.querySelector(`[data-shortcut="${action}"]`);
const capsOf = (element) =>
  Array.from(element.querySelectorAll("kbd"), (kbd) => kbd.textContent);

describe("the ? sheet's Global keys", () => {
  afterEach(() => {
    cleanups.splice(0).forEach((off) => off());
    delete window.navigator.userAgent;
  });

  it("names the palette's key and reads Cmd/Ctrl+K as the assistant on a dashboard page", () => {
    onDashboardPage();
    render(<ShortcutSheet open onClose={() => {}} surface="global" />);
    expect(screen.getByText("Global")).toBeInTheDocument();
    const palette = row("palette");
    expect(within(palette).getByText(PALETTE_ROW)).toBeInTheDocument();
    expect(capsOf(palette)).toEqual([PALETTE_CAP]);
    const commandKey = row("assistant");
    expect(within(commandKey).getByText("Ask Falcon AI")).toBeInTheDocument();
    expect(within(commandKey).queryByText("Command palette")).toBeNull();
    expect(capsOf(commandKey)).toEqual([COMMAND_K_CAP]);
  });

  it("reads Cmd/Ctrl+K as the command palette on the Hub, where no Falcon AI launcher is mounted", () => {
    onHub();
    render(<ShortcutSheet open onClose={() => {}} surface="global" />);
    const commandKey = row("assistant");
    expect(within(commandKey).getByText("Command palette")).toBeInTheDocument();
    expect(within(commandKey).queryByText("Ask Falcon AI")).toBeNull();
    expect(capsOf(commandKey)).toEqual([COMMAND_K_CAP]);
    const palette = row("palette");
    expect(within(palette).getByText(PALETTE_ROW)).toBeInTheDocument();
    expect(capsOf(palette)).toEqual([PALETTE_CAP]);
  });

  it("finds the Hub's Cmd/Ctrl+K row by its words", () => {
    onHub();
    render(
      <ShortcutSheet
        open
        onClose={() => {}}
        surface="global"
        query="command palette"
      />,
    );
    expect(
      within(row("assistant")).getByText("Command palette"),
    ).toBeInTheDocument();
    expect(within(row("palette")).getByText(PALETTE_ROW)).toBeInTheDocument();
  });

  it("names no key for the palette where the browser keeps it", () => {
    onDashboardPage();
    setUserAgent(FIREFOX);
    render(<ShortcutSheet open onClose={() => {}} surface="global" />);
    const palette = row("palette");
    expect(within(palette).getByText(PALETTE_ROW)).toBeInTheDocument();
    expect(capsOf(palette)).toEqual([]);
    expect(capsOf(row("assistant"))).toEqual([COMMAND_K_CAP]);
  });
});
