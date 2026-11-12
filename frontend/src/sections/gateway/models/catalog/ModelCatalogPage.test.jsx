import React, { forwardRef } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { endpoints } from "src/utils/axios";

import {
  MANAGED_ON,
  answerFrom,
  contractFailures,
  demoResponses,
  renderModelsAt,
} from "../test-fixtures/harness";
import { palette } from "src/theme/palette";

import ModelCatalogPage from "./ModelCatalogPage";
import { CATALOG } from "../test-fixtures/catalog";
import { ModelCell } from "./cells";
import { render } from "@testing-library/react";
import {
  MemoryRouter,
  useLocation,
  useNavigate,
  useParams,
} from "react-router-dom";
import ModelDetailPage from "../detail/ModelDetailPage";
import { FACET_PANEL_PX } from "./columns";
import { gridBudget1440, minimumGridWidth } from "../../logs/__tests__/gridFit";

const api = vi.hoisted(() => ({ get: vi.fn(), post: vi.fn() }));
vi.mock("src/utils/axios", async (importOriginal) => {
  const actual = await importOriginal();
  return {
    ...actual,
    default: { ...actual.default, get: api.get, post: api.post },
  };
});
vi.mock("notistack", async (importOriginal) => ({
  ...(await importOriginal()),
  enqueueSnackbar: vi.fn(),
}));
vi.mock("src/auth/hooks", () => ({
  useAuthContext: () => ({ role: "Owner" }),
}));
// Gateway config writes are Owner or Admin only (useGatewayAdmin); these
// cases act as an Owner.
vi.mock("src/contexts/OrganizationContext", () => ({
  useOrganization: () => ({ orgLevel: 15 }),
}));
vi.mock("../../context/useGatewayContext", () => ({
  useGatewayContext: () => ({
    gatewayId: "default",
    gateway: { id: "default", baseUrl: "http://localhost:28490/v1" },
  }),
}));

const grid = vi.hoisted(() => ({
  props: null,
  renderCells: false,
  compact: false,
}));
vi.mock("src/hooks/use-responsive", () => ({
  useResponsive: () => grid.compact,
}));
vi.mock("ag-grid-react", async () => {
  const { MockAgGrid } = await import("../../mcp/testing/agGridMock");
  return {
    AgGridReact: forwardRef(function MockGrid(props, _ref) {
      grid.props = props;
      return grid.renderCells ? (
        <MockAgGrid {...props} />
      ) : (
        <div data-testid="ag-grid" />
      );
    }),
  };
});

const visibleIds = () => (grid.props?.rowData || []).map((r) => r.id);

// The rows in the order AG Grid shows them: the column defs' default sorts
// (sort, sortIndex) applied with each column's comparator, as the grid does.
const gridOrder = () => {
  const sorted = grid.props.columnDefs
    .map((c) => ({
      ...c,
      sort: c.sort ?? c.initialSort,
      sortIndex: c.sortIndex ?? c.initialSortIndex,
    }))
    .filter((c) => c.sort)
    .sort((a, b) => (a.sortIndex ?? 0) - (b.sortIndex ?? 0));
  const value = (col, row) =>
    col.valueGetter ? col.valueGetter({ data: row }) : row[col.field];
  return [...grid.props.rowData]
    .sort((ra, rb) => {
      for (const col of sorted) {
        const desc = col.sort === "desc";
        const va = value(col, ra);
        const vb = value(col, rb);
        let r = col.comparator
          ? col.comparator(va, vb, { data: ra }, { data: rb }, desc)
          : va > vb
            ? 1
            : va < vb
              ? -1
              : 0;
        if (desc) r = -r;
        if (r) return r;
      }
      return 0;
    })
    .map((r) => r.id);
};
const column = (field) =>
  grid.props.columnDefs.find((c) => c.colId === field || c.field === field);

const renderPage = (
  responses = demoResponses(endpoints),
  element = <ModelCatalogPage />,
  url = "/dashboard/gateway/models",
) => {
  api.get.mockImplementation(answerFrom(responses));
  return renderModelsAt(url, element);
};

// The old availability cases opened a drawer from the catalogue row. In v2
// the same action navigates to ModelDetailPage; retain both halves of the check.
function CatalogAndDetail() {
  const { model } = useParams();
  return model ? <ModelDetailPage /> : <ModelCatalogPage />;
}

const waitForRows = () =>
  waitFor(() => expect(visibleIds().length).toBeGreaterThan(0));

// The catalog's facets (checkbox facets with counts, not dropdowns):
// a "Filters" region of named groups, each
// option named "<label> (<count>)".
const facet = (name) =>
  within(screen.getByRole("region", { name: "Filters" })).getByRole("group", {
    name,
  });
const option = (group, label, role = "checkbox") =>
  within(facet(group)).getByRole(role, {
    name: new RegExp(`^${label} \\(\\d+\\)$`),
  });
const count = (group, label, role) =>
  Number(
    option(group, label, role)
      .getAttribute("aria-label")
      .match(/\((\d+)\)$/)[1],
  );

// Every request the page sent must pass the API contract.
afterEach(async () => {
  expect(await contractFailures(api)).toEqual([]);
});

beforeEach(() => {
  api.get.mockReset();
  api.post.mockReset();
  grid.props = null;
  grid.renderCells = false;
  grid.compact = false;
  window.localStorage.clear();
});

