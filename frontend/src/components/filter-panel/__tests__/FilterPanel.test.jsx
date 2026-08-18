import React from "react";
import { describe, expect, it, vi } from "vitest";
import {
  render,
  screen,
  userEvent,
  waitFor,
  within,
} from "src/utils/test-utils";

import FilterPanel, { QueryInput } from "../FilterPanel";

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

// ---------------------------------------------------------------------------
// Mock transitive dependencies pulled in by FilterPanel.jsx
// ---------------------------------------------------------------------------
vi.mock("src/utils/axios", () => ({
  default: { get: vi.fn(), post: vi.fn() },
  endpoints: {},
}));

vi.mock("notistack", () => ({ enqueueSnackbar: vi.fn() }));

vi.mock("src/components/iconify", () => ({
  default: (props) => <span data-testid="iconify" {...props} />,
}));

vi.mock("src/hooks/use-ai-filter", () => ({
  useAIFilter: () => ({
    loading: false,
    error: null,
    generateFilters: vi.fn(),
  }),
}));

// ---------------------------------------------------------------------------
// Shared test fixtures
// ---------------------------------------------------------------------------
const FIELD_MAP = {
  status: { label: "Status", type: "enum", choices: ["OK", "ERROR"] },
  model: { label: "Model", type: "string" },
  latency: { label: "Latency", type: "number" },
  userId: { label: "User ID", type: "string" },
};

const FILTER_FIELDS = Object.entries(FIELD_MAP).map(([value, def]) => ({
  value,
  label: def.label,
  type: def.type,
  ...(def.choices ? { choices: def.choices } : {}),
}));

const TOKEN_STATUS_OK = { field: "status", operator: "equals", value: "OK" };
const TOKEN_MODEL_GPT = {
  field: "model",
  operator: "contains",
  value: "gpt",
};
const TOKEN_LATENCY_HIGH = {
  field: "latency",
  operator: "gt",
  value: "1000",
};
const TOKEN_USER_ID = {
  field: "userId",
  operator: "equals",
  value: "user-42",
};
const TOKEN_STATUS_ARRAY = {
  field: "status",
  operator: "equals",
  value: ["ERROR", "WARN"],
};
const TOKEN_LONG_VALUE = {
  field: "model",
  operator: "contains",
  value:
    "gpt-4o-2024-05-13-with-a-really-long-model-identifier-that-exceeds-fifty-characters",
};

const makeProps = (overrides = {}) => ({
  filterFields: FILTER_FIELDS,
  fieldMap: FIELD_MAP,
  onApply: vi.fn(),
  ...overrides,
});

const renderQueryInput = (props = {}) => {
  const user = userEvent.setup();
  const result = render(<QueryInput {...makeProps(props)} />);
  return { user, ...result };
};

// ---------------------------------------------------------------------------
// Category A: Operator label visibility (token count)
// ---------------------------------------------------------------------------
describe("QueryInput — operator label visibility", () => {
  it("shows no AND label with zero tokens", () => {
    renderQueryInput({ initialTokens: [] });
    expect(screen.queryByText("AND")).not.toBeInTheDocument();
  });

  it("shows no AND label with a single token", () => {
    renderQueryInput({ initialTokens: [TOKEN_STATUS_OK] });
    expect(screen.queryByText("AND")).not.toBeInTheDocument();
  });

  it("shows one AND label between two tokens", () => {
    renderQueryInput({
      initialTokens: [TOKEN_STATUS_OK, TOKEN_MODEL_GPT],
    });
    expect(screen.getAllByText("AND")).toHaveLength(1);
  });

  it("shows N−1 labels with four tokens", () => {
    renderQueryInput({
      initialTokens: [
        TOKEN_STATUS_OK,
        TOKEN_MODEL_GPT,
        TOKEN_LATENCY_HIGH,
        TOKEN_USER_ID,
      ],
    });
    expect(screen.getAllByText("AND")).toHaveLength(3);
  });

  it("never renders an OR label (operator is fixed at AND)", () => {
    renderQueryInput({
      initialTokens: [TOKEN_STATUS_OK, TOKEN_MODEL_GPT],
    });
    expect(screen.queryByText("OR")).not.toBeInTheDocument();
  });
});

// ---------------------------------------------------------------------------
// Category B: Static, non-interactive label
// ---------------------------------------------------------------------------
describe("QueryInput — static AND label", () => {
  it("renders the label as a non-interactive span", () => {
    renderQueryInput({
      initialTokens: [TOKEN_STATUS_OK, TOKEN_MODEL_GPT],
    });
    const label = screen.getByText("AND");
    expect(label.tagName).toBe("SPAN");
  });

  it("has no button semantics", () => {
    renderQueryInput({
      initialTokens: [TOKEN_STATUS_OK, TOKEN_MODEL_GPT],
    });
    const label = screen.getByText("AND");
    expect(label.getAttribute("role")).not.toBe("button");
    expect(label.getAttribute("aria-pressed")).toBeNull();
  });

  it("does not change when clicked (stays AND, no OR)", async () => {
    const { user } = renderQueryInput({
      initialTokens: [TOKEN_STATUS_OK, TOKEN_MODEL_GPT],
    });
    await user.click(screen.getByText("AND"));
    expect(screen.getByText("AND")).toBeInTheDocument();
    expect(screen.queryByText("OR")).not.toBeInTheDocument();
  });
});

