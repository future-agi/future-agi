import React from "react";
import { describe, expect, it, vi } from "vitest";
import {
  render,
  screen,
  userEvent,
  waitFor,
  within,
} from "src/utils/test-utils";

import FilterPanel from "../FilterPanel";

const popoverSpy = vi.fn();
vi.mock("@mui/material", async (importOriginal) => {
  const actual = await importOriginal();
  const { createElement } = await import("react");
  return {
    ...actual,
    Popover: (props) => {
      popoverSpy(props);
      return createElement(actual.Popover, props);
    },
  };
});

// A `single` field carries exactly one value, so a second row pointing at it
// would be merged away on apply while the UI kept showing it as active.
const SINGLE_FIELDS = [
  {
    value: "metric_type",
    label: "Alert Type",
    type: "enum",
    operators: ["is"],
    single: true,
    choices: ["span_response_time"],
    choiceLabels: { span_response_time: "Span response time" },
  },
  {
    value: "status",
    label: "Status",
    type: "enum",
    operators: ["is"],
    single: true,
    choices: ["triggered"],
    choiceLabels: { triggered: "Triggered" },
  },
];

const renderPanel = (fields = SINGLE_FIELDS, onApply = vi.fn(), props = {}) =>
  render(
    <FilterPanel
      anchorEl={document.body}
      open
      onClose={vi.fn()}
      filterFields={fields}
      currentFilters={null}
      onApply={onApply}
      basicOnly
      {...props}
    />,
  );

// The value list renders in its own popover, where the option text collides
// with the chips already shown in the row.
const openValuePicker = async (user, rowIndex) => {
  await user.click(screen.getAllByText("Select values...")[rowIndex]);
  return within(
    screen
      .getByPlaceholderText("Search values...")
      .closest(".MuiPopover-paper"),
  );
};

describe("FilterPanel — single-value fields", () => {
  it("adds a row for the next unused field instead of duplicating the first", async () => {
    const user = userEvent.setup();
    renderPanel();

    expect(screen.getByText("Alert Type")).toBeInTheDocument();
    expect(screen.queryByText("Status")).not.toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: /add filter/i }));

    expect(screen.getByText("Status")).toBeInTheDocument();
    expect(screen.getAllByText("Alert Type")).toHaveLength(1);
  });

  it("stops offering new rows once every single-value field is taken", async () => {
    const user = userEvent.setup();
    renderPanel();

    const addFilter = screen.getByRole("button", { name: /add filter/i });
    expect(addFilter).toBeEnabled();

    await user.click(addFilter);

    expect(addFilter).toBeDisabled();
  });

  it("keeps adding rows when the fields allow multiple values", async () => {
    const user = userEvent.setup();
    renderPanel([
      { value: "name", label: "Name", type: "enum", choices: ["a", "b"] },
    ]);

    const addFilter = screen.getByRole("button", { name: /add filter/i });
    await user.click(addFilter);

    // Two rows on a multi-value field merge into one array, which is coherent —
    // the guard must not block it.
    expect(screen.getAllByText("Name")).toHaveLength(2);
    expect(addFilter).toBeEnabled();
  });

  it("sends a value once when two rows on the same field both select it", async () => {
    const user = userEvent.setup();
    const onApply = vi.fn();
    const multiField = [
      {
        value: "project_id",
        label: "Project",
        type: "enum",
        choices: ["p1", "p2"],
      },
    ];
    renderPanel(multiField, onApply);

    const firstRow = await openValuePicker(user, 0);
    await user.click(firstRow.getByText("p1"));
    await user.click(firstRow.getByText("p2"));
    await user.keyboard("{Escape}");

    await user.click(screen.getByRole("button", { name: /add filter/i }));

    const secondRow = await openValuePicker(user, 0);
    await user.click(secondRow.getByText("p1"));
    await user.keyboard("{Escape}");

    await waitFor(
      () =>
        expect(onApply).toHaveBeenLastCalledWith({ project_id: ["p1", "p2"] }),
      { timeout: 2000 },
    );
  });
});

describe("FilterPanel — the opt-in props", () => {
  it("basicOnly hides the tab strip, the AI box and the caption", () => {
    const { unmount } = renderPanel();
    expect(
      screen.queryByRole("tab", { name: /basic/i }),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByRole("tab", { name: /query/i }),
    ).not.toBeInTheDocument();
    expect(screen.queryByText(/basic filter/i)).not.toBeInTheDocument();
    expect(
      screen.queryByPlaceholderText(/ask ai|e\.g\./i),
    ).not.toBeInTheDocument();
    unmount();

    // Without it, all three come back — otherwise this asserts nothing.
    renderPanel(SINGLE_FIELDS, vi.fn(), { basicOnly: false });
    expect(screen.getByRole("tab", { name: /basic/i })).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: /query/i })).toBeInTheDocument();
    expect(screen.getByText(/basic filter/i)).toBeInTheDocument();
  });

  it("placement maps to the popover's horizontal origins", () => {
    // jsdom has no layout, so both placements compute identical styles —
    // assert the props MUI is handed rather than the rendered position.
    const origins = (placement) => {
      popoverSpy.mockClear();
      const { unmount } = renderPanel(SINGLE_FIELDS, vi.fn(), { placement });
      // The value pickers render their own Popovers; only the panel is open.
      const props = popoverSpy.mock.calls.map(([p]) => p).find((p) => p.open);
      unmount();
      return [props.anchorOrigin.horizontal, props.transformOrigin.horizontal];
    };

    expect(origins("bottom-end")).toEqual(["right", "right"]);
    expect(origins("bottom-start")).toEqual(["left", "left"]);
    expect(origins(undefined)).toEqual(["left", "left"]);
  });

  it("choiceLabels drives search, so a raw key finds nothing", async () => {
    const user = userEvent.setup();
    renderPanel();
    const picker = await openValuePicker(user, 0);

    await user.type(
      screen.getByPlaceholderText("Search values..."),
      "Span resp",
    );
    expect(picker.getByText("Span response time")).toBeInTheDocument();

    await user.clear(screen.getByPlaceholderText("Search values..."));
    await user.type(
      screen.getByPlaceholderText("Search values..."),
      "span_response",
    );
    expect(picker.queryByText("Span response time")).not.toBeInTheDocument();
  });

  it("choiceLabels suppresses the custom-value row", async () => {
    const user = userEvent.setup();
    renderPanel();
    await openValuePicker(user, 0);
    await user.type(
      screen.getByPlaceholderText("Search values..."),
      "whatever",
    );
    // A typed string can never be a valid value when the choices are opaque keys.
    expect(screen.queryByText(/^Specify:/)).not.toBeInTheDocument();
  });

  it("offers the custom-value row when a field has no choiceLabels", async () => {
    const user = userEvent.setup();
    renderPanel([
      { value: "name", label: "Name", type: "enum", choices: ["alpha"] },
    ]);
    await openValuePicker(user, 0);
    await user.type(
      screen.getByPlaceholderText("Search values..."),
      "whatever",
    );
    expect(screen.getByText(/Specify:/)).toBeInTheDocument();
  });
});

