/* eslint-disable react/prop-types */
// One palette for the whole product: Cmd-K (Ctrl-K on Windows and Linux)
// opens the same palette on every page, with the same look and keys.
import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import { MemoryRouter, useLocation } from "react-router-dom";
import { ThemeProvider, createTheme } from "@mui/material/styles";
import { palette } from "src/theme/palette";
import { typography } from "src/theme/typography";
import { useShortcutStore } from "src/sections/common/ux/useShortcuts";
import { gatewayRailGroups } from "src/sections/gateway/shell/gateway-rail";
import useGatewayCommands from "src/sections/gateway/hooks/useGatewayCommands";
import Searchbar from "src/layouts/common/searchbar";
import CommandPalette from "../CommandPalette";
import { useCommandStore, useRegisterCommands } from "../commandRegistry";

const falcon = vi.hoisted(() => ({ allowed: true }));

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
  useFeatureAllowed: () => ({ allowed: falcon.allowed }),
}));
vi.mock("src/contexts/OrganizationContext", () => ({
  useOrganization: () => ({
    isReady: true,
    currentOrganizationId: "org-a",
    orgLevel: 8,
  }),
}));
vi.mock("src/components/scrollbar", () => ({
  default: ({ children }) => <div>{children}</div>,
}));
vi.mock("src/layouts/dashboard/config-navigation", () => ({
  useNavData: () => [
    {
      subheader: "OBSERVE",
      items: [{ title: "Tracing", path: "/dashboard/observe" }],
    },
  ],
}));

function LocationProbe() {
  const location = useLocation();
  return (
    <output data-testid="location">
      {location.pathname}
      {location.search}
    </output>
  );
}

function GatewayCommands() {
  useGatewayCommands(gatewayRailGroups({ noManagedModels: true }));
  return null;
}

function PageVerb() {
  useRegisterCommands("dataset-page", [
    { id: "dataset.addRow", label: "Add row", run: () => {} },
  ]);
  return null;
}

function renderAt(path, children = null) {
  return render(
    <ThemeProvider
      theme={createTheme({ palette: palette("light"), typography })}
    >
      <MemoryRouter initialEntries={[path]}>
        <button type="button">Page button</button>
        {children}
        <CommandPalette />
        <LocationProbe />
      </MemoryRouter>
    </ThemeProvider>,
  );
}

const press = (target, init) =>
  fireEvent.keyDown(target, { key: "k", ...init });
const paletteDialog = () =>
  screen.queryByRole("dialog", { name: "Command palette" });
const groupNames = () =>
  Array.from(
    screen.getByRole("listbox").querySelectorAll("[data-palette-group]"),
  ).map((node) => node.getAttribute("data-palette-group"));

beforeEach(() => {
  falcon.allowed = true;
  useCommandStore.setState({ sources: {} });
  useShortcutStore.getState().setPaletteOpen(false);
  window.localStorage.clear();
});

afterEach(() => {
  cleanup();
  useCommandStore.setState({ sources: {} });
  useShortcutStore.getState().setPaletteOpen(false);
});

