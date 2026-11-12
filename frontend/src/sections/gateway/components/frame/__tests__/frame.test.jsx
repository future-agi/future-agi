import React from "react";
import { forwardRef, useState } from "react";
import Button from "@mui/material/Button";
import { MemoryRouter } from "react-router-dom";
import { HelmetProvider } from "react-helmet-async";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { ThemeProvider, createTheme } from "@mui/material/styles";
import { afterEach, describe, expect, it, vi } from "vitest";

import { palette } from "src/theme/palette";
import { typography } from "src/theme/typography";

import {
  GatewayDetailDrawer,
  GatewayEmptyState,
  GatewayFilterBar,
  GatewayGrid,
  GatewayPageFrame,
  GatewayStatusChip,
  loadColumnState,
  saveColumnState,
} from "..";

const grid = vi.hoisted(() => ({ props: null }));
vi.mock("ag-grid-react", () => ({
  AgGridReact: forwardRef(function MockGrid(props, _ref) {
    grid.props = props;
    return <div data-testid="ag-grid" />;
  }),
}));

function renderFrame(ui, mode = "light") {
  return render(
    <HelmetProvider>
      <ThemeProvider
        theme={createTheme({ palette: palette(mode), typography })}
      >
        <MemoryRouter>{ui}</MemoryRouter>
      </ThemeProvider>
    </HelmetProvider>,
  );
}

describe("GatewayPageFrame", () => {
  // DS foundation adae445253: gateway titles use title_sm (15 px, 600).
  it("draws an 15 px semibold title, one line of description and actions", async () => {
    renderFrame(
      <GatewayPageFrame
        title="Request logs"
        description="Every request through the gateway"
        actions={<button type="button">Export</button>}
      >
        <div>content</div>
      </GatewayPageFrame>,
    );
    const title = screen.getByRole("heading", {
      level: 1,
      name: "Request logs",
    });
    const style = window.getComputedStyle(title);
    expect(style.fontSize).toBe("0.9375rem");
    expect(style.fontWeight).toBe("600");
    expect(
      screen.getByText("Every request through the gateway"),
    ).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Export" })).toBeInTheDocument();
    expect(screen.getByText("content")).toBeInTheDocument();
    await waitFor(() => expect(document.title).toBe("Request logs"));
  });

  it("links back when a parent page is given", () => {
    renderFrame(
      <GatewayPageFrame
        title="gpt-4o"
        back={{ href: "/dashboard/gateway/models", label: "Catalog" }}
      />,
    );
    expect(screen.getByRole("link", { name: /Catalog/ })).toHaveAttribute(
      "href",
      "/dashboard/gateway/models",
    );
  });
});

describe("GatewayPageFrame content column", () => {
  // The content box is a flex column bounded by the page. A child with
  // overflow: hidden (a stat strip, a card) has an automatic min-height of 0
  // and would shrink to nothing on a page taller than the screen (the
  // Rules strip showed at zero height at 390 px). Children keep their own height; a
  // grid that fills the page still grows into the room that is left.
  it("does not let a child collapse to zero height", () => {
    renderFrame(
      <GatewayPageFrame title="Rules">
        <div data-testid="strip" style={{ overflow: "hidden" }}>
          strip
        </div>
        <GatewayGrid rowData={[{ id: 1 }]} columnDefs={[{ field: "id" }]} />
      </GatewayPageFrame>,
    );
    expect(
      window.getComputedStyle(screen.getByTestId("strip")).flexShrink,
    ).toBe("0");
    expect(
      window.getComputedStyle(screen.getByTestId("gateway-grid")).flexGrow,
    ).toBe("1");
  });
});

