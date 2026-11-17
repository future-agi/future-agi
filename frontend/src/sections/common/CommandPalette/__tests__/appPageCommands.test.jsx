/* eslint-disable react/prop-types */
// Go to: every page of the left menu a person may open, from every page.
import React from "react";
import PropTypes from "prop-types";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import { MemoryRouter, useLocation, useNavigate } from "react-router-dom";
import { ThemeProvider, createTheme } from "@mui/material/styles";
import { palette } from "src/theme/palette";
import { typography } from "src/theme/typography";
import { useShortcutStore } from "src/sections/common/ux/useShortcuts";
import CommandPalette from "../CommandPalette";
import AppPageCommands, { pageCommandsOf } from "../AppPageCommands";
import { useCommandStore } from "../commandRegistry";

const h = vi.hoisted(() => ({
  role: "Member",
  orgLevel: 1,
  consoleListed: true,
  consoleOn: true,
  // The real rail hook is memoised: one array while its inputs hold.
  gateway: [
    {
      id: "observe",
      subheader: "Observability",
      items: [
        "Overview",
        "Models",
        "Auto routing",
        "Providers",
        "API keys",
        "Fallbacks",
        "Guardrails",
        "Budgets",
        "Webhooks",
        "Sessions",
      ]
        .map((title) => ({
          title,
          path: `/dashboard/gateway/${title.toLowerCase().replace(/\s+/g, "-")}`,
        }))
        .concat([{ title: "Logs", path: "/dashboard/gateway/logs" }]),
    },
  ],
}));

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
vi.mock("src/auth/hooks", () => ({
  useAuthContext: () => ({ user: { id: "u1", role: h.role } }),
}));
vi.mock("src/contexts/OrganizationContext", () => ({
  useOrganization: () => ({
    isReady: true,
    currentOrganizationId: "org-a",
    orgLevel: h.orgLevel,
  }),
}));
vi.mock("src/sections/omega/shell/flags", () => ({
  isOmegaConsoleEnabled: () => h.consoleOn,
}));
vi.mock("src/sections/omega/shell/useOmegaProducts", () => ({
  useOmegaProducts: () => ({
    listed: (product) => product === "console" && h.consoleListed,
  }),
}));
vi.mock("src/layouts/dashboard/config-navigation", () => ({
  useNavData: () => [
    {
      items: [{ title: "Chat", path: "/dashboard/falcon-ai" }],
    },
    {
      subheader: "BUILD",
      items: [
        { title: "Datasets", path: "/dashboard/develop" },
        { title: "Evals", path: "/dashboard/evals" },
        { title: "Owners only", path: "/dashboard/owners", roles: ["Owner"] },
        { title: "Switched off", path: "/dashboard/off", disabled: true },
      ],
    },
    {
      subheader: "Agent infrastructure",
      items: [{ title: "Sandboxes", inert: true }],
    },
    {
      subheader: "OBSERVE",
      items: [{ title: "Tracing", path: "/dashboard/observe" }],
    },
  ],
  useNavDashBoardData: () => [
    { items: [{ title: "Get started", path: "/dashboard/get-started" }] },
  ],
  useNavUpgradeData: () => [
    {
      subheader: "RESOURCES",
      items: [{ title: "Docs", path: "https://docs.futureagi.com" }],
    },
  ],
  useNavSettingsData: () => [
    {
      items: [
        { title: "Profile", path: "/dashboard/settings/profile-settings" },
      ],
    },
    {
      subheader: "Organization",
      items: [
        { title: "Members", path: "/dashboard/settings/user-management" },
      ],
    },
    {
      subheader: "Your Workspaces",
      items: [{ title: "Acme", path: "/dashboard/settings/workspace/ws-1" }],
    },
  ],
  useNavGatewayData: () => h.gateway,
}));

function LocationProbe() {
  return <output data-testid="location">{useLocation().pathname}</output>;
}

function GoButton({ to }) {
  const navigate = useNavigate();
  return (
    <button type="button" onClick={() => navigate(to)}>
      {`Open ${to}`}
    </button>
  );
}
GoButton.propTypes = { to: PropTypes.string.isRequired };

const tree = (path) => (
  <ThemeProvider theme={createTheme({ palette: palette("light"), typography })}>
    <MemoryRouter initialEntries={[path]}>
      <AppPageCommands />
      <CommandPalette />
      <LocationProbe />
      <GoButton to="/dashboard/evals" />
      <GoButton to="/dashboard/develop" />
    </MemoryRouter>
  </ThemeProvider>
);

function renderAt(path) {
  return render(tree(path));
}