describe("one palette on every page", () => {
  it.each([
    ["Cmd-K", { metaKey: true }],
    ["Ctrl-K", { ctrlKey: true }],
  ])(
    "%s on a dashboard page opens the palette and Escape returns focus",
    async (_name, keys) => {
      renderAt("/dashboard/develop");
      const button = screen.getByRole("button", { name: "Page button" });
      button.focus();
      press(button, keys);
      const dialog = await screen.findByRole("dialog", {
        name: "Command palette",
      });
      expect(dialog).toBeVisible();
      const input = within(dialog).getByRole("combobox", { name: "Command" });
      await waitFor(() => expect(input).toHaveFocus());
      fireEvent.keyDown(input, { key: "Escape" });
      await waitFor(() => expect(paletteDialog()).not.toBeInTheDocument());
      expect(button).toHaveFocus();
    },
  );

  it("an empty query on a dashboard page shows this page's verbs first, then Go to", async () => {
    renderAt("/dashboard/develop", <PageVerb />);
    press(document.body, { metaKey: true });
    await screen.findByRole("dialog", { name: "Command palette" });
    const groups = groupNames();
    expect(groups[0]).toBe("This page");
    expect(groups.indexOf("Go to")).toBeGreaterThan(0);
    expect(groups.indexOf("Go to")).toBeLessThan(groups.indexOf("Actions"));
    expect(screen.getByRole("option", { name: /Add row/ })).toBeVisible();
    expect(screen.getAllByRole("option").length).toBeGreaterThan(1);
  });

  it("Ctrl-K on a gateway page opens the same palette with gateway's commands first, and they run", async () => {
    renderAt("/dashboard/gateway/logs", <GatewayCommands />);
    press(document.body, { ctrlKey: true });
    const dialog = await screen.findByRole("dialog", {
      name: "Command palette",
    });
    expect(groupNames()[0]).toBe("AI Gateway");
    const first = within(dialog).getAllByRole("option")[0];
    expect(first).toHaveAccessibleName(/^Go to /);
    const input = within(dialog).getByRole("combobox", { name: "Command" });
    fireEvent.change(input, { target: { value: "budgets" } });
    const budgets = screen.getByRole("option", { name: /Go to Budgets/ });
    expect(budgets).toHaveAttribute("aria-selected", "true");
    fireEvent.keyDown(input, { key: "Enter" });
    await waitFor(() =>
      expect(screen.getByTestId("location")).toHaveTextContent(
        "/dashboard/gateway/budgets",
      ),
    );
    fireEvent.change(
      await (async () => {
        press(document.body, { ctrlKey: true });
        const again = await screen.findByRole("dialog", {
          name: "Command palette",
        });
        return within(again).getByRole("combobox", { name: "Command" });
      })(),
      { target: { value: "create key" } },
    );
    expect(
      screen.getByRole("option", { name: /Create personal API key/ }),
    ).toBeVisible();
  });

  it("Cmd-K inside an ordinary text field still opens the palette", async () => {
    renderAt(
      "/dashboard/observe",
      <input aria-label="Filter traces" defaultValue="" />,
    );
    const field = screen.getByRole("textbox", { name: "Filter traces" });
    field.focus();
    press(field, { metaKey: true });
    expect(
      await screen.findByRole("dialog", { name: "Command palette" }),
    ).toBeVisible();
  });
});

describe("the top bar search box", () => {
  it("no longer opens its own dialog on Cmd-K: the one palette opens instead", async () => {
    renderAt("/dashboard/develop", <Searchbar variant="nav" />);
    press(document.body, { metaKey: true });
    expect(
      await screen.findByRole("dialog", { name: "Command palette" }),
    ).toBeVisible();
    expect(screen.queryByPlaceholderText("Search...")).not.toBeInTheDocument();
    expect(screen.getAllByRole("dialog")).toHaveLength(1);
    act(() => useShortcutStore.getState().setPaletteOpen(false));
    await waitFor(() => expect(paletteDialog()).not.toBeInTheDocument());
    expect(screen.queryByPlaceholderText("Search...")).not.toBeInTheDocument();
  });
});

describe("Falcon in the palette", () => {
  const openPalette = async (path, host) => {
    render(
      <ThemeProvider
        theme={createTheme({ palette: palette("light"), typography })}
      >
        <MemoryRouter initialEntries={[path]}>
          <CommandPalette host={host} />
        </MemoryRouter>
      </ThemeProvider>,
    );
    press(document.body, { metaKey: true });
    const dialog = await screen.findByRole("dialog", {
      name: "Command palette",
    });
    return within(dialog).getByRole("combobox", { name: "Command" });
  };

  it("offers Ask Falcon with no Cmd-J cap: nothing binds that key", async () => {
    const input = await openPalette("/dashboard/develop");
    fireEvent.change(input, { target: { value: "ask falcon" } });
    const row = screen.getByRole("option", { name: /Ask Falcon…/ });
    expect(row).not.toHaveTextContent(/J$/);
    fireEvent.change(input, { target: { value: "zzqq nothing" } });
    const fallback = screen.getByRole("option", { name: /Ask Falcon: / });
    expect(fallback.textContent).toBe("Ask Falcon: “zzqq nothing”");
  });

  it("offers neither Ask Falcon row where the deployment does not allow Falcon", async () => {
    falcon.allowed = false;
    const input = await openPalette("/dashboard/develop");
    fireEvent.change(input, { target: { value: "falcon" } });
    expect(
      screen.queryByRole("option", { name: /Ask Falcon/ }),
    ).not.toBeInTheDocument();
    fireEvent.change(input, { target: { value: "zzqq nothing" } });
    expect(
      screen.queryByRole("option", { name: /Ask Falcon/ }),
    ).not.toBeInTheDocument();
    expect(screen.getByText("No matches for “zzqq nothing”")).toBeVisible();
  });

  it("says plainly that nothing matched on the Console tab", async () => {
    const input = await openPalette("/dashboard/console/chat", "console");
    fireEvent.change(input, { target: { value: "zzqq nothing" } });
    expect(screen.getByText("No matches for “zzqq nothing”")).toBeVisible();
    expect(
      screen.queryByText(/Type to search datasets/),
    ).not.toBeInTheDocument();
  });
});
