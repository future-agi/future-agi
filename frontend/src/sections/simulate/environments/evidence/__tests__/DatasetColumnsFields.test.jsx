import { describe, it, expect, vi } from "vitest";
import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { render } from "src/utils/test-utils";

const columns = Array.from({ length: 12 }, (_, n) => ({
  field: `col-${n}`,
  headerName: `c${n}`,
  dataType: "text",
}));
vi.mock("src/api/develop/develop-detail", () => ({
  useDatasetColumnConfig: () => columns,
}));

const { default: DatasetColumnsFields } = await import(
  "../DatasetColumnsFields"
);

describe("DatasetColumnsFields", () => {
  it("offers no more label columns than the backend reads", async () => {
    const labels = columns.slice(1, 11).map((one) => one.field);
    render(
      <DatasetColumnsFields
        datasetId="d1"
        value={{ conversation: "col-0", labels, expected: "" }}
        onChange={() => {}}
      />,
    );

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
});