// Future AGI's Falcon button floats over every page's
// bottom right (FalconAIFab: fixed, 72 px up, 40 px tall), over the last
// rows' "..." menu on Sessions. A page whose grid fills it to the bottom ends
// its content above the button: 72 + 40 + an 8 px gap = 120 px.
//
// The button covered the catalog's
// last row, the model page's managed prices and Auto routing's counts, on
// pages that did not ask for the room. Every page ends clear of it.
//
// At 390 px in Chromium, each page scrolled to its end, the button
// still covered the catalog's footnote, MCP tools' last tool call and Model
// comparison's last rows. The frame is a flex item with minHeight 0, so on a
// page taller than its parent the content overflows the frame's box and its
// bottom padding stays behind, inside the box, above the overflow. The room
// is now the frame's last child, a box that cannot shrink: it overflows with
// the content, so the page always scrolls 120 px past its last row (12 px of
// gap and 108 px of box), and a page that fits keeps it too. It is the last
// child of the content box, the box whose content overflows.
describe("GatewayPageFrame and the Falcon button", () => {
  // The room is the last child of the content box (the frame's last
  // child), the box whose content overflows on a tall page.
  const clearance = (page) => {
    const content = page.lastElementChild;
    expect(content.lastElementChild).toHaveAttribute(
      "data-clearance",
      "floating-button",
    );
    return content.lastElementChild;
  };

  it("a page with a grid to the bottom ends its content clear of the button", () => {
    renderFrame(
      <GatewayPageFrame title="Sessions" testId="page">
        <GatewayGrid rowData={[{ id: 1 }]} columnDefs={[{ field: "id" }]} />
      </GatewayPageFrame>,
    );
    const room = clearance(screen.getByTestId("page"));
    expect(room).toHaveAttribute("aria-hidden", "true");
    expect(room).toHaveStyle({ height: "108px", flexShrink: "0" });
    // The content box's 12 px gap sits between the last row and the box.
    expect(room.parentElement).toHaveStyle({ gap: "12px" });
  });

  it("every other page ends clear of it too", () => {
    for (const title of ["Models", "Auto routing", "API keys"]) {
      const { unmount } = renderFrame(
        <GatewayPageFrame title={title} testId="page">
          <p>content</p>
        </GatewayPageFrame>,
      );
      const room = clearance(screen.getByTestId("page"));
      expect(room).toHaveStyle({ height: "108px", flexShrink: "0" });
      expect(room).toBeEmptyDOMElement();
      unmount();
    }
  });
});

// Logs scrolled sideways (scrollWidth 1656 at 1440, 406 at
// 390). The skip link, hidden until focused, was a full-width absolute box:
// in MUI sx a width or height of 1 is 100%, not 1 px. Hidden, it is the
// visually-hidden 1 px box, so it never widens the page.
describe("GatewayPageFrame skip link", () => {
  it("is a 1 px box while hidden, never 100% wide", () => {
    renderFrame(
      <GatewayPageFrame
        title="Request logs"
        skipLink={{ label: "Skip to the request list", onActivate: () => {} }}
      />,
    );
    const link = screen.getByRole("link", {
      name: "Skip to the request list",
    });
    const style = window.getComputedStyle(link);
    expect(style.position).toBe("absolute");
    expect(style.width).toBe("1px");
    expect(style.height).toBe("1px");
    // (jsdom's CSS parser drops negative margins, so the -1 px margin of the
    // pattern is not read here.)
  });
});

