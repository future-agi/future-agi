import { describe, it, expect, vi, beforeEach } from "vitest";
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render } from "src/utils/test-utils";

const all = Array.from({ length: 12 }, (_, n) => ({
  field: `col-${n}`,
  headerName: `c${n}`,
  dataType: "text",
}));
const state = vi.hoisted(() => ({ columns: [], read: null }));
vi.mock("src/api/develop/develop-detail", () => ({
  useDatasetColumnConfig: () => state.columns,
  getDatasetQueryOptions: (id) => ({
    queryKey: ["dataset", id],
    queryFn: state.read,
  }),
}));

const { default: DatasetColumnsFields } = await import(
  "../DatasetColumnsFields"
);

const renderFields = (value) =>
  render(
    <QueryClientProvider
      client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}
    >
      <DatasetColumnsFields datasetId="d1" value={value} onChange={() => {}} />
    </QueryClientProvider>,
  );

beforeEach(() => {
  state.columns = all;
  state.read = () => Promise.resolve({});
});

describe("DatasetColumnsFields", () => {
  it("offers no more label columns than the backend reads, and says so", async () => {
    const labels = all.slice(1, 11).map((one) => one.field);
    renderFields({ conversation: "col-0", labels, expected: "" });

    expect(
      screen.getByText(
        "Read as context for the builder, such as intent. Up to 10.",
      ),
    ).toBeInTheDocument();
    await userEvent.click(
      screen.getByRole("combobox", { name: /^Label columns/ }),
    );

    expect(screen.getByRole("option", { name: "c11" })).toHaveAttribute(
      "aria-disabled",
      "true",
    );
    expect(screen.getByRole("option", { name: "c10" })).not.toHaveAttribute(
      "aria-disabled",
      "true",
    );
  });

  it("says when the dataset's columns could not be loaded, not loading forever", async () => {
    state.columns = [];
    state.read = vi.fn(() => Promise.reject(new Error("boom")));
    renderFields({ conversation: "", labels: [], expected: "" });

    expect(
      screen.getByRole("progressbar", { name: "Loading this dataset's columns…" }),
    ).toBeInTheDocument();
    expect(
      await screen.findByText(
        "Couldn't load this dataset's columns.",
      ),
    ).toBeInTheDocument();
    expect(screen.queryByRole("progressbar")).toBeNull();
    await userEvent.click(screen.getByRole("button", { name: "Retry" }));
    await waitFor(() => expect(state.read).toHaveBeenCalledTimes(2));
  });
});
