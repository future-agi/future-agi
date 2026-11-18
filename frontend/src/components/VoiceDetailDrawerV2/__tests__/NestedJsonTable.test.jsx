import React from "react";
import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import { ThemeProvider, createTheme } from "@mui/material/styles";
import NestedJsonTable, {
  flattenLeaves,
  FlatMatchRow,
} from "../NestedJsonTable";

const renderWithTheme = (ui) =>
  render(<ThemeProvider theme={createTheme()}>{ui}</ThemeProvider>);

// The Highlight component wraps matched tokens in their own <span>, so a
// path like `attributes.llm.model` spans multiple nodes. Match on the
// combined textContent of the outer Typography element (which renders
// as <p> in tree mode and <span> in flat mode) to find the logical row.
const hasRowText = (expected) => (_, el) => {
  if (!el) return false;
  const tag = el.tagName;
  if (tag !== "P" && tag !== "SPAN") return false;
  return el.textContent === expected;
};

describe("flattenLeaves", () => {
  it("emits one leaf per primitive with dotted path for nested objects", () => {
    const tree = { a: { b: "hello", c: 7 }, d: true };
    const leaves = flattenLeaves(tree);
    expect(leaves.map((l) => [l.path, l.value])).toEqual([
      ["a.b", "hello"],
      ["a.c", 7],
      ["d", true],
    ]);
  });

  it("uses [i] bracket notation for array indices", () => {
    const tree = { items: [{ role: "user" }, { role: "assistant" }] };
    const leaves = flattenLeaves(tree);
    expect(leaves.map((l) => l.path)).toEqual([
      "items[0].role",
      "items[1].role",
    ]);
  });

  it("treats null and primitives at the root as a single leaf", () => {
    expect(flattenLeaves(null)).toEqual([
      expect.objectContaining({ path: "", value: null }),
    ]);
    expect(flattenLeaves(42)).toEqual([
      expect.objectContaining({ path: "", value: 42 }),
    ]);
  });

  it("emits an empty container as its own leaf so its path is searchable", () => {
    const tree = { empty_list: [], empty_obj: {} };
    const leaves = flattenLeaves(tree);
    expect(leaves.map((l) => l.path)).toEqual(["empty_list", "empty_obj"]);
  });

  it("precomputes lowercased path and string-value for filtering", () => {
    const leaves = flattenLeaves({ Model: "GPT-4" });
    expect(leaves[0]).toMatchObject({
      pathLower: "model",
      valueLower: "gpt-4",
    });
  });

  it("strips camelCase aliases via canonicalEntries so each field appears once", () => {
    const tree = { user_id: "abc", userId: "abc" };
    const leaves = flattenLeaves(tree);
    expect(leaves.map((l) => l.path)).toEqual(["user_id"]);
  });
});

describe("FlatMatchRow", () => {
  it("renders the dotted path and the quoted string value with token highlight", () => {
    renderWithTheme(
      <FlatMatchRow
        path="attributes.llm.model"
        value="gpt-4"
        tokens={["gpt"]}
      />,
    );
    expect(
      screen.getByText(hasRowText("attributes.llm.model")),
    ).toBeInTheDocument();
    // The matched token is wrapped in its own highlight span.
    expect(screen.getByText("gpt")).toBeInTheDocument();
  });

  it("falls back to 'value' when the leaf has no path (top-level primitive)", () => {
    // Mirrors the synthetic `value` key the tree mode uses for top-level
    // primitives (see `NestedJsonTable.entries` memo) so the two modes
    // render the same label.
    renderWithTheme(<FlatMatchRow path="" value={42} tokens={["42"]} />);
    expect(screen.getByText("value")).toBeInTheDocument();
    // `42` is wrapped in a highlight span inside the value cell.
    expect(screen.getByText("42")).toBeInTheDocument();
  });
});

