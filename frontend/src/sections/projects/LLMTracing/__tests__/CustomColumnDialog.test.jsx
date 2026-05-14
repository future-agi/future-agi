import { describe, it, expect, vi } from "vitest";
import { fireEvent, screen } from "@testing-library/react";
import { render as renderWithProviders } from "src/utils/test-utils";
import CustomColumnDialog from "../CustomColumnDialog";

vi.mock("src/components/iconify", () => ({
  default: () => null,
}));

// notistack's enqueueSnackbar needs a SnackbarProvider in the tree; the
// test-utils wrapper does not include one. Mock it so the "column added"
// success toast added in handleApply doesn't throw during tests.
vi.mock("notistack", () => ({
  enqueueSnackbar: vi.fn(),
}));

// Stub the picker hook so tests can control the items the dialog renders
// without standing up MSW or a real query client.
const hookMock = vi.fn();
vi.mock("src/hooks/use-eval-attributes", () => ({
  useEvalAttributesInfinite: (...args) => hookMock(...args),
}));

const baseHookResult = {
  items: [],
  totalRows: 0,
  isLoading: false,
  isFetching: false,
  isFetchingNextPage: false,
  hasMore: false,
  fetchMore: vi.fn(),
};

describe("CustomColumnDialog — TH-4139", () => {
  it("surfaces existing custom columns whose ids are not in the attributes list", () => {
    hookMock.mockReturnValue({
      ...baseHookResult,
      items: ["llm.token_count.prompt"],
    });
    renderWithProviders(
      <CustomColumnDialog
        open
        onClose={vi.fn()}
        projectId="proj-1"
        existingColumns={[
          { id: "trace_name" },
          { id: "stale.attribute.id", groupBy: "Custom Columns" },
        ]}
        onAddColumns={vi.fn()}
        onRemoveColumns={vi.fn()}
      />,
    );

    expect(screen.getByText("stale.attribute.id")).toBeInTheDocument();
    expect(screen.getByText("llm.token_count.prompt")).toBeInTheDocument();
  });

  it("excludes ids that are already standard columns", () => {
    hookMock.mockReturnValue({
      ...baseHookResult,
      items: ["trace_name", "input", "custom.attr"],
    });
    renderWithProviders(
      <CustomColumnDialog
        open
        onClose={vi.fn()}
        projectId="proj-1"
        existingColumns={[{ id: "trace_name" }, { id: "input" }]}
        onAddColumns={vi.fn()}
        onRemoveColumns={vi.fn()}
      />,
    );
    expect(screen.queryByText("trace_name")).not.toBeInTheDocument();
    expect(screen.queryByText("input")).not.toBeInTheDocument();
    expect(screen.getByText("custom.attr")).toBeInTheDocument();
  });

  it("calls onRemoveColumns for a stale custom column when the user unchecks it", () => {
    hookMock.mockReturnValue({ ...baseHookResult, items: [] });
    const onRemoveColumns = vi.fn();
    const onAddColumns = vi.fn();
    renderWithProviders(
      <CustomColumnDialog
        open
        onClose={vi.fn()}
        projectId="proj-1"
        existingColumns={[
          { id: "stale.attribute.id", groupBy: "Custom Columns" },
        ]}
        onAddColumns={onAddColumns}
        onRemoveColumns={onRemoveColumns}
      />,
    );

    const checkbox = screen.getByRole("checkbox", {
      name: /stale\.attribute\.id/,
    });
    expect(checkbox).toBeChecked();
    fireEvent.click(checkbox);

    fireEvent.click(screen.getByRole("button", { name: /apply/i }));
    expect(onRemoveColumns).toHaveBeenCalledWith(["stale.attribute.id"]);
    expect(onAddColumns).not.toHaveBeenCalled();
  });
});
