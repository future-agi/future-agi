import { fireEvent, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { render } from "src/utils/test-utils";
import { copyToClipboard } from "src/utils/utils";

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
});
