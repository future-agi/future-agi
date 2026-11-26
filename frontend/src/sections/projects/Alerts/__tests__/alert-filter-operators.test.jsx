import React from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { describe, expect, it, vi } from "vitest";
import {
  fireEvent,
  renderWithRouter,
  screen,
  within,
} from "src/utils/test-utils";

import TraceFilterPanel from "src/sections/projects/LLMTracing/TraceFilterPanel";
import { CATEGORIES, SPAN_TYPE_PROPERTY } from "../components/alertFilterRows";

// The row owns its type: the API does not say what an attribute holds, so the
// user picks, and the operators follow that choice.
const ATTRIBUTES = ["customer_tier", "confidence_score", "cache_hit"];

// Mirrors how AlertFilterBar builds `properties`.
const properties = [
  SPAN_TYPE_PROPERTY,
  ...ATTRIBUTES.map((key) => ({
    id: key,
    name: key,
    category: "attribute",
    rawCategory: "custom_attribute",
    type: "text",
    typeSelectable: true,
    apiColType: "SPAN_ATTRIBUTE",
  })),
];

// No property ships operators its type does not have; this stands in for one
// that does, so the fallback is exercised.
const MISDECLARED_PROPERTY = {
  id: "cache_hit_flag",
  name: "Cache Hit Flag",
  category: "system",
  rawCategory: "system_metric",
  type: "boolean",
  operators: ["contains"],
};

const renderPanel = (
  currentFilters,
  onApply = vi.fn(),
  propertyList = properties,
) =>
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
        currentFilters={currentFilters}
        properties={propertyList}
        categories={CATEGORIES}
        projectId="test-project"
        showAi={false}
        showQueryTab={false}
      />
    </QueryClientProvider>,
  );

// Row order is field (a button) -> [type] -> operator -> value. Attribute rows
// carry a Type select, so their operator is the second combobox.
const operatorOptions = async ({ hasTypeSelect = true } = {}) => {
  fireEvent.mouseDown(screen.getAllByRole("combobox")[hasTypeSelect ? 1 : 0]);
  const listbox = await screen.findByRole("listbox");
  return within(listbox)
    .getAllByRole("option")
    .map((o) => o.textContent);
};

describe("alert filter operators follow the row's chosen type", () => {
  const typeSelect = () => screen.getAllByRole("combobox")[0];

  const chooseType = async (label) => {
    fireEvent.mouseDown(typeSelect());
    fireEvent.click(
      within(await screen.findByRole("listbox")).getByText(label),
    );
  };

  it("offers the Type control on attribute rows only", async () => {
    const { unmount } = renderPanel([
      {
        field: "customer_tier",
        fieldType: "text",
        fieldCategory: "attribute",
        operator: "contains",
        value: "premium",
      },
    ]);
    fireEvent.mouseDown(typeSelect());
    expect(
      within(await screen.findByRole("listbox"))
        .getAllByRole("option")
        .map((o) => o.textContent),
    ).toEqual(["Number", "Text", "Boolean"]);
    unmount();

    // Span type is a value list on the wire; its type is not the user's to pick.
    renderPanel([
      {
        field: "observation_type",
        fieldType: "string",
        fieldCategory: "system",
        operator: "in",
        value: ["llm"],
      },
    ]);
    fireEvent.mouseDown(screen.getAllByRole("combobox")[0]);
    expect(
      within(await screen.findByRole("listbox"))
        .getAllByRole("option")
        .map((o) => o.textContent),
    ).not.toContain("Boolean");
  });

  it("resets the operator and clears the value when the type changes", async () => {
    const onApply = vi.fn();
    renderPanel(
      [
        {
          field: "confidence_score",
          fieldType: "text",
          fieldCategory: "attribute",
          operator: "contains",
          value: "0.8",
        },
      ],
      onApply,
    );

    // `contains` is not valid for a number, so it cannot survive the switch.
    await chooseType("Number");

    const options = await operatorOptions();
    expect(options).toEqual(
      expect.arrayContaining(["greater than", "between"]),
    );
    expect(options).not.toContain("contains");
  });

  it("offers span type only `is one of` — the API cannot express any other", async () => {
    renderPanel([
      {
        field: "observation_type",
        fieldType: "string",
        fieldCategory: "system",
        operator: "in",
        value: ["llm"],
      },
    ]);

    // `is not` would save as the positive; `contains` would be a no-op.
    const options = await operatorOptions({ hasTypeSelect: false });
    expect(options).toHaveLength(1);
    expect(options[0]).toBe("equals");
  });

  it("renders span type as multi-select, not a single-value radio list", () => {
    renderPanel([
      {
        field: "observation_type",
        fieldType: "string",
        fieldCategory: "system",
        operator: "in",
        value: ["llm", "retriever"],
      },
    ]);

    // `equals` would make the panel single-select while the row holds two
    // values — clicking an option would silently drop one.
    fireEvent.click(screen.getByText(/llm/i));
    expect(screen.getByText(/select one or more values/i)).toBeInTheDocument();
    expect(
      screen.queryByText(/select a single value/i),
    ).not.toBeInTheDocument();
  });

  it("offers numeric comparisons for a number attribute", async () => {
    renderPanel([
      {
        field: "confidence_score",
        fieldType: "number",
        fieldCategory: "attribute",
        operator: "greater_than",
        value: 0.8,
      },
    ]);

    const options = await operatorOptions();
    expect(options).toEqual(
      expect.arrayContaining([
        "greater than",
        "less than",
        "between",
        "greater than or equals",
      ]),
    );
    expect(options).not.toContain("contains");
  });

  it("narrows a boolean attribute to equality only", async () => {
    renderPanel([
      {
        field: "cache_hit",
        fieldType: "boolean",
        fieldCategory: "attribute",
        operator: "equals",
        value: true,
      },
    ]);

    const options = await operatorOptions();
    expect(options).toEqual(["equals", "not equals", "is null", "is not null"]);
    expect(options).not.toContain("greater than");
    expect(options).not.toContain("contains");
  });

  it("offers text matching for a string attribute", async () => {
    renderPanel([
      {
        field: "customer_tier",
        fieldType: "text",
        fieldCategory: "attribute",
        operator: "equals",
        value: "premium",
      },
    ]);

    const options = await operatorOptions();
    expect(options).toEqual(
      expect.arrayContaining(["contains", "not contains"]),
    );
    expect(options).not.toContain("between");
  });

  it("keeps the type's operators when a property declares ones its type lacks", async () => {
    renderPanel(
      [
        {
          field: "cache_hit_flag",
          fieldType: "boolean",
          fieldCategory: "system",
          operator: "equals",
          value: true,
        },
      ],
      vi.fn(),
      [...properties, MISDECLARED_PROPERTY],
    );

    // Intersecting the declared `contains` with the boolean operators leaves
    // nothing, which would render the row with an empty dropdown.
    const options = await operatorOptions({ hasTypeSelect: false });
    expect(options).toEqual(["equals", "not equals", "is null", "is not null"]);
  });
});