// ---------------------------------------------------------------------------
// Category C: Token lifecycle (label follows token count)
// ---------------------------------------------------------------------------
describe("QueryInput — token lifecycle", () => {
  it("label disappears when deleting the last token (2→1)", async () => {
    const { user } = renderQueryInput({
      initialTokens: [TOKEN_STATUS_OK, TOKEN_MODEL_GPT],
    });
    expect(screen.getByText("AND")).toBeInTheDocument();
    const chips = document.querySelectorAll(".MuiChip-root");
    const secondChipDelete = chips[1]?.querySelector(".MuiChip-deleteIcon");
    if (secondChipDelete) await user.click(secondChipDelete);

    await waitFor(() => {
      expect(screen.queryByText("AND")).not.toBeInTheDocument();
    });
  });

  it("label disappears when deleting the first token (2→1)", async () => {
    const { user } = renderQueryInput({
      initialTokens: [TOKEN_STATUS_OK, TOKEN_MODEL_GPT],
    });
    expect(screen.getByText("AND")).toBeInTheDocument();
    const chips = document.querySelectorAll(".MuiChip-root");
    const firstChipDelete = chips[0]?.querySelector(".MuiChip-deleteIcon");
    if (firstChipDelete) await user.click(firstChipDelete);

    await waitFor(() => {
      expect(screen.queryByText("AND")).not.toBeInTheDocument();
    });
  });

  it("reduces label count when deleting the middle token (3→2)", async () => {
    const { user } = renderQueryInput({
      initialTokens: [TOKEN_STATUS_OK, TOKEN_MODEL_GPT, TOKEN_LATENCY_HIGH],
    });
    expect(screen.getAllByText("AND")).toHaveLength(2);
    const chips = document.querySelectorAll(".MuiChip-root");
    const middleDelete = chips[1]?.querySelector(".MuiChip-deleteIcon");
    if (middleDelete) await user.click(middleDelete);

    await waitFor(() => {
      expect(screen.getAllByText("AND")).toHaveLength(1);
    });
  });

  it("label hides when clicking a chip to edit (2→1)", async () => {
    const { user } = renderQueryInput({
      initialTokens: [TOKEN_STATUS_OK, TOKEN_MODEL_GPT],
    });
    expect(screen.getByText("AND")).toBeInTheDocument();
    const chips = document.querySelectorAll(".MuiChip-root");
    await user.click(chips[0]);

    await waitFor(() => {
      expect(screen.queryByText("AND")).not.toBeInTheDocument();
    });
  });
});

// ---------------------------------------------------------------------------
// Category D: External sync via initialTokens
// ---------------------------------------------------------------------------
describe("QueryInput — external sync via initialTokens", () => {
  it("keeps the label when initialTokens refreshes with the same data", () => {
    const onApply = vi.fn();
    const props = makeProps({
      initialTokens: [TOKEN_STATUS_OK, TOKEN_MODEL_GPT],
      onApply,
    });
    const { rerender } = render(<QueryInput {...props} />);
    expect(screen.getByText("AND")).toBeInTheDocument();

    rerender(<QueryInput {...props} />);
    expect(screen.getByText("AND")).toBeInTheDocument();
  });

  it("hides the label when initialTokens changes to a single token", async () => {
    const onApply = vi.fn();
    const { rerender } = render(
      <QueryInput
        {...makeProps({
          initialTokens: [TOKEN_STATUS_OK, TOKEN_MODEL_GPT],
          onApply,
        })}
      />,
    );
    expect(screen.getByText("AND")).toBeInTheDocument();

    rerender(
      <QueryInput
        {...makeProps({ initialTokens: [TOKEN_STATUS_OK], onApply })}
      />,
    );
    await waitFor(() => {
      expect(screen.queryByText("AND")).not.toBeInTheDocument();
    });
  });

  it("hides the label when initialTokens clears to empty", async () => {
    const onApply = vi.fn();
    const { rerender } = render(
      <QueryInput
        {...makeProps({
          initialTokens: [TOKEN_STATUS_OK, TOKEN_MODEL_GPT],
          onApply,
        })}
      />,
    );
    expect(screen.getByText("AND")).toBeInTheDocument();

    rerender(<QueryInput {...makeProps({ initialTokens: [], onApply })} />);
    await waitFor(() => {
      expect(screen.queryByText("AND")).not.toBeInTheDocument();
    });
  });
});

// ---------------------------------------------------------------------------
// Category E: Token data shapes
// ---------------------------------------------------------------------------
describe("QueryInput — token data shapes", () => {
  it("renders the label with array-value tokens", () => {
    renderQueryInput({
      initialTokens: [TOKEN_STATUS_OK, TOKEN_STATUS_ARRAY],
    });
    expect(screen.getByText("AND")).toBeInTheDocument();
  });

  it("renders the label with long-value tokens", () => {
    renderQueryInput({
      initialTokens: [TOKEN_MODEL_GPT, TOKEN_LONG_VALUE],
    });
    expect(screen.getByText("AND")).toBeInTheDocument();
  });
});

// ---------------------------------------------------------------------------
// Category F: Existing chip behaviour unchanged
// ---------------------------------------------------------------------------
describe("QueryInput — existing chip behaviour unchanged", () => {
  it("renders a chip per token alongside the label", () => {
    renderQueryInput({
      initialTokens: [TOKEN_STATUS_OK, TOKEN_MODEL_GPT],
    });
    const chips = document.querySelectorAll(".MuiChip-root");
    expect(chips.length).toBe(2);
    expect(screen.getByText("AND")).toBeInTheDocument();
  });

  it("keeps a delete icon on each token", () => {
    renderQueryInput({
      initialTokens: [TOKEN_STATUS_OK, TOKEN_MODEL_GPT],
    });
    const deleteIcons = document.querySelectorAll(".MuiChip-deleteIcon");
    expect(deleteIcons.length).toBe(2);
  });
});
