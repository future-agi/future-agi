import { fireEvent, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { render } from "src/utils/test-utils";
import { copyToClipboard } from "src/utils/utils";

import JsonOutputRenderer from "../JsonOutputRenderer";

vi.mock("src/utils/utils", async (importOriginal) => ({
  ...(await importOriginal()),
  copyToClipboard: vi.fn(),
}));

vi.mock("notistack", () => ({ enqueueSnackbar: vi.fn() }));

const OUTPUT = JSON.stringify({
  name: "Ada",
  address: { city: "London" },
  items: [{ id: 7 }],
});

const copyPathButton = (path) =>
  screen.getByRole("button", { name: `Copy path: {{output.${path}}}` });

describe("JsonOutputRenderer", () => {
  beforeEach(() => {
    vi.mocked(copyToClipboard).mockClear();
  });

  it("copies a top-level key as {{output.key}}", () => {
    render(<JsonOutputRenderer data={OUTPUT} columnName="output" />);

    fireEvent.click(copyPathButton("name"));

    expect(copyToClipboard).toHaveBeenCalledWith("{{output.name}}");
  });

  it("expand all opens nested objects and array items", () => {
    render(<JsonOutputRenderer data={OUTPUT} columnName="output" />);
    expect(screen.queryByText("city:")).toBeNull();
    expect(screen.queryByText("id:")).toBeNull();

    fireEvent.click(screen.getByRole("button", { name: "Expand all" }));

    expect(screen.getByText("city:")).toBeInTheDocument();
    expect(screen.getByText("id:")).toBeInTheDocument();
  });

  it("copies nested and array paths in the form the backend resolves", () => {
    render(<JsonOutputRenderer data={OUTPUT} columnName="output" />);
    fireEvent.click(screen.getByRole("button", { name: "Expand all" }));

    fireEvent.click(copyPathButton("address.city"));
    fireEvent.click(copyPathButton("items[0].id"));

    expect(copyToClipboard).toHaveBeenNthCalledWith(
      1,
      "{{output.address.city}}",
    );
    expect(copyToClipboard).toHaveBeenNthCalledWith(
      2,
      "{{output.items[0].id}}",
    );
  });
});