describe("Models catalog page", () => {
  it("searches exact metadata facets, shares their selections and removes their chips", async () => {
    const source = demoResponses(endpoints);
    source[endpoints.gateway.managedModelsCatalog] = {
      status: true,
      result: {
        models: [
          {
            id: "own/audio",
            vendor: "own",
            supported_parameters: ["seed", "logprobs"],
            input_modalities: ["text", "audio"],
          },
          {
            id: "own/structured",
            vendor: "own",
            supported_parameters: ["response_format"],
            input_modalities: ["text"],
          },
          { id: "own/unknown", vendor: "own" },
        ],
      },
    };
    const { location } = renderPage(source);
    await waitForRows();
    await userEvent.type(
      screen.getByLabelText("Search supported parameters"),
      "log",
    );
    expect(
      within(facet("Supported parameters")).queryByRole("checkbox", {
        name: /^response_format/,
      }),
    ).not.toBeInTheDocument();
    await userEvent.click(option("Supported parameters", "logprobs"));
    await userEvent.click(option("Input modalities", "audio"));
    expect(visibleIds()).toEqual(["own/audio"]);
    const query = new URLSearchParams(location.search);
    expect(query.getAll("parameter")).toEqual(["logprobs"]);
    expect(query.getAll("input")).toEqual(["audio"]);
    expect(
      screen.getByText("Supported parameters: logprobs"),
    ).toBeInTheDocument();
    expect(screen.getByText("Input modalities: audio")).toBeInTheDocument();
    await userEvent.click(
      screen.getByRole("button", {
        name: "Remove Supported parameters: logprobs",
      }),
    );
    expect(new URLSearchParams(location.search).getAll("parameter")).toEqual(
      [],
    );
    expect(new URLSearchParams(location.search).getAll("input")).toEqual([
      "audio",
    ]);
    await userEvent.click(
      within(screen.getByRole("region", { name: "Filters" })).getByRole(
        "button",
        { name: "Clear filters" },
      ),
    );
    expect(visibleIds()).toHaveLength(3);
    expect(new URLSearchParams(location.search).getAll("parameter")).toEqual(
      [],
    );
  });

  it("is a Future AGI page: 18 px title, one line of description", async () => {
    renderPage();
    const title = await screen.findByRole("heading", {
      level: 1,
      name: "Models",
    });
    expect(window.getComputedStyle(title).fontSize).toBe("0.9375rem");
    expect(window.getComputedStyle(title).fontWeight).toBe("600");
  });

  it("lists every catalog model in an AG Grid with sortable price and context", async () => {
    renderPage();
    await waitForRows();
    expect(visibleIds()).toHaveLength(11);
    for (const field of ["context", "input", "output"]) {
      expect(column(field).sortable).not.toBe(false);
      expect(typeof column(field).comparator).toBe("function");
    }
    // The newest model first, as the default sort.
    expect(column("released").initialSort).toBe("desc");
  });

  // The first screen leads with what the org can call; the
  // Released order holds inside each group, and the Access filter is as it
  // was.
  it("lists callable models first, newest first within each group", async () => {
    renderPage();
    await waitForRows();
    const rows = new Map(grid.props.rowData.map((r) => [r.id, r]));
    const order = gridOrder();
    const callable = order.map((id) => rows.get(id).access !== "none");
    expect(callable[0]).toBe(true);
    expect(callable.lastIndexOf(true)).toBeLessThan(callable.indexOf(false));
    const released = (group) =>
      order
        .filter((id) => (rows.get(id).access !== "none") === group)
        .map((id) => rows.get(id).released ?? -1);
    for (const group of [true, false]) {
      expect(released(group)).toEqual(
        [...released(group)].sort((a, b) => b - a),
      );
    }
  });

  it("names providers, never their slugs", async () => {
    renderPage();
    await waitForRows();
    const names = grid.props.rowData.map((r) => r.vendorName);
    expect(names.some((n) => /^~|-/.test(n))).toBe(false);
  });

  it("gives each row its provider's logo where Future AGI has one", async () => {
    renderPage();
    await waitForRows();
    await waitFor(() =>
      expect(
        grid.props.rowData.find((r) => r.id === "anthropic/claude-haiku-5.5")
          .vendorLogo,
      ).toBe("/icons/settings/anthropic.png"),
    );
    const row = (id) => grid.props.rowData.find((r) => r.id === id);
    // Provider identity selects bundled artwork, never the API's logo URL.
    expect(row("google/gemini-2.5-flash").vendorLogo).toBe(
      "/icons/settings/google.png",
    );
    // No provider-status row exists for Cohere in this fixture.
    expect(row("cohere/north-mini-code:free").vendorLogo).toBe("");
    expect(column("vendorName").cellRenderer).toBeTruthy();
  });

  it("keeps the default view calm: Capabilities and Max output start hidden", async () => {
    renderPage();
    await waitForRows();
    expect(column("capabilities").hide).toBe(true);
    expect(column("maxOutput").hide).toBe(true);
    expect(column("input").hide).toBeFalsy();
  });

  it("shows what managed credits charge, and no price where they cannot buy the model", async () => {
    const { container } = renderPage();
    await waitForRows();
    const row = (id) => grid.props.rowData.find((r) => r.id === id);
    const input = column("input");
    expect(
      input.valueFormatter({ value: row("anthropic/claude-haiku-5.5").input }),
    ).toBe("$0.105");
    expect(row("black-forest-labs/flux-3-image").input).toBeNull();
    expect(input.valueFormatter({ value: null })).toBe("—");
    // One price, what a request on credits is charged: no margin stated.
    const note = screen.getByText(
      /Prices are per 1M tokens: what requests on Future AGI credits are billed/,
    );
    expect(note).not.toHaveTextContent(/managed/i);
    expect(note).not.toHaveTextContent(
      /markup|future agi price|provider \+|%/i,
    );
    expect(input.headerTooltip).not.toMatch(/markup|provider's price|%/i);
    expect(column("output").headerTooltip).not.toMatch(
      /markup|provider's price|%/i,
    );
    expect(container).not.toHaveTextContent(/managed/i);
    expect(document.body).not.toHaveTextContent(/managed/i);
  });

  it("keeps the dynamic-price fixture unavailable on credits without showing Free", async () => {
    renderPage(
      demoResponses(endpoints, {
        [endpoints.gateway.managedModelsCatalog]: {
          status: true,
          result: CATALOG,
        },
      }),
    );
    await waitForRows();
    const dynamic = grid.props.rowData.find(
      (row) => row.id === "perplexity/sonar-deep-research",
    );
    const input = column("input");
    expect(dynamic).toMatchObject({
      input: null,
      output: null,
      onCredits: false,
    });
    expect(dynamic.accessReason).toMatch(/Not available on Future AGI credits/);
    expect(input.valueFormatter({ value: dynamic.input, data: dynamic })).toBe(
      "—",
    );
    expect(
      column("output").valueFormatter({ value: dynamic.output, data: dynamic }),
    ).toBe("—");
    const cell = render(
      <MemoryRouter>
        <ModelCell data={dynamic} />
      </MemoryRouter>,
    );
    expect(
      within(cell.container).getByText("Not available on credits"),
    ).toBeInTheDocument();
    expect(within(cell.container).queryByText("Free")).toBeNull();
  });

  it("filters by type, access and price", async () => {
    renderPage();
    await waitForRows();
    await userEvent.click(option("Type", "Image"));
    expect(visibleIds()).toEqual(["black-forest-labs/flux-3-image"]);
    await userEvent.click(option("Type", "Image"));
    await userEvent.click(option("Type", "Chat"));
    await userEvent.click(option("Access", "Own key"));
    expect(visibleIds().sort()).toEqual([
      "openai/gpt-4o",
      "openai/gpt-4o-mini",
    ]);
    await userEvent.click(option("Access", "Own key"));
    await userEvent.click(option("Price", "Free", "radio"));
    expect(visibleIds()).toEqual(["cohere/north-mini-code:free"]);
  });

  it("filters by provider and search, and counts what is shown", async () => {
    renderPage();
    await waitForRows();
    await userEvent.click(option("Provider", "Google"));
    expect(visibleIds().sort()).toEqual([
      "google/gemini-2.5-flash",
      "google/gemini-2.5-flash-image",
    ]);
    expect(screen.getByText("2 of 11 models")).toBeInTheDocument();
    await userEvent.type(
      screen.getByRole("textbox", { name: /search/i }),
      "image",
    );
    expect(visibleIds()).toEqual(["google/gemini-2.5-flash-image"]);
  });

  it("filters by capability", async () => {
    renderPage();
    await waitForRows();
    const box = option("Capabilities", "Image input");
    await userEvent.click(box);
    expect(box).toBeChecked();
    expect(visibleIds()).toContain("openai/gpt-4o");
    expect(visibleIds()).not.toContain("cohere/north-mini-code:free");
  });

  // Filters with checkbox facets
  // that show counts; ours were dropdowns. Each option counts the models it
  // would show with the other facets applied; options in a group add up
  // (any of them), groups narrow each other (all of them).
  // The facet panel takes its width from the grid: at 1440 px the grid's
  // columns still fit beside it, with no sideways scroll.
  it("fits its grid beside the facet panel at 1440 px", async () => {
    renderPage();
    await waitForRows();
    expect(FACET_PANEL_PX).toBeGreaterThan(0);
    expect(minimumGridWidth(grid.props)).toBeLessThanOrEqual(
      gridBudget1440() - FACET_PANEL_PX,
    );
  });

  it("filters with facets whose counts follow the other facets", async () => {
    renderPage();
    await waitForRows();
    expect(count("Type", "Chat")).toBe(8);
    expect(count("Type", "Image")).toBe(1);
    expect(count("Provider", "Google")).toBe(2);
    expect(count("Provider", "OpenAI")).toBe(3);

    await userEvent.click(option("Provider", "Google"));
    expect(count("Type", "Chat")).toBe(2);
    expect(count("Type", "Image")).toBe(0);
    expect(option("Type", "Image")).toBeDisabled();
    // Its own group's counts stay those of the other facets.
    expect(count("Provider", "OpenAI")).toBe(3);

    await userEvent.click(option("Provider", "OpenAI"));
    expect(visibleIds()).toHaveLength(5);
    expect(screen.getByText("5 of 11 models")).toBeInTheDocument();
    expect(count("Type", "Embeddings")).toBe(1);

    await userEvent.click(option("Type", "Embeddings"));
    expect(visibleIds()).toEqual(["openai/text-embedding-3-small"]);
    expect(count("Provider", "OpenAI")).toBe(1);
    expect(count("Provider", "Google")).toBe(0);

    await userEvent.click(
      within(screen.getByRole("region", { name: "Filters" })).getByRole(
        "button",
        { name: "Clear filters" },
      ),
    );
    expect(visibleIds()).toHaveLength(11);
    expect(option("Provider", "Google")).not.toBeChecked();
  });

  it("offers to clear filters that match nothing", async () => {
    renderPage();
    await waitForRows();
    await userEvent.type(
      screen.getByRole("textbox", { name: /search/i }),
      "no-such-model",
    );
    await waitFor(() =>
      expect(screen.getByText("No models match")).toBeInTheDocument(),
    );
    // The filter bar's chip and the empty state's button both clear.
    const clear = screen.getAllByRole("button", { name: "Clear filters" });
    expect(clear).toHaveLength(3);
    await userEvent.click(clear.at(-1));
    await waitForRows();
    expect(visibleIds()).toHaveLength(11);
  });

  it("opens the model page from a row", async () => {
    const { location } = renderPage();
    await waitForRows();
    const row = grid.props.rowData.find(
      (r) => r.id === "cohere/north-mini-code:free",
    );
    act(() => grid.props.onRowClicked({ data: row }));
    await waitFor(() =>
      expect(location.pathname).toBe(
        "/dashboard/gateway/models/cohere/north-mini-code%3Afree",
      ),
    );
  });

  // The model name is a link (keyboard, new tab); its click must not open
  // the page a second time through the row click.
  it("lets the model link open the page on its own", async () => {
    const { location } = renderPage();
    await waitForRows();
    const row = grid.props.rowData.find(
      (r) => r.id === "anthropic/claude-haiku-5.5",
    );
    const link = document.createElement("a");
    act(() => grid.props.onRowClicked({ data: row, event: { target: link } }));
    expect(location.pathname).toBe("/dashboard/gateway/models");
  });

  // The grid hides body cells from Tab unless cells take focus; a keyboard
  // user reaches a row with Tab and the arrow keys, and Enter opens it.
  it("opens the model page with Enter on a focused row", async () => {
    const { location } = renderPage();
    await waitForRows();
    expect(grid.props.suppressCellFocus).toBe(false);
    const row = grid.props.rowData.find(
      (r) => r.id === "cohere/north-mini-code:free",
    );
    const cell = document.createElement("div");
    act(() =>
      grid.props.onCellKeyDown({
        data: row,
        event: { key: "ArrowDown", target: cell },
      }),
    );
    expect(location.pathname).toBe("/dashboard/gateway/models");
    act(() =>
      grid.props.onCellKeyDown({
        data: row,
        event: { key: "Enter", target: cell },
      }),
    );
    await waitFor(() =>
      expect(location.pathname).toBe(
        "/dashboard/gateway/models/cohere/north-mini-code%3Afree",
      ),
    );
  });

  it("shows the managed credit cut to the cent, never rounded up", async () => {
    renderPage();
    await waitForRows();
    expect(screen.getByText("$34.999336")).toBeInTheDocument();
    expect(screen.queryByText("$35.00")).toBeNull();
  });

  // Where the prices come from, in secondary text with an info
  // icon; not a warning on the landing screen.
  it("says the price list is the bundled one, quietly, with the detail in a tooltip", async () => {
    renderPage();
    await waitForRows();
    const line = screen.getByText("Bundled price list");
    expect(line).toHaveStyle({ color: palette("light").text.secondary });
    expect(screen.queryByText(/live prices are unavailable/i)).toBeNull();
    await userEvent.hover(line);
    expect(await screen.findByRole("tooltip")).toHaveTextContent(
      /built into this gateway release/,
    );
  });

  it("shows a retryable error when the catalog cannot load", async () => {
    const responses = demoResponses(endpoints, {
      [endpoints.gateway.managedModelsCatalog]: new Error(
        "Gateway error: timeout",
      ),
    });
    renderPage(responses);
    expect(
      await screen.findByText("Couldn't load the model catalog"),
    ).toBeInTheDocument();
    responses[endpoints.gateway.managedModelsCatalog] = {
      status: true,
      result: { markup_percent: 5, providers: [], models: [] },
    };
    await userEvent.click(screen.getByRole("button", { name: "Retry" }));
    expect(await screen.findByText("The catalog is empty")).toBeInTheDocument();
  });

  // The catalog waits for the managed-models status; when that read fails
  // the page shows one error with Retry, not a filter bar over a grid that
  // never stops loading.
  it("shows one retryable error when the managed-models status cannot load", async () => {
    const responses = demoResponses(endpoints, {
      [endpoints.gateway.managedModels]: new Error("Request failed (500)"),
    });
    renderPage(responses);
    expect(
      await screen.findByText("Couldn't load the model catalog"),
    ).toBeInTheDocument();
    expect(screen.getAllByRole("button", { name: "Retry" })).toHaveLength(1);
    expect(screen.queryByRole("textbox", { name: /Search models/ })).toBeNull();
    expect(screen.queryByTestId("ag-grid")).toBeNull();
    expect(
      screen.queryByText("Couldn't load your Future AGI credits."),
    ).toBeNull();
    responses[endpoints.gateway.managedModels] = {
      status: true,
      result: MANAGED_ON,
    };
    await userEvent.click(screen.getByRole("button", { name: "Retry" }));
    await waitForRows();
    expect(screen.queryByText("Couldn't load the model catalog")).toBeNull();
  });

  // The saved layout key moved to v3; a layout saved under v2 is carried
  // over once (order, sort, the user's own widths and shown columns), not
  // discarded.
  it.each(["pending", "error"])(
    "keeps browsing with %s provider access and an honest row label",
    async (state) => {
      grid.renderCells = true;
      renderPage(
        demoResponses(endpoints, {
          [endpoints.gateway.providers("default")]:
            state === "pending"
              ? () => new Promise(() => {})
              : new Error("Provider read failed"),
        }),
      );
      await waitForRows();
      await waitFor(() =>
        expect(
          grid.props.rowData.find(
            (row) => row.id === "openai/text-embedding-3-small",
          )?.providerAccessState,
        ).toBe(state),
      );
      const row = screen.getByRole("row", {
        name: /openai\/text-embedding-3-small/,
      });
      expect(row).toHaveTextContent("Access not verified");
      expect(row).not.toHaveTextContent(/Not callable|Free/);
      expect(screen.queryByRole("button", { name: "Add provider" })).toBeNull();
      if (state === "error")
        expect(
          screen.getByRole("button", { name: "Retry provider access" }),
        ).toBeInTheDocument();
    },
  );

  it.each([
    [
      "catalog",
      "agentcc-managed-models-catalog",
      endpoints.gateway.managedModelsCatalog,
      "Retry catalog",
    ],
    [
      "provider",
      "agentcc-provider-health",
      endpoints.gateway.providers("default"),
      "Retry provider access",
    ],
    [
      "status",
      "agentcc-managed-models",
      endpoints.gateway.managedModels,
      "Retry model status",
    ],
  ])(
    "keeps rows, search, facets, sort and the mounted grid after a %s refresh fails",
    async (_name, queryKey, endpoint, retryLabel) => {
      const responses = demoResponses(endpoints);
      const { client, location } = renderPage(responses);
      await waitForRows();
      await waitFor(() =>
        expect(grid.props.rowData[0].providerAccessState).toBe("ready"),
      );
      await userEvent.click(option("Provider", "OpenAI"));
      await userEvent.click(option("Access", "Own key"));
      await userEvent.type(
        screen.getByRole("textbox", { name: "Search models" }),
        "gpt",
      );
      const gridApi = readyGrid();
      await userEvent.selectOptions(
        screen.getByRole("combobox", { name: "Sort models" }),
        "newest",
      );
      const mounted = screen.getByTestId("ag-grid");
      mounted.scrollTop = 156;
      const ids = visibleIds();
      const search = location.search;
      const success = responses[endpoint];
      responses[endpoint] = new Error("Background read failed");
      await act(async () => {
        await client.refetchQueries({ queryKey: [queryKey] });
      });
      // React Query tells its observers on a later timer tick (setTimeout 0),
      // which act() may return before: wait for the failed read to show, then
      // check that nothing the person set up moved.
      expect(await screen.findByText(/stale/i)).toBeInTheDocument();
      expect(screen.getByTestId("ag-grid")).toBe(mounted);
      expect(mounted.scrollTop).toBe(156);
      expect(visibleIds()).toEqual(ids);
      expect(location.search).toBe(search);
      expect(option("Provider", "OpenAI")).toBeChecked();
      expect(option("Access", "Own key")).toBeChecked();
      expect(
        screen.getByRole("textbox", { name: "Search models" }),
      ).toHaveValue("gpt");
      expect(screen.getByRole("combobox", { name: "Sort models" })).toHaveValue(
        "newest",
      );
      expect(screen.getByText(/stale/i)).toBeInTheDocument();
      const calls = api.get.mock.calls.length;
      responses[endpoint] = success;
      await userEvent.click(screen.getByRole("button", { name: retryLabel }));
      await waitFor(() =>
        expect(screen.queryByRole("button", { name: retryLabel })).toBeNull(),
      );
      expect(api.get.mock.calls.slice(calls).map(([url]) => url)).toEqual([
        endpoint,
      ]);
      expect(screen.getByTestId("ag-grid")).toBe(mounted);
      expect(gridApi.ensureIndexVisible).not.toHaveBeenCalled();
    },
  );

  it("keeps a layout saved before the facet panel", async () => {
    const V2 = "gateway-grid:gateway-models-catalog-v2";
    const V3 = "gateway-grid:gateway-models-catalog-v3";
    window.localStorage.setItem(
      V2,
      JSON.stringify([
        { colId: "released", width: 150, flex: 0.9, sort: "desc" },
        { colId: "name", width: 300, flex: null, hide: false },
        { colId: "capabilities", width: 160, flex: 1.3, hide: false },
      ]),
    );
    renderPage();
    await waitForRows();
    const applyColumnState = vi.fn();
    act(() => grid.props.onGridReady({ api: { applyColumnState } }));
    expect(applyColumnState).toHaveBeenCalledWith({
      state: [
        { colId: "released", sort: "desc" },
        { colId: "name", width: 300, flex: null, hide: false },
        { colId: "capabilities", hide: false },
      ],
      applyOrder: true,
    });
    expect(window.localStorage.getItem(V2)).toBeNull();
    expect(JSON.parse(window.localStorage.getItem(V3))).toHaveLength(3);
    window.localStorage.clear();
  });

  // A deployment that offers no managed models has no catalog: the page
  // shows no refusal and no card, and the catalog is not read (ModelsSection
  // opens Auto routing at these URLs).
  it("shows no refusal on a deployment without managed models", async () => {
    renderPage(
      demoResponses(endpoints, {
        [endpoints.gateway.managedModels]: {
          status: true,
          result: { ...MANAGED_ON, available: false },
        },
      }),
    );
    await waitFor(() =>
      expect(
        api.get.mock.calls.some(
          ([url]) => url === endpoints.gateway.managedModels,
        ),
      ).toBe(true),
    );
    await waitFor(() => expect(screen.queryByText("Models")).toBeNull());
    expect(
      screen.queryByText("No managed catalog on this deployment"),
    ).toBeNull();
    expect(
      screen.queryByText("No Future AGI catalogue on this deployment"),
    ).toBeNull();
    expect(
      screen.queryByRole("checkbox", { name: "Future AGI credits" }),
    ).toBeNull();
    expect(
      screen.queryByText("Future AGI credits are off for this organization."),
    ).toBeNull();
    expect(
      api.get.mock.calls.some(
        ([url]) => url === endpoints.gateway.managedModelsCatalog,
      ),
    ).toBe(false);
  });
});

// Keep the existing detail recovery consumer in this packet's validation
// without changing files owned by the detail packet.
describe("catalog row compatibility with detail recovery", () => {
  it.each(["pending", "error"])(
    "keeps detail tabs unresolved after a cold %s provider read until a successful empty reply",
    async (state) => {
      let resolveProviders;
      const providerEndpoint = endpoints.gateway.providers("default");
      const responses = demoResponses(endpoints, {
        [providerEndpoint]:
          state === "pending"
            ? () =>
                new Promise((resolve) => {
                  resolveProviders = resolve;
                })
            : new Error("Provider GET failed"),
      });
      renderPage(
        responses,
        <ModelDetailPage />,
        "/dashboard/gateway/models/black-forest-labs/flux-3-image?tab=try",
      );
      if (state === "error")
        await screen.findByText("Could not verify your providers");
      await screen.findByText("Provider access not verified");
      expect(
        screen.getByRole("tab", { name: "Try it", selected: true }),
      ).toBeInTheDocument();
      expect(api.get.mock.calls.some(([url]) => url === providerEndpoint)).toBe(
        true,
      );

      for (const name of ["Try it", "Overview", "API"]) {
        await userEvent.click(screen.getByRole("tab", { name }));
        expect(
          screen.getByText("Provider access not verified"),
        ).toBeInTheDocument();
        expect(screen.queryByText("Not callable")).toBeNull();
        expect(
          screen.queryByText("This model cannot be tried here"),
        ).toBeNull();
        expect(
          screen.queryByText("No request to copy for this model"),
        ).toBeNull();
        expect(screen.queryByText(/Add a provider/i)).toBeNull();
        expect(
          screen.queryByRole("button", { name: "Go to Providers" }),
        ).toBeNull();
        expect(
          screen.queryByRole("link", { name: "Go to Providers" }),
        ).toBeNull();
        expect(screen.queryByRole("button", { name: "Run" })).toBeNull();
      }

      await userEvent.click(screen.getByRole("tab", { name: "Try it" }));
      const emptyReply = { status: true, result: { providers: [] } };
      if (state === "pending") {
        await act(async () => resolveProviders(emptyReply));
      } else {
        responses[providerEndpoint] = emptyReply;
        await userEvent.click(screen.getByRole("button", { name: "Retry" }));
      }
      expect(await screen.findByText("Not callable")).toBeInTheDocument();
      expect(
        screen.getByText("This model cannot be tried here"),
      ).toBeInTheDocument();
      expect(
        screen.getByRole("link", { name: "Go to Providers" }),
      ).toBeInTheDocument();
      expect(screen.queryByText("Provider access not verified")).toBeNull();
      expect(screen.queryByText("Could not verify your providers")).toBeNull();
      expect(api.post).not.toHaveBeenCalled();
    },
  );
});

const browse = {};
function BrowseProbe() {
  browse.location = useLocation();
  const navigate = useNavigate();
  const { model } = useParams();
  return (
    <>
      <button onClick={() => navigate(-1)}>History back</button>
      <button onClick={() => navigate(1)}>History forward</button>
      {model ? <div>Model detail destination</div> : <ModelCatalogPage />}
    </>
  );
}

const readyGrid = () => {
  let state = [
    { colId: "name", sort: "asc", sortIndex: 0 },
    { colId: "output", sort: "desc", sortIndex: 1 },
  ];
  const gridApi = {
    getColumnState: () => state,
    applyColumnState: vi.fn((next) => {
      if (next.defaultState?.sort === null) state = next.state;
    }),
    getVerticalPixelRange: () => ({ top: 156, bottom: 500 }),
    getDisplayedRowCount: () => grid.props.rowData.length,
    ensureIndexVisible: vi.fn(),
  };
  act(() => grid.props.onGridReady({ api: gridApi }));
  return gridApi;
};

describe("shareable model shortlists", () => {
  it("offers presets on narrow screens and clears previous multi-sort without changing layout", async () => {
    grid.compact = true;
    const { location } = renderPage();
    await waitForRows();
    const gridApi = readyGrid();
    const select = screen.getByRole("combobox", { name: "Sort models" });
    for (const [label, value, expected] of [
      ["Newest", "newest", [{ colId: "released", sort: "desc", sortIndex: 0 }]],
      [
        "Input price: low to high",
        "price_asc",
        [{ colId: "input", sort: "asc", sortIndex: 0 }],
      ],
      [
        "Input price: high to low",
        "price_desc",
        [{ colId: "input", sort: "desc", sortIndex: 0 }],
      ],
      [
        "Largest context",
        "context_desc",
        [{ colId: "context", sort: "desc", sortIndex: 0 }],
      ],
      [
        "Usable first",
        "usable",
        [
          { colId: "accessLabel", sort: "asc", sortIndex: 0 },
          { colId: "released", sort: "desc", sortIndex: 1 },
        ],
      ],
    ]) {
      expect(
        within(select).getByRole("option", { name: label }),
      ).toBeInTheDocument();
      await userEvent.selectOptions(select, value);
      expect(gridApi.applyColumnState).toHaveBeenLastCalledWith({
        state: expected,
        defaultState: { sort: null, sortIndex: null },
      });
      expect(new URLSearchParams(location.search).get("sort")).toBe(value);
    }
    await userEvent.type(
      screen.getByRole("textbox", { name: "Search models" }),
      "google",
    );
    expect(screen.queryByRole("region", { name: "Filters" })).toBeNull();
    await userEvent.click(
      screen.getByRole("button", { name: "Clear filters" }),
    );
    expect(visibleIds()).toHaveLength(11);
  });

  it("reloads all query choices and removes one chip without resetting the others", async () => {
    const url =
      "/dashboard/gateway/models?q=google&provider=google&provider=openai&capability=tools&capability=vision&context=128000&price=2&sort=context_desc";
    const page = renderPage(undefined, <ModelCatalogPage />, url);
    await waitForRows();
    expect(screen.getByRole("textbox", { name: "Search models" })).toHaveValue(
      "google",
    );
    expect(screen.getByRole("combobox", { name: "Sort models" })).toHaveValue(
      "context_desc",
    );
    await userEvent.click(
      screen.getByRole("button", { name: "Remove Provider: Google" }),
    );
    const query = new URLSearchParams(page.location.search);
    expect(query.getAll("provider")).toEqual(["openai"]);
    expect(query.getAll("capability")).toEqual(["tools", "vision"]);
    expect(query.get("q")).toBe("google");
    expect(query.get("context")).toBe("128000");
    expect(query.get("price")).toBe("2");
    expect(query.get("sort")).toBe("context_desc");
    const reloaded = page.location.pathname + page.location.search;
    page.unmount();
    renderPage(undefined, <ModelCatalogPage />, reloaded);
    expect(screen.getByRole("combobox", { name: "Sort models" })).toHaveValue(
      "context_desc",
    );
    expect(
      await screen.findByRole("button", { name: "Remove Provider: OpenAI" }),
    ).toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "Remove Provider: Google" }),
    ).toBeNull();
  });

  it("follows browser back/forward for filters and sort", async () => {
    renderPage(undefined, <BrowseProbe />);
    await waitForRows();
    await userEvent.click(option("Provider", "Google"));
    await userEvent.selectOptions(
      screen.getByRole("combobox", { name: "Sort models" }),
      "newest",
    );
    await userEvent.click(screen.getByRole("button", { name: "History back" }));
    expect(option("Provider", "Google")).toBeChecked();
    expect(screen.getByRole("combobox", { name: "Sort models" })).toHaveValue(
      "usable",
    );
    await userEvent.click(screen.getByRole("button", { name: "History back" }));
    expect(option("Provider", "Google")).not.toBeChecked();
    await userEvent.click(
      screen.getByRole("button", { name: "History forward" }),
    );
    expect(option("Provider", "Google")).toBeChecked();
  });

  it.each(["row", "link"])(
    "carries exact query/scroll through %s navigation and restores an approximate row",
    async (method) => {
      grid.renderCells = method === "link";
      const search = "?sort=newest&q=+&type=chat";
      renderPage(
        undefined,
        <BrowseProbe />,
        `/dashboard/gateway/models${search}`,
      );
      await waitForRows();
      readyGrid();
      const row = grid.props.rowData[0];
      if (method === "row") act(() => grid.props.onRowClicked({ data: row }));
      else await userEvent.click(screen.getByRole("link", { name: row.name }));
      await screen.findByText("Model detail destination");
      expect(browse.location.state.gatewayModelBrowse).toEqual({
        pathname: "/dashboard/gateway/models",
        search,
        scrollTop: 156,
      });
      await userEvent.click(
        screen.getByRole("button", { name: "History back" }),
      );
      await waitForRows();
      expect(browse.location.search).toBe(search);
      const restored = readyGrid();
      act(() => grid.props.onFirstDataRendered({ api: restored }));
      expect(restored.ensureIndexVisible).toHaveBeenCalledWith(3, "top");
    },
  );

  it("writes advanced header sorting to the URL without resetting widths or visibility", async () => {
    const { location } = renderPage();
    await waitForRows();
    const gridApi = readyGrid();
    gridApi.getColumnState = () => [
      { colId: "name", sort: "desc", sortIndex: 0, width: 300 },
      { colId: "context", sort: "asc", sortIndex: 1, hide: false },
    ];
    act(() =>
      grid.props.onSortChanged({ api: gridApi, source: "uiColumnSorted" }),
    );
    expect(new URLSearchParams(location.search).get("sort")).toBe(
      "name:desc,context:asc",
    );
    expect(screen.getByRole("combobox", { name: "Sort models" })).toHaveValue(
      "custom",
    );
    expect(
      JSON.parse(
        window.localStorage.getItem("gateway-grid:gateway-models-catalog-v3"),
      )[0].width,
    ).toBe(300);
  });
});

