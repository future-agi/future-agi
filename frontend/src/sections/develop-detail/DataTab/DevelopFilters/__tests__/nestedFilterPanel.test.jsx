import React from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "src/utils/test-utils";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import axios, { endpoints } from "src/utils/axios";
import { getDatasetQueryOptions } from "src/api/develop/develop-detail";
import { fetchRowInView } from "src/api/develop/row-view";
import { formatPath } from "src/sections/common/Values/faPath";
import {
  pathFilterEntry,
  splitColumnPath,
} from "src/sections/common/Values/valueActions";
import { useDevelopFilterStore } from "src/sections/develop-detail/states";
import {
  getDataGridFilters,
  useDataGridViewStore,
} from "../../DataGrid/useDataGridView";
import { gridNavQuery } from "../../DatapointDrawerV2/gridNavQuery";
import DevelopFilterBox from "../DevelopFilterBox";
import valueActions from "../../../../../../../futureagi/model_hub/tests/fixtures/nested_value_action_cases.json";

const h = vi.hoisted(() => ({
  onFilterChanged: vi.fn(),
  columns: [
    {
      field: "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
      headerName: "metadata",
      col: {
        id: "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
        name: "metadata",
        data_type: "json",
      },
    },
    {
      field: "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
      headerName: "threshold",
      col: {
        id: "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
        name: "threshold",
        data_type: "float",
      },
    },
  ],
  schemas: {}, // No discovered nested property: exercise the string fallback.
  values: { data: [], isLoading: false, isError: false },
  catalog: {
    metrics: [],
    legacyFallbackRequired: false,
    queryReadState: "complete",
    isLoading: false,
    isFetching: false,
    isError: false,
    isSuccess: true,
    error: null,
    hasNextPage: false,
    data: { pages: [] },
  },
}));

vi.mock("src/utils/axios", async (importOriginal) => {
  const real = await importOriginal();
  return {
    ...real,
    default: { ...real.default, get: vi.fn(), post: vi.fn() },
  };
});
vi.mock("src/sections/develop-detail/DataTab/common", () => ({
  getColumnConfig: vi.fn(),
}));
vi.mock("src/api/develop/develop-detail", async (importOriginal) => ({
  ...(await importOriginal()),
  useDatasetColumnConfig: () => h.columns,
  useGetJsonColumnSchema: () => ({ data: h.schemas }),
}));
vi.mock("src/routes/hooks", () => ({
  useParams: () => ({ dataset: "11111111-1111-4111-8111-111111111111" }),
}));
vi.mock("src/sections/develop-detail/Context/DevelopDetailContext", () => ({
  useDevelopDetailContext: () => ({
    gridApi: { current: { onFilterChanged: h.onFilterChanged } },
  }),
}));
vi.mock("src/api/develop/row-collab", () => ({
  useAssignments: () => ({ data: null }),
  useTranslateInfo: () => ({ data: null }),
}));
vi.mock("src/hooks/useDashboards", async (importOriginal) => ({
  ...(await importOriginal()),
  useDatasetColumnValues: () => h.values,
  useDashboardFilterValues: () => h.values,
  usePropertyCatalog: () => h.catalog,
}));
vi.mock("src/hooks/use-ai-filter", () => ({
  useAIFilter: () => ({ parseQuery: vi.fn(), loading: false, error: null }),
}));
vi.mock("src/utils/prototype", async (importOriginal) => ({
  ...(await importOriginal()),
  isPrototypeMode: () => false,
}));
vi.mock("src/components/iconify", () => ({ default: () => null }));

const DATASET = "11111111-1111-4111-8111-111111111111";
const CLICKED = "22222222-2222-4222-8222-222222222221";
const OTHER = "22222222-2222-4222-8222-222222222223";
let queryClient;

beforeEach(() => {
  queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: 0 } },
  });
  useDevelopFilterStore.setState({ filters: [], isDevelopFilterOpen: false });
  useDataGridViewStore.setState({ views: {} });
  h.onFilterChanged.mockReset();
  axios.get.mockReset();
  axios.post.mockReset();
});

afterEach(() => {
  cleanup();
  queryClient.clear();
  useDevelopFilterStore.getState().resetFilters();
  useDataGridViewStore.setState({ views: {} });
});

