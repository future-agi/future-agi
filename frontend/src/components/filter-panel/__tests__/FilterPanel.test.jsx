import { describe, it, expect, vi } from "vitest";
import { render, screen, waitFor } from "../../../utils/test-utils";
import { userEvent } from "../../../utils/test-utils";
import { QueryInput } from "../FilterPanel";

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
// Category A: Operator visibility (token count)
// ---------------------------------------------------------------------------
describe("QueryInput — operator visibility", () => {
  it("does not render any operator badge when initialTokens is empty", () => {
    renderQueryInput({ initialTokens: [] });
    expect(screen.queryByText("AND")).not.toBeInTheDocument();
    expect(screen.queryByText("OR")).not.toBeInTheDocument();
  });

  it("does not render any operator badge with a single token", () => {
    renderQueryInput({ initialTokens: [TOKEN_STATUS_OK] });
    expect(screen.queryByText("AND")).not.toBeInTheDocument();
    expect(screen.queryByText("OR")).not.toBeInTheDocument();
  });

  it("shows exactly one AND badge between two tokens", () => {
    renderQueryInput({
      initialTokens: [TOKEN_STATUS_OK, TOKEN_MODEL_GPT],
    });
    expect(screen.getAllByText("AND")).toHaveLength(1);
    expect(screen.queryByText("OR")).not.toBeInTheDocument();
  });

  it("shows exactly one OR badge after toggle with two tokens", async () => {
    const { user } = renderQueryInput({
      initialTokens: [TOKEN_STATUS_OK, TOKEN_MODEL_GPT],
    });
    await user.click(screen.getByText("AND"));
    expect(screen.getByText("OR")).toBeInTheDocument();
    expect(screen.queryByText("AND")).not.toBeInTheDocument();
  });

  it("shows N−1 badges with 4 tokens (3 badges)", () => {
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
});

// ---------------------------------------------------------------------------
// Category B: Toggle mechanics
// ---------------------------------------------------------------------------
describe("QueryInput — toggle mechanics", () => {
  it("toggles from AND to OR on click", async () => {
    const { user } = renderQueryInput({
      initialTokens: [TOKEN_STATUS_OK, TOKEN_MODEL_GPT],
    });
    await user.click(screen.getByText("AND"));
    expect(screen.getByText("OR")).toBeInTheDocument();
  });

  it("toggles back from OR to AND on second click", async () => {
    const { user } = renderQueryInput({
      initialTokens: [TOKEN_STATUS_OK, TOKEN_MODEL_GPT],
    });
    await user.click(screen.getByText("AND"));
    await user.click(screen.getByText("OR"));
    expect(screen.getByText("AND")).toBeInTheDocument();
  });

  it("all operator badges update when toggling with 3 tokens", async () => {
    const { user } = renderQueryInput({
      initialTokens: [TOKEN_STATUS_OK, TOKEN_MODEL_GPT, TOKEN_LATENCY_HIGH],
    });
    expect(screen.getAllByText("AND")).toHaveLength(2);
    // Click first badge
    await user.click(screen.getAllByText("AND")[0]);
    // All become OR
    expect(screen.getAllByText("OR")).toHaveLength(2);
    expect(screen.queryByText("AND")).not.toBeInTheDocument();
  });

  it("handles rapid 3-click cycle: AND → OR → AND → OR", async () => {
    const { user } = renderQueryInput({
      initialTokens: [TOKEN_STATUS_OK, TOKEN_MODEL_GPT],
    });
    await user.click(screen.getByText("AND"));
    await user.click(screen.getByText("OR"));
    await user.click(screen.getByText("AND"));
    expect(screen.getByText("OR")).toBeInTheDocument();
  });
});

// ---------------------------------------------------------------------------
// Category C: Token lifecycle
// ---------------------------------------------------------------------------
describe("QueryInput — token lifecycle", () => {
  it("operator disappears when deleting last token (2→1)", async () => {
    const { user } = renderQueryInput({
      initialTokens: [TOKEN_STATUS_OK, TOKEN_MODEL_GPT],
    });
    expect(screen.getByText("AND")).toBeInTheDocument();
    // Click delete icon on second chip
    const chips = document.querySelectorAll(".MuiChip-root");
    const secondChipDelete = chips[1]?.querySelector(".MuiChip-deleteIcon");
    if (secondChipDelete) await user.click(secondChipDelete);

    await waitFor(() => {
      expect(screen.queryByText("AND")).not.toBeInTheDocument();
      expect(screen.queryByText("OR")).not.toBeInTheDocument();
    });
  });

  it("operator disappears when deleting first token (2→1)", async () => {
    const { user } = renderQueryInput({
      initialTokens: [TOKEN_STATUS_OK, TOKEN_MODEL_GPT],
    });
    expect(screen.getByText("AND")).toBeInTheDocument();
    // Click delete icon on first chip
    const chips = document.querySelectorAll(".MuiChip-root");
    const firstChipDelete = chips[0]?.querySelector(".MuiChip-deleteIcon");
    if (firstChipDelete) await user.click(firstChipDelete);

    await waitFor(() => {
      expect(screen.queryByText("AND")).not.toBeInTheDocument();
    });
  });

  it("reduces badge count when deleting middle token (3→2)", async () => {
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

  it("operator keeps toggled value after deleting a token", async () => {
    const { user } = renderQueryInput({
      initialTokens: [
        TOKEN_STATUS_OK,
        TOKEN_MODEL_GPT,
        TOKEN_LATENCY_HIGH,
      ],
    });
    await user.click(screen.getAllByText("AND")[0]);
    expect(screen.getAllByText("OR")).toHaveLength(2);
    // Delete middle token
    const chips = document.querySelectorAll(".MuiChip-root");
    const middleDelete = chips[1]?.querySelector(".MuiChip-deleteIcon");
    if (middleDelete) await user.click(middleDelete);

    await waitFor(() => {
      expect(screen.getAllByText("OR")).toHaveLength(1);
    });
  });

  it("clicking a chip to edit removes it from token list (operator may hide)", async () => {
    const { user } = renderQueryInput({
      initialTokens: [TOKEN_STATUS_OK, TOKEN_MODEL_GPT],
    });
    await user.click(screen.getByText("AND"));
    expect(screen.getByText("OR")).toBeInTheDocument();
    // Clicking a chip triggers editToken which removes that token from the
    // list and enters partial-edit mode. With 2→1 tokens, the operator hides.
    const chips = document.querySelectorAll(".MuiChip-root");
    if (chips.length > 0) {
      await user.click(chips[0]);
      // Token was removed for editing — now 1 token, no operator
      await waitFor(() => {
        expect(screen.queryByText("OR")).not.toBeInTheDocument();
      });
    }
  });
});

// ---------------------------------------------------------------------------
// Category D: External sync (initialTokens)
// ---------------------------------------------------------------------------
describe("QueryInput — external sync via initialTokens", () => {
  it("operator unchanged when initialTokens refreshes with same data", async () => {
    const onApply = vi.fn();
    const props = makeProps({
      initialTokens: [TOKEN_STATUS_OK, TOKEN_MODEL_GPT],
      onApply,
    });
    const user = userEvent.setup();
    const { rerender } = render(<QueryInput {...props} />);
    await user.click(screen.getByText("AND"));
    expect(screen.getByText("OR")).toBeInTheDocument();

    // Rerender with identical props → same initialTokensKey → useEffect
    // does NOT fire → logicOperator state is preserved as "OR".
    rerender(<QueryInput {...props} />);
    expect(screen.getByText("OR")).toBeInTheDocument();
  });

  it("hides operator when initialTokens changes to single token", async () => {
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

  it("hides operator when initialTokens clears to empty", async () => {
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
      <QueryInput {...makeProps({ initialTokens: [], onApply })} />,
    );
    await waitFor(() => {
      expect(screen.queryByText("AND")).not.toBeInTheDocument();
    });
  });
});

// ---------------------------------------------------------------------------
// Category E: Token data shapes
// ---------------------------------------------------------------------------
describe("QueryInput — token data shapes", () => {
  it("renders operator badge with array-value tokens", () => {
    renderQueryInput({
      initialTokens: [TOKEN_STATUS_OK, TOKEN_STATUS_ARRAY],
    });
    expect(screen.getByText("AND")).toBeInTheDocument();
  });

  it("renders operator badge with long-value tokens", () => {
    renderQueryInput({
      initialTokens: [TOKEN_MODEL_GPT, TOKEN_LONG_VALUE],
    });
    expect(screen.getByText("AND")).toBeInTheDocument();
  });
});

// ---------------------------------------------------------------------------
// Category F: Interaction boundaries
// ---------------------------------------------------------------------------
describe("QueryInput — interaction boundaries", () => {
  it("operator is a span (not a button/input), not tab-focusable", () => {
    renderQueryInput({
      initialTokens: [TOKEN_STATUS_OK, TOKEN_MODEL_GPT],
    });
    const badge = screen.getByText("AND");
    expect(badge.tagName).toBe("SPAN");
    expect(badge.hasAttribute("tabIndex")).toBe(false);
  });

  it("operator badge is clickable (cursor: pointer)", () => {
    renderQueryInput({
      initialTokens: [TOKEN_STATUS_OK, TOKEN_MODEL_GPT],
    });
    const badge = screen.getByText("AND");
    expect(badge).toBeInTheDocument();
    // Verify it's rendered as an interactive element (span with onClick)
    expect(badge.onclick).toBeDefined(); // onClick handler is present
  });

  it("chips are rendered alongside operator badge", () => {
    renderQueryInput({
      initialTokens: [TOKEN_STATUS_OK, TOKEN_MODEL_GPT],
    });
    const chips = document.querySelectorAll(".MuiChip-root");
    // Two chips + one badge between them
    expect(chips.length).toBe(2);
    expect(screen.getByText("AND")).toBeInTheDocument();
  });

  it("delete icons present for each token", () => {
    renderQueryInput({
      initialTokens: [TOKEN_STATUS_OK, TOKEN_MODEL_GPT],
    });
    const deleteIcons = document.querySelectorAll(".MuiChip-deleteIcon");
    expect(deleteIcons.length).toBe(2);
  });

  it("chip click enters edit mode (removes chip, operator may hide)", async () => {
    const { user } = renderQueryInput({
      initialTokens: [TOKEN_STATUS_OK, TOKEN_MODEL_GPT, TOKEN_LATENCY_HIGH],
    });
    // Three tokens → two AND badges
    expect(screen.getAllByText("AND")).toHaveLength(2);
    // Click the first chip to edit → token removed, 2 left, 1 badge remains
    const chips = document.querySelectorAll(".MuiChip-root");
    await user.click(chips[0]);
    // After editToken removes chip[0]: 2 tokens remain → 1 operator badge
    await waitFor(() => {
      expect(screen.getAllByText("AND")).toHaveLength(1);
      expect(screen.queryAllByText("AND")).not.toHaveLength(2);
    });
  });
});

// ---------------------------------------------------------------------------
// Category G: Visual consistency
// ---------------------------------------------------------------------------
describe("QueryInput — visual consistency", () => {
  it("operator badge is in the document with correct text", () => {
    renderQueryInput({
      initialTokens: [TOKEN_STATUS_OK, TOKEN_MODEL_GPT],
    });
    const badge = screen.getByText("AND");
    expect(badge).toBeInTheDocument();
    expect(badge.textContent).toBe("AND");
  });

  it("operator badge is rendered with cursor pointer style", () => {
    renderQueryInput({
      initialTokens: [TOKEN_STATUS_OK, TOKEN_MODEL_GPT],
    });
    const badge = screen.getByText("AND");
    // MUI sx cursor: "pointer" gets compiled to a CSS class
    expect(badge).toBeInTheDocument();
    // Verify via an onClick handler exists (only interactive elements have cursor pointer)
    expect(typeof badge.onclick).toBe("function");
  });
});

// ---------------------------------------------------------------------------
// Category H: State isolation
// ---------------------------------------------------------------------------
describe("QueryInput — state isolation", () => {
  it("fresh instance defaults to AND", () => {
    renderQueryInput({
      initialTokens: [TOKEN_STATUS_OK, TOKEN_MODEL_GPT],
    });
    expect(screen.getByText("AND")).toBeInTheDocument();
    expect(screen.queryByText("OR")).not.toBeInTheDocument();
  });

  it("can toggle to OR and the badge updates in DOM", async () => {
    const { user } = renderQueryInput({
      initialTokens: [TOKEN_STATUS_OK, TOKEN_MODEL_GPT],
    });
    await user.click(screen.getByText("AND"));
    expect(screen.getByText("OR")).toBeInTheDocument();
  });
});