const goToLabels = () =>
  Array.from(
    screen
      .getByRole("listbox")
      .querySelectorAll('[data-palette-group="Go to"] [role="option"]'),
  ).map((node) => node.getAttribute("data-command"));

async function openAndType(query) {
  fireEvent.keyDown(document.body, { key: "k", metaKey: true });
  const dialog = await screen.findByRole("dialog", {
    name: "Command palette",
  });
  const input = within(dialog).getByRole("combobox", { name: "Command" });
  fireEvent.change(input, { target: { value: query } });
  return input;
}

beforeEach(() => {
  h.role = "Member";
  h.orgLevel = 1;
  h.consoleListed = true;
  h.consoleOn = true;
  useCommandStore.setState({ sources: {} });
  useShortcutStore.getState().setPaletteOpen(false);
  window.localStorage.clear();
});

afterEach(() => {
  cleanup();
  useCommandStore.setState({ sources: {} });
  useShortcutStore.getState().setPaletteOpen(false);
});

describe("Go to every page a person may open", () => {
  it("lists a menu page from any page and Enter opens it", async () => {
    renderAt("/dashboard/gateway/logs");
    const input = await openAndType("tracing");
    const option = screen.getByRole("option", { name: /Go to Tracing/ });
    expect(option).toHaveAttribute("aria-selected", "true");
    fireEvent.keyDown(input, { key: "Enter" });
    await waitFor(() =>
      expect(screen.getByTestId("location")).toHaveTextContent(
        "/dashboard/observe",
      ),
    );
  });

  it("finds settings pages and gateway pages from a dashboard page", async () => {
    renderAt("/dashboard/develop");
    await openAndType("members");
    expect(screen.getByRole("option", { name: /Go to Members/ })).toBeVisible();
    fireEvent.change(screen.getByRole("combobox", { name: "Command" }), {
      target: { value: "logs" },
    });
    expect(screen.getByRole("option", { name: /Go to Logs/ })).toBeVisible();
  });

  it("finds a page by its address, as the top bar search did", async () => {
    renderAt("/dashboard/develop");
    await openAndType("user-management");
    expect(screen.getByRole("option", { name: /Go to Members/ })).toBeVisible();
  });

  it("does not list a page the role may not open, a withheld row, a switched-off row or an outside link", async () => {
    renderAt("/dashboard/develop");
    const input = await openAndType("owners");
    expect(
      screen.queryByRole("option", { name: /Owners only/ }),
    ).not.toBeInTheDocument();
    for (const hidden of ["Switched off", "Sandboxes", "Docs"]) {
      fireEvent.change(input, { target: { value: hidden } });
      expect(
        screen.queryByRole("option", { name: new RegExp(`Go to ${hidden}`) }),
      ).not.toBeInTheDocument();
    }
  });

  it("lists the owners-only page for an owner", async () => {
    h.role = "Owner";
    renderAt("/dashboard/develop");
    await openAndType("owners");
    expect(
      screen.getByRole("option", { name: /Go to Owners only/ }),
    ).toBeVisible();
  });

  it("lists the Console's pages only while the Console is offered", async () => {
    renderAt("/dashboard/develop");
    await openAndType("console");
    expect(
      screen.getByRole("option", { name: /Go to Console chat/ }),
    ).toBeVisible();
    cleanup();
    h.consoleListed = false;
    useShortcutStore.getState().setPaletteOpen(false);
    renderAt("/dashboard/develop");
    await openAndType("console");
    expect(
      screen.queryByRole("option", { name: /Go to Console chat/ }),
    ).not.toBeInTheDocument();
  });

  it("shows each destination once", async () => {
    renderAt("/dashboard/develop");
    await openAndType("evals");
    expect(screen.getAllByRole("option", { name: /Go to Evals/ })).toHaveLength(
      1,
    );
  });
});

describe("pageCommandsOf", () => {
  it("keeps only rows the menu would show as links for this role", () => {
    const sections = [
      {
        subheader: "BUILD",
        items: [
          { title: "Datasets", path: "/dashboard/develop" },
          { title: "Owners only", path: "/x", roles: ["Owner"] },
          { title: "Withheld", inert: true },
          { title: "Off", path: "/off", disabled: true },
          { title: "Docs", path: "https://docs.futureagi.com" },
        ],
      },
    ];
    const commands = pageCommandsOf(sections, { role: "Member" });
    expect(commands.map((c) => c.label)).toEqual(["Go to Datasets"]);
    expect(commands[0]).toMatchObject({
      group: "Go to",
      href: "/dashboard/develop",
      hint: "Build",
    });
  });
});

