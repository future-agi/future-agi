import React, { createRef } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "src/utils/test-utils";
import ObserveListView from "../ObserveListView";

const { readPage, tableRenders } = vi.hoisted(() => ({
  readPage: vi.fn(),
  tableRenders: vi.fn(),
}));

vi.mock("src/api/project/observe-project-list", () => ({
  readObserveProjectPage: readPage,
}));
// A short list has its project records read to spot the sign-up sample
// project; here every project is the user's own.
vi.mock("src/utils/axios", () => ({
  default: {
    get: async () => ({
      data: { status: true, result: { source: "prototype" } },
    }),
  },
  endpoints: {
    project: {
      projectExperimentDetail: (projectId) => `/tracer/project/${projectId}/`,
    },
  },
}));
/* eslint-disable react/prop-types */
vi.mock("../TagEditor", () => ({ default: () => null }));
vi.mock("../VolumeBarChart", () => ({ default: () => null }));

// Run the real shared DataTable; only its visual grid is mocked. Observe every
// rendered checkbox identity, including frames before selection reconciliation.
vi.mock("@mui/x-data-grid", () => {
  function DataGrid({
    rows,
    getRowId,
    rowSelectionModel,
    onRowSelectionModelChange,
    onSortModelChange,
    localeText,
  }) {
    tableRenders({
      ids: rows.map((row) => row.id),
      selectedIds: rows
        .filter((row) => rowSelectionModel.includes(getRowId(row)))
        .map((row) => row.id),
    });
    return React.createElement(
      "div",
      null,
      React.createElement(
        "button",
        {
          type: "button",
          onClick: () => onSortModelChange([{ field: "name", sort: "asc" }]),
        },
        "Sort by name",
      ),
      rows.length === 0
        ? React.createElement("p", null, localeText.noRowsLabel)
        : null,
      rows.map((row) =>
        React.createElement("input", {
          key: getRowId(row),
          type: "checkbox",
          "aria-label": row.name,
          checked: rowSelectionModel.includes(getRowId(row)),
          onChange: (event) => {
            const id = getRowId(row);
            onRowSelectionModelChange(
              event.target.checked
                ? [...rowSelectionModel, id]
                : rowSelectionModel.filter((selectedId) => selectedId !== id),
            );
          },
        }),
      ),
    );
  }
  return { DataGrid };
});

vi.mock("src/components/data-table", async () => {
  const { default: DataTable } = await import(
    "src/components/data-table/DataTable"
  );

  function DataTablePagination({
    page,
    pageSize,
    total,
    onPageChange,
    onPageSizeChange,
  }) {
    return React.createElement(
      "div",
      null,
      React.createElement("span", { "data-testid": "page" }, String(page)),
      React.createElement("span", { "data-testid": "total" }, String(total)),
      React.createElement(
        "button",
        {
          type: "button",
          disabled: page === 0,
          onClick: () => onPageChange(page - 1),
        },
        "Previous",
      ),
      React.createElement(
        "button",
        {
          type: "button",
          disabled: (page + 1) * pageSize >= total,
          onClick: () => onPageChange(page + 1),
        },
        "Next",
      ),
      React.createElement(
        "button",
        {
          type: "button",
          onClick: () => onPageSizeChange(50),
        },
        "Show 50",
      ),
    );
  }
  return { DataTable, DataTablePagination };
});
/* eslint-enable react/prop-types */

const makeProjects = (count) =>
  Array.from({ length: count }, (_, index) => ({
    id: `project-${index}`,
    name: `Project ${index}`,
    issues: 0,
    daily_volume: [],
  }));
const clients = [];
let projects;

function responseFor(params) {
  let population = projects;
  if (params.name)
    population = population.filter((row) => row.name.includes(params.name));
  // The filter regression uses a single restrictive project-name filter.
  if (params.filters)
    population = population.filter((row) => row.id === "project-25");
  if (params.sort_by === "name") {
    population = [...population].sort((a, b) => a.name.localeCompare(b.name));
  }
  return {
    rows: population.slice(
      params.page_number * params.page_size,
      (params.page_number + 1) * params.page_size,
    ),
    totalRows: population.length,
    totalPages: Math.ceil(population.length / params.page_size),
  };
}

function mountList(initialProps = {}) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: 0 } },
  });
  clients.push(client);
  const setSelectedRowsData = vi.fn();
  const ref = createRef();
  let props = initialProps;
  const element = () =>
    React.createElement(
      QueryClientProvider,
      { client },
      React.createElement(ObserveListView, {
        ...props,
        ref,
        setSelectedRowsData,
      }),
    );
  const view = render(element());
  return {
    client,
    ref,
    setSelectedRowsData,
    update(next) {
      props = { ...props, ...next };
      view.rerender(element());
    },
  };
}

async function nextPage() {
  const page = Number(screen.getByTestId("page").textContent);
  fireEvent.click(screen.getByRole("button", { name: "Next" }));
  await waitFor(() =>
    expect(screen.getByTestId("page")).toHaveTextContent(String(page + 1)),
  );
  await waitFor(() =>
    expect(
      screen.getByRole("checkbox", { name: `Project ${(page + 1) * 25}` }),
    ).toBeInTheDocument(),
  );
}

