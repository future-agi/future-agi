import React from "react";
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";
import FalconAIFab from "../FalconAIFab";
import useFalconStore from "../../store/useFalconStore";
import {
  appPaletteOwnsCommandShortcut,
  registerPaletteHost,
} from "src/sections/common/CommandPalette/shortcutOwner";
import { useShortcutStore } from "src/sections/common/ux/useShortcuts";

const capability = { allowed: false, isLoading: false, isError: false };
vi.mock("src/hooks/useCapabilities", () => ({
  useFeatureAllowed: () => capability,
}));

vi.mock("src/components/tooltip", () => ({
  default: ({ children }) => children,
}));

function Location() {
  return <p data-testid="location">{useLocation().pathname}</p>;
}

function renderFab(path = "/dashboard/gateway/overview") {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <FalconAIFab />
      <Routes>
        <Route path="*" element={<Location />} />
      </Routes>
    </MemoryRouter>,
  );
}

// Sent through fireEvent so React applies what the key starts (a route
// change) before the test reads the page.
function pressCmdK(init = { metaKey: true }) {
  const event = new KeyboardEvent("keydown", {
    key: "k",
    ...init,
    bubbles: true,
    cancelable: true,
  });
  fireEvent(window, event);
  return event;
}

const button = () => screen.queryByRole("button", { name: "Open Falcon AI" });
const cleanups = [];

describe("FalconAIFab (the Falcon AI Cmd-K launcher)", () => {
  beforeEach(() => {
    capability.allowed = false;
    capability.isLoading = false;
    capability.isError = false;
    useFalconStore.setState({ isSidebarOpen: false });
    useShortcutStore.getState().setPaletteOpen(false);
  });
  afterEach(() => {
    cleanups.splice(0).forEach((off) => off());
    useShortcutStore.getState().setPaletteOpen(false);
  });

  it("is shown when falcon_ai is not allowed; a click opens the Falcon AI page", () => {
    renderFab();
    expect(button()).toBeInTheDocument();
    fireEvent.click(button());
    expect(useFalconStore.getState().isSidebarOpen).toBe(false);
    expect(screen.getByTestId("location")).toHaveTextContent(
      "/dashboard/falcon-ai",
    );
  });

  it.each([
    ["Cmd+K", { metaKey: true }],
    ["Ctrl+K", { ctrlKey: true }],
  ])("%s opens the Falcon AI page when falcon_ai is not allowed", (_, init) => {
    renderFab();
    const event = pressCmdK(init);
    expect(event.defaultPrevented).toBe(true);
    expect(useFalconStore.getState().isSidebarOpen).toBe(false);
    expect(screen.getByTestId("location")).toHaveTextContent(
      "/dashboard/falcon-ai",
    );
  });

  it("is shown while capabilities load or fail, and opens the page", () => {
    capability.isLoading = true;
    const { unmount } = renderFab();
    expect(button()).toBeInTheDocument();
    unmount();
    capability.isLoading = false;
    capability.isError = true;
    renderFab();
    expect(button()).toBeInTheDocument();
    fireEvent.click(button());
    expect(screen.getByTestId("location")).toHaveTextContent(
      "/dashboard/falcon-ai",
    );
  });

  it("shows the launcher when falcon_ai is allowed, and Cmd+K opens the sidebar", () => {
    capability.allowed = true;
    renderFab();
    expect(
      screen.getByRole("button", { name: "Open Falcon AI" }),
    ).toBeInTheDocument();
    const event = pressCmdK();
    expect(event.defaultPrevented).toBe(true);
    expect(useFalconStore.getState().isSidebarOpen).toBe(true);
  });

  it("opens the sidebar on click when allowed", () => {
    capability.allowed = true;
    renderFab();
    fireEvent.click(screen.getByRole("button", { name: "Open Falcon AI" }));
    expect(useFalconStore.getState().isSidebarOpen).toBe(true);
  });

  it("names its shortcut", () => {
    renderFab();
    expect(button()).toHaveAttribute("aria-keyshortcuts", "Meta+K Control+K");
  });

  it("keeps Cmd/Ctrl+K where a command palette is mounted too", () => {
    capability.allowed = true;
    cleanups.push(registerPaletteHost("dashboard"));
    renderFab();
    expect(appPaletteOwnsCommandShortcut()).toBe(false);
    const event = pressCmdK({ ctrlKey: true });
    expect(event.defaultPrevented).toBe(true);
    expect(useFalconStore.getState().isSidebarOpen).toBe(true);
  });

  // The command palette is open over the page: the key never toggles a
  // sidebar hidden behind it. It closes the palette and brings the
  // assistant up in its place; a sidebar already open stays open.
  it.each([
    ["Cmd+K", { metaKey: true }],
    ["Ctrl+K", { ctrlKey: true }],
  ])("%s with the palette open closes it and opens the sidebar", (_, init) => {
    capability.allowed = true;
    renderFab();
    useShortcutStore.getState().setPaletteOpen(true);
    const event = pressCmdK(init);
    expect(event.defaultPrevented).toBe(true);
    expect(useShortcutStore.getState().paletteOpen).toBe(false);
    expect(useFalconStore.getState().isSidebarOpen).toBe(true);
  });

  it("with the palette open over an open sidebar, Cmd+K closes the palette and leaves the sidebar open", () => {
    capability.allowed = true;
    useFalconStore.setState({ isSidebarOpen: true });
    renderFab();
    useShortcutStore.getState().setPaletteOpen(true);
    pressCmdK();
    expect(useShortcutStore.getState().paletteOpen).toBe(false);
    expect(useFalconStore.getState().isSidebarOpen).toBe(true);
  });

  it("with the palette open where falcon_ai is not allowed, Ctrl+K closes it and opens the Falcon AI page", () => {
    renderFab();
    useShortcutStore.getState().setPaletteOpen(true);
    pressCmdK({ ctrlKey: true });
    expect(useShortcutStore.getState().paletteOpen).toBe(false);
    expect(useFalconStore.getState().isSidebarOpen).toBe(false);
    expect(screen.getByTestId("location")).toHaveTextContent(
      "/dashboard/falcon-ai",
    );
  });

  it("leaves Cmd/Ctrl+Shift+P to the palette", () => {
    capability.allowed = true;
    renderFab();
    const event = pressCmdK({ metaKey: true, shiftKey: true, key: "P" });
    expect(event.defaultPrevented).toBe(false);
    expect(useFalconStore.getState().isSidebarOpen).toBe(false);
  });

  it("is hidden on the Falcon AI page and while the sidebar is open", () => {
    const { unmount } = renderFab("/dashboard/falcon-ai");
    expect(button()).toBeNull();
    unmount();
    capability.allowed = true;
    useFalconStore.setState({ isSidebarOpen: true });
    renderFab();
    expect(button()).toBeNull();
  });
});
