import { describe, expect, it } from "vitest";
import { render, screen } from "src/utils/test-utils";
import DataTable from "../DataTable";

const columns = [
  { id: "name", accessorKey: "name", header: "Name", meta: { flex: 1 } },
  { id: "runs", accessorKey: "runs", header: "Runs", size: 80 },
];

describe("DataTable loading", () => {
  it("draws skeleton rows when asked, instead of the spinner", () => {
    render(
      <DataTable
        columns={columns}
        data={[]}
        isLoading
        loadingVariant="skeleton"
      />,
    );
    expect(screen.getByTestId("data-table-skeleton")).toBeInTheDocument();
    expect(screen.queryByRole("progressbar")).toBeNull();
  });

  it("covers the previous rows with the skeleton while new ones load", () => {
    render(
      <DataTable
        columns={columns}
        data={[{ id: "1", name: "Old row", runs: 1 }]}
        isLoading
        loadingVariant="skeleton"
      />,
    );
    expect(screen.getByTestId("data-table-skeleton")).toBeInTheDocument();
  });

  it("keeps the spinner by default", () => {
    render(<DataTable columns={columns} data={[]} isLoading />);
    expect(screen.queryByTestId("data-table-skeleton")).toBeNull();
  });
});
