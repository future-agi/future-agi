import { act, renderHook } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { rowsToFilters } from "src/sections/evals/components/evalFilterAdapter";
import { useEvalsList } from "src/sections/evals/hooks/useEvalsList";
import { useEvalPickerData } from "./useEvalPickerData";

const catalog = vi.hoisted(() => ({ rows: [] }));

vi.mock("src/hooks/use-debounce", () => ({ useDebounce: (value) => value }));
vi.mock("src/utils/axios", () => ({ default: {}, endpoints: {} }));
vi.mock("src/utils/utils", () => ({ paramsSerializer: () => ({}) }));
vi.mock("@tanstack/react-query", () => ({
  useQuery: () => ({
    data: { evals: catalog.rows },
    isLoading: false,
    isFetching: false,
  }),
}));
vi.mock("src/sections/evals/hooks/useEvalsList", () => ({
  // Model server filtering before pagination; also assert the request below.
  // No query function is executed and no network requests are made.
  useEvalsList: vi.fn(({ search, filters, page, pageSize }) => {
    const rows = catalog.rows.filter((row) => {
      if (search && !row.name.toLowerCase().includes(search.toLowerCase()))
        return false;
      if (filters?.eval_type_not?.includes(row.eval_type)) return false;
      return ["eval_type", "output_type", "template_type"].every(
        (key) => !filters?.[key]?.length || filters[key].includes(row[key]),
      );
    });
    return {
      data: {
        items: rows.slice(page * pageSize, (page + 1) * pageSize),
        total: rows.length,
      },
      isLoading: false,
      isFetching: false,
    };
  }),
}));

const lockedFilters = {
  template_type: ["single"],
  output_type: ["pass_fail"],
  eval_type_not: ["dag"],
};

function nameFilter(value) {
  return rowsToFilters([
    { field: "name", fieldType: "string", operator: "contains", value: [value] },
  ]);
}

beforeEach(() => {
  vi.clearAllMocks();
  catalog.rows = [
    ...Array.from({ length: 25 }, (_, i) => ({
      id: `beta-${i}`,
      name: `Beta ${i}`,
      eval_type: "llm",
      output_type: "pass_fail",
      template_type: "single",
    })),
    {
      id: "alpha",
      name: "Alpha",
      eval_type: "llm",
      output_type: "pass_fail",
      template_type: "single",
    },
    {
      id: "nested-alpha",
      name: "Alpha composite",
      eval_type: "llm",
      output_type: "pass_fail",
      template_type: "composite",
    },
  ];
});

describe("W10-FILTERS-L03 picker panel request filters", () => {
  it("sends Name contains before server pagination and clears it", () => {
    const { result } = renderHook(() =>
      useEvalPickerData({ sourceId: null, lockedFilters }),
    );
    expect(result.current.total).toBe(26);
    expect(result.current.items).toHaveLength(25);

    act(() => result.current.setFilters(nameFilter("aLpHa")));

    expect(result.current.filters).toEqual({ search: "aLpHa" });
    expect(useEvalsList).toHaveBeenLastCalledWith(
      expect.objectContaining({
        enabled: true,
        search: "aLpHa",
        filters: lockedFilters,
      }),
    );
    expect(result.current.items.map((item) => item.id)).toEqual(["alpha"]);
    expect(result.current.total).toBe(1);

    act(() => result.current.setFilters(rowsToFilters([])));
    expect(useEvalsList).toHaveBeenLastCalledWith(
      expect.objectContaining({ search: null, filters: lockedFilters }),
    );
    expect(result.current.total).toBe(26);
  });

  it("prefers the search box and restores the panel Name when it is cleared", () => {
    const { result } = renderHook(() => useEvalPickerData({ lockedFilters }));
    act(() => {
      result.current.setFilters(nameFilter("Alpha"));
      result.current.setSearchQuery("  Beta  ");
    });
    expect(useEvalsList).toHaveBeenLastCalledWith(
      expect.objectContaining({ search: "Beta" }),
    );
    expect(result.current.total).toBe(25);

    act(() => result.current.setSearchQuery("   "));
    expect(useEvalsList).toHaveBeenLastCalledWith(
      expect.objectContaining({ search: "Alpha" }),
    );
    expect(result.current.items.map((item) => item.id)).toEqual(["alpha"]);
    expect(result.current.total).toBe(1);
  });

  it.each(["single", "composite"])(
    "sends an unlocked %s template type as a backend list",
    (templateType) => {
      const { result } = renderHook(() => useEvalPickerData());
      act(() => result.current.setFilters(rowsToFilters([
        { field: "template_type", operator: "equals", value: [templateType] },
      ])));
      expect(useEvalsList).toHaveBeenLastCalledWith(
        expect.objectContaining({
          enabled: true,
          filters: { template_type: [templateType] },
        }),
      );
      expect(result.current.total).toBe(templateType === "single" ? 26 : 1);
      expect(result.current.items.every((item) => item.template_type === templateType)).toBe(true);
    },
  );

  it("keeps locked constraints while forwarding the other editable fields", () => {
    const { result } = renderHook(() => useEvalPickerData({ lockedFilters }));
    act(() => result.current.setFilters(rowsToFilters([
      { field: "name", operator: "contains", value: ["Alpha"] },
      { field: "owner", operator: "equals", value: ["user"] },
      { field: "eval_type", operator: "in", value: ["llm"] },
      { field: "output_type", operator: "in", value: ["percentage"] },
      { field: "template_type", operator: "equals", value: ["composite"] },
      { field: "tags", operator: "in", value: ["quality, safety"] },
    ])));
    expect(useEvalsList).toHaveBeenLastCalledWith(
      expect.objectContaining({
        search: "Alpha",
        ownerFilter: "user",
        filters: {
          eval_type: ["llm"],
          tags: ["quality", "safety"],
          ...lockedFilters,
        },
      }),
    );
  });

  it("keeps the scoped endpoint's existing client-side Name filtering", () => {
    const { result } = renderHook(() =>
      useEvalPickerData({ sourceId: "dataset-id", lockedFilters }),
    );
    act(() => result.current.setFilters(nameFilter("Alpha")));
    expect(useEvalsList).toHaveBeenLastCalledWith(
      expect.objectContaining({ enabled: false }),
    );
    expect(result.current.items.map((item) => item.id)).toEqual(["alpha"]);
    expect(result.current.total).toBe(1);
  });
});
