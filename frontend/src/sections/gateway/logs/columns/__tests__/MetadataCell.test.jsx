import React from "react";
import { afterEach, describe, expect, it } from "vitest";
import { render, screen, userEvent, within } from "src/utils/test-utils";
import MetadataCell from "../MetadataCell";

// The real MUI Tooltip, not a mock: the cell must open it from the keyboard.
function renderCell(value) {
  const view = render(
    <>
      <table>
        <tbody>
          <tr>
            <MetadataCell metadata={{ note: value }} name="note" />
          </tr>
        </tbody>
      </table>
      <button type="button">After</button>
    </>,
  );
  const cell = view.container.querySelector('td[data-column="metadata:note"]');
  return { ...view, cell };
}

describe("MetadataCell keyboard access (R41/AC17)", () => {
  afterEach(() => {
    delete window.__metadataCellExecuted;
  });

  it("reaches a truncated value with Tab and shows the full text in a tooltip", async () => {
    const user = userEvent.setup();
    const value = `${"tenant-".repeat(20)}end`;
    const { cell } = renderCell(value);
    const preview = within(cell).getByText(`${value.slice(0, 79)}…`);
    expect(screen.queryByRole("tooltip")).toBeNull();

    await user.tab();

    expect(preview).toHaveFocus();
    const tooltip = await screen.findByRole("tooltip");
    expect(tooltip.textContent).toBe(value);
  });

  it("keeps script-like text literal in the cell and the tooltip", async () => {
    const user = userEvent.setup();
    const value =
      "<script>window.__metadataCellExecuted = true</script>" +
      '<img src="x" onerror="window.__metadataCellExecuted = true">' +
      "x".repeat(40);
    const { cell } = renderCell(value);

    await user.tab();

    const tooltip = await screen.findByRole("tooltip");
    expect(tooltip.textContent).toBe(value);
    expect(cell.textContent).toBe(`${value.slice(0, 79)}…`);
    expect(
      document.body.querySelector("script, img, iframe, object, embed"),
    ).toBeNull();
    expect(window.__metadataCellExecuted).toBeUndefined();
  });

  it("adds no tab stop for a value that fits", async () => {
    const user = userEvent.setup();
    const { cell } = renderCell("acme");
    expect(within(cell).getByText("acme")).not.toHaveAttribute("tabindex");

    await user.tab();

    expect(screen.getByRole("button", { name: "After" })).toHaveFocus();
    expect(screen.queryByRole("tooltip")).toBeNull();
  });
});