describe("GatewayFilterBar", () => {
  it.each(["click", "keyboard"])(
    "clears search once by %s, restores focus and preserves other filters",
    async (method) => {
      const onSearchChange = vi.fn();
      function Filters() {
        const [search, setSearch] = useState("timeout");
        return (
          <GatewayFilterBar
            search={search}
            onSearchChange={(value) => {
              onSearchChange(value);
              setSearch(value);
            }}
            searchPlaceholder="Search logs"
            actions={<Button>Columns</Button>}
          >
            <select aria-label="Status" defaultValue="failed">
              <option value="all">All statuses</option>
              <option value="failed">Failed</option>
            </select>
            <input
              aria-label="From date"
              type="date"
              defaultValue="2026-11-09"
            />
          </GatewayFilterBar>
        );
      }
      renderFrame(<Filters />);
      const input = screen.getByRole("textbox", { name: "Search logs" });
      const clear = screen.getByRole("button", { name: "Clear search" });
      if (method === "click") await userEvent.click(clear);
      else {
        await userEvent.click(input);
        await userEvent.tab();
        expect(clear).toHaveFocus();
        await userEvent.keyboard("{Enter}");
      }
      expect(onSearchChange).toHaveBeenCalledTimes(1);
      expect(onSearchChange).toHaveBeenCalledWith("");
      expect(input).toHaveValue("");
      expect(input).toHaveFocus();
      expect(
        screen.queryByRole("button", { name: "Clear search" }),
      ).not.toBeInTheDocument();
      expect(screen.getByRole("combobox", { name: "Status" })).toHaveValue(
        "failed",
      );
      expect(screen.getByLabelText("From date")).toHaveValue("2026-11-09");
      expect(screen.getByRole("button", { name: "Columns" })).toBeEnabled();
    },
  );

  it("has no clear control for empty search or an omitted search field", () => {
    const { unmount } = renderFrame(
      <GatewayFilterBar search="" onSearchChange={vi.fn()} />,
    );
    expect(
      screen.queryByRole("button", { name: "Clear search" }),
    ).not.toBeInTheDocument();
    unmount();
    renderFrame(<GatewayFilterBar actions={<Button>Export</Button>} />);
    expect(screen.queryByRole("textbox")).not.toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "Clear search" }),
    ).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Export" })).toBeInTheDocument();
  });

  it.each([
    [320, "light"],
    [390, "light"],
    [320, "dark"],
    [390, "dark"],
  ])(
    "keeps all controls rendered with wrapping actions at %s px in %s mode",
    (width, mode) => {
      const original = window.matchMedia;
      window.matchMedia = vi.fn((query) => ({
        matches: /min-width:\s*(\d+)px/.test(query)
          ? width >= Number(query.match(/min-width:\s*(\d+)px/)[1])
          : /max-width:\s*([\d.]+)px/.test(query) &&
            width <= Number(query.match(/max-width:\s*([\d.]+)px/)[1]),
        media: query,
        onchange: null,
        addListener: vi.fn(),
        removeListener: vi.fn(),
        addEventListener: vi.fn(),
        removeEventListener: vi.fn(),
        dispatchEvent: vi.fn(),
      }));
      try {
        renderFrame(
          <GatewayFilterBar
            search="timeout"
            onSearchChange={vi.fn()}
            searchPlaceholder="Search logs"
            actions={
              <>
                <Button>Saved filters</Button>
                <Button>Filters</Button>
                <Button>Columns</Button>
                <Button>Export</Button>
              </>
            }
          >
            <select aria-label="Provider" defaultValue="all">
              <option value="all">All providers</option>
            </select>
          </GatewayFilterBar>,
          mode,
        );
        expect(
          screen.getByRole("textbox", { name: "Search logs" }),
        ).toBeVisible();
        expect(
          screen.getByRole("button", { name: "Clear search" }),
        ).toBeVisible();
        expect(
          screen.getByRole("combobox", { name: "Provider" }),
        ).toBeVisible();
        for (const name of ["Saved filters", "Filters", "Columns", "Export"]) {
          expect(screen.getByRole("button", { name })).toBeVisible();
        }
        const actions = screen.getByRole("button", {
          name: "Columns",
        }).parentElement;
        // jsdom verifies the wrapping contract, not pixel-level overflow.
        expect(window.getComputedStyle(actions).flexWrap).toBe("wrap");
        expect(
          window.getComputedStyle(screen.getByRole("search")).flexWrap,
        ).toBe("wrap");
      } finally {
        window.matchMedia = original;
      }
    },
  );

  it("has a search box, filter slots and actions", async () => {
    const onSearchChange = vi.fn();
    renderFrame(
      <GatewayFilterBar
        search=""
        onSearchChange={onSearchChange}
        searchPlaceholder="Search models"
        actions={<button type="button">Columns</button>}
      >
        <span>Provider filter</span>
      </GatewayFilterBar>,
    );
    await userEvent.type(screen.getByPlaceholderText("Search models"), "g");
    expect(onSearchChange).toHaveBeenCalledWith("g");
    expect(screen.getByText("Provider filter")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Columns" })).toBeInTheDocument();
  });
});

