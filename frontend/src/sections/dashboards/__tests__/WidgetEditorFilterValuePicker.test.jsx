import React from "react";
import { describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen } from "src/utils/test-utils";

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
  buildWidgetFilterConfig,
  buildWidgetCursorAttributeOptions,
  FilterValuePickerPopup,
  getWidgetFilterDefaults,
  getWidgetFilterOperators,
  getWidgetMetricCatalogRequest,
  getWidgetMetricDataType,
  hasWidgetFilterValue,
  isWidgetCatalogOptionAllowed,
  mergeWidgetCursorAttributeOptions,
  restoreWidgetFilterConfig,
  WidgetCatalogPaginationControl,
} from "../WidgetEditorView";

describe("WidgetEditor filter-value picker", () => {
  it("loads each property catalog page only through an explicit single-flight action", () => {
    const fetchNextPage = vi.fn(() => new Promise(() => {}));
    const { rerender } = render(
      <WidgetCatalogPaginationControl
        pickerCategory="all"
        hasNextPage
        isFetchingNextPage={false}
        onLoadMore={fetchNextPage}
      />,
    );

    const loadMore = screen.getByRole("button", {
      name: "Load more",
    });
    fireEvent.click(loadMore);
    fireEvent.click(loadMore);
    expect(fetchNextPage).toHaveBeenCalledOnce();

    rerender(
      <WidgetCatalogPaginationControl
        pickerCategory="all"
        hasNextPage
        isFetchingNextPage
        onLoadMore={fetchNextPage}
      />,
    );
    expect(screen.getByRole("status")).toHaveTextContent("Loading more");
  });

  it("advances both All-category cursors through one single-flight action", () => {
    const fetchNextPage = vi.fn(() => new Promise(() => {}));
    const fetchNextAttributePage = vi.fn(() => new Promise(() => {}));
    render(
      <WidgetCatalogPaginationControl
        pickerCategory="all"
        hasNextPage
        isFetchingNextPage={false}
        onLoadMore={fetchNextPage}
        attributeHasNextPage
        isFetchingAttributeNextPage={false}
        onLoadMoreAttributes={fetchNextAttributePage}
      />,
    );

    const loadMore = screen.getByRole("button", { name: "Load more" });
    expect(screen.getAllByRole("button", { name: "Load more" })).toHaveLength(
      1,
    );
    fireEvent.click(loadMore);
    fireEvent.click(loadMore);

    expect(fetchNextPage).toHaveBeenCalledOnce();
    expect(fetchNextAttributePage).toHaveBeenCalledOnce();
    expect(screen.getByRole("status")).toHaveTextContent("Loading more");
  });

  it("advances the unified catalog cursor in the trace-attribute category", () => {
    const fetchNextPage = vi.fn();
    render(
      <WidgetCatalogPaginationControl
        pickerCategory="custom_attribute"
        hasNextPage
        isFetchingNextPage={false}
        onLoadMore={fetchNextPage}
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: "Load more" }));
    expect(fetchNextPage).toHaveBeenCalledOnce();
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
        enabled: true,
        excludeCustomAttributes: true,
        pageSize: 20,
        role: "metric",
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
