import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import {
  cleanup,
  render,
  screen,
  userEvent,
  waitFor,
  within,
} from "src/utils/test-utils";
import SpanDetailPane from "../SpanDetailPane";

vi.mock("src/components/iconify", () => ({
  default: ({ icon, ...props }) => (
    <span data-testid="iconify" data-icon={icon} {...props} />
  ),
}));

// Keep the actual panes, search controls and hook; isolate preview formatting
// so each pane has exactly two matches in a stable text node.
vi.mock("../SmartPreview", () => ({
  default: ({ input }) => <p>{input}</p>,
}));

const entries = {
  a: {
    observation_span: {
      id: "span-a",
      trace: "trace-a",
      name: "Current span",
      input: "alpha alpha",
    },
  },
  b: {
    observation_span: {
      id: "span-b",
      trace: "trace-b",
      name: "Comparison span",
      input: "bravo bravo",
    },
  },
};

function renderComparedPanes() {
  const client = new QueryClient();
  const panes = (showComparison) => (
    <QueryClientProvider client={client}>
      <section data-testid="pane-a">
        <SpanDetailPane entry={entries.a} projectId="project-1" readOnly />
      </section>
      {showComparison && (
        <section data-testid="pane-b">
          <SpanDetailPane entry={entries.b} projectId="project-1" readOnly />
        </section>
      )}
    </QueryClientProvider>
  );
  const view = render(panes(true));
  return {
    a: screen.getByTestId("pane-a"),
    b: screen.getByTestId("pane-b"),
    removeComparison: () => view.rerender(panes(false)),
  };
}

function rangesIn(pane, name = "trace-search") {
  return [...(CSS.highlights.get(name) || [])].filter((range) =>
    pane.contains(range.startContainer),
  );
}

async function search(user, pane, query) {
  await user.type(within(pane).getByPlaceholderText("Search"), query);
  await waitFor(() => {
    expect(within(pane).getByText("1 of 2")).toBeInTheDocument();
    expect(rangesIn(pane)).toHaveLength(2);
    expect(rangesIn(pane, "trace-search-active")).toHaveLength(1);
  });
}

function snapshotRanges(pane) {
  return {
    matches: rangesIn(pane),
    active: rangesIn(pane, "trace-search-active"),
  };
}

function expectRangesUnchanged(pane, saved) {
  expect(rangesIn(pane)).toEqual(saved.matches);
  expect(rangesIn(pane, "trace-search-active")).toEqual(saved.active);
  expect(within(pane).getByText("1 of 2")).toBeInTheDocument();
}

beforeEach(() => {
  // Highlight is setlike and its registry is maplike. jsdom does not paint
  // highlights, but these mocks preserve range identity and shared ownership.
  vi.stubGlobal("CSS", { ...globalThis.CSS, highlights: new Map() });
  vi.stubGlobal("Highlight", class Highlight extends Set {});
});

afterEach(() => {
  // Unmount while the registry still exists so effect cleanup is exercised.
  cleanup();
  vi.unstubAllGlobals();
  document.getElementById("trace-search-highlight-styles")?.remove();
});

describe("SpanDetailPane independent comparison search highlights", () => {
  it("keeps A's match and active ranges when searching in B", async () => {
    const user = userEvent.setup();
    const { a, b } = renderComparedPanes();
    await search(user, a, "alpha");
    const saved = snapshotRanges(a);

    await search(user, b, "bravo");

    expectRangesUnchanged(a, saved);
    expect(CSS.highlights.get("trace-search").size).toBe(4);
    expect(CSS.highlights.get("trace-search-active").size).toBe(2);
  });

  it("keeps A's ranges when the comparison pane unmounts", async () => {
    const user = userEvent.setup();
    const { a, b, removeComparison } = renderComparedPanes();
    // Search A last so this tests cleanup independently of B's search apply.
    await search(user, b, "bravo");
    await search(user, a, "alpha");
    const saved = snapshotRanges(a);

    removeComparison();

    expectRangesUnchanged(a, saved);
    expect([...CSS.highlights.get("trace-search")]).toEqual(saved.matches);
    expect([...CSS.highlights.get("trace-search-active")]).toEqual(saved.active);
  });

  it("keeps A's active range when navigating B's matches", async () => {
    const user = userEvent.setup();
    const { a, b } = renderComparedPanes();
    await search(user, b, "bravo");
    await search(user, a, "alpha");
    const saved = snapshotRanges(a);

    await user.click(within(b).getByRole("button", { name: "Next match" }));

    expect(within(b).getByText("2 of 2")).toBeInTheDocument();
    expectRangesUnchanged(a, saved);
    expect(rangesIn(b, "trace-search-active")).toEqual([rangesIn(b)[1]]);
    expect(CSS.highlights.get("trace-search-active").size).toBe(2);
  });

  it("clears only B's ranges when clearing B's query", async () => {
    const user = userEvent.setup();
    const { a, b } = renderComparedPanes();
    await search(user, b, "bravo");
    await search(user, a, "alpha");
    const saved = snapshotRanges(a);

    await user.clear(within(b).getByPlaceholderText("Search"));

    expectRangesUnchanged(a, saved);
    expect(rangesIn(b)).toHaveLength(0);
    expect(rangesIn(b, "trace-search-active")).toHaveLength(0);
    expect([...CSS.highlights.get("trace-search")]).toEqual(saved.matches);
    expect([...CSS.highlights.get("trace-search-active")]).toEqual(saved.active);
  });
});
