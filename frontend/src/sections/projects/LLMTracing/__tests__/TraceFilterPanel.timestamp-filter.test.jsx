import { beforeEach, describe, expect, it, vi } from "vitest";
import {
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "src/utils/test-utils";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import TraceFilterPanel from "../TraceFilterPanel";
import {
  apiFiltersToPanelRows,
  panelRowsToApiFilters,
  TIMESTAMP_FILTER_FIELD,
} from "../../observeFilterConfig";

// The Traces list and the Spans view (Group by
// Span) offer the rows' Timestamp in the Basic and Query filters, with date
// operators and the date + time input, and a saved Timestamp filter reopens
// as the local date and time it was set to. The panel gets the field the way
// the Observe toolbar hands it over (ObserveToolbar.timestamp-filter pins
// that). Catalog rows below are synthetic copies of the backend manifest
// (source_adapters.py: traces/spans start_time "Timestamp" datetime, traces
// time_to_first_token number); no request leaves the test. No fixture here
// carries an icon of its own.

const propertyCatalogMock = vi.hoisted(() => vi.fn());
const dashboardFilterValuesMock = vi.hoisted(() => vi.fn());
const exactAttributePropertiesMock = vi.hoisted(() => vi.fn());

vi.mock("src/hooks/useDashboards", async (importOriginal) => ({
  ...(await importOriginal()),
  useDashboardFilterValues: dashboardFilterValuesMock,
  usePropertyCatalog: propertyCatalogMock,
}));

vi.mock("src/hooks/use-ai-filter", async (importOriginal) => ({
  ...(await importOriginal()),
  useAIFilter: () => ({ parseQuery: vi.fn(), loading: false, error: null }),
}));

vi.mock("../useExactTraceAttributeProperties", async (importOriginal) => ({
  ...(await importOriginal()),
  useExactTraceAttributeProperties: exactAttributePropertiesMock,
}));

const systemMetric = (source, name, display_name, type) => ({
  name,
  display_name,
  type,
  category: "system_metric",
  source,
  sources: ["system", source, name],
  property_id: `system_attribute:${source}:${name}`,
});

const TRACE_METRICS = [
  systemMetric("traces", "model", "Model", "string"),
  systemMetric("traces", "start_time", "Timestamp", "datetime"),
  systemMetric(
    "traces",
    "time_to_first_token",
    "Time to First Token",
    "number",
  ),
];
// The catalog's spans source also lists the trace definitions.
const SPAN_METRICS = [
  ...TRACE_METRICS,
  systemMetric("spans", "start_time", "Timestamp", "datetime"),
];

const catalogFor =
  (metrics) =>
  ({ category = "", search = "" }) => {
    const matching = metrics.filter(
      ({ name, display_name }) =>
        !search || name.includes(search) || display_name.includes(search),
    );
    return {
      metrics: matching.filter(
        (metric) => !category || metric.category === category,
      ),
      categoryCounts: {
        all: matching.length,
        system_metric: matching.length,
        eval_metric: 0,
        annotation_metric: 0,
        custom_attribute: 0,
        custom_column: 0,
      },
      categoryCountsExact: true,
      queryProvenance: "current_property_catalog",
      legacyFallbackRequired: false,
      error: null,
      isLoading: false,
      isFetching: false,
      isRemoteCatalogSearchPending: false,
      isRemoteCatalogNextPagePending: false,
      isError: false,
      isSuccess: true,
      hasNextPage: false,
      continuationKey: null,
      isFetchingNextPage: false,
      isFetchNextPageError: false,
      cursorChainStopped: false,
      fetchNextPage: vi.fn(),
      data: { pages: [] },
    };
  };

const SURFACES = [
  ["Traces", { tab: "trace", isSpansView: false }, TRACE_METRICS],
  ["Spans", { tab: "spans", isSpansView: true }, SPAN_METRICS],
];

const TIMESTAMP_OPERATORS = [
  "before",
  "after",
  "on or after",
  "on or before",
  "between",
  "not between",
];

const ZONED = (localDateTime) =>
  new RegExp(`^${localDateTime}:00[+-]\\d{2}:\\d{2}$`);

function renderPanel({ currentFilters = [], showQueryTab = false, ...props }) {
  const anchorEl = document.createElement("button");
  document.body.appendChild(anchorEl);
  const onApply = vi.fn();
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  const utils = render(
    <QueryClientProvider client={queryClient}>
      <TraceFilterPanel
        anchorEl={anchorEl}
        open
        onClose={vi.fn()}
        onApply={onApply}
        currentFilters={currentFilters}
        showQueryTab={showQueryTab}
        projectId="timestamp-project"
        source="traces"
        propertyNamespace="traces"
        filterFields={
          TIMESTAMP_FILTER_FIELD ? [TIMESTAMP_FILTER_FIELD] : undefined
        }
        {...props}
      />
    </QueryClientProvider>,
  );
  return { anchorEl, onApply, ...utils };
}

const timestampOptions = () =>
  document.querySelectorAll('[data-filter-property-option="start_time"]');

beforeEach(() => {
  propertyCatalogMock.mockReset();
  globalThis.IntersectionObserver = class IntersectionObserver {
    observe() {}
    unobserve() {}
    disconnect() {}
  };
  dashboardFilterValuesMock.mockReturnValue({
    data: [],
    isLoading: false,
    isError: false,
    queryReadState: "complete",
    fetchNextPage: vi.fn(),
    hasNextPage: false,
    isFetchingNextPage: false,
    isFetchNextPageError: false,
    continuationKey: null,
    refetch: vi.fn(),
  });
  exactAttributePropertiesMock.mockReturnValue({
    data: [],
    isFetching: false,
    fetchNextPage: vi.fn(),
    hasNextPage: false,
    isFetchingNextPage: false,
    fetchNextExactPage: vi.fn(),
    hasNextExactPage: false,
    isFetchingExactSearch: false,
    isFetchingNextExactPage: false,
    isFetchNextPageError: false,
    queryReadState: "complete",
    browseStatus: "exhausted",
    totalCount: 0,
    pageCount: 1,
    exactSearchMatched: false,
    cursorRetryExhausted: false,
    continuationKey: null,
    debouncedSearch: "",
    refetch: vi.fn(),
  });
});

describe("filter traces and spans by Timestamp", () => {
  it.each(SURFACES)(
    "%s: the Basic picker offers Timestamp once and applies a local date-time bound",
    async (_surface, surfaceProps, metrics) => {
      propertyCatalogMock.mockImplementation(catalogFor(metrics));
      const { anchorEl, onApply } = renderPanel(surfaceProps);

      fireEvent.click(
        screen.getByRole("button", { name: "Property", exact: true }),
      );
      expect(timestampOptions()).toHaveLength(1);

      const search = screen.getByPlaceholderText("Search properties...");
      fireEvent.change(search, { target: { value: "Time" } });
      await waitFor(() =>
        expect(propertyCatalogMock).toHaveBeenCalledWith(
          expect.objectContaining({ search: "Time", enabled: true }),
        ),
      );
      expect(timestampOptions()).toHaveLength(1);
      expect(
        document.querySelector(
          '[data-filter-property-option="time_to_first_token"]',
        ),
      ).toBeInTheDocument();

      fireEvent.change(search, { target: { value: "Timestamp" } });
      await waitFor(() =>
        expect(propertyCatalogMock).toHaveBeenCalledWith(
          expect.objectContaining({ search: "Timestamp", enabled: true }),
        ),
      );
      expect(timestampOptions()).toHaveLength(1);
      fireEvent.click(timestampOptions()[0]);

      const operator = screen.getByRole("combobox", {
        name: "Condition 1 operator",
      });
      expect(operator).toHaveTextContent(/^after$/);
      fireEvent.mouseDown(operator);
      expect(
        within(screen.getByRole("listbox"))
          .getAllByRole("option")
          .map((option) => option.textContent),
      ).toEqual(TIMESTAMP_OPERATORS);
      fireEvent.click(
        screen.getByRole("option", { name: "after", exact: true }),
      );

      const input = document.querySelector('input[type="datetime-local"]');
      expect(input).toBeInTheDocument();
      fireEvent.change(input, { target: { value: "2026-11-10T10:30" } });
      await waitFor(() => expect(onApply).toHaveBeenCalled());
      const [row] = onApply.mock.calls.at(-1)[0];
      expect(row).toMatchObject({
        field: "start_time",
        registryId: "system_attribute:traces:start_time",
        fieldName: "Timestamp",
        apiColType: "SYSTEM_METRIC",
        operator: "greater_than",
        value: "2026-11-10T10:30",
      });
      const [filter] = panelRowsToApiFilters([row]);
      expect(filter).toMatchObject({
        column_id: "start_time",
        property_id: "system_attribute:traces:start_time",
        filter_config: {
          filter_type: "datetime",
          filter_op: "greater_than",
          col_type: "SYSTEM_METRIC",
        },
      });
      expect(filter.filter_config.filter_value).toMatch(
        ZONED("2026-11-10T10:30"),
      );
      expect(Date.parse(filter.filter_config.filter_value)).toBe(
        new Date(2026, 10, 10, 10, 30).getTime(),
      );

      fireEvent.mouseDown(
        screen.getByRole("combobox", { name: "Condition 1 operator" }),
      );
      fireEvent.click(
        screen.getByRole("option", { name: "between", exact: true }),
      );
      const bounds = document.querySelectorAll('input[type="datetime-local"]');
      expect(bounds).toHaveLength(2);
      fireEvent.change(bounds[0], { target: { value: "2026-11-09T08:00" } });
      fireEvent.change(bounds[1], { target: { value: "2026-11-10T18:45" } });
      await waitFor(() =>
        expect(onApply.mock.calls.at(-1)[0][0].value).toEqual([
          "2026-11-09T08:00",
          "2026-11-10T18:45",
        ]),
      );
      const [range] = panelRowsToApiFilters(onApply.mock.calls.at(-1)[0]);
      expect(range.filter_config.filter_op).toBe("between");
      expect(range.filter_config.filter_value.map(Date.parse)).toEqual([
        new Date(2026, 10, 9, 8, 0).getTime(),
        new Date(2026, 10, 10, 18, 45).getTime(),
      ]);
      document.body.removeChild(anchorEl);
    },
  );

  it.each(SURFACES)(
    "%s: the Query tab lists Timestamp beside Time to First Token and edits it as a date",
    async (_surface, surfaceProps, metrics) => {
      propertyCatalogMock.mockImplementation(catalogFor(metrics));
      const { anchorEl, onApply } = renderPanel({
        ...surfaceProps,
        showQueryTab: true,
      });

      fireEvent.click(screen.getByRole("tab", { name: "Query" }));
      const input = screen.getByRole("combobox");
      fireEvent.focus(input);
      fireEvent.change(input, { target: { value: "Time" } });
      expect(await screen.findByText("Timestamp")).toBeInTheDocument();
      expect(screen.getByText("Time to First Token")).toBeInTheDocument();

      fireEvent.change(input, { target: { value: "Timestamp" } });
      fireEvent.keyDown(input, { key: "ArrowDown" });
      fireEvent.keyDown(input, { key: "Enter" });
      await waitFor(() =>
        expect(input).toHaveAttribute("placeholder", "pick operator..."),
      );
      // "after" also lists "on or after"; pick the operator named "after".
      fireEvent.change(input, { target: { value: "after" } });
      fireEvent.click(await screen.findByRole("option", { name: /^after$/ }));

      const value = await screen.findByLabelText("Date and time");
      expect(value).toHaveAttribute("type", "datetime-local");
      fireEvent.change(value, { target: { value: "2026-11-10T10:30" } });
      fireEvent.keyDown(value, { key: "Enter" });

      await waitFor(() => expect(onApply).toHaveBeenCalled());
      const [row] = onApply.mock.calls.at(-1)[0];
      expect(row).toMatchObject({
        field: "start_time",
        registryId: "system_attribute:traces:start_time",
        operator: "greater_than",
      });
      const [filter] = panelRowsToApiFilters([row]);
      expect(filter.filter_config).toMatchObject({
        filter_type: "datetime",
        filter_op: "greater_than",
        col_type: "SYSTEM_METRIC",
      });
      expect(Date.parse(filter.filter_config.filter_value)).toBe(
        new Date(2026, 10, 10, 10, 30).getTime(),
      );
      document.body.removeChild(anchorEl);
    },
  );

  it.each(SURFACES)(
    "%s: a saved Timestamp filter reopens as the local date and time",
    async (_surface, surfaceProps, metrics) => {
      propertyCatalogMock.mockImplementation(catalogFor(metrics));
      // Stored the way the list API echoes an instant back: UTC.
      const saved = {
        column_id: "start_time",
        property_id: "system_attribute:traces:start_time",
        display_name: "Timestamp",
        filter_config: {
          filter_type: "datetime",
          filter_op: "greater_than",
          filter_value: new Date(2026, 10, 10, 10, 30).toISOString(),
          col_type: "SYSTEM_METRIC",
        },
      };
      const { anchorEl } = renderPanel({
        ...surfaceProps,
        showQueryTab: true,
        currentFilters: apiFiltersToPanelRows([saved]),
      });

      expect(
        screen.getByRole("button", { name: "Timestamp", exact: true }),
      ).toBeInTheDocument();
      expect(
        screen.getByRole("combobox", { name: "Condition 1 operator" }),
      ).toHaveTextContent(/^after$/);
      expect(
        document.querySelector('input[type="datetime-local"]'),
      ).toHaveValue("2026-11-10T10:30");

      fireEvent.click(screen.getByRole("tab", { name: "Query" }));
      expect(
        await screen.findByText("Timestamp after 2026-11-10T10:30"),
      ).toBeInTheDocument();
      document.body.removeChild(anchorEl);
    },
  );
});