function selectProject(index, setSelectedRowsData) {
  fireEvent.click(screen.getByRole("checkbox", { name: `Project ${index}` }));
  expect(setSelectedRowsData).toHaveBeenLastCalledWith([`project-${index}`]);
}

function expectCleared(setSelectedRowsData) {
  expect(setSelectedRowsData).toHaveBeenLastCalledWith([]);
  screen
    .queryAllByRole("checkbox")
    .forEach((checkbox) => expect(checkbox).not.toBeChecked());
}

beforeEach(() => {
  projects = makeProjects(26);
  readPage.mockReset();
  tableRenders.mockClear();
  readPage.mockImplementation(async ({ params }) => responseFor(params));
});

afterEach(() => {
  cleanup();
  clients.splice(0).forEach((client) => client.clear());
});

describe("ObserveListView pagination and selection", () => {
  it("clears checkbox and deletion IDs on page changes before replacement rows arrive", async () => {
    let finishNextPage;
    readPage.mockImplementation(({ params }) =>
      params.page_number === 1
        ? new Promise((resolve) => {
            finishNextPage = () => resolve(responseFor(params));
          })
        : Promise.resolve(responseFor(params)),
    );
    const { setSelectedRowsData } = mountList();
    await screen.findByRole("checkbox", { name: "Project 0" });
    selectProject(0, setSelectedRowsData);

    fireEvent.click(screen.getByRole("button", { name: "Next" }));
    await waitFor(() => expect(finishNextPage).toEqual(expect.any(Function)));
    // keepPreviousData still shows page one's rows while the request is pending.
    expect(
      screen.getByRole("checkbox", { name: "Project 0" }),
    ).toBeInTheDocument();
    expectCleared(setSelectedRowsData);

    // Even a new selection made on placeholder rows cannot move to another ID.
    selectProject(0, setSelectedRowsData);
    tableRenders.mockClear();
    await act(async () => {
      finishNextPage();
    });
    await screen.findByRole("checkbox", { name: "Project 25" });
    await waitFor(() => expectCleared(setSelectedRowsData));
    expect(
      tableRenders.mock.calls.some(([frame]) =>
        frame.selectedIds.includes("project-25"),
      ),
    ).toBe(false);
  });

  it.each(["replacement", "reorder"])(
    "reconciles both selections on returned row-ID %s without transferring the checkbox",
    async (change) => {
      projects = makeProjects(2);
      const { client, setSelectedRowsData } = mountList();
      await screen.findByRole("checkbox", { name: "Project 0" });
      selectProject(0, setSelectedRowsData);
      tableRenders.mockClear();
      const rows =
        change === "reorder" ? [...projects].reverse() : [projects[1]];

      act(() => {
        client.setQueriesData(
          { queryKey: ["observe-projects"] },
          {
            rows,
            totalRows: rows.length,
            totalPages: 1,
          },
        );
      });

      await waitFor(() =>
        expect(tableRenders).toHaveBeenLastCalledWith(
          expect.objectContaining({ ids: rows.map((row) => row.id) }),
        ),
      );
      await waitFor(() => expectCleared(setSelectedRowsData));
      expect(
        tableRenders.mock.calls.some(([frame]) =>
          frame.selectedIds.includes("project-1"),
        ),
      ).toBe(false);
    },
  );

  it.each(["search", "filter"])(
    "resets %s to page zero and clears both selections",
    async (change) => {
      const { update, setSelectedRowsData } = mountList();
      await screen.findByRole("checkbox", { name: "Project 0" });
      await nextPage();
      selectProject(25, setSelectedRowsData);

      update(
        change === "search"
          ? { searchQuery: "Project 25" }
          : {
              filters: [
                { field: "name", operator: "contains", value: "Project 25" },
              ],
            },
      );

      await waitFor(() =>
        expect(readPage).toHaveBeenCalledWith(
          expect.objectContaining({
            params: expect.objectContaining({
              page_number: 0,
              ...(change === "search"
                ? { name: "Project 25" }
                : { filters: expect.any(String) }),
            }),
          }),
        ),
      );
      await waitFor(() =>
        expect(screen.getByTestId("total")).toHaveTextContent("1"),
      );
      expect(screen.getByTestId("page")).toHaveTextContent("0");
      expect(
        screen.getByRole("checkbox", { name: "Project 25" }),
      ).toBeInTheDocument();
      expect(screen.getAllByRole("checkbox")).toHaveLength(1);
      expectCleared(setSelectedRowsData);
    },
  );

  it.each(["sort", "page size"])(
    "clears both selections on a %s change even when row IDs stay the same",
    async (change) => {
      projects = makeProjects(2);
      const { setSelectedRowsData } = mountList();
      await screen.findByRole("checkbox", { name: "Project 0" });
      selectProject(0, setSelectedRowsData);

      fireEvent.click(
        screen.getByRole("button", {
          name: change === "sort" ? "Sort by name" : "Show 50",
        }),
      );

      await waitFor(() =>
        expect(readPage).toHaveBeenCalledWith(
          expect.objectContaining({
            params: expect.objectContaining(
              change === "sort" ? { sort_by: "name" } : { page_size: 50 },
            ),
          }),
        ),
      );
      expectCleared(setSelectedRowsData);
    },
  );

  it("clamps and refetches the last remaining page after deleting the final later-page row", async () => {
    projects = makeProjects(51);
    const { client, setSelectedRowsData } = mountList();
    await screen.findByRole("checkbox", { name: "Project 0" });
    await nextPage();
    await nextPage();
    selectProject(50, setSelectedRowsData);
    projects = projects.slice(0, 50);
    readPage.mockClear();

    await act(async () => {
      await client.invalidateQueries({ queryKey: ["observe-projects"] });
    });

    await waitFor(() =>
      expect(screen.getByTestId("page")).toHaveTextContent("1"),
    );
    await screen.findByRole("checkbox", { name: "Project 25" });
    expect(readPage).toHaveBeenCalledWith(
      expect.objectContaining({
        params: expect.objectContaining({ page_number: 1 }),
      }),
    );
    expect(screen.getAllByRole("checkbox")).toHaveLength(25);
    expectCleared(setSelectedRowsData);
  });

  it("does not clamp from an undersized cached page while its refetch is in flight", async () => {
    // Keep the empty cached page in the list, not the first-run view.
    // Every project still matches this search.
    const { client } = mountList({ searchQuery: "Project" });
    client.setQueryDefaults(["observe-projects"], { gcTime: Infinity });
    await screen.findByRole("checkbox", { name: "Project 0" });
    await nextPage();
    fireEvent.click(screen.getByRole("button", { name: "Previous" }));
    await screen.findByRole("checkbox", { name: "Project 0" });

    const laterPage = client
      .getQueryCache()
      .findAll({ queryKey: ["observe-projects"] })
      .find((query) => query.queryKey[1].page === 1);
    act(() => {
      client.setQueryData(
        laterPage.queryKey,
        { rows: [], totalRows: 0, totalPages: 0 },
        { updatedAt: 1 },
      );
    });
    let finishPage;
    readPage.mockImplementation(({ params }) =>
      params.page_number === 1
        ? new Promise((resolve) => {
            finishPage = () => resolve(responseFor(params));
          })
        : Promise.resolve(responseFor(params)),
    );
    fireEvent.click(screen.getByRole("button", { name: "Next" }));
    await waitFor(() => expect(finishPage).toEqual(expect.any(Function)));
    expect(screen.getByTestId("total")).toHaveTextContent("0");
    expect(screen.getByTestId("page")).toHaveTextContent("1");

    await act(async () => finishPage());
    await screen.findByRole("checkbox", { name: "Project 25" });
    expect(screen.getByTestId("page")).toHaveTextContent("1");
  });

  it("returns to page zero when deletion removes every matching project", async () => {
    const { client, setSelectedRowsData } = mountList({
      searchQuery: "Project",
    });
    await screen.findByRole("checkbox", { name: "Project 0" });
    await nextPage();
    selectProject(25, setSelectedRowsData);
    projects = [];
    readPage.mockClear();

    await act(async () => {
      await client.invalidateQueries({ queryKey: ["observe-projects"] });
    });

    await waitFor(() =>
      expect(screen.getByTestId("page")).toHaveTextContent("0"),
    );
    expect(readPage).toHaveBeenCalledWith(
      expect.objectContaining({
        params: expect.objectContaining({ page_number: 0 }),
      }),
    );
    expect(screen.getByText("No projects found")).toBeInTheDocument();
    expectCleared(setSelectedRowsData);
  });

  it("clears parent deletion IDs through the imperative clearSelection handle", async () => {
    const { ref, setSelectedRowsData } = mountList();
    await screen.findByRole("checkbox", { name: "Project 0" });
    selectProject(0, setSelectedRowsData);

    act(() => ref.current.clearSelection());

    expectCleared(setSelectedRowsData);
  });

  it("preserves selection across a refresh with the same ordered project IDs", async () => {
    const { client, setSelectedRowsData } = mountList();
    await screen.findByRole("checkbox", { name: "Project 0" });
    selectProject(0, setSelectedRowsData);
    projects = projects.map((row) => ({ ...row, issues: 2 }));

    await act(async () => {
      await client.invalidateQueries({ queryKey: ["observe-projects"] });
    });

    expect(screen.getByRole("checkbox", { name: "Project 0" })).toBeChecked();
    expect(setSelectedRowsData).toHaveBeenLastCalledWith(["project-0"]);
  });

  it("preserves later-page selection when equivalent filters are recreated", async () => {
    const { update, setSelectedRowsData } = mountList({ filters: [] });
    await screen.findByRole("checkbox", { name: "Project 0" });
    await nextPage();
    selectProject(25, setSelectedRowsData);
    readPage.mockClear();

    update({ filters: [] });

    expect(screen.getByTestId("page")).toHaveTextContent("1");
    expect(screen.getByRole("checkbox", { name: "Project 25" })).toBeChecked();
    expect(setSelectedRowsData).toHaveBeenLastCalledWith(["project-25"]);
    expect(readPage).not.toHaveBeenCalled();
  });
});