// SC2 ports of ModelAvailability.test.jsx: the same offers, through the v2 route.
describe("credit availability in the v2 catalogue", () => {
  // As in ModelAvailability.test.jsx: keep the
  // omitted/null/legacy fixtures exact, without inheriting a priced model.
  it.each([
    ["omitted prices", {}],
    [
      "null prices",
      { prices: null, input_per_token: null, output_per_token: null },
    ],
    [
      "legacy raw prices and tiers",
      {
        input_per_token: 0.000003,
        output_per_token: 0.000015,
        cache_read_per_token: 0.000001,
        input_per_mtok: 3,
        output_per_mtok: 15,
        cache_read_per_mtok: 1,
        billed_input_per_mtok: 3.15,
        billed_output_per_mtok: 15.75,
        prices: {
          prompt: 0.000003,
          completion: 0.000015,
          cache_read: 0.000001,
          cache_write: 0.000004,
          request: 0.04,
        },
        price_tiers: [
          { min_prompt_tokens: 100000, prices: { prompt: 0.000006 } },
        ],
      },
    ],
    ["legacy request price", { prices: { request: 0.04 } }],
  ])("never renders a blocked model's %s", async (_label, prices) => {
    const reason =
      "Not available on Future AGI credits: supported pricing is unavailable. Use your own provider key.";
    const model = {
      id: "acme/blocked",
      name: "Acme: Blocked model",
      vendor: "acme",
      state: "blocked",
      state_reason: reason,
      ...prices,
    };
    grid.renderCells = true;
    const { location } = renderPage(
      demoResponses(endpoints, {
        [endpoints.gateway.managedModelsCatalog]: {
          status: true,
          result: { ...CATALOG, models: [model] },
        },
        [endpoints.gateway.providers("default")]: {
          status: true,
          result: { providers: [] },
        },
      }),
      <CatalogAndDetail />,
    );
    await waitForRows();
    expect(
      api.get.mock.calls.filter(
        ([url]) => url === endpoints.gateway.managedModelsCatalog,
      ).length,
    ).toBeGreaterThan(0);
    expect(grid.props.rowData).toHaveLength(1);
    expect(grid.props.rowData[0]).toMatchObject({
      id: model.id,
      input: null,
      output: null,
      requestPrice: null,
      onCredits: false,
      access: "none",
      accessReason: reason,
    });
    const row = screen.getByRole("row", { name: /Blocked model/ });
    // v2 renders unavailable token prices as dashes, not the old "Unavailable".
    const cells = within(row).getAllByRole("gridcell");
    for (const field of ["input", "output"]) {
      expect(
        cells.find((cell) => cell.dataset.col === field),
      ).toHaveTextContent(/^—$/);
    }
    expect(
      within(row).getByText("Not available on credits"),
    ).toBeInTheDocument();
    expect(row).not.toHaveTextContent(/\$\d|\bFree\b/);
    expect(count("Price", "Free", "radio")).toBe(0);

    await userEvent.click(row);
    expect(
      await screen.findByRole("heading", { level: 1, name: "Blocked model" }),
    ).toBeInTheDocument();
    expect(location.pathname).toBe("/dashboard/gateway/models/acme/blocked");
    const pricing = screen.getByRole("region", { name: "Pricing" });
    expect(within(pricing).getByText(reason)).toBeInTheDocument();
    // Preserve the retired drawer's price-omission assertions on v2's Pricing
    // region. Soft assertions expose all leaked prices without skipping API.
    expect.soft(within(pricing).queryByTestId("ag-grid")).toBeNull();
    expect.soft(pricing).not.toHaveTextContent(/\$\d|\bFree\b/);
    expect(screen.queryByText("Free")).toBeNull();
    await userEvent.click(
      screen.getByRole("tab", { name: "API", exact: true }),
    );
    expect(
      await screen.findByText("No request to copy for this model"),
    ).toBeInTheDocument();
    expect(screen.queryByText("Call this model")).toBeNull();
    expect(screen.queryByRole("tab", { name: "cURL" })).toBeNull();
    expect(api.post).not.toHaveBeenCalled();
  });

  it("marks a blocked offer, blanks both prices and never calls it Free", async () => {
    const reason =
      "Not available on Future AGI credits: supported pricing is unavailable. Use your own provider key.";
    const model = {
      ...CATALOG.models[0],
      id: "acme/unpriced-generation",
      name: "Acme: Unpriced generation",
      state: "blocked",
      state_reason: reason,
      billed_input_per_mtok: 0,
      billed_output_per_mtok: 0,
    };
    renderPage(
      demoResponses(endpoints, {
        [endpoints.gateway.managedModelsCatalog]: {
          status: true,
          result: { ...CATALOG, models: [model] },
        },
        [endpoints.gateway.providers("default")]: {
          status: true,
          result: { providers: [] },
        },
      }),
    );
    await waitForRows();
    const row = grid.props.rowData.find((item) => item.id === model.id);
    expect(row).toMatchObject({
      input: null,
      output: null,
      onCredits: false,
      accessReason: reason,
    });
    expect(
      column("input").valueFormatter({ value: row.input, data: row }),
    ).toBe("—");
    expect(
      column("output").valueFormatter({ value: row.output, data: row }),
    ).toBe("—");
    const cell = render(
      <MemoryRouter>
        <ModelCell data={row} />
      </MemoryRouter>,
    );
    expect(
      within(cell.container).getByText("Not available on credits"),
    ).toBeInTheDocument();
    expect(within(cell.container).queryByText("Free")).toBeNull();
  });

  it("keeps token rates when an offer also charges a request fee", async () => {
    const model = {
      ...CATALOG.models[0],
      id: "acme/mixed-price",
      name: "Acme: Mixed price",
      state: "active",
      dynamic_price: false,
      unpriced: false,
      stale_price: false,
      input_per_mtok: 3,
      output_per_mtok: 15,
      billed_input_per_mtok: 3.15,
      billed_output_per_mtok: 15.75,
      prices: { prompt: 0.000003, completion: 0.000015, request: 0.04 },
    };
    renderPage(
      demoResponses(endpoints, {
        [endpoints.gateway.managedModelsCatalog]: {
          status: true,
          result: { ...CATALOG, models: [model] },
        },
      }),
    );
    await waitForRows();
    const row = grid.props.rowData.find((item) => item.id === model.id);
    expect(
      column("input").valueFormatter({ value: row.input, data: row }),
    ).toBe("$3.15");
    expect(
      column("output").valueFormatter({ value: row.output, data: row }),
    ).toBe("$15.75");
  });
});