// Opening the panel rebuilds the applied object from scratch. Callers that key
// off its identity — Issues.jsx hands it to an AG Grid datasource, which drops
// its cache and refetches from row 0 — paid a round trip per funnel click.
describe("re-applying unchanged filters", () => {
  const FIELDS = [
    {
      value: "status",
      label: "Status",
      type: "enum",
      operators: ["is"],
      choices: ["open", "closed"],
    },
  ];

  const panel = (props) => (
    <FilterPanel
      anchorEl={document.body}
      onClose={() => {}}
      filterFields={FIELDS}
      basicOnly
      {...props}
    />
  );

  it("stays quiet when the panel is opened and nothing is touched", async () => {
    const onApply = vi.fn();
    const currentFilters = { status: ["open"] };
    const { rerender } = render(
      panel({ open: false, currentFilters, onApply }),
    );

    rerender(panel({ open: true, currentFilters, onApply }));

    await new Promise((r) => setTimeout(r, 800));
    expect(onApply).not.toHaveBeenCalled();
  });

  it("still applies once the user actually changes a row", async () => {
    const user = userEvent.setup();
    const onApply = vi.fn();
    const currentFilters = { status: ["open"] };
    render(panel({ open: true, currentFilters, onApply }));

    // The row hydrates with "open" already picked, so the trigger shows a
    // chip rather than the placeholder; clicking it bubbles to the picker.
    await user.click(screen.getByText("open"));
    const row = within(
      screen
        .getByPlaceholderText("Search values...")
        .closest(".MuiPopover-paper"),
    );
    await user.click(row.getByText("closed"));
    await user.keyboard("{Escape}");

    await waitFor(
      () =>
        expect(onApply).toHaveBeenCalledWith({ status: ["open", "closed"] }),
      { timeout: 2000 },
    );
    expect(onApply).toHaveBeenCalledTimes(1);
  });
});

// Reopening used to push one row per array value, so filtering on three
// projects came back as three identical "Project" rows the user never made.
describe("hydrating multi-value filters", () => {
  const FIELDS = [
    {
      value: "project_id",
      label: "Project",
      type: "enum",
      choices: ["p1", "p2", "p3"],
      choiceLabels: { p1: "Alpha", p2: "Beta", p3: "Gamma" },
    },
    {
      value: "status",
      label: "Status",
      type: "enum",
      single: true,
      choices: ["triggered", "healthy"],
    },
    { value: "name", label: "Name", type: "string" },
  ];

  const openWith = (currentFilters, onApply = vi.fn()) =>
    render(
      <FilterPanel
        anchorEl={document.body}
        open
        onClose={vi.fn()}
        filterFields={FIELDS}
        currentFilters={currentFilters}
        onApply={onApply}
        basicOnly
      />,
    );

  const rowLabels = () =>
    screen.getAllByRole("combobox").map((el) => el.textContent);

  it("keeps three project values in one row", () => {
    openWith({ project_id: ["p1", "p2", "p3"] });

    expect(rowLabels().filter((l) => l === "Project")).toHaveLength(1);
    // Two chips render, then a "+1" overflow marker.
    expect(screen.getByText("Alpha")).toBeInTheDocument();
    expect(screen.getByText("Beta")).toBeInTheDocument();
    expect(screen.getByText("+1")).toBeInTheDocument();
  });

  it("still gives a negated key its own row", () => {
    openWith({ project_id: ["p1"], project_id_not: ["p2"] });

    expect(rowLabels().filter((l) => l === "Project")).toHaveLength(2);
  });

  it("keeps one value for a single-value field", () => {
    openWith({ status: ["triggered", "healthy"] });

    expect(rowLabels().filter((l) => l === "Status")).toHaveLength(1);
    expect(screen.getByText("triggered")).toBeInTheDocument();
    expect(screen.queryByText("healthy")).not.toBeInTheDocument();
  });

  it("still splits a text field, which has nowhere to put a second value", () => {
    openWith({ name: ["alpha", "beta"] });

    expect(rowLabels().filter((l) => l === "Name")).toHaveLength(2);
  });

  it("applies the same object it hydrated from", async () => {
    const onApply = vi.fn();
    openWith({ project_id: ["p1", "p2", "p3"] }, onApply);

    // The guard compares by value, so an unchanged set stays quiet.
    await new Promise((r) => setTimeout(r, 800));
    expect(onApply).not.toHaveBeenCalled();
  });
});
