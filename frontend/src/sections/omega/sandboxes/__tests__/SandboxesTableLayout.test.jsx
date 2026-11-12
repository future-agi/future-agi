import React from "react";
import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import { ThemeProvider, createTheme } from "@mui/material/styles";
import useMediaQuery from "@mui/material/useMediaQuery";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import { palette } from "src/theme/palette";
import { typography } from "src/theme/typography";

import { formatExactTime } from "../lib/format";
import SandboxesTable from "../list/SandboxesTable";
import {
  CAPS,
  PROVIDER,
  fakeApi,
  renderSandboxes,
  sandboxView,
} from "./helpers";
import { session, workspace } from "./session-mock";

vi.mock("src/contexts/WorkspaceContext", () => ({
  useWorkspace: () => workspace,
}));
vi.mock("src/sections/omega/shared", async (importOriginal) => ({
  ...(await importOriginal()),
  useOmegaSession: () => session,
}));
vi.mock("@mui/material/useMediaQuery", () => ({ default: vi.fn() }));

const { default: SandboxesListPage } = await import("../SandboxesListPage");

// Answer the list's media query the way a browser this wide would.
function viewport(width) {
  return (query) => {
    const max = /max-width:\s*([\d.]+)px/.exec(String(query));
    const min = /min-width:\s*([\d.]+)px/.exec(String(query));
    return (
      (!max || width <= Number(max[1])) && (!min || width >= Number(min[1]))
    );
  };
}

const theme = createTheme({ palette: palette("light"), typography });

function renderAt(width, sandboxes) {
  useMediaQuery.mockImplementation(viewport(width));
  return renderSandboxes(
    <ThemeProvider theme={theme}>
      <SandboxesTable sandboxes={sandboxes} onSort={vi.fn()} />
    </ThemeProvider>,
  );
}

afterEach(() => {
  useMediaQuery.mockReset();
  vi.restoreAllMocks();
});

// The list's own width, as a browser lays it out (jsdom measures 0).
function listIs(width) {
  vi.spyOn(HTMLElement.prototype, "getBoundingClientRect").mockReturnValue({
    width,
    height: 600,
    top: 0,
    left: 0,
    right: width,
    bottom: 600,
    x: 0,
    y: 0,
    toJSON: () => ({}),
  });
}

