/* eslint-disable react/prop-types */
// The Console route mounts the palette once, in its layout: Cmd-K opens it in
// every state of the tab, the viewer panel and the loading screen included.
import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { useShortcutStore } from "src/sections/common/ux/useShortcuts";
import { LoadingScreen } from "src/components/loading-screen";
import OmegaConsoleLayout from "src/sections/omega/shell/OmegaConsoleLayout";
import ConsoleViewerPanel from "src/sections/omega/console/ConsoleViewerPanel";
import {
  ShellTestProviders,
  ensureWebStorage,
} from "src/sections/omega/shell/__tests__/test-providers";
import { useCommandStore } from "../commandRegistry";

vi.mock("../providers", async (importOriginal) => ({
  ...(await importOriginal()),
  useDatasetCommands: () => [],
  useDatasetKind: () => null,
  useRowCommands: () => [],
  useRunCommands: () => [],
}));
vi.mock("../AppPageCommands", () => ({ default: () => null }));
vi.mock("src/components/iconify", () => ({ default: () => null }));
vi.mock("src/components/settings", () => ({
  useSettingsContext: () => ({ onUpdate: vi.fn() }),
}));
vi.mock("src/hooks/useCapabilities", async (original) => ({
  ...(await original()),
  useFeatureAllowed: () => ({ allowed: true }),
}));
vi.mock("src/auth/hooks", () => ({
  useAuthContext: () => ({ user: { id: "u1" }, logout: () => {} }),
}));
vi.mock("src/components/two-factor-enforcement/TwoFactorBanner", () => ({
  default: () => null,
}));

function renderTab(content) {
  return render(
    <ShellTestProviders>
      <MemoryRouter initialEntries={["/dashboard/console/chat"]}>
        <OmegaConsoleLayout>{content}</OmegaConsoleLayout>
      </MemoryRouter>
    </ShellTestProviders>,
  );
}

beforeEach(() => {
  ensureWebStorage();
  useCommandStore.setState({ sources: {} });
  useShortcutStore.getState().setPaletteOpen(false);
  window.localStorage.clear();
});

afterEach(() => {
  cleanup();
  useCommandStore.setState({ sources: {} });
  useShortcutStore.getState().setPaletteOpen(false);
});

describe("the palette on every state of the Console tab", () => {
  it.each([
    [
      "the viewer panel",
      () => (
        <ConsoleViewerPanel
          checkAccess={() => new Promise(() => {})}
          onAllowed={() => {}}
        />
      ),
    ],
    ["the loading screen", () => <LoadingScreen sx={undefined} />],
    [
      "the frame",
      () => <iframe data-testid="omega-embed-frame" title="Omega Console" />,
    ],
  ])("Cmd-K on %s opens the palette", async (_name, content) => {
    renderTab(content());
    // The layout's palette loads in its own chunk.
    await vi.waitFor(
      async () => {
        const { appPaletteOwnsCommandShortcut } = await import(
          "../shortcutOwner"
        );
        expect(appPaletteOwnsCommandShortcut()).toBe(true);
      },
      { timeout: 25000 },
    );
    fireEvent.keyDown(document.body, { key: "k", metaKey: true });
    expect(
      await screen.findByRole("dialog", { name: "Command palette" }),
    ).toBeVisible();
    expect(screen.getAllByRole("dialog")).toHaveLength(1);
  });
});
