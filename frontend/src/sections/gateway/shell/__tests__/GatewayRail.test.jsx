import React from "react";
import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { MemoryRouter, useLocation } from "react-router-dom";
import { ThemeProvider, createTheme } from "@mui/material/styles";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { palette } from "src/theme/palette";
import { typography } from "src/theme/typography";

import CommandPalette from "src/sections/common/CommandPalette/CommandPalette";
import {
  registeredCommands,
  useCommandStore,
} from "src/sections/common/CommandPalette/commandRegistry";
import { useShortcutStore } from "src/sections/common/ux/useShortcuts";
import GatewayRail from "../GatewayRail";
import GatewayRailScroll from "../GatewayRailScroll";
import { gatewayRailGroups } from "../gateway-rail";

// Keep the actual palette, command registry, shortcut store, MUI dialog and
// deferred navigation. Match the shared palette fixture's resource boundaries;
// global dataset/run/row providers may perform permitted reads in the real app.
const h = vi.hoisted(() => ({ request: vi.fn() }));
vi.mock("src/utils/axios", () => ({
  default: Object.assign(h.request, {
    defaults: { headers: { common: {} } },
    get: h.request,
    post: h.request,
    put: h.request,
    patch: h.request,
    delete: h.request,
  }),
  endpoints: {},
}));
vi.mock("src/components/iconify", () => ({ default: () => null }));
vi.mock("src/components/settings", () => ({
  useSettingsContext: () => ({ onUpdate: vi.fn() }),
}));
vi.mock("src/hooks/useCapabilities", () => ({
  useFeatureAllowed: () => ({ allowed: true }),
}));
vi.mock("src/sections/common/CommandPalette/providers", () => ({
  datasetIdOfPath: () => null,
  useDatasetCommands: () => [],
  useDatasetKind: () => null,
  useGlobalCommands: () => [],
  useRowCommands: () => [],
  useRunCommands: () => [],
}));

// jsdom lays nothing out, so every scroll size is 0: stub the three the cue
// reads, on every element, for the length of a test.
const box = { scrollHeight: 0, clientHeight: 0 };
const stubbed = ["scrollHeight", "clientHeight"];
let saved;

beforeEach(() => {
  useCommandStore.setState({ sources: {} });
  useShortcutStore.getState().setPaletteOpen(false);
  window.localStorage.clear();
  h.request.mockClear();
  saved = stubbed.map((name) => [
    name,
    Object.getOwnPropertyDescriptor(HTMLElement.prototype, name),
  ]);
  stubbed.forEach((name) =>
    Object.defineProperty(HTMLElement.prototype, name, {
      configurable: true,
      get: () => box[name],
    }),
  );
});

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
  saved.forEach(([name, descriptor]) =>
    descriptor
      ? Object.defineProperty(HTMLElement.prototype, name, descriptor)
      : delete HTMLElement.prototype[name],
  );
});

function renderScroll() {
  render(
    <ThemeProvider
      theme={createTheme({ palette: palette("light"), typography })}
    >
      <GatewayRailScroll>
        <nav>rail</nav>
      </GatewayRailScroll>
    </ThemeProvider>,
  );
  return screen.getByTestId("gateway-rail-scroll");
}

// At 1440x960 the rail scrolls by 49 px under the
// Resources footer and nothing shows that it does ("Budgets" cut in half,
// "Billing & credits" hidden).
describe("GatewayRail overflow cue", () => {
  it("shows the overflow cue when scrollHeight > clientHeight", () => {
    box.scrollHeight = 812;
    box.clientHeight = 763;
    const area = renderScroll();
    expect(area).toHaveAttribute("data-more-below", "true");
    expect(
      window.getComputedStyle(area).maskImage || area.style.maskImage,
    ).toMatch(/linear-gradient/);
  });

  it("drops the cue once the rail is scrolled to its end", () => {
    box.scrollHeight = 812;
    box.clientHeight = 763;
    const area = renderScroll();
    const scroller = area.querySelector("[data-rail-scroller]");
    act(() => {
      scroller.scrollTop = 49;
      fireEvent.scroll(scroller);
    });
    expect(area).toHaveAttribute("data-more-below", "false");
  });

  it("shows no cue when the rail fits", () => {
    box.scrollHeight = 700;
    box.clientHeight = 763;
    expect(renderScroll()).toHaveAttribute("data-more-below", "false");
  });
});