describe("GatewayGrid", () => {
  afterEach(() => {
    grid.props = null;
    window.localStorage.clear();
  });

  it("renders AG Grid on the FA theme with sortable, resizable columns", () => {
    renderFrame(
      <GatewayGrid
        rowData={[{ id: "r1", model: "gpt-4o" }]}
        columnDefs={[{ field: "model", headerName: "Model" }]}
        getRowId={(params) => params.data.id}
      />,
    );
    expect(screen.getByTestId("ag-grid")).toBeInTheDocument();
    expect(grid.props.theme).toBeTruthy();
    expect(grid.props.rowData).toEqual([{ id: "r1", model: "gpt-4o" }]);
    expect(grid.props.defaultColDef).toMatchObject({
      sortable: true,
      resizable: true,
    });
    // AG Grid's own root has no height unless told to fill its box.
    expect(grid.props.containerStyle).toEqual({
      height: "100%",
      width: "100%",
    });
  });

  it("shows the one empty state instead of an empty grid", () => {
    renderFrame(
      <GatewayGrid
        rowData={[]}
        columnDefs={[{ field: "model" }]}
        emptyState={{ title: "No requests yet", description: "Send one." }}
      />,
    );
    expect(screen.queryByTestId("ag-grid")).not.toBeInTheDocument();
    expect(screen.getByText("No requests yet")).toBeInTheDocument();
  });

  it("takes a fixed height outside the page frame's flex column", () => {
    renderFrame(
      <GatewayGrid
        rowData={[{ id: "r1" }]}
        columnDefs={[{ field: "m" }]}
        height={420}
      />,
    );
    const box = screen.getByTestId("gateway-grid");
    expect(window.getComputedStyle(box).height).toBe("420px");
    expect(window.getComputedStyle(box).flexGrow).toBe("0");
  });

  it("keeps the grid while loading", () => {
    renderFrame(
      <GatewayGrid rowData={undefined} loading columnDefs={[{ field: "m" }]} />,
    );
    expect(screen.getByTestId("ag-grid")).toBeInTheDocument();
    expect(grid.props.loading).toBe(true);
  });

  it("remembers how the user shaped the columns", () => {
    saveColumnState("gw-test", [{ colId: "model", width: 240, hide: false }]);
    expect(loadColumnState("gw-test")).toEqual([
      { colId: "model", width: 240, hide: false },
    ]);
    renderFrame(
      <GatewayGrid
        rowData={[{ id: "r1" }]}
        columnDefs={[{ field: "model" }]}
        stateKey="gw-test"
      />,
    );
    const api = {
      applyColumnState: vi.fn(),
      getColumnState: () => [{ colId: "model", width: 300 }],
    };
    grid.props.onGridReady({ api });
    expect(api.applyColumnState).toHaveBeenCalledWith({
      state: [{ colId: "model", width: 240, hide: false }],
      applyOrder: true,
    });
    grid.props.onColumnResized({ api, finished: true });
    expect(loadColumnState("gw-test")).toEqual([
      { colId: "model", width: 300 },
    ]);
  });

  it("survives storage that throws", () => {
    const original = Storage.prototype.getItem;
    Storage.prototype.getItem = () => {
      throw new Error("blocked");
    };
    try {
      expect(loadColumnState("gw-test")).toBeNull();
    } finally {
      Storage.prototype.getItem = original;
    }
  });
});

describe("GatewayGrid row clicks and header menus", () => {
  afterEach(() => {
    grid.props = null;
  });

  // AG Grid reports a row click even when it landed on a button, link, menu
  // or input inside the row, so a row's own controls also opened its drawer
  //. Every
  // gateway grid now skips those clicks.
  it("does not open a row for a click on a control inside it", () => {
    const onRowClicked = vi.fn();
    renderFrame(
      <GatewayGrid
        rowData={[{ id: 1 }]}
        columnDefs={[{ field: "id" }]}
        onRowClicked={onRowClicked}
      />,
    );
    const button = document.createElement("button");
    const link = document.createElement("a");
    const menu = document.createElement("div");
    menu.setAttribute("role", "menuitem");
    const cell = document.createElement("div");
    [button, link, menu].forEach((target) =>
      grid.props.onRowClicked({ event: { target }, data: { id: 1 } }),
    );
    expect(onRowClicked).not.toHaveBeenCalled();
    grid.props.onRowClicked({ event: { target: cell }, data: { id: 1 } });
    expect(onRowClicked).toHaveBeenCalledTimes(1);
  });

  // One rule for every gateway grid, as on Observe's grids: no column-menu
  // button in the header (calm, and the Logs and Runs headers fit at 1440).
  // The column menu (pin, autosize, Choose Columns, reset) stays one right
  // click away on any header, so no option is lost.
  it("hides the header menu button and keeps the right-click column menu", () => {
    renderFrame(
      <GatewayGrid rowData={[{ id: 1 }]} columnDefs={[{ field: "id" }]} />,
    );
    expect(grid.props.defaultColDef.suppressHeaderMenuButton).toBe(true);
    expect(grid.props.suppressHeaderContextMenu).not.toBe(true);
  });

  it("lets a page show the menu button for its own columns", () => {
    renderFrame(
      <GatewayGrid
        rowData={[{ id: 1 }]}
        columnDefs={[{ field: "id" }]}
        defaultColDef={{ suppressHeaderMenuButton: false }}
      />,
    );
    expect(grid.props.defaultColDef.suppressHeaderMenuButton).toBe(false);
  });
});

