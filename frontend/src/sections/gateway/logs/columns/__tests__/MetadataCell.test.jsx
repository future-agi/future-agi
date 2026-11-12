import React from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import {
  cleanup,
  fireEvent,
  render,
  screen,
  userEvent,
  waitForElementToBeRemoved,
  within,
} from "src/utils/test-utils";
import MetadataCell, { MetadataValue } from "../MetadataCell";
import { CUSTOM_COLUMN_TEXT_WIDTH, CUSTOM_COLUMN_WIDTH } from "../columnModel";

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

describe("MetadataCell keyboard access", () => {
  afterEach(() => {
    delete window.__metadataCellExecuted;
    vi.restoreAllMocks();
  });

  it("reaches a truncated value with Tab and shows the full text in a tooltip", async () => {
    const user = userEvent.setup();
    const value = `${"tenant-".repeat(20)}end`;
    const { cell } = renderCell(value);
    const preview = within(cell).getByText(`${value.slice(0, 79)}…`);
    expect(preview).toHaveAttribute("aria-label", value);
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

  it("reaches a shorter value that its bounded cell clips and shows it in full", async () => {
    // jsdom has no layout, so report the text as wider than its box.
    vi.spyOn(Element.prototype, "scrollWidth", "get").mockReturnValue(300);
    vi.spyOn(Element.prototype, "clientWidth", "get").mockReturnValue(240);
    const user = userEvent.setup();
    const value = "acme-corporation-eu-west-1-production";
    const { cell } = renderCell(value);

    await user.tab();

    expect(within(cell).getByText(value)).toHaveFocus();
    const tooltip = await screen.findByRole("tooltip");
    expect(tooltip.textContent).toBe(value);
  });

  it("bounds the cell text to one ellipsized line that keeps the column at its 140px width", () => {
    const { cell } = renderCell("acme");
    const text = within(cell).getByText("acme");
    expect(text).toHaveStyle({
      maxWidth: `${CUSTOM_COLUMN_TEXT_WIDTH}px`,
      overflow: "hidden",
      textOverflow: "ellipsis",
      whiteSpace: "nowrap",
    });
    const { paddingLeft, paddingRight } = getComputedStyle(cell);
    expect(
      CUSTOM_COLUMN_TEXT_WIDTH +
        parseFloat(paddingLeft) +
        parseFloat(paddingRight),
    ).toBe(CUSTOM_COLUMN_WIDTH);
    expect(CUSTOM_COLUMN_WIDTH).toBe(140);
  });

  it("shows a capped value's truncation marker first, in a tooltip wide enough for 2,000 characters", async () => {
    const user = userEvent.setup();
    renderCell("v".repeat(2500));

    await user.tab();

    const tooltip = await screen.findByRole("tooltip");
    expect(tooltip.textContent).toBe(`[truncated] ${"v".repeat(2000)}`);
    expect(tooltip.firstElementChild).toHaveStyle({
      maxWidth: "720px",
      maxHeight: "40vh",
      overflowY: "auto",
    });
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

// In the logs grid the keyboard moves from cell to cell (AG Grid focuses the
// cell, not the value inside it), so the tooltip follows its grid cell's focus.
describe("MetadataValue in a grid cell", () => {
  function renderGridCell(value) {
    const cellElement = document.createElement("div");
    cellElement.setAttribute("role", "gridcell");
    cellElement.tabIndex = -1;
    cellElement.dataset.testNode = "cell";
    document.body.appendChild(cellElement);
    const outside = document.createElement("button");
    outside.dataset.testNode = "outside";
    outside.textContent = "Outside";
    document.body.appendChild(outside);
    const view = render(
      <MetadataValue
        metadata={{ note: value }}
        name="note"
        cell={cellElement}
      />,
      { container: cellElement },
    );
    return { ...view, cellElement, outside };
  }

  afterEach(() => {
    cleanup();
    vi.restoreAllMocks();
    document.body
      .querySelectorAll("[data-test-node]")
      .forEach((node) => node.remove());
  });

  it("shows a truncated value in full while its grid cell has focus, and hides it after", async () => {
    const value = `${"tenant-".repeat(20)}end`;
    const { cellElement, outside } = renderGridCell(value);
    expect(screen.queryByRole("tooltip")).toBeNull();

    cellElement.focus();

    const tooltip = await screen.findByRole("tooltip");
    expect(tooltip.textContent).toBe(value);

    outside.focus();

    await waitForElementToBeRemoved(() => screen.queryByRole("tooltip"));
  });

  it("opens nothing when a click, not the keyboard, focuses the cell", async () => {
    const value = `${"tenant-".repeat(20)}end`;
    const { cellElement } = renderGridCell(value);
    // A pointer focus shows no focus ring: the cell does not match :focus-visible.
    const matches = cellElement.matches.bind(cellElement);
    vi.spyOn(cellElement, "matches").mockImplementation((selector) =>
      selector === ":focus-visible" ? false : matches(selector),
    );

    cellElement.focus();

    await new Promise((resolve) => setTimeout(resolve, 50));
    expect(screen.queryByRole("tooltip")).toBeNull();
  });

  it("closes on Escape while the cell keeps focus", async () => {
    const value = `${"tenant-".repeat(20)}end`;
    const { cellElement } = renderGridCell(value);
    cellElement.focus();
    expect(await screen.findByRole("tooltip")).toBeInTheDocument();

    fireEvent.keyDown(cellElement, { key: "Escape" });

    await waitForElementToBeRemoved(() => screen.queryByRole("tooltip"));
    expect(cellElement).toHaveFocus();
  });

  it("opens nothing on a focused cell whose value fits", async () => {
    const { cellElement } = renderGridCell("acme");

    cellElement.focus();

    await new Promise((resolve) => setTimeout(resolve, 50));
    expect(screen.queryByRole("tooltip")).toBeNull();
  });
});
