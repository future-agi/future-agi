import React from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import {
  fireEvent,
  render,
  screen,
  userEvent,
  waitFor,
  within,
} from "src/utils/test-utils";
import ColumnsPickerPopover from "../ColumnsPickerPopover";
import {
  DEFAULT_CONFIG,
  MAX_COLUMN_ENTRIES,
  moveColumn,
  removeColumn,
  resolveColumns,
} from "../columnModel";

afterEach(() => vi.restoreAllMocks());

function buildProps(overrides = {}) {
  const declarationsAvailable =
    overrides.available ?? (overrides.status || "success") === "success";
  const resolved = resolveColumns({
    config: overrides.config || {
      v: 1,
      columns: [...DEFAULT_CONFIG.columns, { id: "metadata:tenant" }],
      hidden: [],
    },
    declarations: overrides.declarations || [
      { name: "tenant" },
      { name: "feature" },
    ],
    status: declarationsAvailable ? "success" : "pending",
  });
  return {
    anchorEl: document.body,
    open: true,
    onClose: vi.fn(),
    entries: resolved.entries,
    stale: resolved.stale,
    visibleCount: resolved.visibleCount,
    totalCount: resolved.totalCount,
    declarationStatus: overrides.status || "success",
    declarationsAvailable,
    unavailableCustomCount: overrides.unavailableCustomCount || 0,
    atCap: resolved.atCap,
    reservedDeclared: resolved.reservedDeclared,
    onToggle: vi.fn(),
    onMove: vi.fn(),
    onRemove: vi.fn(),
    onReset: vi.fn(),
    onRetryDeclarations: vi.fn(),
  };
}

// Re-renders on every move, as the real hook does, so focus is checked
// against the moved row.
function MovablePicker() {
  const [config, setConfig] = React.useState(DEFAULT_CONFIG);
  const { movableIds } = resolveColumns({
    config,
    declarations: [],
    status: "success",
  });
  return (
    <ColumnsPickerPopover
      {...buildProps({ config })}
      onMove={(id, delta) =>
        setConfig(moveColumn(config, id, delta, movableIds))
      }
    />
  );
}

// Re-renders on every Remove, as the real hook does, so focus is checked
// against the stale rows that remain.
function StalePicker() {
  const [config, setConfig] = React.useState({
    v: 1,
    columns: [
      ...DEFAULT_CONFIG.columns,
      { id: "metadata:a" },
      { id: "metadata:b" },
      { id: "metadata:c" },
    ],
    hidden: [],
  });
  return (
    <ColumnsPickerPopover
      {...buildProps({ config, declarations: [{ name: "feature" }] })}
      onRemove={(id) => setConfig(removeColumn(config, id))}
    />
  );
}

// Tab from the dialog until `target` has focus, as a keyboard user would.
async function tabTo(user, target) {
  for (let i = 0; i < 10 && document.activeElement !== target; i += 1) {
    await user.tab();
  }
  expect(target).toHaveFocus();
}