function LocationProbe() {
  const location = useLocation();
  return (
    <output data-testid="gateway-location">
      {location.pathname}
      {location.search}
    </output>
  );
}

function paletteFixture(
  collapsed,
  groups = gatewayRailGroups({ noManagedModels: true }),
) {
  return (
    <ThemeProvider
      theme={createTheme({ palette: palette("light"), typography })}
    >
      <MemoryRouter initialEntries={["/dashboard/gateway?keep=1"]}>
        <GatewayRail groups={groups} collapsed={collapsed} />
        <CommandPalette />
        <LocationProbe />
      </MemoryRouter>
    </ThemeProvider>
  );
}

async function openFinder() {
  const trigger = screen.getByRole("button", { name: "Find a gateway page" });
  fireEvent.click(trigger);
  const input = await screen.findByRole("combobox", { name: "Command" });
  await waitFor(() => expect(input).toHaveFocus());
  return { input, trigger };
}

describe.each([false, true])(
  "gateway finder in the existing palette (collapsed=%s)",
  (collapsed) => {
    it("opens from the visible trigger and Escape restores trigger focus without changing the URL", async () => {
      render(paletteFixture(collapsed));
      const { input, trigger } = await openFinder();
      expect(
        screen.getByRole("dialog", { name: "Command palette" }),
      ).toBeVisible();
      fireEvent.keyDown(input, { key: "Escape" });
      await waitFor(() =>
        expect(screen.queryByRole("dialog")).not.toBeInTheDocument(),
      );
      expect(useShortcutStore.getState().paletteOpen).toBe(false);
      expect(trigger).toHaveFocus();
      expect(screen.getByTestId("gateway-location")).toHaveTextContent(
        "/dashboard/gateway?keep=1",
      );
    });

    it("uses the palette's Arrow keys and Enter to navigate to the selected gateway page", async () => {
      render(paletteFixture(collapsed));
      const { input, trigger } = await openFinder();
      fireEvent.change(input, { target: { value: "models" } });
      const model = screen.getByRole("option", { name: /Go to Models/ });
      const routing = screen.getByRole("option", {
        name: /Go to Auto routing/,
      });
      expect(model).toHaveAttribute("aria-selected", "true");
      fireEvent.keyDown(input, { key: "ArrowDown" });
      expect(routing).toHaveAttribute("aria-selected", "true");
      fireEvent.keyDown(input, { key: "ArrowUp" });
      expect(model).toHaveAttribute("aria-selected", "true");
      fireEvent.keyDown(input, { key: "ArrowDown" });
      fireEvent.keyDown(input, { key: "Enter" });
      await waitFor(() =>
        expect(screen.getByTestId("gateway-location")).toHaveTextContent(
          "/dashboard/gateway/models/auto",
        ),
      );
      await waitFor(() =>
        expect(screen.queryByRole("dialog")).not.toBeInTheDocument(),
      );
      expect(trigger).toHaveFocus();
    });

    it("adds no gateway requests when opening and searching and retains only visible destinations", async () => {
      const fetch = vi.fn();
      vi.stubGlobal("fetch", fetch);
      const xhr = vi.spyOn(XMLHttpRequest.prototype, "open");
      const { unmount } = render(paletteFixture(collapsed));
      const { input } = await openFinder();
      for (const [query, title] of [
        ["API keys", "API keys"],
        ["logs", "Logs"],
        ["budgets", "Budgets"],
        ["provider", "Providers"],
        ["models", "Models"],
      ]) {
        fireEvent.change(input, { target: { value: query } });
        expect(
          screen.getByRole("option", { name: new RegExp(`Go to ${title}`) }),
        ).toBeVisible();
      }
      for (const hidden of ["Catalog", "Integrations", "Policies"]) {
        fireEvent.change(input, { target: { value: hidden } });
        expect(
          screen.queryByRole("option", { name: new RegExp(`Go to ${hidden}`) }),
        ).not.toBeInTheDocument();
      }
      expect(h.request).not.toHaveBeenCalled();
      expect(fetch).not.toHaveBeenCalled();
      expect(xhr).not.toHaveBeenCalled();
      unmount();
      expect(registeredCommands(useCommandStore.getState().sources)).toEqual(
        [],
      );
    });

    it("updates an open palette when visible groups change", async () => {
      const { rerender } = render(
        paletteFixture(collapsed, gatewayRailGroups({ integrations: true })),
      );
      const { input } = await openFinder();
      fireEvent.change(input, { target: { value: "integrations" } });
      expect(
        screen.getByRole("option", { name: /Go to Integrations/ }),
      ).toBeVisible();
      rerender(
        paletteFixture(collapsed, gatewayRailGroups({ integrations: false })),
      );
      expect(
        screen.queryByRole("option", { name: /Go to Integrations/ }),
      ).not.toBeInTheDocument();
      expect(input).toHaveFocus();
    });

    it("does not resurrect a now-hidden destination from shared palette recents", async () => {
      const { rerender } = render(
        paletteFixture(collapsed, gatewayRailGroups({ integrations: true })),
      );
      const { input } = await openFinder();
      fireEvent.change(input, { target: { value: "integrations" } });
      fireEvent.keyDown(input, { key: "Enter" });
      await waitFor(() =>
        expect(screen.getByTestId("gateway-location")).toHaveTextContent(
          "/dashboard/gateway/integrations",
        ),
      );
      await waitFor(() =>
        expect(screen.queryByRole("dialog")).not.toBeInTheDocument(),
      );
      await openFinder();
      expect(screen.getByText("Recent")).toBeVisible();
      expect(
        screen.getAllByRole("option", { name: /Go to Integrations/ }),
      ).toHaveLength(2);
      rerender(
        paletteFixture(collapsed, gatewayRailGroups({ integrations: false })),
      );
      expect(
        registeredCommands(useCommandStore.getState().sources).some(
          (command) => command.label === "Go to Integrations",
        ),
      ).toBe(false);
      expect(
        screen.queryByRole("option", { name: /Go to Integrations/ }),
      ).not.toBeInTheDocument();
      fireEvent.keyDown(screen.getByRole("combobox", { name: "Command" }), {
        key: "Escape",
      });
      await waitFor(() =>
        expect(screen.queryByRole("dialog")).not.toBeInTheDocument(),
      );
      await openFinder();
      expect(
        screen.queryByRole("option", { name: /Go to Integrations/ }),
      ).not.toBeInTheDocument();
    });

    it("Ctrl+K from the rail trigger opens the one palette and Escape returns to the trigger", async () => {
      render(paletteFixture(collapsed));
      const trigger = screen.getByRole("button", {
        name: "Find a gateway page",
      });
      trigger.focus();
      fireEvent.keyDown(trigger, { key: "k", ctrlKey: true });
      expect(useShortcutStore.getState().paletteOpen).toBe(true);
      const input = await screen.findByRole("combobox", { name: "Command" });
      expect(
        screen.getByRole("dialog", { name: "Command palette" }),
      ).toBeVisible();
      fireEvent.keyDown(input, { key: "Escape" });
      await waitFor(() =>
        expect(screen.queryByRole("dialog")).not.toBeInTheDocument(),
      );
      expect(trigger).toHaveFocus();
    });
  },
);