describe("Go to order on an empty query", () => {
  it("leads with Future AGI pages after re-renders and route changes", async () => {
    const view = renderAt("/dashboard/develop");
    await waitFor(() =>
      expect(useCommandStore.getState().sources).toHaveProperty("app-pages"),
    );
    const before = Object.keys(useCommandStore.getState().sources);
    for (let i = 0; i < 3; i += 1) view.rerender(tree("/dashboard/develop"));
    fireEvent.click(
      screen.getByRole("button", { name: "Open /dashboard/evals" }),
    );
    fireEvent.click(
      screen.getByRole("button", { name: "Open /dashboard/develop" }),
    );
    // The page source keeps its place in the registry.
    expect(Object.keys(useCommandStore.getState().sources)).toEqual(before);
    fireEvent.keyDown(document.body, { key: "k", metaKey: true });
    await screen.findByRole("dialog", { name: "Command palette" });
    const rows = goToLabels();
    expect(rows[0]).toMatch(/^go\.page:/);
    expect(rows).toContain("go.page:/dashboard/develop");
    expect(rows).toContain("go.page:/dashboard/observe");
    // Future AGI pages, then Settings, then the other areas.
    const firstGateway = rows.findIndex((id) => id.startsWith("go.gateway:"));
    const lastProduct = rows.reduce(
      (last, id, i) => (id.startsWith("go.page:") ? i : last),
      -1,
    );
    if (firstGateway !== -1) expect(firstGateway).toBeGreaterThan(lastProduct);
  });

  it("puts the current area's pages first whatever order the sources registered in", async () => {
    useCommandStore.getState().register(
      "registered-first",
      Array.from({ length: 10 }, (_, i) => ({
        id: `go.gateway:/dashboard/gateway/early-${i}`,
        group: "Go to",
        label: `Go to Early ${i}`,
        hint: "AI Gateway",
        href: `/dashboard/gateway/early-${i}`,
      })),
    );
    renderAt("/dashboard/develop");
    await waitFor(() =>
      expect(useCommandStore.getState().sources).toHaveProperty("app-pages"),
    );
    fireEvent.keyDown(document.body, { key: "k", metaKey: true });
    await screen.findByRole("dialog", { name: "Command palette" });
    const rows = goToLabels();
    expect(rows.slice(0, 3).every((id) => id.startsWith("go.page:"))).toBe(
      true,
    );
  });
});

describe("Recent lists only what a live source still registers", () => {
  it("drops an owner-only page from Recent once the role becomes Member", async () => {
    h.role = "Owner";
    const view = renderAt("/dashboard/develop");
    const input = await openAndType("owners");
    expect(
      screen.getByRole("option", { name: /Go to Owners only/ }),
    ).toHaveAttribute("aria-selected", "true");
    fireEvent.keyDown(input, { key: "Enter" });
    await waitFor(() =>
      expect(screen.getByTestId("location")).toHaveTextContent(
        "/dashboard/owners",
      ),
    );
    await waitFor(() =>
      expect(
        screen.queryByRole("dialog", { name: "Command palette" }),
      ).not.toBeInTheDocument(),
    );
    h.role = "Member";
    view.rerender(tree("/dashboard/develop"));
    fireEvent.keyDown(document.body, { key: "k", metaKey: true });
    await screen.findByRole("dialog", { name: "Command palette" });
    expect(
      screen.queryByRole("option", { name: /Go to Owners only/ }),
    ).not.toBeInTheDocument();
  });
});

describe("workspace and Console rows", () => {
  it("labels a workspace's row by what it opens: that workspace's settings", async () => {
    renderAt("/dashboard/develop");
    await openAndType("acme");
    expect(
      screen.getByRole("option", { name: /Open Acme settings/ }),
    ).toBeVisible();
    expect(
      screen.queryByRole("option", { name: /Go to Acme/ }),
    ).not.toBeInTheDocument();
  });

  it("gives a Console viewer the switcher's hint on the Console links, and keeps the rows", async () => {
    h.orgLevel = 1;
    renderAt("/dashboard/develop");
    await openAndType("console");
    expect(
      screen.getByRole("option", { name: /Go to Omega Console/ }),
    ).toHaveTextContent("Ask an admin for access");
    cleanup();
    useShortcutStore.getState().setPaletteOpen(false);
    h.orgLevel = 8;
    renderAt("/dashboard/develop");
    await openAndType("console");
    expect(
      screen.getByRole("option", { name: /Go to Omega Console/ }),
    ).not.toHaveTextContent("Ask an admin for access");
  });
});
