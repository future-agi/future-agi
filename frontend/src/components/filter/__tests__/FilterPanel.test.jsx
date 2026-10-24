import { describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "src/utils/test-utils";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import FilterButton from "../FilterButton";
import FilterPanel from "../FilterPanel";
import { fieldsToCategories, fieldsToProperties } from "../fieldSchema";

vi.mock("src/hooks/use-ai-filter", () => ({
  useAIFilter: () => ({ parseQuery: vi.fn(), loading: false, error: null }),
}));

const fields = [
  {
    id: "status",
    label: "Status",
    type: "enum",
    options: [
      { value: "running", label: "Running" },
      { value: "failed", label: "Failed" },
    ],
    operators: ["equals"],
  },
  { id: "name", label: "Name", type: "text", operators: ["contains"] },
  { id: "rate", label: "Rate", type: "number" },
];

const wrap = (node) => (
  <QueryClientProvider
    client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}
  >
    {node}
  </QueryClientProvider>
);

describe("fieldSchema", () => {
  it("turns the schema into panel properties", () => {
    const properties = fieldsToProperties(fields);
    expect(properties[0]).toMatchObject({
      id: "status",
      name: "Status",
      type: "categorical",
      operators: ["equals"],
    });
    expect(properties[0].choices).toHaveLength(2);
    expect(properties[1]).toMatchObject({ type: "string" });
    expect(properties[2]).toMatchObject({ type: "number" });
    expect(properties[2].operators).toBeUndefined();
  });

  it("lists several choices as a pick-many string field", () => {
    const [property] = fieldsToProperties([
      {
        id: "t",
        label: "T",
        type: "enum",
        multiple: true,
        options: ["a", "b"],
      },
    ]);
    expect(property.type).toBe("string");
  });

  it("adds a category sidebar only when fields come from several groups", () => {
    expect(fieldsToCategories(fields)).toEqual([]);
    const grouped = fieldsToCategories(
      [
        { id: "a", label: "A", category: "one" },
        { id: "b", label: "B", category: "two" },
      ],
      { one: "First" },
    );
    expect(grouped.map((c) => c.label)).toEqual(["All", "First", "two"]);
  });
});

describe("FilterPanel", () => {
  it("hides Ask AI unless the screen asks for it", () => {
    const anchorEl = document.createElement("button");
    document.body.appendChild(anchorEl);
    const { rerender } = render(
      wrap(
        <FilterPanel
          anchorEl={anchorEl}
          open
          onClose={vi.fn()}
          fields={fields}
          value={[]}
          onChange={vi.fn()}
        />,
      ),
    );
    expect(screen.queryByPlaceholderText(/Ask AI/i)).not.toBeInTheDocument();

    rerender(
      wrap(
        <FilterPanel
          anchorEl={anchorEl}
          open
          onClose={vi.fn()}
          fields={fields}
          value={[]}
          onChange={vi.fn()}
          showAi
        />,
      ),
    );
    expect(screen.getByPlaceholderText(/Ask AI/i)).toBeInTheDocument();
    document.body.removeChild(anchorEl);
  });

  it("hydrates rows from the value and clears with null", async () => {
    const anchorEl = document.createElement("button");
    document.body.appendChild(anchorEl);
    const onChange = vi.fn();
    render(
      wrap(
        <FilterPanel
          anchorEl={anchorEl}
          open
          onClose={vi.fn()}
          fields={fields}
          value={[
            {
              field: "status",
              fieldType: "categorical",
              operator: "equals",
              value: ["failed"],
            },
          ]}
          onChange={onChange}
        />,
      ),
    );
    expect(await screen.findByText("Status")).toBeInTheDocument();
    expect(screen.getByText("Failed")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: /clear all/i }));
    await waitFor(() => expect(onChange).toHaveBeenCalledWith(null));
    document.body.removeChild(anchorEl);
  });
});

describe("FilterButton", () => {
  it("shows the active count and opens the panel", async () => {
    render(
      wrap(
        <FilterButton
          fields={fields}
          value={[
            {
              field: "name",
              fieldType: "string",
              operator: "contains",
              value: ["x"],
            },
          ]}
          onChange={vi.fn()}
        />,
      ),
    );
    const button = screen.getByRole("button", { name: /filter/i });
    expect(button).toHaveTextContent("Filter1");
    expect(button).toHaveAttribute("aria-expanded", "false");

    fireEvent.click(button);
    await waitFor(() =>
      expect(button).toHaveAttribute("aria-expanded", "true"),
    );
    expect(await screen.findByText("Name")).toBeInTheDocument();
  });

  it("can be driven from outside", async () => {
    const onOpenChange = vi.fn();
    render(
      wrap(
        <FilterButton
          fields={fields}
          value={[]}
          onChange={vi.fn()}
          open={false}
          onOpenChange={onOpenChange}
        />,
      ),
    );
    fireEvent.click(screen.getByRole("button", { name: /filter/i }));
    expect(onOpenChange).toHaveBeenCalledWith(true);
  });
});