// Screen pass (owner: "items are getting cut"): at 1024px the seven-column
// table cut "Needs recovery" into the next column and left the image about
// eight characters.
describe("Sandboxes list at desktop widths", () => {
  it("shows each sandbox as a card at 1024px, with every value the table shows", async () => {
    const image = "registry.example.internal/team/python-data:2026.11.10";
    renderAt(1024, [sandboxView({ state: "recovery_required", image })]);
    const row = await screen.findByTestId("sandbox-row");
    expect(row.tagName).toBe("BUTTON");
    expect(row).toHaveTextContent("Needs recovery");
    expect(row).toHaveTextContent(image);
    expect(row).toHaveTextContent(/CPU · /);
    expect(row).toHaveTextContent("active ");
    expect(row).toHaveTextContent("created 1 h ago");
  });

  it("gives a card its own Copy id, outside the card's button", async () => {
    renderAt(1024, [sandboxView()]);
    const row = await screen.findByTestId("sandbox-row");
    const copy = screen.getByRole("button", { name: "Copy sandbox id" });
    expect(copy).toHaveTextContent("Copy id");
    expect(row.contains(copy)).toBe(false);
  });

  it("names when an active sandbox hibernates on its card", async () => {
    renderAt(1024, [sandboxView()]);
    expect(await screen.findByTestId("sandbox-row")).toHaveTextContent(
      "hibernates in",
    );
  });

  it.each([
    [1199, "BUTTON"],
    [1200, "TR"],
    [1280, "TR"],
  ])("at %ipx shows each sandbox as a %s", async (width, tag) => {
    renderAt(width, [sandboxView()]);
    expect((await screen.findByTestId("sandbox-row")).tagName).toBe(tag);
  });

  it("shows cards in a wide window when a side panel leaves the list too narrow for the table", async () => {
    listIs(788);
    renderAt(1440, [sandboxView()]);
    expect((await screen.findByTestId("sandbox-row")).tagName).toBe("BUTTON");
  });

  it("keeps the table when the list itself is wide enough", async () => {
    listIs(1208);
    renderAt(1440, [sandboxView()]);
    expect((await screen.findByTestId("sandbox-row")).tagName).toBe("TR");
  });

  it("gives the state chip room for its longest label, and the image the rest with its runtime beneath", async () => {
    renderAt(1280, [sandboxView({ state: "recovery_required" })]);
    const row = await screen.findByTestId("sandbox-row");
    const header = (name) => screen.getByRole("columnheader", { name });
    expect(header("State")).toHaveStyle({ width: "148px" });
    expect(header("CPU · Memory")).toHaveStyle({ width: "124px" });
    expect(header("Last activity")).toHaveStyle({ width: "112px" });
    expect(header("Created")).toHaveStyle({ width: "104px" });
    expect(header("Hibernates")).toHaveStyle({ width: "116px" });
    expect(header("Image · Runtime")).toBeInTheDocument();
    expect(within(row).getByTestId("sandbox-runtime")).toHaveTextContent(
      "Container",
    );
  });

  it("lets a longer size or time wrap in its own column and names a cut name in full", async () => {
    const name = "customer-onboarding-data-pipeline-nightly-backfill-eu-west";
    renderAt(1280, [sandboxView({ name })]);
    await screen.findByTestId("sandbox-row");
    expect(screen.getByTestId("sandbox-limits")).not.toHaveStyle({
      whiteSpace: "nowrap",
    });
    expect(screen.getByTestId("sandbox-hibernates")).not.toHaveStyle({
      whiteSpace: "nowrap",
    });
    expect(screen.getByTitle(name)).toHaveTextContent(name);
  });

  it("puts a table row's times in the tab order, with the DS focus ring", async () => {
    renderAt(1280, [sandboxView()]);
    const created = await screen.findByTestId("sandbox-created");
    expect(created).toHaveAttribute("tabindex", "0");
    // The emitted :focus-visible rule (jsdom draws no outline itself).
    const focus = [...created.classList].map((c) => `.${c}:focus-visible`);
    let outline = null;
    for (const sheet of Array.from(document.styleSheets)) {
      for (const rule of Array.from(sheet.cssRules)) {
        const names = String(rule.selectorText).split(",");
        if (names.some((name) => focus.includes(name.trim()))) {
          outline = rule.style.getPropertyValue("outline") || outline;
        }
      }
    }
    expect(outline?.toLowerCase()).toBe(
      `2px solid ${theme.palette.border.focus}`.toLowerCase(),
    );
  });

  // A card is one button: a focusable time inside it would nest controls.
  it("keeps a card's times out of the tab order", async () => {
    renderAt(1024, [sandboxView()]);
    const card = await screen.findByTestId("sandbox-row");
    expect(card.querySelectorAll("time").length).toBeGreaterThan(0);
    expect(card.querySelector("time[tabindex]")).toBeNull();
  });

  // The card's own times answer hover only, so the card's button carries the
  // exact times for a keyboard and a screen reader.
  it("gives a card's button its exact times with the time zone, and shows them on keyboard focus", async () => {
    const user = userEvent.setup();
    const sandbox = sandboxView();
    renderAt(1024, [sandbox]);
    const card = await screen.findByTestId("sandbox-row");
    const exact = [
      `Last active ${formatExactTime(sandbox.lastActivityAt)}`,
      `Created ${formatExactTime(sandbox.createdAt)}`,
      `Hibernates ${formatExactTime(sandbox.hibernatesAt)}`,
    ];
    expect(exact[1]).toMatch(/(UTC|GMT|[A-Z]{2,5})[+\-:\d]*$/);
    expect(card).toHaveAccessibleDescription(exact.join("; "));
    expect(card).toHaveAccessibleName(/^data-cleanup/);
    await user.tab();
    expect(card).toHaveFocus();
    const tip = await screen.findByRole("tooltip");
    for (const line of exact) expect(tip).toHaveTextContent(line);
  });

  it("leaves Hibernates out of a hibernated card's exact times, as the card does", async () => {
    const sandbox = sandboxView({ state: "hibernated" });
    renderAt(1024, [sandbox]);
    const card = await screen.findByTestId("sandbox-row");
    expect(card).toHaveAccessibleDescription(
      `Last active ${formatExactTime(sandbox.lastActivityAt)}; Created ${formatExactTime(sandbox.createdAt)}`,
    );
  });
});