describe("GatewayDetailDrawer", () => {
  it("opens on the right with a title and closes", async () => {
    const onClose = vi.fn();
    renderFrame(
      <GatewayDetailDrawer open title="Request 01M5" onClose={onClose}>
        <div>detail</div>
      </GatewayDetailDrawer>,
    );
    const dialog = screen.getByRole("dialog", { name: "Request 01M5" });
    expect(within(dialog).getByText("detail")).toBeInTheDocument();
    await userEvent.click(
      within(dialog).getByRole("button", { name: "Close" }),
    );
    expect(onClose).toHaveBeenCalled();
  });

  it("puts the actions on their own row under the title on a phone", () => {
    // At 390 px a long title beside Edit / Remove wrapped to four lines
    //. Below sm the actions take a row of their own; the
    // close button stays next to the title. jsdom applies the base (phone)
    // styles and skips the sm media rule.
    renderFrame(
      <GatewayDetailDrawer
        open
        title="openrouter-managed-upstream-eu-west"
        onClose={() => {}}
        actions={
          <>
            <button type="button">Edit</button>
            <button type="button">Remove</button>
          </>
        }
      >
        <div>detail</div>
      </GatewayDetailDrawer>,
    );
    const dialog = screen.getByRole("dialog", {
      name: "openrouter-managed-upstream-eu-west",
    });
    const actions = within(dialog).getByRole("button", {
      name: "Edit",
    }).parentElement;
    expect(
      within(actions).getByRole("button", { name: "Remove" }),
    ).toBeInTheDocument();
    const header = actions.parentElement;
    expect(window.getComputedStyle(header).flexWrap).toBe("wrap");
    expect(window.getComputedStyle(actions).flexBasis).toBe("100%");
    const close = within(dialog).getByRole("button", { name: "Close" });
    expect(close.parentElement).toBe(header);
    expect(Number(window.getComputedStyle(close).order)).toBeLessThan(
      Number(window.getComputedStyle(actions).order),
    );
  });

  it("draws no empty actions row when there are no actions", () => {
    renderFrame(
      <GatewayDetailDrawer open title="Request 01M5" onClose={() => {}}>
        <div>detail</div>
      </GatewayDetailDrawer>,
    );
    const dialog = screen.getByRole("dialog", { name: "Request 01M5" });
    const header = within(dialog).getByRole("button", {
      name: "Close",
    }).parentElement;
    expect(header.children).toHaveLength(2);
  });
});

describe("GatewayStatusChip", () => {
  it.each([
    ["healthy", "Healthy"],
    ["error", "Failed"],
    ["warning", "Degraded"],
    ["pending", "Pending"],
    ["neutral", "Off"],
  ])("draws the %s tone with the FA status chip", (tone, label) => {
    renderFrame(<GatewayStatusChip tone={tone} label={label} />, "dark");
    expect(screen.getByText(label)).toBeInTheDocument();
  });
});

describe("GatewayEmptyState", () => {
  it("is the FA empty layout with one action", async () => {
    const onAction = vi.fn();
    renderFrame(
      <GatewayEmptyState
        title="No keys yet"
        description="Create a key to call the gateway."
        actionLabel="Create key"
        onAction={onAction}
      />,
    );
    expect(screen.getByText("No keys yet")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Create key" }));
    expect(onAction).toHaveBeenCalled();
  });

  it("keeps the presets the gateway pages already use", () => {
    renderFrame(<GatewayEmptyState variant="no-gateway" onAction={() => {}} />);
    expect(screen.getByText("Gateway page unavailable")).toBeInTheDocument();
    expect(
      screen.getByText(
        "We couldn't load the gateway page. Retry in a moment. If this continues, contact your administrator.",
      ),
    ).toBeInTheDocument();
    expect(screen.queryByText(/provisioned|few seconds/)).toBeNull();
    expect(screen.getByRole("button", { name: "Retry" })).toBeInTheDocument();
  });
});