describe("ColumnsPickerPopover", () => {
  it("is a named dialog that lists groups, counts and a locked Timestamp", () => {
    const props = buildProps();
    render(<ColumnsPickerPopover {...props} />);

    const dialog = screen.getByRole("dialog", { name: "Choose columns" });
    expect(within(dialog).getByText("11 of 12 visible")).toBeInTheDocument();
    expect(within(dialog).getByText("Built-in")).toBeInTheDocument();
    expect(within(dialog).getByText("Custom properties")).toBeInTheDocument();

    const timestamp = within(dialog).getByRole("checkbox", {
      name: "Timestamp (always visible)",
    });
    expect(timestamp).toBeChecked();
    expect(timestamp).toBeDisabled();
    expect(
      within(dialog).queryByRole("button", { name: "Move Timestamp up" }),
    ).toBeNull();
    expect(
      within(dialog).getByRole("button", { name: "Move Model up" }),
    ).toBeDisabled();
    expect(
      within(dialog).getByRole("button", { name: "Move Model down" }),
    ).toBeEnabled();
    expect(
      within(dialog).getByRole("button", { name: "Move Session ID down" }),
    ).toBeDisabled();
    // Search is always available for the built-in and custom columns.
    expect(
      within(dialog).getByRole("textbox", { name: "Search columns" }),
    ).toBeInTheDocument();
    expect(within(dialog).queryByText(/Column limit reached/)).toBeNull();
  });

  it("does not duplicate keyboard stops for fitting built-in, custom, or stale labels", () => {
    const props = buildProps({
      config: {
        ...DEFAULT_CONFIG,
        columns: [
          ...DEFAULT_CONFIG.columns,
          { id: "metadata:tenant" },
          { id: "metadata:deleted" },
        ],
      },
    });
    render(<ColumnsPickerPopover {...props} />);
    for (const label of ["Model", "tenant", "deleted"]) {
      expect(screen.getByText(label, { exact: true })).not.toHaveAttribute(
        "tabindex",
      );
    }
    expect(screen.getByRole("checkbox", { name: "tenant" })).toBeEnabled();
    expect(
      screen.getByRole("button", { name: "Remove deleted" }),
    ).toBeEnabled();
  });

  it("lets keyboard users reveal clipped custom and stale labels", async () => {
    const user = userEvent.setup();
    const name = "billing_reconciliation_account_identifier";
    const staleName = "removed_billing_reconciliation_account_identifier";
    vi.spyOn(HTMLElement.prototype, "scrollWidth", "get").mockImplementation(
      function () {
        return [name, staleName].includes(this.textContent) ? 300 : 80;
      },
    );
    vi.spyOn(HTMLElement.prototype, "clientWidth", "get").mockReturnValue(108);
    const props = buildProps({
      config: {
        ...DEFAULT_CONFIG,
        columns: [
          ...DEFAULT_CONFIG.columns,
          { id: `metadata:${name}` },
          { id: `metadata:${staleName}` },
        ],
      },
      declarations: [{ name }],
    });
    render(<ColumnsPickerPopover {...props} />);
    expect(screen.getByText("Model", { exact: true })).not.toHaveAttribute(
      "tabindex",
    );
    const search = screen.getByRole("textbox", { name: "Search columns" });
    await user.type(search, name);
    const label = within(
      screen.getByRole("dialog", { name: "Choose columns" }),
    ).getByText(name, { exact: true });
    expect(label).toHaveAttribute("tabindex", "0");
    await tabTo(user, label);
    expect(await screen.findByRole("tooltip", { name })).toBeVisible();
    await user.clear(search);
    await user.type(search, staleName);
    const staleLabel = within(
      screen.getByRole("dialog", { name: "Choose columns" }),
    ).getByText(staleName, { exact: true });
    expect(staleLabel).toHaveAttribute("tabindex", "0");
    await tabTo(user, staleLabel);
    expect(
      await screen.findByRole("tooltip", { name: staleName }),
    ).toBeVisible();
  });

  it("scrolls only the column list, so the header and Reset never cover a focused control", () => {
    render(<ColumnsPickerPopover {...buildProps()} />);
    const dialog = screen.getByRole("dialog", { name: "Choose columns" });
    let list = within(dialog).getByText("Built-in");
    while (list !== dialog && getComputedStyle(list).overflowY !== "auto") {
      list = list.parentElement;
    }
    expect(list).not.toBe(dialog);
    expect(list).toContainElement(
      within(dialog).getByRole("button", { name: "Move Model down" }),
    );
    expect(list).not.toContainElement(
      within(dialog).getByRole("textbox", { name: "Search columns" }),
    );
    expect(list).not.toContainElement(within(dialog).getByText(/visible$/));
    expect(list).not.toContainElement(
      within(dialog).getByRole("button", { name: "Reset to default" }),
    );
    // The list's wrapper holds the 480px cap and may shrink below it inside the
    // paper, which keeps MUI's own viewport limit: on a short window the list,
    // not the paper, absorbs the shortfall and Reset stays in view.
    expect(list.parentElement).toHaveStyle({
      display: "flex",
      flexDirection: "column",
      maxHeight: "480px",
      minHeight: "0",
    });
    expect(dialog).toHaveStyle({ display: "flex", flexDirection: "column" });
    expect(dialog).not.toHaveStyle({ maxHeight: "480px" });
  });

  it("toggles, moves and resets through keyboard-operable controls", async () => {
    const user = userEvent.setup();
    const props = buildProps();
    render(<ColumnsPickerPopover {...props} />);
    const dialog = screen.getByRole("dialog", { name: "Choose columns" });

    await tabTo(
      user,
      within(dialog).getByRole("button", { name: "Move Model down" }),
    );
    await user.keyboard("{Enter}");
    expect(props.onMove).toHaveBeenCalledWith("builtin:model", 1);

    await user.tab();
    expect(
      within(dialog).getByRole("checkbox", { name: "Provider" }),
    ).toHaveFocus();
    await user.keyboard(" ");
    expect(props.onToggle).toHaveBeenCalledWith("builtin:provider");

    await user.click(within(dialog).getByRole("checkbox", { name: "feature" }));
    expect(props.onToggle).toHaveBeenCalledWith("metadata:feature");
    expect(
      within(dialog).getByRole("button", { name: "Move feature up" }),
    ).toBeDisabled();

    await user.click(
      within(dialog).getByRole("button", { name: "Reset to default" }),
    );
    expect(props.onReset).toHaveBeenCalledTimes(1);

    await user.click(within(dialog).getByRole("button", { name: "Close" }));
    expect(props.onClose).toHaveBeenCalledTimes(1);
  });

  it("keeps focus on the pressed Move button while its column moves", async () => {
    const user = userEvent.setup();
    render(<MovablePicker />);
    const dialog = screen.getByRole("dialog", { name: "Choose columns" });
    const moveDown = () =>
      within(dialog).getByRole("button", { name: "Move Model down" });
    const order = () =>
      within(dialog)
        .getAllByRole("checkbox")
        .slice(1, 4)
        .map((box) => box.getAttribute("aria-label"));

    await tabTo(user, moveDown());
    await user.keyboard("{Enter}");
    expect(order()).toEqual(["Provider", "Model", "Application"]);
    expect(moveDown()).toHaveFocus();
    await user.keyboard("{Enter}");
    expect(order()).toEqual(["Provider", "Application", "Model"]);
    expect(moveDown()).toHaveFocus();
  });

  it("moves focus to the opposite Move button when a column reaches the end of its group", async () => {
    const user = userEvent.setup();
    render(<MovablePicker />);
    const dialog = screen.getByRole("dialog", { name: "Choose columns" });
    const button = (name) => within(dialog).getByRole("button", { name });

    await user.click(button("Move Tokens down"));
    expect(button("Move Tokens down")).toBeDisabled();
    expect(button("Move Tokens up")).toHaveFocus();

    await user.click(button("Move Provider up"));
    expect(button("Move Provider up")).toBeDisabled();
    expect(button("Move Provider down")).toHaveFocus();
  });

  it("filters entries by search text without touching the data", async () => {
    const user = userEvent.setup();
    render(<ColumnsPickerPopover {...buildProps()} />);
    const dialog = screen.getByRole("dialog", { name: "Choose columns" });
    const search = within(dialog).getByRole("textbox", {
      name: "Search columns",
    });
    await user.type(search, "ten");
    expect(
      within(dialog).getByRole("checkbox", { name: "tenant" }),
    ).toBeInTheDocument();
    expect(
      within(dialog).queryByRole("checkbox", { name: "Provider" }),
    ).toBeNull();

    // Moving would swap with a neighbour the search hides; toggling stays.
    const moveDown = within(dialog).getByRole("button", {
      name: "Move Latency down",
    });
    expect(moveDown).toBeDisabled();
    expect(moveDown).toHaveAccessibleDescription(
      "Clear the search to reorder columns.",
    );
    expect(
      within(dialog).getByRole("checkbox", { name: "Latency" }),
    ).toBeEnabled();

    await user.clear(search);
    expect(moveDown).toBeEnabled();
    expect(
      within(dialog).queryByText("Clear the search to reorder columns."),
    ).toBeNull();
  });

  it("announces that no columns match instead of showing empty or unfiltered groups (WCAG 4.1.3)", async () => {
    const user = userEvent.setup();
    // The saved tenant column is no longer declared, so it is a stale row.
    render(
      <ColumnsPickerPopover
        {...buildProps({ declarations: [{ name: "feature" }] })}
      />,
    );
    const dialog = screen.getByRole("dialog", { name: "Choose columns" });
    const status = within(dialog).getByRole("status");
    expect(status).toBeEmptyDOMElement();

    await user.type(
      within(dialog).getByRole("textbox", { name: "Search columns" }),
      "zzz",
    );
    expect(status).toHaveTextContent("No matching columns");
    expect(within(dialog).queryByText("Built-in")).toBeNull();
    expect(within(dialog).queryByText("Custom properties")).toBeNull();
    expect(within(dialog).queryByText("No longer declared")).toBeNull();
    expect(
      within(dialog).queryByRole("button", { name: "Remove tenant" }),
    ).toBeNull();
  });

  it("filters the no-longer-declared rows by the search too", async () => {
    const user = userEvent.setup();
    render(
      <ColumnsPickerPopover
        {...buildProps({ declarations: [{ name: "feature" }] })}
      />,
    );
    const dialog = screen.getByRole("dialog", { name: "Choose columns" });
    await user.type(
      within(dialog).getByRole("textbox", { name: "Search columns" }),
      "tena",
    );
    expect(within(dialog).getByText("No longer declared")).toBeInTheDocument();
    expect(
      within(dialog).getByRole("button", { name: "Remove tenant" }),
    ).toBeInTheDocument();
    expect(within(dialog).getByRole("status")).toBeEmptyDOMElement();
  });

  it("hides the empty Custom properties group when a search matches nothing", async () => {
    const user = userEvent.setup();
    render(
      <ColumnsPickerPopover
        {...buildProps({ config: DEFAULT_CONFIG, declarations: [] })}
      />,
    );
    const dialog = screen.getByRole("dialog", { name: "Choose columns" });
    await user.type(
      within(dialog).getByRole("textbox", { name: "Search columns" }),
      "zzz",
    );
    expect(within(dialog).getByRole("status")).toHaveTextContent(
      "No matching columns",
    );
    expect(within(dialog).queryByText("Custom properties")).toBeNull();
    expect(
      within(dialog).queryByText(/No custom properties declared/),
    ).toBeNull();
  });

  it("keeps the declaration error and Retry during a search instead of claiming nothing matches", async () => {
    const user = userEvent.setup();
    render(<ColumnsPickerPopover {...buildProps({ status: "error" })} />);
    const dialog = screen.getByRole("dialog", { name: "Choose columns" });
    await user.type(
      within(dialog).getByRole("textbox", { name: "Search columns" }),
      "zzz",
    );
    expect(within(dialog).getByRole("status")).toBeEmptyDOMElement();
    expect(
      within(dialog).getByRole("button", {
        name: "Retry loading custom properties",
      }),
    ).toBeInTheDocument();
  });

  it("clears the search when the picker closes, even if it reopens mid-close", async () => {
    const props = buildProps();
    // RequestExplorerSection keeps the picker mounted and toggles `open`.
    const { rerender } = render(<ColumnsPickerPopover {...props} />);
    const search = () =>
      screen.getByRole("textbox", { name: "Search columns" });
    const typeSearch = (value) =>
      fireEvent.change(search(), { target: { value } });

    typeSearch("ten");
    rerender(<ColumnsPickerPopover {...props} open={false} />);
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    rerender(<ColumnsPickerPopover {...props} />);
    expect(search()).toHaveValue("");
    expect(
      screen.getByRole("checkbox", { name: "Provider" }),
    ).toBeInTheDocument();

    // A fast keyboard user can reopen before the close transition ends.
    typeSearch("ten");
    rerender(<ColumnsPickerPopover {...props} open={false} />);
    rerender(<ColumnsPickerPopover {...props} />);
    expect(search()).toHaveValue("");
  });

  it("shows the stale group with a Remove action after a successful fetch", async () => {
    const user = userEvent.setup();
    const props = buildProps({ declarations: [{ name: "feature" }] });
    render(<ColumnsPickerPopover {...props} />);
    const dialog = screen.getByRole("dialog", { name: "Choose columns" });
    expect(within(dialog).getByText("No longer declared")).toBeInTheDocument();
    expect(
      within(dialog).queryByRole("checkbox", { name: "tenant" }),
    ).toBeNull();
    await user.click(
      within(dialog).getByRole("button", { name: "Remove tenant" }),
    );
    expect(props.onRemove).toHaveBeenCalledWith("metadata:tenant");
  });

  it("moves focus to the next, else the previous, stale row after Remove, then to the search", async () => {
    const user = userEvent.setup();
    render(<StalePicker />);
    const dialog = screen.getByRole("dialog", { name: "Choose columns" });
    const button = (name) => within(dialog).getByRole("button", { name });

    await user.click(button("Remove b"));
    expect(button("Remove c")).toHaveFocus();
    await user.keyboard("{Enter}");
    expect(button("Remove a")).toHaveFocus();
    await user.keyboard("{Enter}");
    expect(within(dialog).queryByText("No longer declared")).toBeNull();
    expect(
      within(dialog).getByRole("textbox", { name: "Search columns" }),
    ).toHaveFocus();
  });

  it("shows an error row with an accessible Retry and no stored names while declarations fail", async () => {
    const user = userEvent.setup();
    const props = buildProps({ status: "error", unavailableCustomCount: 1 });
    render(<ColumnsPickerPopover {...props} />);
    const dialog = screen.getByRole("dialog", { name: "Choose columns" });
    expect(
      within(dialog).getByText(/Couldn't load custom properties/),
    ).toBeInTheDocument();
    expect(
      within(dialog).getByText(/1 saved custom column unavailable/),
    ).toBeInTheDocument();
    expect(within(dialog).queryByText("tenant")).toBeNull();
    expect(within(dialog).queryByText("No longer declared")).toBeNull();
    await user.click(
      within(dialog).getByRole("button", {
        name: "Retry loading custom properties",
      }),
    );
    expect(props.onRetryDeclarations).toHaveBeenCalledTimes(1);
  });

  it("keeps cached custom controls usable alongside a failed-refresh Retry", async () => {
    const user = userEvent.setup();
    const props = buildProps({
      status: "error",
      available: true,
      config: {
        ...DEFAULT_CONFIG,
        columns: [
          ...DEFAULT_CONFIG.columns,
          { id: "metadata:tenant" },
          { id: "metadata:feature" },
        ],
      },
    });
    render(<ColumnsPickerPopover {...props} />);
    expect(screen.getByRole("alert")).toHaveTextContent(
      "Couldn't refresh custom properties. Showing previously loaded properties.",
    );
    const tenant = screen.getByRole("checkbox", {
      name: "tenant",
      exact: true,
    });
    expect(tenant).toBeChecked();
    expect(tenant).toBeEnabled();
    await user.click(tenant);
    expect(props.onToggle).toHaveBeenCalledWith("metadata:tenant");
    await user.click(screen.getByRole("button", { name: "Move tenant down" }));
    expect(props.onMove).toHaveBeenCalledWith("metadata:tenant", 1);
    await user.type(
      screen.getByRole("textbox", { name: "Search columns" }),
      "no_match",
    );
    expect(
      screen.getByRole("button", { name: "Retry loading custom properties" }),
    ).toBeEnabled();
    expect(screen.queryByText("No matching columns")).not.toBeInTheDocument();
  });

  it("keeps cached rows during Retry and keeps empty cached snapshots explicit", () => {
    const props = buildProps({ status: "error", available: true });
    const { rerender } = render(
      <ColumnsPickerPopover {...props} declarationsFetching />,
    );
    expect(
      screen.getByRole("checkbox", { name: "tenant", exact: true }),
    ).toBeEnabled();
    expect(
      screen.getByRole("button", { name: "Retry loading custom properties" }),
    ).toBeDisabled();
    rerender(
      <ColumnsPickerPopover
        {...buildProps({ status: "error", available: true, declarations: [] })}
      />,
    );
    expect(
      screen.getByText("No custom properties declared.", { exact: false }),
    ).toBeInTheDocument();
    expect(screen.getByRole("alert")).toHaveTextContent(
      "Showing previously loaded properties.",
    );
    expect(
      screen.getByRole("button", { name: "Remove tenant" }),
    ).toBeInTheDocument();
  });

  it("restores Retry focus to Search when success filters out every custom declaration", async () => {
    const user = userEvent.setup();
    const errorProps = buildProps({ status: "error" });
    const successProps = buildProps({ status: "success" });
    const { rerender } = render(<ColumnsPickerPopover {...errorProps} />);
    const dialog = screen.getByRole("dialog", { name: "Choose columns" });
    const search = within(dialog).getByRole("textbox", {
      name: "Search columns",
    });

    await user.type(search, "lat");
    await user.click(
      within(dialog).getByRole("button", {
        name: "Retry loading custom properties",
      }),
    );
    rerender(<ColumnsPickerPopover {...successProps} />);

    expect(search).toHaveFocus();
    expect(
      within(dialog).getByRole("checkbox", { name: "Latency" }),
    ).toBeInTheDocument();
    expect(within(dialog).queryByText("Custom properties")).toBeNull();
  });

  it("keeps focus on the Custom properties heading while Retry reloads", async () => {
    const user = userEvent.setup();
    const props = buildProps({ status: "error" });
    const { rerender } = render(<ColumnsPickerPopover {...props} />);
    const dialog = screen.getByRole("dialog", { name: "Choose columns" });
    const heading = within(dialog).getByText("Custom properties");
    const retry = () =>
      within(dialog).getByRole("button", {
        name: "Retry loading custom properties",
      });

    await user.click(retry());
    expect(props.onRetryDeclarations).toHaveBeenCalledTimes(1);
    expect(heading).toHaveFocus();
    // A first load turns the error row into the loading row, then back.
    rerender(<ColumnsPickerPopover {...props} declarationStatus="pending" />);
    expect(heading).toHaveFocus();
    rerender(<ColumnsPickerPopover {...props} />);
    expect(heading).toHaveFocus();
    await user.tab();
    expect(retry()).toHaveFocus();
  });

  it("disables Retry and shows progress while declarations reload", () => {
    render(
      <ColumnsPickerPopover
        {...buildProps({ status: "error" })}
        declarationsFetching
      />,
    );
    const dialog = screen.getByRole("dialog", { name: "Choose columns" });
    expect(
      within(dialog).getByRole("button", {
        name: "Retry loading custom properties",
      }),
    ).toBeDisabled();
    expect(within(dialog).getByRole("progressbar")).toBeInTheDocument();
  });

  it("shows a loading row while declarations are pending", () => {
    render(<ColumnsPickerPopover {...buildProps({ status: "pending" })} />);
    const dialog = screen.getByRole("dialog", { name: "Choose columns" });
    expect(
      within(dialog).getByText(/Loading custom properties/),
    ).toBeInTheDocument();
    expect(within(dialog).queryByText("tenant")).toBeNull();
  });

  it("points to Custom Properties when nothing is declared", () => {
    render(
      <ColumnsPickerPopover
        {...buildProps({ config: DEFAULT_CONFIG, declarations: [] })}
      />,
    );
    const dialog = screen.getByRole("dialog", { name: "Choose columns" });
    expect(
      within(dialog).getByText(/No custom properties declared/),
    ).toBeInTheDocument();
    expect(
      within(dialog).getByRole("link", {
        name: "Declare one in Custom Properties",
      }),
    ).toHaveAttribute("href", "/dashboard/gateway/custom-properties");
    // Search remains available even when there are no custom declarations.
    expect(
      within(dialog).getByRole("textbox", { name: "Search columns" }),
    ).toBeInTheDocument();
  });

  it("says Application and Service are built-in when they are the only declarations", () => {
    render(
      <ColumnsPickerPopover
        {...buildProps({
          config: DEFAULT_CONFIG,
          declarations: [{ name: "application" }, { name: "service" }],
        })}
      />,
    );
    const dialog = screen.getByRole("dialog", { name: "Choose columns" });
    expect(
      within(dialog).getByText(
        /No other custom properties declared \(Application and Service are already built-in columns\)/,
      ),
    ).toBeInTheDocument();
    expect(
      within(dialog).queryByText(/No custom properties declared/),
    ).toBeNull();
    expect(
      within(dialog).getByRole("link", {
        name: "Declare one in Custom Properties",
      }),
    ).toBeInTheDocument();
  });

  it("disables columns that cannot be added once the column limit is reached", () => {
    const names = Array.from(
      { length: MAX_COLUMN_ENTRIES - DEFAULT_CONFIG.columns.length },
      (_, i) => `p${i}`,
    );
    render(
      <ColumnsPickerPopover
        {...buildProps({
          config: {
            v: 1,
            columns: [
              ...DEFAULT_CONFIG.columns,
              ...names.map((name) => ({ id: `metadata:${name}` })),
            ],
            hidden: [],
          },
          declarations: [...names, "extra"].map((name) => ({ name })),
        })}
      />,
    );
    const dialog = screen.getByRole("dialog", { name: "Choose columns" });
    expect(
      within(dialog).getByText(/Column limit reached/),
    ).toBeInTheDocument();
    expect(
      within(dialog).getByRole("checkbox", { name: "extra" }),
    ).toBeDisabled();
    expect(within(dialog).getByRole("checkbox", { name: "p0" })).toBeEnabled();
  });
});