describe("NestedJsonTable flat-match mode", () => {
  const data = {
    attributes: {
      llm: { model: "gpt-4", temperature: 0.7 },
      user_id: "abc",
    },
    raw_log: "hello world",
  };

  it("renders the nested tree when searchQuery is empty", () => {
    renderWithTheme(<NestedJsonTable data={data} searchQuery="" />);
    expect(screen.getByText("attributes")).toBeInTheDocument();
    expect(screen.getByText("raw_log")).toBeInTheDocument();
    // Flat dotted paths should NOT appear in tree mode.
    expect(screen.queryByText(hasRowText("attributes.llm.model"))).toBeNull();
  });

  it("renders a flat list of matching leaves when searchQuery is non-empty", () => {
    renderWithTheme(<NestedJsonTable data={data} searchQuery="llm" />);
    expect(
      screen.getByText(hasRowText("attributes.llm.model")),
    ).toBeInTheDocument();
    expect(
      screen.getByText(hasRowText("attributes.llm.temperature")),
    ).toBeInTheDocument();
    expect(screen.queryByText(hasRowText("attributes.user_id"))).toBeNull();
    expect(screen.queryByText(hasRowText("raw_log"))).toBeNull();
  });

  it("matches against path OR value; every token must appear somewhere", () => {
    const { unmount: unmountGpt } = renderWithTheme(
      <NestedJsonTable data={data} searchQuery="gpt" />,
    );
    expect(
      screen.getByText(hasRowText("attributes.llm.model")),
    ).toBeInTheDocument();
    expect(
      screen.queryByText(hasRowText("attributes.llm.temperature")),
    ).toBeNull();
    unmountGpt();

    renderWithTheme(<NestedJsonTable data={data} searchQuery="llm gpt" />);
    expect(
      screen.getByText(hasRowText("attributes.llm.model")),
    ).toBeInTheDocument();
    expect(
      screen.queryByText(hasRowText("attributes.llm.temperature")),
    ).toBeNull();
  });

  it("shows a 'no matches' row when the flat filter returns nothing", () => {
    renderWithTheme(
      <NestedJsonTable data={data} searchQuery="nonexistent-token-xyz" />,
    );
    expect(screen.getByText(/No matches for/i)).toBeInTheDocument();
  });

  it("caps the flat list at 500 rows with an overflow footer", () => {
    const big = {};
    for (let i = 0; i < 600; i += 1) big[`key_${i}`] = "hit";
    renderWithTheme(<NestedJsonTable data={big} searchQuery="hit" />);
    expect(
      screen.getByText(/100 more matches, refine your query/),
    ).toBeInTheDocument();
  });
});

describe("flattenLeaves word pre-computation", () => {
  it("emits an alphanumeric word list for the fuzzy fallback", () => {
    const leaves = flattenLeaves({
      attributes: { llm: { model: "gpt-4" } },
    });
    expect(leaves[0].words).toEqual(["attributes", "llm", "model", "gpt", "4"]);
  });
});

describe("NestedJsonTable fuzzy search", () => {
  it("still matches exact substrings (regression check for prior behaviour)", () => {
    renderWithTheme(
      <NestedJsonTable
        data={{ station_id: "central" }}
        searchQuery="station"
      />,
    );
    expect(screen.getByText(hasRowText("station_id"))).toBeInTheDocument();
  });

  it("surfaces 'station' when the user types 'sation' (single-char typo)", () => {
    // `sation` is not a substring of `station_id`, but Levenshtein
    // distance to the word `station` is 1 and threshold for a 6-char
    // token is 1.
    renderWithTheme(
      <NestedJsonTable data={{ station_id: "central" }} searchQuery="sation" />,
    );
    expect(screen.getByText(hasRowText("station_id"))).toBeInTheDocument();
  });

  it("does not fuzzy-match 3-char tokens (threshold is 0 there)", () => {
    // `abd` vs `abc` is edit distance 1, but ≤ 3-char tokens are
    // exact-only to keep the false-positive rate sane.
    renderWithTheme(<NestedJsonTable data={{ abc: "x" }} searchQuery="abd" />);
    expect(screen.getByText(/No matches for/i)).toBeInTheDocument();
  });

  it("requires every token to match on the SAME leaf (exact or fuzzy)", () => {
    // `station` matches leaf 1 exactly; `modesl` (fuzzy for `models`)
    // matches leaf 2. No single leaf satisfies both → no matches.
    renderWithTheme(
      <NestedJsonTable
        data={{ station_id: "central", models: { gpt: "x" } }}
        searchQuery="station modesl"
      />,
    );
    expect(screen.getByText(/No matches for/i)).toBeInTheDocument();
  });
});
