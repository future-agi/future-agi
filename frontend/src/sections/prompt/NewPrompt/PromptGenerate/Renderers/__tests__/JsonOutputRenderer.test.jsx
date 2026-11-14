import { fireEvent, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { render } from "src/utils/test-utils";
import { copyToClipboard } from "src/utils/utils";

import { getAddColumnApiCallValidation } from "src/sections/develop-detail/AddColumn/AddColumnApiCall/validation";

import JsonOutputRenderer from "../JsonOutputRenderer";

vi.mock("src/utils/utils", async (importOriginal) => ({
  ...(await importOriginal()),
  copyToClipboard: vi.fn(),
}));
vi.mock("notistack", () => ({ enqueueSnackbar: vi.fn() }));

const OUTPUT =
  '{"name": "Ada", "address": {"city": "L"}, "items": [{"id": 7}]}';

const copyPath = (path) =>
  fireEvent.click(screen.getByLabelText(`Copy path: {{output.${path}}}`));

describe("JsonOutputRenderer", () => {
  it("copies a top-level key as {{output.key}}", () => {
    render(<JsonOutputRenderer data={OUTPUT} columnName="output" />);
    copyPath("name");
    expect(copyToClipboard).toHaveBeenLastCalledWith("{{output.name}}");
  });

  it("expand all opens nested keys, copied in the form the backend resolves", () => {
    render(<JsonOutputRenderer data={OUTPUT} columnName="output" />);
    expect(screen.queryByText("id:")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Expand all" }));
    copyPath("address.city");
    expect(copyToClipboard).toHaveBeenLastCalledWith("{{output.address.city}}");
    copyPath("items[0].id");
    expect(copyToClipboard).toHaveBeenLastCalledWith("{{output.items[0].id}}");
  });

  it("copies top-level array items and nested array objects", () => {
    render(<JsonOutputRenderer data={["first", [{ id: 7 }]]} />);
    copyPath("[0]");
    expect(copyToClipboard).toHaveBeenLastCalledWith("{{output.[0]}}");
    fireEvent.click(screen.getByRole("button", { name: "Expand all" }));
    copyPath("[1][0].id");
    expect(copyToClipboard).toHaveBeenLastCalledWith("{{output.[1][0].id}}");
  });

  it("collapse all keeps the root visible and hides nested object and array contents", () => {
    render(<JsonOutputRenderer data={OUTPUT} />);
    fireEvent.click(screen.getByRole("button", { name: "Expand all" }));
    expect(screen.getByText("id:")).toBeVisible();
    expect(screen.getByText("city:")).toBeVisible();
    fireEvent.click(screen.getByRole("button", { name: "Collapse all" }));
    expect(screen.getByText("name:")).toBeVisible();
    expect(screen.queryByText("id:")).toBeNull();
    expect(screen.queryByText("city:")).toBeNull();
    copyPath("name");
    expect(copyToClipboard).toHaveBeenLastCalledWith("{{output.name}}");
    fireEvent.click(screen.getByRole("button", { name: "Expand all" }));
    copyPath("items[0].id");
    expect(copyToClipboard).toHaveBeenLastCalledWith("{{output.items[0].id}}");
  });

  it.each([
    [OUTPUT, "items[0].id"],
    [["first", [{ id: 7 }]], "[1][0].id"],
  ])(
    "preserves the copied path through API-column validation: %j",
    (data, path) => {
      const columnId = "11111111-1111-4111-8111-111111111111";
      render(<JsonOutputRenderer data={data} />);
      fireEvent.click(screen.getByRole("button", { name: "Expand all" }));
      copyPath(path);
      const copied = copyToClipboard.mock.lastCall[0];
      const parsed = getAddColumnApiCallValidation([
        { headerName: "output", field: columnId },
      ]).parse({
        column_name: "Derived result",
        concurrency: 1,
        config: {
          url: "http://localhost/example",
          method: "POST",
          params: [],
          headers: [],
          body: JSON.stringify({ selected: copied }),
          output_type: "object",
        },
      });
      expect(parsed.config.body.selected).toBe(`{{${columnId}.${path}}}`);
    },
  );

  it("preserves a real root property and reports a custom column path", () => {
    const onPathCopy = vi.fn();
    render(
      <JsonOutputRenderer
        data={{ root: { enabled: false } }}
        columnName="result"
        initialExpanded={false}
        onPathCopy={onPathCopy}
      />,
    );
    expect(screen.queryByText("root:")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Expand all" }));
    fireEvent.click(
      screen.getByLabelText("Copy path: {{result.root.enabled}}"),
    );
    expect(copyToClipboard).toHaveBeenLastCalledWith("{{result.root.enabled}}");
    expect(onPathCopy).toHaveBeenLastCalledWith("{{result.root.enabled}}");
  });
});