// Review of the list pass: at 1024px the cards had no sort at all, since only
// the table's column headings sorted (list-filtered-sorted--1024 kept the
// API's order).
describe("Sandboxes list page at 1024px", () => {
  const MIN = 60_000;
  const ago = (ms) => new Date(Date.now() - ms).toISOString();
  // Listed in this order by the API: neither by name nor by any time.
  const fleet = () => [
    sandboxView({
      id: "sbx_aaaaaaaaaaaaaaaaaaaaaaaa",
      name: "beta-api",
      createdAt: ago(180 * MIN),
      lastActivityAt: ago(10 * MIN),
    }),
    sandboxView({
      id: "sbx_bbbbbbbbbbbbbbbbbbbbbbbb",
      name: "alpha-worker",
      state: "hibernated",
      createdAt: ago(60 * MIN),
      lastActivityAt: ago(120 * MIN),
    }),
    sandboxView({
      id: "sbx_cccccccccccccccccccccccc",
      name: "gamma-browser",
      createdAt: ago(2880 * MIN),
      lastActivityAt: ago(MIN),
    }),
  ];
  function renderPageAt(width) {
    useMediaQuery.mockImplementation(viewport(width));
    const list = vi.fn(async () => ({
      sandboxes: fleet(),
      caps: CAPS,
      provider: PROVIDER,
    }));
    return renderSandboxes(
      <ThemeProvider theme={theme}>
        <SandboxesListPage />
      </ThemeProvider>,
      { api: fakeApi({ list }) },
    );
  }
  // Each card as the letter its id repeats: "a" for sbx_aaaa….
  const letter = (row) => row.getAttribute("data-sandbox-id").slice(4, 5);
  const order = () => screen.getAllByTestId("sandbox-row").map(letter);
  async function sortBy(label) {
    fireEvent.mouseDown(screen.getByRole("combobox", { name: /Sort by/ }));
    fireEvent.click(await screen.findByRole("option", { name: label }));
    await waitFor(() => expect(screen.queryByRole("listbox")).toBeNull());
  }

  it("sorts the cards from Sort by in the toolbar, by any column and either way", async () => {
    renderPageAt(1024);
    const [first] = await screen.findAllByTestId("sandbox-row");
    expect(first.tagName).toBe("BUTTON");
    expect(order()).toEqual(["a", "b", "c"]);
    await sortBy("Name, A to Z");
    expect(order()).toEqual(["b", "a", "c"]);
    await sortBy("Last activity, newest first");
    expect(order()).toEqual(["c", "a", "b"]);
    await sortBy("Created, oldest first");
    expect(order()).toEqual(["c", "a", "b"]);
    await sortBy("Created, newest first");
    expect(order()).toEqual(["b", "a", "c"]);
    await sortBy("State, Running first");
    expect(order()).toEqual(["a", "c", "b"]);
    await sortBy("Default order");
    expect(order()).toEqual(["a", "b", "c"]);
  });

  it("keeps a sort chosen on the cards through a search that matches nothing", async () => {
    renderPageAt(1024);
    await screen.findAllByTestId("sandbox-row");
    await sortBy("Name, Z to A");
    expect(order()).toEqual(["c", "a", "b"]);
    fireEvent.change(screen.getByTestId("sandboxes-search"), {
      target: { value: "no-such-sandbox" },
    });
    const empty = screen.getByTestId("sandboxes-no-match");
    const menu = screen.getByRole("combobox", { name: /Sort by/ });
    expect(menu).toHaveTextContent("Name, Z to A");
    fireEvent.click(
      within(empty).getByRole("button", { name: "Clear filters" }),
    );
    expect(order()).toEqual(["c", "a", "b"]);
  });

  // A toolbar select reads its value inline, 32px tall like the search box
  // beside it; a label above it would make it taller than the row.
  it("shows Sort by as a toolbar select with its value inline and no label above it", async () => {
    renderPageAt(1024);
    await screen.findAllByTestId("sandbox-row");
    const toolbar = screen.getByTestId("sandboxes-toolbar");
    const menu = within(toolbar).getByRole("combobox", { name: "Sort by" });
    expect(menu).toHaveTextContent(/^Sort: Default order$/);
    expect(toolbar.querySelector("label")).toBeNull();
    // Named by its aria-label alone, never by its own value.
    expect(menu).not.toHaveAttribute("aria-labelledby");
    // Room for the longest choice, "Sort: Last activity, newest first".
    expect(menu.closest(".MuiFormControl-root")).toHaveStyle({
      width: "260px",
    });
    await sortBy("Last activity, newest first");
    expect(menu).toHaveTextContent(/^Sort: Last activity, newest first$/);
  });

  it("offers no Sort by beside the table, whose column headings sort", async () => {
    renderPageAt(1280);
    const [first] = await screen.findAllByTestId("sandbox-row");
    expect(first.tagName).toBe("TR");
    expect(screen.queryByRole("combobox", { name: /Sort by/ })).toBeNull();
    expect(screen.getByTestId("sort-name")).toBeInTheDocument();
  });
});
