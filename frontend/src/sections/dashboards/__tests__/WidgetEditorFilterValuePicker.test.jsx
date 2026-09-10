import React from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import {
  act,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "src/utils/test-utils";

const useResolvedFilterOptionsMock = vi.fn();

vi.mock("src/components/filter-value-label", () => ({
  default: () => null,
  shouldShowFilterValueContinuation: ({ hasNextPage, isFetchNextPageError }) =>
    Boolean(hasNextPage || isFetchNextPageError),
  useResolvedFilterOptions: (...args) => useResolvedFilterOptionsMock(...args),
}));

vi.mock("react-apexcharts", () => ({ default: () => null }));

import {
  buildLinkedProjectFilter,
  buildWidgetCatalogPickerOptions,
  buildWidgetFilterConfig,
  buildWidgetCursorAttributeOptions,
  FilterValuePickerPopup,
  getWidgetCatalogCategoryCountPresentation,
  getWidgetCatalogExactResultCount,
  getWidgetCatalogSidebarCategoryCount,
  getWidgetFilterDefaults,
  getWidgetFilterOperators,
  getWidgetMetricCatalogRequest,
  getWidgetMetricDataType,
  hasWidgetFilterValue,
  isWidgetCatalogOptionAllowed,
  isWidgetCatalogInventoryLoading,
  mergeWidgetCursorAttributeOptions,
  resolveWidgetCatalogResultMetrics,
  resolveWidgetCatalogSidebarCounts,
  restoreWidgetFilterConfig,
  WidgetCatalogOption,
  WidgetCatalogPaginationControl,
} from "../WidgetEditorView";

const installIntersectionObserver = () => {
  const observers = [];
  class IntersectionObserverMock {
    constructor(callback, options) {
      this.callback = callback;
      this.options = options;
      this.observe = vi.fn();
      this.disconnect = vi.fn();
      observers.push(this);
    }
  }
  vi.stubGlobal("IntersectionObserver", IntersectionObserverMock);
  const emit = (isIntersecting) => {
    const observer = observers.at(-1);
    if (!observer) throw new Error("No IntersectionObserver was created");
    act(() => observer.callback([{ isIntersecting }]));
  };
  return { observers, emit };
};

const deferred = () => {
  let resolve;
  const promise = new Promise((done) => {
    resolve = done;
  });
  return { promise, resolve };
};

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("WidgetEditor annotation breakdown capability", () => {
  const label = "44444444-4444-4444-4444-444444444444";
  const annotation = {
    id: label,
    name: "Quality",
    type: "annotation",
    source: "both",
  };
  const own = {
    name: label,
    display_name: "Quality",
    category: "annotation_metric",
    source: "both",
  };
  const foreign = { ...own, name: "55555555-5555-4555-8555-555555555555" };
  const model = { name: "model", category: "system_metric", source: "traces" };
  const context = {
    selectedMetricSources: ["traces"],
    selectedMetrics: [annotation],
  };

  it("offers the same label by identity, not a same-named label or system dimension", () => {
    const options = buildWidgetCatalogPickerOptions({
      metrics: [own, foreign, model],
      pickerMode: "breakdown",
      ...context,
    });
    expect(options.map((option) => option.id)).toEqual([label]);
  });

  it("requires a breakdown supported by every selected annotation metric", () => {
    expect(
      isWidgetCatalogOptionAllowed(own, "breakdown", {
        ...context,
        selectedMetrics: [annotation, { ...annotation, id: foreign.name }],
      }),
    ).toBe(false);
    expect(
      isWidgetCatalogOptionAllowed(own, "breakdown", {
        ...context,
        selectedMetrics: [
          annotation,
          { id: "trace_count", type: "system", source: "traces" },
        ],
      }),
    ).toBe(true);
  });

  it("does not offer a second dimension but permits replacing the sole dimension", () => {
    expect(
      isWidgetCatalogOptionAllowed(own, "breakdown", {
        ...context,
        otherBreakdownCount: 1,
      }),
    ).toBe(false);
    expect(
      isWidgetCatalogOptionAllowed(own, "breakdown", {
        ...context,
        otherBreakdownCount: 0,
      }),
    ).toBe(true);
  });

  it("preserves filters, ungrouped metric discovery and non-annotation capabilities", () => {
    expect(isWidgetCatalogOptionAllowed(foreign, "filter", context)).toBe(true);
    expect(isWidgetCatalogOptionAllowed(model, "metric_filter", context)).toBe(
      true,
    );
    expect(isWidgetCatalogOptionAllowed(model, "metric", context)).toBe(true);
    expect(
      isWidgetCatalogOptionAllowed(model, "breakdown", {
        selectedMetricSources: ["traces"],
        selectedMetrics: [],
      }),
    ).toBe(true);
  });

  it("does not offer an annotation metric incompatible with an existing dimension", () => {
    expect(
      isWidgetCatalogOptionAllowed(own, "metric", {
        selectedBreakdowns: [{ id: label, type: "annotation", source: "both" }],
      }),
    ).toBe(true);
    expect(
      isWidgetCatalogOptionAllowed(foreign, "metric", {
        selectedBreakdowns: [{ id: label, type: "annotation", source: "both" }],
      }),
    ).toBe(false);
    expect(
      isWidgetCatalogOptionAllowed(own, "metric", {
        selectedBreakdowns: [{ id: "model", type: "system", source: "traces" }],
      }),
    ).toBe(false);
  });

  it("matches canonical label UUIDs independently of letter casing", () => {
    const lowercase = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa";
    expect(
      isWidgetCatalogOptionAllowed({ ...own, name: lowercase }, "breakdown", {
        ...context,
        selectedMetrics: [{ ...annotation, id: lowercase.toUpperCase() }],
      }),
    ).toBe(true);
  });
});

describe("WidgetEditor picker capability closure", () => {
  const label = "44444444-4444-4444-4444-444444444444";
  const annotation = { id: label, type: "annotation", source: "both" };
  const own = { name: label, category: "annotation_metric", source: "both" };
  const trace = {
    name: "trace_count",
    category: "system_metric",
    source: "traces",
  };
  const dataset = {
    name: "row_count",
    category: "system_metric",
    source: "datasets",
  };
  const attributes = [{ key: "customer.cost", type: "number" }];

  it.each(["datasets", "simulation"])(
    "gates cursor fallback by the %s adapter before merging",
    (source) => {
      for (const pickerMode of ["filter", "metric_filter", "breakdown"]) {
        const options = buildWidgetCursorAttributeOptions(
          attributes,
          pickerMode,
          {
            selectedMetricSources: [source],
            targetMetricSource: source,
          },
        );
        expect(mergeWidgetCursorAttributeOptions([], options, true)).toEqual(
          [],
        );
      }
    },
  );

  it.each([
    "trace",
    "eval_metric",
    "annotation",
    "dataset",
    "simulation",
    "user",
    "prompt",
  ])(
    "keeps cursor fallback out of the explicit %s category",
    (pickerCategory) => {
      expect(
        buildWidgetCursorAttributeOptions(attributes, "filter", {
          pickerCategory,
          selectedMetricSources: ["traces"],
        }),
      ).toEqual([]);
    },
  );

  it.each(["all", "custom_attribute"])(
    "retains valid trace cursor options in %s without weakening annotation grouping",
    (pickerCategory) => {
      const context = {
        pickerCategory,
        selectedMetrics: [annotation],
        selectedMetricSources: ["traces"],
        selectedBreakdowns: [annotation],
        targetMetricSource: "traces",
      };
      for (const pickerMode of ["filter", "metric_filter"]) {
        expect(
          buildWidgetCursorAttributeOptions(
            attributes,
            pickerMode,
            context,
          ).map(({ id }) => id),
        ).toEqual(["customer.cost"]);
      }
      expect(
        buildWidgetCursorAttributeOptions(attributes, "breakdown", context),
      ).toEqual([]);
      expect(
        buildWidgetCursorAttributeOptions(attributes, "metric", context),
      ).toEqual([]);
      expect(
        buildWidgetCursorAttributeOptions(attributes, "metric", {
          ...context,
          selectedBreakdowns: [],
        }).map(({ id }) => id),
      ).toEqual(["customer.cost"]);
      expect(
        buildWidgetCursorAttributeOptions(attributes, "breakdown", {
          ...context,
          selectedMetrics: [
            { id: "trace_count", type: "system", source: "traces" },
          ],
        }).map(({ id }) => id),
      ).toEqual(["customer.cost"]);
    },
  );

  it.each([null, 0])(
    "rejects dataset metric addition/replacement at %s with retained annotation breakdown",
    (metricTargetIndex) => {
      const context = {
        selectedMetrics: [annotation],
        selectedMetricSources: ["traces"],
        selectedBreakdowns: [annotation],
        metricTargetIndex,
      };
      const saved = structuredClone(context);
      expect(
        buildWidgetCatalogPickerOptions({
          metrics: [dataset],
          pickerMode: "metric",
          ...context,
        }),
      ).toEqual([]);
      expect(
        isWidgetCatalogOptionAllowed(own, "breakdown", {
          selectedMetrics: [annotation, dataset],
          selectedMetricSources: ["traces", "datasets"],
        }),
      ).toBe(false);
      expect(context).toEqual(saved);
    },
  );

  it("checks every prospective metric and removes only the replacement target", () => {
    const context = {
      selectedMetrics: [annotation, dataset],
      selectedMetricSources: ["traces", "datasets"],
      selectedBreakdowns: [annotation],
    };
    const saved = structuredClone(context);
    expect(isWidgetCatalogOptionAllowed(trace, "metric", context)).toBe(false);
    expect(
      isWidgetCatalogOptionAllowed(trace, "metric", {
        ...context,
        metricTargetIndex: 0,
      }),
    ).toBe(false);
    expect(
      isWidgetCatalogOptionAllowed(trace, "metric", {
        ...context,
        metricTargetIndex: 1,
      }),
    ).toBe(true);
    expect(
      isWidgetCatalogOptionAllowed(own, "breakdown", {
        selectedMetrics: [annotation, trace],
        selectedMetricSources: ["traces"],
      }),
    ).toBe(true);
    expect(context).toEqual(saved);
  });

  it("checks prospective cursor metrics against retained breakdowns too", () => {
    const context = {
      selectedMetrics: [annotation, dataset],
      selectedMetricSources: ["traces", "datasets"],
      selectedBreakdowns: [annotation],
    };
    expect(
      buildWidgetCursorAttributeOptions(attributes, "metric", context),
    ).toEqual([]);
    expect(
      buildWidgetCursorAttributeOptions(attributes, "metric", {
        ...context,
        metricTargetIndex: 1,
      }),
    ).toEqual([]);
    expect(
      buildWidgetCursorAttributeOptions(attributes, "metric", {
        ...context,
        metricTargetIndex: 1,
        selectedBreakdowns: [],
      }).map(({ id }) => id),
    ).toEqual(["customer.cost"]);
  });

  // _build_custom_attr_query rejects all annotation/eval joined dimensions,
  // including a same-label annotation otherwise valid for a companion metric.
  it.each(["annotation_metric", "eval_metric"])(
    "rejects custom metric additions/replacements with retained %s joins",
    (category) => {
      const breakdown = { id: label, type: category, source: "traces" };
      const custom = {
        name: "customer.cost",
        category: "custom_attribute",
        source: "traces",
        data_type: "number",
      };
      for (const metricTargetIndex of [null, 0]) {
        const context = {
          selectedMetrics: [trace],
          selectedMetricSources: ["traces"],
          selectedBreakdowns: [breakdown],
          metricTargetIndex,
        };
        const saved = structuredClone(context);
        expect(
          buildWidgetCatalogPickerOptions({
            metrics: [custom],
            pickerMode: "metric",
            ...context,
          }),
        ).toEqual([]);
        expect(
          buildWidgetCursorAttributeOptions(attributes, "metric", context),
        ).toEqual([]);
        expect(context).toEqual(saved);
      }
    },
  );

  it.each(["annotation_metric", "eval_metric"])(
    "rejects selecting/replacing a %s breakdown while any custom metric remains",
    (category) => {
      const custom = {
        id: "customer.cost",
        type: "custom_attribute",
        source: "traces",
      };
      const option = { name: label, category, source: "both" };
      for (const otherBreakdownCount of [0, 1]) {
        const context = {
          selectedMetrics: [trace, custom],
          selectedMetricSources: ["traces"],
          otherBreakdownCount,
        };
        expect(
          buildWidgetCatalogPickerOptions({
            metrics: [option],
            pickerMode: "breakdown",
            ...context,
          }),
        ).toEqual([]);
        expect(isWidgetCatalogOptionAllowed(option, "filter", context)).toBe(
          true,
        );
        expect(
          isWidgetCatalogOptionAllowed(option, "metric_filter", {
            ...context,
            targetMetricSource: "traces",
          }),
        ).toBe(true);
      }
      expect(
        isWidgetCatalogOptionAllowed(trace, "metric", {
          selectedMetrics: [trace, custom],
          selectedBreakdowns: [{ id: label, type: category, source: "both" }],
          metricTargetIndex: 1,
        }),
      ).toBe(true);
    },
  );

  it.each([
    { selectedBreakdowns: [] },
    { selectedBreakdowns: [{ id: "model", type: "system", source: "traces" }] },
    {
      selectedBreakdowns: [
        { id: "customer.tier", type: "custom_attribute", source: "traces" },
      ],
    },
  ])(
    "keeps custom metrics available without joined breakdowns: %j",
    ({ selectedBreakdowns }) => {
      const custom = {
        name: "customer.cost",
        category: "custom_attribute",
        source: "traces",
        data_type: "number",
      };
      const context = {
        selectedMetrics: [trace],
        selectedMetricSources: ["traces"],
        selectedBreakdowns,
      };
      expect(
        buildWidgetCatalogPickerOptions({
          metrics: [custom],
          pickerMode: "metric",
          ...context,
        }).map(({ id }) => id),
      ).toEqual(["customer.cost"]);
      expect(
        buildWidgetCursorAttributeOptions(attributes, "metric", context).map(
          ({ id }) => id,
        ),
      ).toEqual(["customer.cost"]);
    },
  );

  it.each([
    ["datasets", "cell_status", "model"],
    ["simulation", "status", "duration"],
  ])(
    "preserves existing %s dimensions in both selection orders",
    (source, allowed, denied) => {
      const candidate = { name: "metric", category: "system_metric", source };
      for (const [id, expected] of [
        [allowed, true],
        [denied, false],
      ]) {
        const breakdown = { id, name: "Display label", type: "system", source };
        expect(
          isWidgetCatalogOptionAllowed(candidate, "metric", {
            selectedBreakdowns: [breakdown],
          }),
        ).toBe(expected);
        expect(
          isWidgetCatalogOptionAllowed(
            { name: id, category: "system_metric", source },
            "breakdown",
            {
              selectedMetrics: [candidate],
              selectedMetricSources: [source],
            },
          ),
        ).toBe(expected);
      }
      expect(isWidgetCatalogOptionAllowed(candidate, "metric")).toBe(true);
    },
  );

  it.each(["filter", "metric_filter", "breakdown", "metric"])(
    "does not present raw inventory as an eligible %s result total",
    (pickerMode) => {
      expect(
        getWidgetCatalogExactResultCount({
          request: { category: "annotation_metric" },
          categoryCounts: { annotation_metric: 27 },
          categoryCountsExact: true,
          requestSettled: true,
          pickerMode,
          selectedBreakdowns: pickerMode === "metric" ? [annotation] : [],
        }),
      ).toBeNull();
    },
  );

  it.each([0, 27])(
    "keeps unrestricted inventory totals exact, including %s",
    (count) => {
      expect(
        getWidgetCatalogExactResultCount({
          request: { category: "annotation_metric" },
          categoryCounts: { annotation_metric: count },
          categoryCountsExact: true,
          requestSettled: true,
          pickerMode: "metric",
          selectedBreakdowns: [{ id: "", name: "" }],
        }),
      ).toBe(count);
    },
  );
});

describe("WidgetEditor catalog source labels", () => {
  it("distinguishes same-named dimensions without changing their identities", () => {
    const onSelect = vi.fn();
    const options = buildWidgetCatalogPickerOptions({
      metrics: ["all", "datasets"].map((source) => ({
        name: "dataset",
        display_name: "Dataset",
        property_id: `system_attribute:${source}:dataset`,
        category: "system_metric",
        role: "dimension",
        data_type: "string",
        output_type: "string",
        source,
        // Captured DASH001 response includes search/category tokens here;
        // these must not become displayed source names.
        sources: [
          source === "all" ? "all" : "dataset",
          source === "all" ? "dataset" : "datasets",
          "system",
        ],
      })),
      pickerMode: "filter",
      pickerCategory: "dataset",
      selectedMetricSources: ["datasets"],
    });
    expect(options).toHaveLength(2);
    render(
      options.map((option) => (
        <WidgetCatalogOption
          key={option.registryId}
          option={option}
          onSelect={onSelect}
        />
      )),
    );

    const dataset = screen.getByRole("button", {
      name: "Dataset (string, Datasets)",
      exact: true,
    });
    expect(dataset).toHaveTextContent("Datasets");
    expect(
      screen.getByRole("button", {
        name: "Dataset (string, All sources)",
        exact: true,
      }),
    ).toHaveTextContent("All sources");
    fireEvent.click(dataset);
    expect(onSelect).toHaveBeenCalledExactlyOnceWith(options[1]);
    expect(options[1]).toMatchObject({
      id: "dataset",
      source: "datasets",
      registryId: "system_attribute:datasets:dataset",
    });
  });

  it("keeps numeric and string attributes separately selectable", () => {
    const onSelect = vi.fn();
    const options = ["string", "number"].map((dataType) => ({
      id: "company_id",
      name: "Company ID",
      type: "custom_attribute",
      source: "traces",
      dataType,
    }));
    render(
      options.map((option) => (
        <WidgetCatalogOption
          key={option.dataType}
          option={option}
          onSelect={onSelect}
        />
      )),
    );
    fireEvent.click(
      screen.getByRole("button", {
        name: "Company ID (number, Traces)",
        exact: true,
      }),
    );
    expect(onSelect).toHaveBeenCalledExactlyOnceWith(options[1]);
    expect(
      screen.getByRole("button", {
        name: "Company ID (string, Traces)",
        exact: true,
      }),
    ).toHaveTextContent("string");
  });

  it("shows the shared source without displaying category tokens", () => {
    const option = {
      id: "quality",
      name: "Quality",
      type: "eval_metric",
      source: "both",
      sources: ["annotation", "datasets", "traces"],
      outputType: "CHOICE",
    };
    render(<WidgetCatalogOption option={option} onSelect={vi.fn()} />);
    const row = screen.getByRole("button", {
      name: "Quality (choice, Traces, Datasets)",
      exact: true,
    });
    expect(row).toHaveTextContent("choice");
    expect(row).toHaveTextContent("Traces, Datasets");
    expect(row).not.toHaveTextContent("annotation");
  });
});

describe("WidgetEditor property-catalog loading state", () => {
  it("keeps a settled partial search loading while the remote catalog request is in flight", () => {
    expect(
      isWidgetCatalogInventoryLoading({
        requestSettled: true,
        usesLegacyCatalog: false,
        legacyCatalogLoading: false,
        propertyCatalogLoading: false,
        propertyCatalogSearchPending: true,
        propertyCatalogNotReady: false,
      }),
    ).toBe(true);
  });

  it("allows the empty state only after the remote catalog search settles", () => {
    expect(
      isWidgetCatalogInventoryLoading({
        requestSettled: true,
        usesLegacyCatalog: false,
        legacyCatalogLoading: false,
        propertyCatalogLoading: false,
        propertyCatalogSearchPending: false,
        propertyCatalogNotReady: false,
      }),
    ).toBe(false);
  });
});

describe("WidgetEditor filter-value picker", () => {
  it("automatically continues retained values at the list end without a load-more button", async () => {
    const intersection = installIntersectionObserver();
    const nextPage = deferred();
    const fetchNextPage = vi.fn(() => nextPage.promise);
    useResolvedFilterOptionsMock.mockReturnValue({
      options: [{ value: "retained", label: "Retained" }],
      isLoading: false,
      isError: false,
      fetchNextPage,
      hasNextPage: true,
      continuationKey: "values-cursor-2",
      isFetchingNextPage: false,
      isFetchNextPageError: false,
      queryReadState: "complete",
      refetch: vi.fn(),
    });
    const anchorEl = document.createElement("button");
    document.body.appendChild(anchorEl);

    render(
      <FilterValuePickerPopup
        anchorEl={anchorEl}
        filter={{ field: "typed-choice", value: [] }}
        onClose={vi.fn()}
        onApply={vi.fn()}
        source="traces"
      />,
    );

    expect(
      screen.queryByRole("button", { name: /load more values/i }),
    ).not.toBeInTheDocument();
    const sentinel = screen.getByTestId(
      "widget-filter-value-pagination-sentinel",
    );
    expect(intersection.observers.at(-1).options.root).toBe(
      sentinel.parentElement,
    );
    intersection.emit(true);
    intersection.emit(true);
    expect(fetchNextPage).toHaveBeenCalledOnce();
    expect(screen.getByRole("status")).toHaveTextContent("Loading more values");

    await act(async () => {
      nextPage.resolve();
      await nextPage.promise;
    });
    document.body.removeChild(anchorEl);
  });

  it("bounds oversized exact attribute values before lookup or selection", () => {
    useResolvedFilterOptionsMock.mockReturnValue({
      options: [],
      isLoading: false,
      isError: false,
      fetchNextPage: vi.fn(),
      hasNextPage: false,
      continuationKey: null,
      isFetchingNextPage: false,
      isFetchNextPageError: false,
      queryReadState: "complete",
      cursorChainStopped: false,
      retryFreshPage: vi.fn(),
      isRetryingFreshPage: false,
      refetch: vi.fn(),
    });
    const anchorEl = document.createElement("button");
    document.body.appendChild(anchorEl);

    render(
      <FilterValuePickerPopup
        anchorEl={anchorEl}
        filter={{
          id: "long.attribute",
          type: "custom_attribute",
          dataType: "string",
          value: [],
        }}
        onClose={vi.fn()}
        onApply={vi.fn()}
        source="traces"
      />,
    );

    fireEvent.change(screen.getByPlaceholderText("Search..."), {
      target: { value: "x".repeat(16 * 1024 + 2) },
    });

    expect(screen.getByPlaceholderText("Search...")).toHaveValue(
      "x".repeat(16 * 1024 + 1),
    );
    expect(
      screen.getByText(/no longer than 16,384 UTF-8 bytes/i),
    ).toBeInTheDocument();
    expect(
      document.querySelector("[data-widget-filter-exact-value]"),
    ).not.toBeInTheDocument();
    expect(useResolvedFilterOptionsMock).toHaveBeenLastCalledWith(
      expect.objectContaining({ id: "long.attribute" }),
      "traces",
      false,
      "",
      "",
    );

    document.body.removeChild(anchorEl);
  });

  it("ellipsizes long valid values while retaining the full text as a title", () => {
    const longValue = "metadata.".repeat(100);
    useResolvedFilterOptionsMock.mockReturnValue({
      options: [{ value: longValue, label: longValue, type: "string" }],
      isLoading: false,
      isError: false,
      fetchNextPage: vi.fn(),
      hasNextPage: false,
      continuationKey: null,
      isFetchingNextPage: false,
      isFetchNextPageError: false,
      queryReadState: "complete",
      cursorChainStopped: false,
      retryFreshPage: vi.fn(),
      isRetryingFreshPage: false,
      refetch: vi.fn(),
    });
    const anchorEl = document.createElement("button");
    document.body.appendChild(anchorEl);

    render(
      <FilterValuePickerPopup
        anchorEl={anchorEl}
        filter={{
          id: "long.attribute",
          type: "custom_attribute",
          dataType: "string",
          value: [],
        }}
        onClose={vi.fn()}
        onApply={vi.fn()}
        source="traces"
      />,
    );

    expect(screen.getByTitle(longValue)).toHaveStyle({
      overflow: "hidden",
      textOverflow: "ellipsis",
      whiteSpace: "nowrap",
    });

    document.body.removeChild(anchorEl);
  });

  it("automatically loads each new cursor once while the end remains visible", async () => {
    const intersection = installIntersectionObserver();
    const firstPage = deferred();
    const secondPage = deferred();
    const fetchNextPage = vi
      .fn()
      .mockImplementationOnce(() => firstPage.promise)
      .mockImplementationOnce(() => secondPage.promise);
    const { rerender } = render(
      <WidgetCatalogPaginationControl
        pickerCategory="all"
        hasNextPage
        continuationKey="catalog-cursor-2"
        isFetchingNextPage={false}
        onLoadMore={fetchNextPage}
      />,
    );

    const sentinel = screen.getByTestId("widget-catalog-pagination-sentinel");
    expect(intersection.observers[0].options.root).toBe(sentinel.parentElement);
    expect(
      screen.queryByRole("button", { name: /load more/i }),
    ).not.toBeInTheDocument();

    intersection.emit(true);
    intersection.emit(true);
    expect(fetchNextPage).toHaveBeenCalledOnce();
    expect(screen.getByRole("status")).toHaveTextContent("Loading more");

    await act(async () => {
      firstPage.resolve();
      await firstPage.promise;
    });
    await waitFor(() =>
      expect(screen.queryByRole("status")).not.toBeInTheDocument(),
    );

    // Repeated observer notifications for the same cursor are coalesced.
    intersection.emit(true);
    expect(fetchNextPage).toHaveBeenCalledOnce();

    // A short appended page can leave the sentinel visible. Publishing a new
    // cursor still advances exactly once without requiring an exit/re-entry.
    rerender(
      <WidgetCatalogPaginationControl
        pickerCategory="all"
        hasNextPage
        continuationKey="catalog-cursor-3"
        isFetchingNextPage={false}
        onLoadMore={fetchNextPage}
      />,
    );
    await waitFor(() => expect(fetchNextPage).toHaveBeenCalledTimes(2));

    intersection.emit(true);
    expect(fetchNextPage).toHaveBeenCalledTimes(2);
  });

  it("advances both All-category cursors through one end intersection", () => {
    const intersection = installIntersectionObserver();
    const fetchNextPage = vi.fn(() => new Promise(() => {}));
    const fetchNextAttributePage = vi.fn(() => new Promise(() => {}));
    render(
      <WidgetCatalogPaginationControl
        pickerCategory="all"
        hasNextPage
        continuationKey="catalog-cursor-2"
        isFetchingNextPage={false}
        onLoadMore={fetchNextPage}
        attributeHasNextPage
        attributeContinuationKey="attribute-cursor-2"
        isFetchingAttributeNextPage={false}
        onLoadMoreAttributes={fetchNextAttributePage}
      />,
    );

    intersection.emit(true);
    intersection.emit(true);

    expect(fetchNextPage).toHaveBeenCalledOnce();
    expect(fetchNextAttributePage).toHaveBeenCalledOnce();
    expect(screen.getByRole("status")).toHaveTextContent("Loading more");
  });

  it("advances the unified catalog cursor in the trace-attribute category", () => {
    const intersection = installIntersectionObserver();
    const fetchNextPage = vi.fn(() => new Promise(() => {}));
    render(
      <WidgetCatalogPaginationControl
        pickerCategory="custom_attribute"
        hasNextPage
        continuationKey="catalog-cursor-2"
        isFetchingNextPage={false}
        onLoadMore={fetchNextPage}
      />,
    );

    intersection.emit(true);
    expect(fetchNextPage).toHaveBeenCalledOnce();
  });

  it("offers Retry only for an actual failed cursor and retries only that cursor", async () => {
    const intersection = installIntersectionObserver();
    const fetchNextPage = vi.fn();
    const retryAttributePage = vi.fn().mockResolvedValue(undefined);
    render(
      <WidgetCatalogPaginationControl
        pickerCategory="all"
        hasNextPage
        continuationKey="catalog-cursor-2"
        isFetchingNextPage={false}
        onLoadMore={fetchNextPage}
        attributeHasNextPage
        attributeContinuationKey="attribute-cursor-2"
        isFetchingAttributeNextPage={false}
        isFetchNextAttributePageError
        onLoadMoreAttributes={retryAttributePage}
      />,
    );

    intersection.emit(true);
    expect(fetchNextPage).not.toHaveBeenCalled();
    expect(retryAttributePage).not.toHaveBeenCalled();
    expect(screen.getByRole("alert")).toHaveTextContent("The next page failed");

    fireEvent.click(screen.getByRole("button", { name: "Retry" }));
    expect(retryAttributePage).toHaveBeenCalledOnce();
    expect(fetchNextPage).not.toHaveBeenCalled();
    await waitFor(() =>
      expect(screen.queryByRole("status")).not.toBeInTheDocument(),
    );
  });

  it("offers only properties supported by the selected widget adapters", () => {
    const datasetEval = {
      name: "eval-config-id",
      category: "eval_metric",
      source: "all",
      sources: ["all"],
    };
    const datasetColumn = {
      name: "column-id",
      category: "custom_column",
      sources: ["datasets"],
    };
    const datasetDimension = {
      name: "cell_status",
      category: "system_metric",
      source: "datasets",
    };

    for (const mode of ["filter", "metric_filter", "breakdown"]) {
      const context =
        mode === "metric_filter"
          ? { targetMetricSource: "datasets" }
          : { selectedMetricSources: ["datasets"] };
      expect(isWidgetCatalogOptionAllowed(datasetEval, mode, context)).toBe(
        false,
      );
      expect(isWidgetCatalogOptionAllowed(datasetColumn, mode, context)).toBe(
        false,
      );
      expect(
        isWidgetCatalogOptionAllowed(datasetDimension, mode, context),
      ).toBe(true);
    }
    expect(
      isWidgetCatalogOptionAllowed(datasetEval, "filter", {
        selectedMetricSources: ["traces"],
      }),
    ).toBe(true);
    expect(
      isWidgetCatalogOptionAllowed(datasetEval, "metric_filter", {
        targetMetricSource: "traces",
      }),
    ).toBe(true);
    expect(
      isWidgetCatalogOptionAllowed(datasetColumn, "metric_filter", {
        targetMetricSource: "traces",
      }),
    ).toBe(false);
    expect(
      isWidgetCatalogOptionAllowed(datasetColumn, "filter", {
        selectedMetricSources: ["traces"],
      }),
    ).toBe(false);
    expect(
      isWidgetCatalogOptionAllowed(
        {
          name: "customer.attr",
          category: "custom_attribute",
          source: "traces",
        },
        "filter",
        { selectedMetricSources: ["datasets"] },
      ),
    ).toBe(false);
    expect(isWidgetCatalogOptionAllowed(datasetEval, "metric")).toBe(true);

    const simulationEval = {
      name: "simulation-eval-config",
      category: "eval_metric",
      source: "simulation",
    };
    const simulationStatus = {
      name: "status",
      category: "system_metric",
      source: "simulation",
    };
    const simulationDuration = {
      name: "duration",
      category: "system_metric",
      source: "simulation",
    };
    expect(
      isWidgetCatalogOptionAllowed(simulationEval, "filter", {
        selectedMetricSources: ["simulation"],
      }),
    ).toBe(false);
    expect(
      isWidgetCatalogOptionAllowed(simulationEval, "metric_filter", {
        targetMetricSource: "simulation",
      }),
    ).toBe(false);
    expect(
      isWidgetCatalogOptionAllowed(simulationStatus, "breakdown", {
        selectedMetricSources: ["simulation"],
      }),
    ).toBe(true);
    expect(
      isWidgetCatalogOptionAllowed(simulationDuration, "filter", {
        selectedMetricSources: ["simulation"],
      }),
    ).toBe(true);
    expect(
      isWidgetCatalogOptionAllowed(simulationDuration, "breakdown", {
        selectedMetricSources: ["simulation"],
      }),
    ).toBe(false);
    expect(isWidgetCatalogOptionAllowed(simulationEval, "metric")).toBe(true);
  });

  it("stamps auto-linked observability projects with registry identity", () => {
    expect(buildLinkedProjectFilter(["project-1", "project-2"])).toEqual({
      id: "project",
      registryId: "system_attribute:traces:project",
      name: "Project",
      type: "system",
      dataType: "string",
      source: "traces",
      operator: "contains",
      value: ["project-1", "project-2"],
    });
  });

  it("offers a bounded fresh retry for a stopped cursor while retaining rows", () => {
    const retryFreshPage = vi.fn(() => Promise.resolve());
    useResolvedFilterOptionsMock.mockReturnValue({
      options: [{ value: "retained", label: "Retained" }],
      isLoading: false,
      isError: false,
      fetchNextPage: vi.fn(),
      hasNextPage: false,
      isFetchingNextPage: false,
      isFetchNextPageError: false,
      queryReadState: "degraded",
      cursorChainStopped: true,
      retryFreshPage,
      isRetryingFreshPage: false,
      refetch: vi.fn(),
    });
    const anchorEl = document.createElement("button");
    document.body.appendChild(anchorEl);

    render(
      <FilterValuePickerPopup
        anchorEl={anchorEl}
        filter={{ field: "broken-value", value: [] }}
        onClose={vi.fn()}
        onApply={vi.fn()}
        source="traces"
      />,
    );

    expect(screen.getByText("Retained")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Retry" }));
    expect(retryFreshPage).toHaveBeenCalledOnce();
    document.body.removeChild(anchorEl);
  });

  it("discloses sampled results and lets users specify a stored-only exact value", () => {
    useResolvedFilterOptionsMock.mockReturnValue({
      options: [{ value: "configured", label: "Configured" }],
      isLoading: false,
      isError: false,
      fetchNextPage: vi.fn(),
      hasNextPage: false,
      isFetchingNextPage: false,
      isFetchNextPageError: false,
      queryReadState: "sampled",
      refetch: vi.fn(),
    });
    const onApply = vi.fn();
    const anchorEl = document.createElement("button");
    document.body.appendChild(anchorEl);

    render(
      <FilterValuePickerPopup
        anchorEl={anchorEl}
        filter={{
          field: "annotation-label",
          field_type: "annotation",
          value: [],
        }}
        onClose={vi.fn()}
        onApply={onApply}
        source="traces"
      />,
    );

    expect(screen.getByRole("status")).toHaveTextContent(
      "Showing configured or recent suggestions only",
    );
    fireEvent.change(screen.getByPlaceholderText("Search..."), {
      target: { value: "historical-only" },
    });
    fireEvent.click(screen.getByText("historical-only"));
    fireEvent.click(screen.getByRole("button", { name: "Add" }));

    expect(onApply).toHaveBeenCalledWith(["historical-only"], ["string"]);
    document.body.removeChild(anchorEl);
  });

  it("finds and applies a stored value when its display label differs", () => {
    useResolvedFilterOptionsMock.mockReturnValue({
      options: [{ value: "refund_code", label: "Refund requested" }],
      isLoading: false,
      isError: false,
      fetchNextPage: vi.fn(),
      hasNextPage: false,
      isFetchingNextPage: false,
      isFetchNextPageError: false,
      queryReadState: "complete",
      refetch: vi.fn(),
    });
    const onApply = vi.fn();
    const anchorEl = document.createElement("button");
    document.body.appendChild(anchorEl);

    render(
      <FilterValuePickerPopup
        anchorEl={anchorEl}
        filter={{ field: "annotation-label", value: [] }}
        onClose={vi.fn()}
        onApply={onApply}
        source="traces"
      />,
    );
    fireEvent.change(screen.getByPlaceholderText("Search..."), {
      target: { value: "refund_code" },
    });
    fireEvent.click(screen.getByText("Refund requested"));
    fireEvent.click(screen.getByRole("button", { name: "Add" }));

    expect(onApply).toHaveBeenCalledWith(["refund_code"], ["string"]);
    document.body.removeChild(anchorEl);
  });

  it("resynchronizes selections and search when the active filter changes", () => {
    useResolvedFilterOptionsMock.mockReturnValue({
      options: [
        { value: "alpha", label: "Alpha", type: "string" },
        { value: "beta", label: "Beta", type: "string" },
        { value: "gamma", label: "Gamma", type: "string" },
      ],
      isLoading: false,
      isError: false,
      fetchNextPage: vi.fn(),
      hasNextPage: false,
      isFetchingNextPage: false,
      isFetchNextPageError: false,
      queryReadState: "complete",
      refetch: vi.fn(),
    });
    const onApply = vi.fn();
    const anchorEl = document.createElement("button");
    document.body.appendChild(anchorEl);
    const renderPicker = (filter) => (
      <FilterValuePickerPopup
        anchorEl={anchorEl}
        filter={filter}
        onClose={vi.fn()}
        onApply={onApply}
        source="traces"
      />
    );
    const { rerender } = render(
      renderPicker({
        field: "first-property",
        value: ["alpha"],
        valueTypes: ["string"],
      }),
    );

    fireEvent.click(screen.getByText("Beta"));
    fireEvent.change(screen.getByPlaceholderText("Search..."), {
      target: { value: "beta" },
    });
    rerender(
      renderPicker({
        field: "second-property",
        value: ["gamma"],
        valueTypes: ["string"],
      }),
    );

    expect(screen.getByPlaceholderText("Search...")).toHaveValue("");
    fireEvent.click(screen.getByRole("button", { name: "Add" }));
    expect(onApply).toHaveBeenLastCalledWith(["gamma"], ["string"]);
    document.body.removeChild(anchorEl);
  });

  describe("saved array membership popup", () => {
    let anchorEl;
    afterEach(() => anchorEl?.remove());

    const savedWire = {
      filter_type: "array",
      filter_op: "contains",
      filter_value: ["hit"],
      col_type: "SPAN_ATTRIBUTE",
    };
    const setOptions = (options) =>
      useResolvedFilterOptionsMock.mockReturnValue({
        options,
        isLoading: false,
        isError: false,
        hasNextPage: false,
        isFetchingNextPage: false,
        isFetchNextPageError: false,
        queryReadState: "complete",
        refetch: vi.fn(),
      });
    const checkbox = (label) =>
      within(screen.getByText(label).parentElement).getByRole("checkbox");
    const openSaved = (wire, options, overrides = {}) => {
      anchorEl = document.createElement("button");
      document.body.appendChild(anchorEl);
      setOptions(options);
      const filter = {
        id: "tags",
        registryId: "custom_attribute:tags",
        type: "custom_attribute",
        ...restoreWidgetFilterConfig(wire),
        ...overrides,
      };
      const onApply = vi.fn();
      const picker = (activeFilter) => (
        <FilterValuePickerPopup
          anchorEl={anchorEl}
          filter={activeFilter}
          source="traces"
          onClose={vi.fn()}
          onApply={onApply}
        />
      );
      return { ...render(picker(filter)), filter, onApply, picker };
    };

    it.each(["contains", "not_contains"])(
      "restores literal saved %s members as checked and deselects once without duplicates",
      (filterOp) => {
        const wire = { ...savedWire, filter_op: filterOp };
        const { filter, onApply } = openSaved(wire, [
          { value: "hit", label: "Hit", type: "array" },
          { value: "other", label: "Other", type: "array" },
        ]);
        expect(filter.valueTypes).toBeUndefined();
        expect(checkbox("Hit")).toBeChecked();
        fireEvent.click(screen.getByText("Hit"));
        expect(checkbox("Hit")).not.toBeChecked();
        expect(screen.getByRole("button", { name: "Add" })).toBeDisabled();
        fireEvent.click(screen.getByText("Other"));
        fireEvent.click(screen.getByRole("button", { name: "Add" }));
        expect(onApply).toHaveBeenCalledWith(["other"], ["array"]);
        const [value, valueTypes] = onApply.mock.calls[0];
        expect(
          buildWidgetFilterConfig({ ...filter, value, valueTypes }),
        ).toEqual({
          filter_type: "array",
          filter_op: filterOp,
          filter_value: ["other"],
          col_type: "SPAN_ATTRIBUTE",
        });
      },
    );

    it("keeps Specify members selected after apply, save and reopen without array wire tags", () => {
      const { filter, onApply, rerender, picker } = openSaved(savedWire, [
        { value: "hit", label: "Hit", type: "array" },
      ]);
      fireEvent.change(screen.getByPlaceholderText("Search..."), {
        target: { value: "miss" },
      });
      fireEvent.click(screen.getByText("miss"));
      fireEvent.click(screen.getByRole("button", { name: "Add" }));
      expect(onApply).toHaveBeenCalledWith(["hit", "miss"], ["array", "array"]);
      const [value, valueTypes] = onApply.mock.calls[0];
      const applied = { ...filter, value, valueTypes };
      rerender(null);
      rerender(picker(applied));
      fireEvent.change(screen.getByPlaceholderText("Search..."), {
        target: { value: "miss" },
      });
      expect(
        within(
          screen.getByText("miss").closest("[data-widget-filter-exact-value]"),
        ).getByRole("checkbox"),
      ).toBeChecked();

      const wire = buildWidgetFilterConfig(applied);
      expect(wire).toEqual({ ...savedWire, filter_value: ["hit", "miss"] });
      expect(wire).not.toHaveProperty("attribute_value_types");
      rerender(null);
      // Once this member is observed, the fetched option must have the same
      // identity as the earlier Specify entry, even after saved-wire hydration.
      setOptions([
        { value: "hit", label: "Hit", type: "array" },
        { value: "miss", label: "Miss", type: "array" },
      ]);
      rerender(picker({ ...filter, ...restoreWidgetFilterConfig(wire) }));
      expect(checkbox("Hit")).toBeChecked();
      expect(checkbox("Miss")).toBeChecked();
      fireEvent.click(screen.getByText("Miss"));
      fireEvent.click(screen.getByRole("button", { name: "Add" }));
      expect(onApply).toHaveBeenLastCalledWith(["hit"], ["array"]);
    });

    it.each([
      {
        action: "selects",
        initial: ["hidden", false, "false"],
        expected: ["hidden", false, "false", 0, "0"],
      },
      {
        action: "deselects",
        initial: ["hidden", false, "false", 0, "0"],
        expected: ["hidden", false, "false"],
      },
    ])(
      "$action visible typed members without losing hidden saved array selections",
      ({ initial, expected }) => {
        const { filter, onApply } = openSaved(
          { ...savedWire, filter_value: initial },
          [
            { value: "hidden", label: "Hidden", type: "array" },
            { value: false, label: "False boolean", type: "array" },
            { value: "false", label: "False text", type: "array" },
            { value: 0, label: "Zero number", type: "array" },
            { value: "0", label: "Zero text", type: "array" },
          ],
        );
        fireEvent.change(screen.getByPlaceholderText("Search..."), {
          target: { value: "Zero" },
        });
        fireEvent.click(screen.getByText("Select all in list (2)"));
        fireEvent.click(screen.getByRole("button", { name: "Add" }));
        expect(onApply).toHaveBeenCalledWith(
          expected,
          expected.map(() => "array"),
        );
        const [value, valueTypes] = onApply.mock.calls[0];
        expect(
          buildWidgetFilterConfig({ ...filter, value, valueTypes }),
        ).toEqual({
          ...savedWire,
          filter_value: expected,
        });
      },
    );

    it("resynchronizes only-dataType changes without retaining scalar or unsaved selections", () => {
      const { filter, onApply, rerender, picker } = openSaved(
        savedWire,
        [
          { value: "hit", label: "Hit", type: "string" },
          { value: "extra", label: "Extra", type: "string" },
        ],
        { dataType: "string" },
      );
      expect(checkbox("Hit")).toBeChecked();
      fireEvent.click(screen.getByText("Extra"));
      fireEvent.change(screen.getByPlaceholderText("Search..."), {
        target: { value: "Hit" },
      });
      setOptions([
        { value: "hit", label: "Hit", type: "array" },
        { value: "extra", label: "Extra", type: "array" },
      ]);
      rerender(picker({ ...filter, dataType: "array" }));
      expect(screen.getByPlaceholderText("Search...")).toHaveValue("");
      expect(checkbox("Hit")).toBeChecked();
      expect(checkbox("Extra")).not.toBeChecked();
      fireEvent.click(screen.getByRole("button", { name: "Add" }));
      expect(onApply).toHaveBeenCalledWith(["hit"], ["array"]);
    });
  });

  it("retains distinct boolean, numeric, and string option values", () => {
    useResolvedFilterOptionsMock.mockReturnValue({
      options: [
        { value: false, label: "Disabled", type: "boolean" },
        { value: 0, label: "Zero code", type: "number" },
        { value: "0", label: "String zero", type: "string" },
      ],
      isLoading: false,
      isError: false,
      fetchNextPage: vi.fn(),
      hasNextPage: false,
      isFetchingNextPage: false,
      isFetchNextPageError: false,
      queryReadState: "complete",
      refetch: vi.fn(),
    });
    const onApply = vi.fn();
    const anchorEl = document.createElement("button");
    document.body.appendChild(anchorEl);

    render(
      <FilterValuePickerPopup
        anchorEl={anchorEl}
        filter={{ field: "typed-choice", value: [] }}
        onClose={vi.fn()}
        onApply={onApply}
        source="traces"
      />,
    );
    fireEvent.click(screen.getByText("Disabled"));
    fireEvent.click(screen.getByText("Zero code"));
    fireEvent.click(screen.getByText("String zero"));
    fireEvent.click(screen.getByRole("button", { name: "Add" }));

    expect(onApply).toHaveBeenCalledWith(
      [false, 0, "0"],
      ["boolean", "number", "string"],
    );
    document.body.removeChild(anchorEl);
  });

  it.each([
    {
      label: "selects a visible value",
      initialValues: [0, "0"],
      initialTypes: ["number", "string"],
      expectedValues: [0, "0", false],
      expectedTypes: ["number", "string", "boolean"],
    },
    {
      label: "deselects a visible value",
      initialValues: [0, "0", false],
      initialTypes: ["number", "string", "boolean"],
      expectedValues: [0, "0"],
      expectedTypes: ["number", "string"],
    },
  ])(
    "preserves hidden typed selections when Select all $label",
    ({ initialValues, initialTypes, expectedValues, expectedTypes }) => {
      useResolvedFilterOptionsMock.mockReturnValue({
        options: [
          { value: false, label: "Disabled", type: "boolean" },
          { value: 0, label: "Zero code", type: "number" },
          { value: "0", label: "String zero", type: "string" },
        ],
        isLoading: false,
        isError: false,
        fetchNextPage: vi.fn(),
        hasNextPage: false,
        isFetchingNextPage: false,
        isFetchNextPageError: false,
        queryReadState: "complete",
        refetch: vi.fn(),
      });
      const onApply = vi.fn();
      const anchorEl = document.createElement("button");
      document.body.appendChild(anchorEl);

      render(
        <FilterValuePickerPopup
          anchorEl={anchorEl}
          filter={{
            field: "typed-choice",
            value: initialValues,
            valueTypes: initialTypes,
          }}
          onClose={vi.fn()}
          onApply={onApply}
          source="traces"
        />,
      );
      fireEvent.change(screen.getByPlaceholderText("Search..."), {
        target: { value: "Disabled" },
      });
      fireEvent.click(screen.getByText("Select all in list (1)"));
      fireEvent.click(screen.getByRole("button", { name: "Add" }));

      expect(onApply).toHaveBeenCalledWith(expectedValues, expectedTypes);
      document.body.removeChild(anchorEl);
    },
  );

  it("derives annotation and eval filter data types from output_type", () => {
    expect(
      getWidgetMetricDataType({
        category: "annotation_metric",
        output_type: "categorical",
      }),
    ).toBe("string");
    expect(
      getWidgetMetricDataType({
        category: "annotation_metric",
        output_type: "numeric",
      }),
    ).toBe("number");
    expect(
      getWidgetMetricDataType({
        category: "eval_metric",
        output_type: "CHOICE",
      }),
    ).toBe("string");
  });

  it("replaces capped catalog attributes with cursor-backed workspace attributes", () => {
    const catalogOptions = [
      { id: "latency", type: "system", source: "traces" },
      {
        id: "catalog-only-before-cap",
        type: "custom_attribute",
        source: "traces",
      },
    ];
    const cursorOptions = buildWidgetCursorAttributeOptions(
      [
        { key: "historical.after.cap", type: "number" },
        { key: "saved.string", type: "string" },
      ],
      "filter",
    );

    expect(
      mergeWidgetCursorAttributeOptions(catalogOptions, cursorOptions, true),
    ).toEqual([
      { id: "latency", type: "system", source: "traces" },
      {
        id: "historical.after.cap",
        registryId: "custom_attribute:historical.after.cap",
        name: "historical.after.cap",
        type: "custom_attribute",
        source: "traces",
        dataType: "number",
        attributeTypes: ["number"],
        attributeTypesExact: false,
      },
      {
        id: "saved.string",
        registryId: "custom_attribute:saved.string",
        name: "saved.string",
        type: "custom_attribute",
        source: "traces",
        dataType: "string",
        attributeTypes: ["string"],
        attributeTypesExact: false,
      },
    ]);
  });

  it("enforces Widget eligibility for every cursor attribute value family", () => {
    const attributes = [
      { key: "numeric.attribute", type: "number" },
      { key: "string.attribute", type: "string" },
      { key: "boolean.attribute", type: "boolean" },
      { key: "array.attribute", type: "array" },
      { key: "map.attribute", type: "map" },
      {
        key: "mixed.attribute",
        type: "string",
        types: ["string", "number"],
        types_exact: true,
      },
    ];

    expect(
      buildWidgetCursorAttributeOptions(attributes, "metric").map(
        ({ id }) => id,
      ),
    ).toEqual(["numeric.attribute", "mixed.attribute"]);
    expect(
      buildWidgetCursorAttributeOptions(attributes, "breakdown").map(
        ({ id }) => id,
      ),
    ).toEqual([
      "numeric.attribute",
      "string.attribute",
      "boolean.attribute",
      "mixed.attribute",
      "mixed.attribute",
    ]);
    for (const mode of ["filter", "metric_filter"]) {
      expect(
        buildWidgetCursorAttributeOptions(attributes, mode).map(({ id }) => id),
      ).toEqual([
        "numeric.attribute",
        "string.attribute",
        "boolean.attribute",
        "array.attribute",
        "mixed.attribute",
        "mixed.attribute",
      ]);
    }
    expect(
      buildWidgetCursorAttributeOptions(attributes, "metric").find(
        ({ id }) => id === "mixed.attribute",
      ),
    ).toEqual(
      expect.objectContaining({
        dataType: "number",
        attributeTypes: ["string", "number"],
        attributeTypesExact: true,
      }),
    );
  });

  it("uses type-safe defaults and canonical configs for Widget filters", () => {
    expect(getWidgetFilterDefaults("number")).toEqual({
      operator: "equal_to",
      value: "",
      opensValuePicker: false,
    });
    expect(getWidgetFilterDefaults("date")).toEqual({
      operator: "equal_to",
      value: "",
      opensValuePicker: false,
    });
    expect(getWidgetFilterDefaults("string")).toEqual({
      operator: "contains",
      value: [],
      opensValuePicker: true,
    });
    expect(getWidgetFilterDefaults("boolean")).toEqual({
      operator: "equal_to",
      value: "",
      opensValuePicker: false,
    });
    expect(getWidgetFilterDefaults("array")).toEqual({
      operator: "str_contains",
      value: [],
      opensValuePicker: true,
    });

    expect(
      getWidgetFilterOperators("boolean").map(({ value }) => value),
    ).toEqual(["equal_to", "not_equal_to", "is_set", "is_not_set"]);
    expect(
      getWidgetFilterOperators("number").map(({ value }) => value),
    ).toEqual([
      "equal_to",
      "not_equal_to",
      "greater_than",
      "greater_than_or_equal",
      "less_than",
      "less_than_or_equal",
      "between",
      "not_between",
      "is_set",
      "is_not_set",
    ]);
    expect(getWidgetFilterOperators("date")).toEqual(
      getWidgetFilterOperators("number"),
    );
    expect(getWidgetFilterOperators("array").map(({ value }) => value)).toEqual(
      ["str_contains", "str_not_contains", "is_set", "is_not_set"],
    );

    expect(
      buildWidgetFilterConfig({
        type: "custom_attribute",
        dataType: "number",
        operator: "equal_to",
        value: "7.5",
      }),
    ).toEqual({
      filter_type: "number",
      filter_op: "equals",
      filter_value: 7.5,
      col_type: "SPAN_ATTRIBUTE",
    });
    expect(
      buildWidgetFilterConfig({
        type: "system",
        dataType: "date",
        operator: "greater_than",
        value: "2026-08-25T11:00:00Z",
      }),
    ).toEqual({
      filter_type: "datetime",
      filter_op: "greater_than",
      filter_value: "2026-08-25T11:00:00Z",
      col_type: "SYSTEM_METRIC",
    });
    expect(
      buildWidgetFilterConfig({
        type: "custom_attribute",
        dataType: "number",
        operator: "is_not_set",
        value: "",
      }),
    ).toEqual({
      filter_type: "number",
      filter_op: "is_null",
      filter_value: null,
      col_type: "SPAN_ATTRIBUTE",
    });
    expect(
      buildWidgetFilterConfig({
        type: "custom_attribute",
        dataType: "number",
        operator: "is_set",
        value: "",
      }),
    ).toEqual({
      filter_type: "number",
      filter_op: "is_not_null",
      filter_value: null,
      col_type: "SPAN_ATTRIBUTE",
    });
    expect(
      buildWidgetFilterConfig({
        type: "custom_attribute",
        dataType: "string",
        operator: "contains",
        value: ["paid", "retry"],
        valueTypes: ["string", "number"],
      }),
    ).toEqual({
      filter_type: "text",
      filter_op: "in",
      filter_value: ["paid", "retry"],
      col_type: "SPAN_ATTRIBUTE",
      attribute_value_types: ["string", "number"],
    });
    expect(
      buildWidgetFilterConfig({
        type: "custom_attribute",
        dataType: "boolean",
        operator: "not_equal_to",
        value: "false",
      }),
    ).toEqual({
      filter_type: "boolean",
      filter_op: "not_equals",
      filter_value: false,
      col_type: "SPAN_ATTRIBUTE",
    });
    expect(
      buildWidgetFilterConfig({
        type: "custom_attribute",
        dataType: "array",
        operator: "str_not_contains",
        value: ["paid", 7, false],
      }),
    ).toEqual({
      filter_type: "array",
      filter_op: "not_contains",
      filter_value: ["paid", 7, false],
      col_type: "SPAN_ATTRIBUTE",
    });

    expect(
      restoreWidgetFilterConfig({
        filter_type: "text",
        filter_op: "in",
        filter_value: ["paid", 7, false],
        attribute_value_types: ["string", "number", "boolean"],
      }),
    ).toEqual({
      dataType: "string",
      operator: "contains",
      value: ["paid", 7, false],
      valueTypes: ["string", "number", "boolean"],
      attributeTypes: ["string", "number", "boolean"],
    });
  });

  it("retains boolean false while rejecting missing Widget filter values", () => {
    expect(
      hasWidgetFilterValue({
        id: "enabled",
        operator: "equal_to",
        value: false,
      }),
    ).toBe(true);
    expect(
      hasWidgetFilterValue({ id: "enabled", operator: "equal_to", value: "" }),
    ).toBe(false);
    expect(
      hasWidgetFilterValue({ id: "tags", operator: "str_contains", value: [] }),
    ).toBe(false);
  });

  it.each([
    ["metric", { selectedMetricSources: [], targetMetricSource: null }],
    ["filter", { selectedMetricSources: ["traces"], targetMetricSource: null }],
    [
      "metric_filter",
      { selectedMetricSources: [], targetMetricSource: "traces" },
    ],
    [
      "breakdown",
      { selectedMetricSources: ["traces"], targetMetricSource: null },
    ],
  ])(
    "keeps System and custom cost matches without stale base rows in %s mode",
    (pickerMode, adapterContext) => {
      const baseMetrics = [
        {
          name: "latency",
          display_name: "Latency",
          property_id: "system_metric:traces:latency",
          category: "system_metric",
          source: "traces",
          sources: ["traces"],
          type: "number",
          role: "metric",
        },
      ];
      const scopedMetrics = [
        {
          name: "cost",
          display_name: "Cost",
          property_id: "system_metric:traces:cost",
          category: "system_metric",
          source: "traces",
          sources: ["traces"],
          type: "number",
          role: "metric",
        },
        {
          name: "cost_breakdown.analysisCost.total",
          display_name: "cost_breakdown.analysisCost.total",
          property_id:
            "custom_attribute:traces:cost_breakdown.analysisCost.total",
          category: "custom_attribute",
          source: "traces",
          sources: ["traces"],
          type: "number",
          role: "metric",
        },
      ];
      const activeMetrics = resolveWidgetCatalogResultMetrics({
        baseMetrics,
        scopedMetrics,
        scopedRequestActive: true,
        requestSettled: true,
      });
      const options = buildWidgetCatalogPickerOptions({
        metrics: activeMetrics,
        pickerMode,
        pickerCategory: "all",
        search: "cost",
        requestSettled: true,
        ...adapterContext,
      });

      expect(options.map(({ id }) => id)).toEqual([
        "cost",
        "cost_breakdown.analysisCost.total",
      ]);
      expect(options.map(({ type }) => type)).toEqual([
        "system",
        "custom_attribute",
      ]);
      expect(options.some(({ id }) => id === "latency")).toBe(false);
      expect(
        resolveWidgetCatalogResultMetrics({
          baseMetrics,
          scopedMetrics,
          scopedRequestActive: true,
          requestSettled: false,
        }),
      ).toEqual([]);
    },
  );

  it.each([
    ["metric", {}, ["number"]],
    ["filter", { selectedMetricSources: ["traces"] }, ["string", "number"]],
    ["metric_filter", { targetMetricSource: "traces" }, ["string", "number"]],
    ["breakdown", { selectedMetricSources: ["traces"] }, ["string", "number"]],
  ])(
    "expands active mixed attributes into eligible scalar lanes in %s mode",
    (pickerMode, adapterContext, expectedDataTypes) => {
      const options = buildWidgetCatalogPickerOptions({
        metrics: [
          {
            name: "mixed.status",
            display_name: "Mixed status",
            property_id: "custom_attribute:mixed.status",
            category: "custom_attribute",
            source: "traces",
            sources: ["traces"],
            type: "json",
            attribute_types: ["string", "number"],
            attribute_types_exact: true,
            role: "metric",
          },
        ],
        pickerMode,
        pickerCategory: "all",
        ...adapterContext,
      });

      expect(options.map(({ dataType }) => dataType)).toEqual(
        expectedDataTypes,
      );
      expect(options).not.toContainEqual(
        expect.objectContaining({ dataType: "json" }),
      );
      expect(options).toEqual(
        expect.arrayContaining([
          expect.objectContaining({
            attributeTypes: ["string", "number"],
            attributeTypesExact: true,
          }),
        ]),
      );
    },
  );

  it.each(["metric", "filter", "metric_filter", "breakdown"])(
    "strictly isolates an explicit Dashboard category in %s mode",
    (pickerMode) => {
      const metrics = [
        {
          name: "cost",
          display_name: "Cost",
          property_id: "system_metric:traces:cost",
          category: "system_metric",
          source: "traces",
          sources: ["traces"],
          type: "number",
          role: "metric",
        },
        {
          name: "cost_breakdown.total",
          property_id: "custom_attribute:traces:cost_breakdown.total",
          category: "custom_attribute",
          source: "traces",
          sources: ["traces"],
          type: "number",
          role: "metric",
        },
      ];
      const adapterContext =
        pickerMode === "metric_filter"
          ? { targetMetricSource: "traces" }
          : pickerMode === "metric"
            ? {}
            : { selectedMetricSources: ["traces"] };

      expect(
        buildWidgetCatalogPickerOptions({
          metrics,
          pickerMode,
          pickerCategory: "custom_attribute",
          search: "cost",
          ...adapterContext,
        }).map(({ id }) => id),
      ).toEqual(["cost_breakdown.total"]);
      expect(
        buildWidgetCatalogPickerOptions({
          metrics,
          pickerMode,
          pickerCategory: "trace",
          search: "cost",
          ...adapterContext,
        }).map(({ id }) => id),
      ).toEqual(["cost"]);
    },
  );

  it("keeps exact search counts stable while category result counts stay scoped", () => {
    const allSearchCounts = {
      all: 35,
      system_metric: 6,
      eval_metric: 0,
      annotation_metric: 0,
      custom_attribute: 29,
      custom_column: 0,
    };
    const customResponseCounts = {
      all: 29,
      system_metric: 0,
      eval_metric: 0,
      annotation_metric: 0,
      custom_attribute: 29,
      custom_column: 0,
    };
    const sidebarCounts = resolveWidgetCatalogSidebarCounts({
      requestSettled: true,
      search: "cost",
      baseCategoryCounts: null,
      baseCategoryCountsExact: false,
      allSearchCategoryCounts: allSearchCounts,
      allSearchCategoryCountsExact: true,
    });

    expect(
      getWidgetCatalogSidebarCategoryCount({
        pickerCategory: "all",
        categoryCounts: sidebarCounts,
        categoryCountsExact: true,
      }),
    ).toBe(35);
    expect(
      getWidgetCatalogSidebarCategoryCount({
        pickerCategory: "custom_attribute",
        categoryCounts: sidebarCounts,
        categoryCountsExact: true,
      }),
    ).toBe(29);
    expect(
      getWidgetCatalogExactResultCount({
        request: { category: "custom_attribute" },
        categoryCounts: customResponseCounts,
        categoryCountsExact: true,
        requestSettled: true,
      }),
    ).toBe(29);
    expect(
      getWidgetCatalogExactResultCount({
        request: { category: "" },
        categoryCounts: allSearchCounts,
        categoryCountsExact: true,
        requestSettled: false,
      }),
    ).toBeNull();
    expect(
      resolveWidgetCatalogSidebarCounts({
        requestSettled: true,
        search: "cost",
        baseCategoryCounts: allSearchCounts,
        baseCategoryCountsExact: true,
        allSearchCategoryCounts: null,
        allSearchCategoryCountsExact: false,
      }),
    ).toBeNull();
    expect(getWidgetCatalogCategoryCountPresentation(29)).toEqual({
      text: "29",
      title: null,
      exact: true,
    });
    const derivedPromptCount = getWidgetCatalogSidebarCategoryCount({
      pickerCategory: "prompt",
      categoryCounts: sidebarCounts,
      categoryCountsExact: true,
    });
    expect(derivedPromptCount).toBeNull();
    expect(
      getWidgetCatalogCategoryCountPresentation(derivedPromptCount),
    ).toEqual({
      text: "—",
      title: "Exact count unavailable",
      exact: false,
    });
  });

  it("uses one 20-item unified catalog for every property category", () => {
    expect(
      getWidgetMetricCatalogRequest({
        pickerCategory: "all",
        search: "historical.attribute",
        pickerOpen: true,
      }),
    ).toEqual(
      expect.objectContaining({
        enabled: true,
        excludeCustomAttributes: true,
        pageSize: 20,
        role: "metric",
      }),
    );
    expect(
      getWidgetMetricCatalogRequest({
        pickerCategory: "eval_metric",
        search: "historical.attribute",
        pickerOpen: true,
      }),
    ).toEqual(
      expect.objectContaining({
        enabled: true,
        excludeCustomAttributes: true,
        pageSize: 20,
        role: "metric",
      }),
    );
    expect(
      getWidgetMetricCatalogRequest({
        pickerCategory: "custom_attribute",
        search: "historical.attribute",
        pickerOpen: true,
      }),
    ).toEqual(
      expect.objectContaining({
        category: "custom_attribute",
        enabled: true,
        excludeCustomAttributes: true,
        pageSize: 20,
        role: "metric",
        source: "traces",
      }),
    );
    expect(
      getWidgetMetricCatalogRequest({
        pickerCategory: "all",
        search: "model",
        pickerOpen: true,
        pickerMode: "filter",
      }),
    ).toEqual(expect.objectContaining({ role: "" }));
  });
});
