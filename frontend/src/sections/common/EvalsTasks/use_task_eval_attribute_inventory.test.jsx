import {
  act,
  fireEvent,
  render,
  renderHook,
  screen,
} from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { useExactEvalAttributeFields } from "src/sections/evals/components/useExactEvalAttributeFields";
import {
  canonicalTaskEvalFields,
  useTaskEvalAttributeInventory,
} from "./use_task_eval_attribute_inventory";

vi.mock(
  "src/sections/evals/components/useExactEvalAttributeFields",
  async (importOriginal) => ({
    ...(await importOriginal()),
    useExactEvalAttributeFields: vi.fn(),
  }),
);

const exactInventory = (overrides = {}) => ({
  data: ["customer.plan"],
  queryReadState: "complete",
  isFetching: false,
  hasNextPage: false,
  isFetchingNextPage: false,
  isFetchNextPageError: false,
  fetchNextPage: vi.fn(() => Promise.resolve()),
  ...overrides,
});

describe("useTaskEvalAttributeInventory", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    useExactEvalAttributeFields.mockReturnValue(exactInventory());
  });

  it("adds exact retained attributes to deterministic trace paths", () => {
    useExactEvalAttributeFields.mockReturnValue(
      exactInventory({ data: ["spans.0.customer.plan"] }),
    );
    const { result } = renderHook(() =>
      useTaskEvalAttributeInventory({
        projectId: "project-1",
        rowType: "traces",
        enabled: true,
      }),
    );

    const fields = result.current.sourceColumns.map(({ field }) => field);
    expect(fields).toContain("input");
    expect(fields).toContain("spans.0.model");
    expect(fields).toContain("spans.0.customer.plan");
    expect(fields).not.toContain("customer.plan");
  });

  it("uses canonical session paths without sampled cardinality", () => {
    expect(canonicalTaskEvalFields("sessions")).toEqual(
      expect.arrayContaining([
        "name",
        "bookmarked",
        "traces.0.input",
        "traces.0.spans.0.model",
      ]),
    );
    expect(canonicalTaskEvalFields("sessions")).not.toContain("traces.1.input");
  });

  it("exposes exact search and one explicit read-more control", async () => {
    const fetchNextPage = vi.fn(() => Promise.resolve());
    useExactEvalAttributeFields.mockReturnValue(
      exactInventory({ hasNextPage: true, fetchNextPage }),
    );
    const { result, rerender } = renderHook(() =>
      useTaskEvalAttributeInventory({
        projectId: "project-1",
        rowType: "spans",
        enabled: true,
      }),
    );

    act(() => {
      result.current.onSourceColumnSearchChange("rare.customer.key");
    });
    rerender();
    expect(useExactEvalAttributeFields).toHaveBeenLastCalledWith(
      expect.objectContaining({ search: "rare.customer.key" }),
    );

    render(result.current.sourceColumnInventoryControls);
    fireEvent.click(screen.getByRole("button", { name: "Load more" }));
    expect(fetchNextPage).toHaveBeenCalledTimes(1);
  });
});
