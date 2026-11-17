// HO D3 review fix: a Collaboration field's live key answers keep (`in`)
// and drop (`not_in`) only, so the filter panel offers exactly `equals` and
// `not equals` for it (TraceFilterPanel narrows a property's operators by
// `property.operators`); a plain text column keeps its full list.
import React from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, within } from "src/utils/test-utils";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import TraceFilterPanel from "src/sections/projects/LLMTracing/TraceFilterPanel";
import {
  COLLAB_FIELD,
  COLLAB_OPERATORS,
  collabFieldProperties,
} from "../collabFilterFields";

const propertyCatalogMock = vi.hoisted(() => vi.fn());
const dashboardFilterValuesMock = vi.hoisted(() => vi.fn());
const exactAttributePropertiesMock = vi.hoisted(() => vi.fn());
vi.mock("src/hooks/use-ai-filter", () => ({
  useAIFilter: () => ({ parseQuery: vi.fn(), loading: false, error: null }),
}));
vi.mock("src/hooks/useDashboards", async (importOriginal) => ({
  ...(await importOriginal()),
  useDashboardFilterValues: dashboardFilterValuesMock,
  usePropertyCatalog: propertyCatalogMock,
}));
vi.mock(
  "src/sections/projects/LLMTracing/useExactTraceAttributeProperties",
  () => ({ useExactTraceAttributeProperties: exactAttributePropertiesMock }),
);

const PROPS = collabFieldProperties({
  assignments: {
    me: { id: "u1", name: "Maya Chen" },
    people: [{ id: "u2", name: "Ravi Menon" }],
  },
  translate: {
    dataset_language_name: "English",
    languages: [
      { code: "en", name: "English" },
      { code: "es", name: "Spanish" },
    ],
  },
});
const TEXT_COLUMN = {
  id: "c-msg",
  name: "customer_message",
  category: "custom",
  type: "string",
};
const Picker = () => <input aria-label="value" readOnly value="" />;

function operatorsOf(field) {
  const anchorEl = document.createElement("button");
  document.body.appendChild(anchorEl);
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const utils = render(
    <QueryClientProvider client={qc}>
      <TraceFilterPanel
        anchorEl={anchorEl}
        open
        onClose={vi.fn()}
        onApply={vi.fn()}
        currentFilters={[
          { field, fieldType: "string", operator: "in", value: [] },
        ]}
        properties={[TEXT_COLUMN, ...PROPS]}
        ValuePickerOverride={Picker}
        projectId="ds1"
        source="dataset"
      />
    </QueryClientProvider>,
  );
  const op = screen
    .getAllByRole("combobox")
    .find((c) => c.textContent.trim() === "equals");
  fireEvent.mouseDown(op);
  const listbox = screen.getByRole("listbox");
  const ops = within(listbox)
    .getAllByRole("option")
    .map((o) => o.textContent.trim());
  utils.unmount();
  document.body.removeChild(anchorEl);
  return ops;
}

describe("HO D3 · Collaboration fields offer only equals / not equals", () => {
  beforeEach(() => {
    propertyCatalogMock.mockReturnValue({
      metrics: [],
      legacyFallbackRequired: true,
      error: null,
    });
    dashboardFilterValuesMock.mockReturnValue({
      data: [],
      isLoading: false,
      isError: false,
      fetchNextPage: vi.fn(),
      hasNextPage: false,
      isFetchingNextPage: false,
    });
    exactAttributePropertiesMock.mockReturnValue({
      data: [],
      isFetching: false,
      fetchNextPage: vi.fn(),
      hasNextPage: false,
      isFetchingNextPage: false,
    });
  });

  it("every collaboration property declares keep / drop only", () => {
    expect(COLLAB_OPERATORS).toEqual(["in", "not_in"]);
    expect(PROPS.map((p) => p.id)).toEqual(Object.values(COLLAB_FIELD));
    PROPS.forEach((p) => expect(p.operators).toEqual(["in", "not_in"]));
  });

  it.each(Object.values(COLLAB_FIELD))(
    "%s: the panel's operator list is equals / not equals",
    (field) => {
      expect(operatorsOf(field)).toEqual(["equals", "not equals"]);
    },
  );

  it("a text column keeps its full operator list (contains, is null, …)", () => {
    const ops = operatorsOf(TEXT_COLUMN.id);
    expect(ops).toEqual(expect.arrayContaining(["contains", "is null"]));
    expect(ops.length).toBeGreaterThan(2);
  });
});