// Transport fixtures only: Django tests evaluate the same shared requests
// against stored 12.0, "12.0", another numeric row, and an unrelated filter.
async function assertReaders(expectedFilters, expectedIds) {
  const filters = useDevelopFilterStore.getState().filters;
  const tableFilters = getDataGridFilters(DATASET);
  const nav = gridNavQuery({ filters });
  expect(tableFilters).toEqual(expectedFilters);
  expect(nav.filters).toEqual(expectedFilters);
  const population = expectedIds.map((rowId) => ({ row_id: rowId }));
  axios.get.mockImplementation(async (url, { params }) => {
    if (url === endpoints.develop.getDatasetDetail(DATASET)) {
      expect(JSON.parse(params.filters)).toEqual(expectedFilters);
      return {
        data: {
          result: {
            table: population.slice(0, params.page_size),
            metadata: { total_rows: expectedIds.length },
          },
        },
      };
    }
    expect(url).toBe(`${endpoints.develop.splits.list(DATASET)}membership/`);
    expect(params.row_id).toBe(CLICKED);
    return { data: { result: { split_ids: [] } } };
  });
  axios.post.mockImplementation(async (url, body) => {
    expect(url).toBe(endpoints.develop.getRowData(DATASET));
    expect(body.filters).toEqual(expectedFilters);
    expect(body.row_id).toBe(CLICKED);
    return {
      data: {
        result: {
          current: population[0],
          next: { row_id: expectedIds.slice(1) },
        },
      },
    };
  });
  const table = await getDatasetQueryOptions(
    DATASET,
    0,
    tableFilters,
    [],
    "",
  ).queryFn();
  expect(table.data.result.table.map((row) => row.row_id)).toEqual(expectedIds);
  expect(table.data.result.metadata.total_rows).toBe(expectedIds.length);
  const row = await fetchRowInView(DATASET, CLICKED, nav);
  expect(row.current.row_id).toBe(CLICKED);
  expect(row.next).toEqual(expectedIds.slice(1));
  expect(row.outside).toBe(false);
  const count = await getDatasetQueryOptions(DATASET, 0, tableFilters, [], "", {
    pageSize: 1,
  }).queryFn();
  expect(count.data.result.table.map((item) => item.row_id)).toEqual([CLICKED]);
  expect(count.data.result.metadata.total_rows).toBe(expectedIds.length);
}

describe("nested value action filter panel round trip", () => {
  it("retains the clicked float after opening the panel and editing another filter", async () => {
    const sample = valueActions.cases.find(
      (item) => item.name === "integral float",
    );
    const fullPath = formatPath(
      "metadata",
      sample.keys.map((key) => ({ type: "key", key })),
    );
    const { rel } = splitColumnPath(fullPath);
    const unrelated = valueActions.panel_roundtrip.before[1];
    useDevelopFilterStore.setState({
      filters: [
        pathFilterEntry(valueActions.column_id, rel, sample.value, "clicked"),
        {
          id: "threshold",
          columnId: unrelated.column_id,
          registryId: unrelated.property_id,
          filterConfig: {
            filterType: "number",
            filterOp: "greater_than",
            filterValue: 0,
          },
        },
      ],
    });
    await assertReaders(valueActions.panel_roundtrip.before, [CLICKED, OTHER]);
    render(
      <QueryClientProvider client={queryClient}>
        <button
          type="button"
          data-develop-filter-anchor
          onClick={() =>
            useDevelopFilterStore.getState().setDevelopFilterOpen(true)
          }
        >
          Open filters
        </button>
        <DevelopFilterBox />
      </QueryClientProvider>,
    );
    fireEvent.click(screen.getByRole("button", { name: "Open filters" }));
    const threshold = await screen.findByDisplayValue("0");
    expect(useDevelopFilterStore.getState().isDevelopFilterOpen).toBe(true);
    expect(h.onFilterChanged).toHaveBeenCalledTimes(0);
    // Do not touch the nested row. The real editor hydrates equals -> in and
    // auto-applies the whole set when this unrelated numeric input changes.
    fireEvent.change(threshold, { target: { value: "5" } });
    await waitFor(() => {
      expect(useDevelopFilterStore.getState().isDevelopFilterOpen).toBe(false);
    });
    expect(h.onFilterChanged).toHaveBeenCalledTimes(1);
    expect(useDevelopFilterStore.getState().filters[0].filterConfig).toEqual({
      filterType: "text",
      filterOp: "in",
      filterValue: ["12"],
    });
    await assertReaders(valueActions.panel_roundtrip.after, [CLICKED]);
  });
});
