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
  buildWidgetFilterConfig,
  buildWidgetCursorAttributeOptions,
  FilterValuePickerPopup,
  getWidgetFilterDefaults,
  getWidgetFilterOperators,
  getWidgetMetricCatalogRequest,
  getWidgetMetricDataType,
  hasWidgetFilterValue,
  mergeWidgetCursorAttributeOptions,
  restoreWidgetFilterConfig,
} from "../WidgetEditorView";

describe("WidgetEditor filter-value picker", () => {
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
        name: "historical.after.cap",
        type: "custom_attribute",
        source: "traces",
        dataType: "number",
        attributeTypes: ["number"],
        attributeTypesExact: false,
      },
      {
        id: "saved.string",
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

  it("keeps every finite category off the capped custom-attribute catalog", () => {
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
        enabled: false,
        excludeCustomAttributes: true,
      }),
    );
  });
});