// The filter panel: a sticky column that scrolls by itself, with its
// title and Clear filters kept at the top, groups that fold from their
// heading, and a drawer on narrow screens.
describe("filter panel", () => {
  it("is a sticky column beside the grid that scrolls by itself", async () => {
    renderPage();
    await waitForRows();
    const panel = screen
      .getByRole("region", { name: "Filters" })
      .closest('[data-testid="catalog-filter-panel"]');
    expect(panel).not.toBeNull();
    expect(panel).toHaveStyle({ position: "sticky", overflowY: "auto" });
    // The title row stays on top of the groups scrolling under it, above an
    // outlined field's label (z-index 1).
    const title = screen.getByRole("heading", { level: 2, name: "Filters" });
    expect(title.parentElement).toHaveStyle({
      position: "sticky",
      zIndex: "3",
    });
  });

  it("folds a group from its heading and says how many options are on", async () => {
    renderPage();
    await waitForRows();
    const heading = within(facet("Provider")).getByRole("button", {
      name: "Provider",
    });
    expect(heading).toHaveAttribute("aria-expanded", "true");
    await userEvent.click(option("Provider", "OpenAI"));
    // The count of options on sits beside the name, outside the group's name.
    expect(within(heading).getByText("1")).toBeInTheDocument();
    expect(heading).toHaveAccessibleName("Provider (1 selected)");
    expect(facet("Provider")).toBeInTheDocument();
    await userEvent.click(heading);
    expect(heading).toHaveAttribute("aria-expanded", "false");
    expect(
      within(facet("Provider")).queryByRole("checkbox", { name: /^OpenAI/ }),
    ).toBeNull();
    await userEvent.click(heading);
    expect(option("Provider", "OpenAI")).toBeChecked();
  });

  it("opens as a drawer on narrow screens and closes onto the filtered list", async () => {
    grid.compact = true;
    renderPage();
    await waitForRows();
    expect(screen.queryByRole("region", { name: "Filters" })).toBeNull();
    await userEvent.click(screen.getByRole("button", { name: "Filters" }));
    expect(
      await screen.findByRole("region", { name: "Filters" }),
    ).toBeInTheDocument();
    expect(screen.getByRole("dialog", { name: "Filters" })).toBeInTheDocument();
    await userEvent.click(option("Type", "Embeddings"));
    await userEvent.click(screen.getByRole("button", { name: "Show 1 model" }));
    await waitFor(() =>
      expect(screen.queryByRole("region", { name: "Filters" })).toBeNull(),
    );
    expect(visibleIds()).toEqual(["openai/text-embedding-3-small"]);
    await userEvent.click(screen.getByRole("button", { name: /^Filters/ }));
    await userEvent.click(
      await screen.findByRole("button", { name: "Close filters" }),
    );
    await waitFor(() =>
      expect(screen.queryByRole("region", { name: "Filters" })).toBeNull(),
    );
  });
});
