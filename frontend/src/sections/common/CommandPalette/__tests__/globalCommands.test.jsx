import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  act,
  cleanup,
  fireEvent,
  render,
  renderHook,
  screen,
  waitFor,
} from "@testing-library/react";
import { MemoryRouter, useLocation } from "react-router-dom";
import { ThemeProvider, createTheme } from "@mui/material/styles";
import { palette } from "src/theme/palette";
import { typography } from "src/theme/typography";
import { currentProtoStage } from "src/sections/experiment-detail/results/shareLink";
import { useShortcutStore } from "src/sections/common/ux/useShortcuts";
import CommandPalette from "../CommandPalette";
import {
  pushRecent,
  readRecent,
  registeredCommands,
  useCommandStore,
} from "../commandRegistry";
import { useGlobalCommands, viewLink } from "../providers";

// Keep the real global commands and palette; only unrelated resource reads and
// surrounding application context are stubbed for the recent-link fixture.
vi.mock("../providers", async (importOriginal) => ({
  ...(await importOriginal()),
  useDatasetCommands: () => [],
  useDatasetKind: () => null,
  useRowCommands: () => [],
  useRunCommands: () => [],
}));
vi.mock("src/components/iconify", () => ({ default: () => null }));
vi.mock("src/components/settings", () => ({
  useSettingsContext: () => ({ onUpdate: vi.fn() }),
}));
vi.mock("src/hooks/useCapabilities", () => ({
  useFeatureAllowed: () => ({ allowed: true }),
}));

const ctx = (extra = {}) => ({
  isDark: false,
  hasTips: false,
  showTips: vi.fn(),
  openSheet: vi.fn(),
  copy: vi.fn(),
  askFalcon: vi.fn(),
  toggleDark: vi.fn(),
  ...extra,
});

describe("global palette commands (one view, G-04)", () => {
  it("has no Simple / Advanced switch", () => {
    const { result } = renderHook(() => useGlobalCommands(ctx()));
    const ids = result.current.map((c) => c.id);
    expect(ids.some((id) => id.startsWith("ui."))).toBe(false);
    expect(
      result.current.some((c) => /Switch to (Simple|Advanced)/.test(c.label)),
    ).toBe(false);
    expect(ids).toContain("view.copyLink");
  });

  it("offers `Show tips on this page` only when the page has tips", () => {
    const none = renderHook(() => useGlobalCommands(ctx()));
    expect(none.result.current.some((c) => c.id === "help.tips")).toBe(false);
    const c = ctx({ hasTips: true });
    const some = renderHook(() => useGlobalCommands(c));
    const tips = some.result.current.find((x) => x.id === "help.tips");
    expect(tips.label).toBe("Show tips on this page");
    tips.run();
    expect(c.showTips).toHaveBeenCalled();
  });

  it("Copy link to this view drops the legacy `ui` param", () => {
    window.history.replaceState(
      {},
      "",
      "/dashboard/develop?ui=simple&tab=data",
    );
    expect(viewLink()).toBe(
      `${window.location.origin}/dashboard/develop?tab=data`,
    );
  });
});

function LocationProbe() {
  const location = useLocation();
  return <output data-testid="palette-location">{location.pathname}</output>;
}

function renderRecentPalette() {
  return render(
    <ThemeProvider
      theme={createTheme({ palette: palette("light"), typography })}
    >
      <MemoryRouter initialEntries={["/dashboard/gateway"]}>
        <CommandPalette />
        <LocationProbe />
      </MemoryRouter>
    </ThemeProvider>,
  );
}

describe("palette recent destinations", () => {
  beforeEach(() => {
    useCommandStore.setState({ sources: {} });
    useShortcutStore.getState().setPaletteOpen(false);
    window.localStorage.clear();
  });

  afterEach(() => {
    cleanup();
    useCommandStore.setState({ sources: {} });
    useShortcutStore.getState().setPaletteOpen(false);
    window.localStorage.clear();
  });

  it("renders and runs an unmarked cross-page recent after its source unregisters", async () => {
    const command = {
      id: "page.saved-view",
      label: "Open saved view",
      href: "/dashboard/develop",
    };
    useCommandStore.getState().register("other-page", [command]);
    pushRecent(null, command, currentProtoStage());
    useCommandStore.getState().unregister("other-page");
    expect(registeredCommands(useCommandStore.getState().sources)).toEqual([]);
    expect(readRecent(null, currentProtoStage())[0]).not.toHaveProperty(
      "requiresLiveRegistration",
    );

    renderRecentPalette();
    act(() => useShortcutStore.getState().setPaletteOpen(true));
    const recent = await screen.findByRole("option", {
      name: /Open saved view/,
    });
    expect(screen.getByText("Recent")).toBeVisible();
    expect(recent).toBeVisible();
    fireEvent.click(recent);
    await waitFor(() =>
      expect(screen.getByTestId("palette-location")).toHaveTextContent(
        "/dashboard/develop",
      ),
    );
  });

  it("does not execute a marked recent unregistered before deferred navigation", async () => {
    const command = {
      id: "page.visible-destination",
      label: "Open permitted destination",
      href: "/dashboard/develop",
      requiresLiveRegistration: true,
    };
    useCommandStore.getState().register("visible-page", [command]);
    pushRecent(null, command, currentProtoStage());
    expect(readRecent(null, currentProtoStage())[0]).toMatchObject({
      requiresLiveRegistration: true,
    });

    renderRecentPalette();
    act(() => useShortcutStore.getState().setPaletteOpen(true));
    const [recent] = await screen.findAllByRole("option", {
      name: /Open permitted destination/,
    });
    expect(screen.getByText("Recent")).toBeVisible();
    expect(recent).toBeVisible();
    await act(async () => {
      fireEvent.click(recent);
      useCommandStore.getState().unregister("visible-page");
      // Let the queued navigation run before checking that it was refused.
      await new Promise((resolve) => setTimeout(resolve, 0));
    });
    await waitFor(() =>
      expect(screen.queryByRole("dialog")).not.toBeInTheDocument(),
    );
    expect(screen.getByTestId("palette-location")).toHaveTextContent(
      "/dashboard/gateway",
    );
  });
});
