import { describe, it, expect, vi } from "vitest";
import { act, screen, fireEvent } from "@testing-library/react";
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

  it("allows a hidden standard attribute to be selected as a persisted override", () => {
    const onAddColumns = vi.fn();
    renderWithProviders(
      <CustomColumnDialog
        open
        onClose={vi.fn()}
        attributes={["user_interruption_count"]}
        existingColumns={[
          {
            id: "user_interruption_count",
            name: "User Interrupts",
            isVisible: false,
            groupBy: "Call Columns",
          },
        ]}
        onAddColumns={onAddColumns}
        onRemoveColumns={vi.fn()}
      />,
    );

    fireEvent.click(
      screen.getByRole("checkbox", { name: "user_interruption_count" }),
    );
    fireEvent.click(screen.getByRole("button", { name: /apply/i }));

    expect(onAddColumns).toHaveBeenCalledWith([
      {
        id: "user_interruption_count",
        name: "user_interruption_count",
        isVisible: true,
        groupBy: "Custom Columns",
      },
    ]);
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

  it("forwards free-text search and advances near the attribute-list end", async () => {
    const onAttributeSearchChange = vi.fn();
    let resolvePage;
    const fetchNextAttributePage = vi.fn(
      () => new Promise((resolve) => (resolvePage = resolve)),
    );
    renderWithProviders(
      <CustomColumnDialog
        open
        onClose={vi.fn()}
        attributes={["recent.attribute"]}
        existingColumns={[]}
        onAddColumns={vi.fn()}
        onRemoveColumns={vi.fn()}
        onAttributeSearchChange={onAttributeSearchChange}
        hasNextAttributePage
        fetchNextAttributePage={fetchNextAttributePage}
      />,
    );

    fireEvent.change(screen.getByPlaceholderText("Search attributes..."), {
      target: { value: "older.attribute" },
    });
    expect(onAttributeSearchChange).toHaveBeenLastCalledWith("older.attribute");

    const scrollContainer = document.querySelector(
      "[data-attribute-inventory-scroll-container]",
    );
    Object.defineProperties(scrollContainer, {
      scrollTop: { configurable: true, value: 180 },
      clientHeight: { configurable: true, value: 220 },
      scrollHeight: { configurable: true, value: 400 },
    });
    fireEvent.scroll(scrollContainer);
    fireEvent.scroll(scrollContainer);
    expect(fetchNextAttributePage).toHaveBeenCalledTimes(1);
    expect(
      screen.queryByRole("button", { name: /load more|continue searching/i }),
    ).not.toBeInTheDocument();

    await act(async () => resolvePage());
  });

  it("renders the shared inventory retry state inside the dialog", async () => {
    const onRetry = vi.fn(() => Promise.resolve());
    renderWithProviders(
      <CustomColumnDialog
        open
        onClose={vi.fn()}
        attributes={[]}
        existingColumns={[]}
        onAddColumns={vi.fn()}
        onRemoveColumns={vi.fn()}
        inventoryControlProps={{
          isError: true,
          canRetry: true,
          onRetry,
        }}
      />,
    );

    expect(
      screen.getByText("Properties could not be loaded. Retry this page."),
    ).toBeInTheDocument();
    await act(async () =>
      fireEvent.click(screen.getByRole("button", { name: "Retry properties" })),
    );
    expect(onRetry).toHaveBeenCalledTimes(1);
  });
});

describe("CustomColumnDialog — Skills columns (SKILL-TRACES §7.1)", () => {
  const SKILL_COLUMNS = [
    { id: "skills", name: "Skills" },
    { id: "gen_ai.agent.name", name: "Agent" },
    { id: "fi.context.name", name: "Context" },
  ];

  it("offers Skills, Agent and Context under Skills, unchecked by default", () => {
    renderWithProviders(
      <CustomColumnDialog
        open
        onClose={vi.fn()}
        attributes={["gen_ai.agent.name", "custom.attr"]}
        existingColumns={[{ id: "trace_name" }]}
        skillColumns={SKILL_COLUMNS}
      />,
    );
    expect(screen.getByText("Skills", { selector: "span" })).toBeInTheDocument();
    expect(screen.getByLabelText("Agent")).not.toBeChecked();
    expect(screen.getByLabelText("Context")).not.toBeChecked();
    // The agent attribute is listed once, under Skills, not again as a raw key.
    expect(screen.queryByText("gen_ai.agent.name")).not.toBeInTheDocument();
    expect(screen.getByText("custom.attr")).toBeInTheDocument();
  });

  it("adds a Skills column with its readable name", () => {
    const onAddColumns = vi.fn();
    const onClose = vi.fn();
    renderWithProviders(
      <CustomColumnDialog
        open
        onClose={onClose}
        attributes={[]}
        existingColumns={[{ id: "trace_name" }]}
        skillColumns={SKILL_COLUMNS}
        onAddColumns={onAddColumns}
      />,
    );
    fireEvent.click(screen.getByLabelText("Agent"));
    fireEvent.click(screen.getByRole("button", { name: "Apply" }));
    expect(onAddColumns).toHaveBeenCalledWith([
      { id: "gen_ai.agent.name", name: "Agent", isVisible: true, groupBy: "Custom Columns" },
    ]);
  });

  it("shows no Skills section without skill columns", () => {
    renderWithProviders(
      <CustomColumnDialog open onClose={vi.fn()} attributes={["a.b"]} existingColumns={[]} />,
    );
    expect(screen.queryByText("Agent")).not.toBeInTheDocument();
  });
});
