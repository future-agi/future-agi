/* eslint-disable react/prop-types */
// The Console tab: the same palette opens there, and the embedded Console
// can ask for it and hand it its own commands over the embed's channel.
import React, { useRef } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { ThemeProvider, createTheme } from "@mui/material/styles";
import { palette } from "src/theme/palette";
import { typography } from "src/theme/typography";
import { useShortcutStore } from "src/sections/common/ux/useShortcuts";
import { useCommandStore } from "../commandRegistry";
import ConsolePaletteHost from "../ConsolePaletteHost";
import {
  PALETTE_MESSAGE,
  parseConsoleCommands,
  useConsolePaletteBridge,
} from "../consoleBridge";

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
vi.mock("src/hooks/useCapabilities", () => ({
  useFeatureAllowed: () => ({ allowed: true }),
}));

const ORIGIN = "https://console.example.test";

// The frame keeps the bridge; the route's layout mounts the palette once.
function ConsoleFrameStandIn({ enabled }) {
  const ref = useRef(null);
  useConsolePaletteBridge({
    containerRef: ref,
    expectedOrigin: ORIGIN,
    enabled,
  });
  return (
    <div ref={ref}>
      <iframe data-testid="omega-embed-frame" title="Omega Console" />
    </div>
  );
}

function ConsoleTab({ enabled = true }) {
  return (
    <>
      <ConsoleFrameStandIn enabled={enabled} />
      <ConsolePaletteHost />
    </>
  );
}

function renderTab(props) {
  return render(
    <ThemeProvider
      theme={createTheme({ palette: palette("light"), typography })}
    >
      <MemoryRouter initialEntries={["/dashboard/console/chat"]}>
        <ConsoleTab {...props} />
      </MemoryRouter>
    </ThemeProvider>,
  );
}

const frameWindow = () => screen.getByTestId("omega-embed-frame").contentWindow;

function post(data, { origin = ORIGIN, source = frameWindow() } = {}) {
  act(() => {
    window.dispatchEvent(new MessageEvent("message", { data, origin, source }));
  });
}

const COMMANDS = {
  type: "embed.palette.commands",
  commands: [
    {
      id: "chat.new",
      title: "Start a new chat",
      group: "Chat",
      keywords: ["conversation"],
    },
    { id: "files.open", title: "Open files", keywords: ["sandbox"] },
  ],
};

const paletteDialog = () =>
  screen.queryByRole("dialog", { name: "Command palette" });

beforeEach(() => {
  useCommandStore.setState({ sources: {} });
  useShortcutStore.getState().setPaletteOpen(false);
  window.localStorage.clear();
});

afterEach(() => {
  cleanup();
  useCommandStore.setState({ sources: {} });
  useShortcutStore.getState().setPaletteOpen(false);
});

describe("the palette on the Console tab", () => {
  it("Cmd-K on the Console tab opens the same palette", async () => {
    renderTab();
    fireEvent.keyDown(document.body, { key: "k", metaKey: true });
    expect(
      await screen.findByRole("dialog", { name: "Command palette" }),
    ).toBeVisible();
  });

  it("lists links into the Console's pages until the Console sends commands", async () => {
    renderTab();
    act(() => useShortcutStore.getState().setPaletteOpen(true));
    await screen.findByRole("dialog", { name: "Command palette" });
    expect(screen.getByText("Omega Console")).toBeVisible();
    expect(
      screen.getByRole("option", { name: /Go to Console chat/ }),
    ).toBeVisible();
  });

  it("opens when the Console asks, from the Console's frame and origin only", async () => {
    renderTab();
    post({ type: "embed.palette.open" }, { origin: "https://evil.example" });
    post({ type: "embed.palette.open" }, { source: window });
    expect(paletteDialog()).not.toBeInTheDocument();
    post({ type: PALETTE_MESSAGE.open });
    expect(
      await screen.findByRole("dialog", { name: "Command palette" }),
    ).toBeVisible();
  });

  it("ignores the Console until it is connected", async () => {
    renderTab({ enabled: false });
    post({ type: "embed.palette.open" });
    post(COMMANDS);
    await new Promise((resolve) => setTimeout(resolve, 0));
    expect(paletteDialog()).not.toBeInTheDocument();
    expect(useCommandStore.getState().sources).not.toHaveProperty(
      "omega-console",
    );
  });

  it("accepts commands only from the Console's origin, lists them first and asks the Console to run the one picked", async () => {
    renderTab();
    post(
      {
        ...COMMANDS,
        commands: [{ id: "steal", title: "Sent from elsewhere" }],
      },
      { origin: "https://evil.example" },
    );
    post(COMMANDS);
    const spy = vi.spyOn(frameWindow(), "postMessage");
    act(() => useShortcutStore.getState().setPaletteOpen(true));
    await screen.findByRole("dialog", { name: "Command palette" });
    expect(
      screen.queryByRole("option", { name: /Sent from elsewhere/ }),
    ).not.toBeInTheDocument();
    const [first] = screen.getAllByRole("option");
    expect(first).toHaveAccessibleName(/Start a new chat/);
    expect(
      screen.queryByRole("option", { name: /Go to Console chat/ }),
    ).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("option", { name: /Open files/ }));
    await waitFor(() =>
      expect(spy).toHaveBeenCalledWith(
        { type: "embed.palette.run", id: "files.open" },
        ORIGIN,
      ),
    );
    await waitFor(() => expect(paletteDialog()).not.toBeInTheDocument());
  });

  it("drops the Console's commands when the tab goes away", async () => {
    const { unmount } = renderTab();
    post(COMMANDS);
    expect(
      Object.keys(useCommandStore.getState().sources).some((id) =>
        id.startsWith("omega-console"),
      ),
    ).toBe(true);
    unmount();
    expect(
      Object.keys(useCommandStore.getState().sources).some((id) =>
        id.startsWith("omega-console"),
      ),
    ).toBe(false);
  });
});

describe("parseConsoleCommands", () => {
  it("keeps well-formed commands and refuses a malformed list whole", () => {
    expect(parseConsoleCommands(COMMANDS.commands)).toEqual([
      {
        id: "chat.new",
        title: "Start a new chat",
        group: "Chat",
        keywords: ["conversation"],
      },
      { id: "files.open", title: "Open files", keywords: ["sandbox"] },
    ]);
    expect(parseConsoleCommands([{ id: "a b", title: "Bad id" }])).toBeNull();
    expect(
      parseConsoleCommands([{ id: "ok", title: "\u0007bell" }]),
    ).toBeNull();
    expect(parseConsoleCommands("nope")).toBeNull();
    expect(
      parseConsoleCommands(
        Array.from({ length: 101 }, (_, i) => ({ id: `c${i}`, title: "x" })),
      ),
    ).toBeNull();
  });
});
