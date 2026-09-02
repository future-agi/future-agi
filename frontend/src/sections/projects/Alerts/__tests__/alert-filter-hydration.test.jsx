import React from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { describe, expect, it, vi } from "vitest";
import {
  fireEvent,
  renderWithRouter,
  screen,
  waitFor,
  within,
} from "src/utils/test-utils";

import TraceFilterPanel from "src/sections/projects/LLMTracing/TraceFilterPanel";
import {
  CATEGORIES,
  SPAN_TYPE_PROPERTY,
  toFormRows,
  toPanelRows,
  toPanelType,
} from "../components/alertFilterRows";
import { convertFiltersToPayload } from "../common";
import { transformFilterResponse } from "../components/validation";

const properties = [
  SPAN_TYPE_PROPERTY,
  {
    id: "confidence_score",
    name: "confidence_score",
    category: "attribute",
    rawCategory: "custom_attribute",
    type: "number",
    typeSelectable: true,
    apiColType: "SPAN_ATTRIBUTE",
  },
  {
    id: "region",
    name: "region",
    category: "attribute",
    rawCategory: "custom_attribute",
    type: toPanelType("string"),
    apiColType: "SPAN_ATTRIBUTE",
  },
  {
    id: "customer_tier",
    name: "customer_tier",
    category: "attribute",
    rawCategory: "custom_attribute",
    type: toPanelType("string"),
    apiColType: "SPAN_ATTRIBUTE",
  },
];

// Round-trip a saved alert through the *real panel*, not just the conversion
// helpers: the panel rewrites operators on hydration, which the helper-only
// round-trip test cannot see.
const openPanelWith = (payload) => {
  const onApply = vi.fn();
  renderWithRouter(
    <QueryClientProvider
      client={
        new QueryClient({ defaultOptions: { queries: { retry: false } } })
      }
    >
      <TraceFilterPanel
        anchorEl={document.body}
        open
        onClose={vi.fn()}
        onApply={onApply}
        currentFilters={toPanelRows(transformFilterResponse(payload))}
        properties={properties}
        categories={CATEGORIES}
        projectId="test-project"
        showAi={false}
        showQueryTab={false}
      />
    </QueryClientProvider>,
  );
  return onApply;
};

describe("saved alert survives a visit to the filter panel", () => {
  it("emits nothing when the panel is merely opened", async () => {
    const onApply = openPanelWith({
      observation_type: [],
      span_attributes_filters: [
        {
          column_id: "customer_tier",
          filter_config: {
            filter_type: "text",
            filter_op: "equals",
            filter_value: "enterprise",
            col_type: "SPAN_ATTRIBUTE",
          },
        },
      ],
    });
    await new Promise((r) => setTimeout(r, 700));
    expect(onApply).not.toHaveBeenCalled();
  });

  it("does not rewrite an untouched row's saved operator when another row is edited", async () => {
    const payload = {
      observation_type: [],
      span_attributes_filters: [
        {
          column_id: "customer_tier",
          filter_config: {
            filter_type: "text",
            filter_op: "equals",
            filter_value: "enterprise",
            col_type: "SPAN_ATTRIBUTE",
          },
        },
        {
          column_id: "region",
          filter_config: {
            filter_type: "text",
            filter_op: "equals",
            filter_value: "us-east",
            col_type: "SPAN_ATTRIBUTE",
          },
        },
      ],
    };

    const onApply = openPanelWith(payload);

    // Edit the SECOND row's operator; the first row must survive verbatim.
    const combos = screen.getAllByRole("combobox");
    fireEvent.mouseDown(combos[combos.length - 1]);
    const listbox = await screen.findByRole("listbox");
    fireEvent.click(within(listbox).getByText("contains"));

    await waitFor(() => expect(onApply).toHaveBeenCalled());
    // The panel row legitimately holds `in`; what must not drift is the
    // payload the API receives for the row nobody touched.
    const sent = convertFiltersToPayload(
      toFormRows(onApply.mock.calls.at(-1)[0]),
    );
    const untouched = sent.span_attributes_filters.find(
      (f) => f.column_id === "customer_tier",
    );
    expect(untouched.filter_config.filter_op).toBe("equals");
    expect(untouched.filter_config.filter_value).toBe("enterprise");
  });

  it("saves an edited numeric value as a number, not the input's string", async () => {
    // The panel's numeric input is a plain TextField. The old form ran
    // parseFloat before storing, so an edit must not change filter_value's
    // type on save.
    const payload = {
      observation_type: [],
      span_attributes_filters: [
        {
          column_id: "confidence_score",
          filter_config: {
            filter_type: "number",
            filter_op: "greater_than",
            filter_value: 0.8,
            col_type: "SPAN_ATTRIBUTE",
          },
        },
      ],
    };

    const onApply = openPanelWith(payload);

    const input = screen.getByDisplayValue("0.8");
    fireEvent.change(input, { target: { value: "0.95" } });

    await waitFor(() => expect(onApply).toHaveBeenCalled());
    const sent = convertFiltersToPayload(
      toFormRows(onApply.mock.calls.at(-1)[0]),
    );
    const value = sent.span_attributes_filters[0].filter_config.filter_value;
    expect(value).toBe(0.95);
    expect(typeof value).toBe("number");
  });
});
