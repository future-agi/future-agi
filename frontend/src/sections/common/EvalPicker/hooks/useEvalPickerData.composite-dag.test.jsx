import { act, renderHook } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { axisToLockedFilters } from "src/sections/evals/components/CompositeAxisTabs";
import { useEvalsList } from "src/sections/evals/hooks/useEvalsList";
import { useEvalPickerData } from "./useEvalPickerData";

const catalog = vi.hoisted(() => ({ rows: [] }));

vi.mock("src/hooks/use-debounce", () => ({ useDebounce: (value) => value }));
vi.mock("src/components/iconify", () => ({ default: () => null }));
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
  // Mirror the existing list endpoint's type/output/template filters. This
  // mock never runs product code or makes a request; assertions below also
  // pin the exact exclusion passed to the server before pagination.
  useEvalsList: vi.fn(({ filters, page, pageSize }) => {
    const rows = catalog.rows.filter((row) => {
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

function row(id, eval_type, output_type, template_type = "single") {
  return { id, name: id, eval_type, output_type, template_type, owner: "user" };
}

beforeEach(() => {
  vi.clearAllMocks();
  catalog.rows = [];
});

describe("ordinary composite child filters", () => {
  it.each(["pass_fail", "percentage", "choices", "code", ""])(
    "excludes DAGs for the %s axis, including legacy unset axes",
    (axis) => {
      expect(axisToLockedFilters(axis)).toMatchObject({
        template_type: ["single"],
        eval_type_not: ["dag"],
      });
    },
  );

  describe.each(["", "dataset-id"])("sourceId=%s", (sourceId) => {
    it.each(["pass_fail", "percentage"])(
      "removes DAGs before pagination but retains ordinary %s children",
      (axis) => {
        catalog.rows = [
          ...Array.from({ length: 25 }, (_, i) => row(`dag-${i}`, "dag", axis)),
          row("ordinary-llm", "llm", axis),
          row("ordinary-code", "code", axis),
          row("ordinary-agent", "agent", axis),
          row("nested", "llm", axis, "composite"),
          row(
            "wrong-axis",
            "llm",
            axis === "pass_fail" ? "percentage" : "pass_fail",
          ),
        ];
        const lockedFilters = axisToLockedFilters(axis);
        const { result } = renderHook(() =>
          useEvalPickerData({ sourceId, lockedFilters }),
        );
        expect(result.current.items.map((item) => item.id)).toEqual([
          "ordinary-llm",
          "ordinary-code",
          "ordinary-agent",
        ]);
        expect(result.current.total).toBe(3);
        expect(useEvalsList).toHaveBeenLastCalledWith(
          expect.objectContaining({
            enabled: !sourceId,
            filters: {
              eval_type_not: ["dag"],
              output_type: [axis],
              template_type: ["single"],
            },
          }),
        );
      },
    );

    it("keeps the DAG exclusion when user type filters change", () => {
      catalog.rows = [
        row("dag", "dag", "pass_fail"),
        row("ordinary-llm", "llm", "pass_fail"),
        row("ordinary-code", "code", "pass_fail"),
      ];
      const lockedFilters = axisToLockedFilters("pass_fail");
      const { result } = renderHook(() =>
        useEvalPickerData({ sourceId, lockedFilters }),
      );
      act(() => result.current.setFilters({ eval_type: ["code"] }));
      expect(result.current.items.map((item) => item.id)).toEqual([
        "ordinary-code",
      ]);
      act(() => result.current.setFilters({ eval_type: ["dag"] }));
      expect(result.current.items).toEqual([]);
      expect(result.current.total).toBe(0);
      act(() => result.current.setFilters(null));
      expect(result.current.items.map((item) => item.id)).toEqual([
        "ordinary-llm",
        "ordinary-code",
      ]);
    });

    it("also excludes DAGs when editing a legacy composite with no axis", () => {
      catalog.rows = [
        row("dag", "dag", "percentage"),
        row("ordinary", "llm", "percentage"),
      ];
      const lockedFilters = axisToLockedFilters("");
      const { result } = renderHook(() =>
        useEvalPickerData({ sourceId, lockedFilters }),
      );
      expect(result.current.items.map((item) => item.id)).toEqual(["ordinary"]);
      expect(result.current.total).toBe(1);
    });

    it("leaves DAGs visible for the standalone picker", () => {
      catalog.rows = [
        row("dag", "dag", "pass_fail"),
        row("ordinary", "llm", "pass_fail"),
      ];
      const { result } = renderHook(() => useEvalPickerData({ sourceId }));
      expect(result.current.items.map((item) => item.id)).toEqual([
        "dag",
        "ordinary",
      ]);
      expect(result.current.total).toBe(2);
      expect(useEvalsList).toHaveBeenLastCalledWith(
        expect.objectContaining({ filters: null }),
      );
    });
  });
});
