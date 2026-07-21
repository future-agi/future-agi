import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
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

describe("CustomColumnDialog — TH-4139", () => {
  it("surfaces existing custom columns whose ids are not in the attributes list", () => {
    const onAddColumns = vi.fn();
    const onRemoveColumns = vi.fn();
    renderWithProviders(
      <CustomColumnDialog
        open
        onClose={vi.fn()}
        // The "stale" custom column id is not present in the API attributes
        attributes={["llm.token_count.prompt"]}
        existingColumns={[
          { id: "trace_name" },
          { id: "stale.attribute.id", groupBy: "Custom Columns" },
        ]}
        onAddColumns={onAddColumns}
        onRemoveColumns={onRemoveColumns}
      />,
    );

    // The stale custom column appears in the dialog so the user can
    // see and uncheck it — without this, the dialog would silently
    // hide the column while it still counted on the panel badge.
    expect(screen.getByText("stale.attribute.id")).toBeInTheDocument();
    expect(screen.getByText("llm.token_count.prompt")).toBeInTheDocument();
  });

  it("excludes ids that are already standard columns", () => {
    renderWithProviders(
      <CustomColumnDialog
        open
        onClose={vi.fn()}
        attributes={["trace_name", "input", "custom.attr"]}
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
    const onRemoveColumns = vi.fn();
    const onAddColumns = vi.fn();
    renderWithProviders(
      <CustomColumnDialog
        open
        onClose={vi.fn()}
        attributes={[]}
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

describe("CustomColumnDialog — span attribute source (#1380)", () => {
  // The picker is sourced from the span-attribute-keys endpoint, which returns
  // objects shaped { key, type, count } — not the plain strings the earlier
  // tests cover, and not the eval-attributes list it was wired to before the
  // fix. Feeding that object shape must render each attribute's key as a
  // selectable row.
  it("renders span-attribute-keys objects ({ key, type, count }) as selectable columns", () => {
    renderWithProviders(
      <CustomColumnDialog
        open
        onClose={vi.fn()}
        attributes={[
          { key: "llm.model_name", type: "string", count: 12 },
          { key: "test_string", type: "string", count: 3 },
        ]}
        existingColumns={[]}
        onAddColumns={vi.fn()}
        onRemoveColumns={vi.fn()}
      />,
    );

    expect(screen.getByText("llm.model_name")).toBeInTheDocument();
    expect(screen.getByText("test_string")).toBeInTheDocument();
  });

  it("shows the empty state when the source returns no attributes", () => {
    // Before #1380 the dialog was fed the eval-attributes list, which was
    // empty for any project without configured evals — leaving the picker
    // permanently blank. An empty source must surface the explicit empty
    // state, and (post-fix) a project whose spans carry attributes no longer
    // hits this path.
    renderWithProviders(
      <CustomColumnDialog
        open
        onClose={vi.fn()}
        attributes={[]}
        existingColumns={[]}
        onAddColumns={vi.fn()}
        onRemoveColumns={vi.fn()}
      />,
    );

    expect(
      screen.getByText("No attributes found for this project"),
    ).toBeInTheDocument();
  });
});
