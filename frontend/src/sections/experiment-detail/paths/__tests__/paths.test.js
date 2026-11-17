// Paths screen helpers: §C10 (no digit literals in the sentence
// templates), the default column set and the graph decorations.
import fs from "node:fs";
import path from "node:path";
import process from "node:process";
import { describe, expect, it } from "vitest";
import { edgeLabelOf, pathsGraphData } from "../PathsGraph";
import { pathColumns } from "../PathsTable";
import { sentenceFor } from "../PathSentence";

const SRC = path.join(process.cwd(), "src/sections");
const read = (p) => fs.readFileSync(path.join(SRC, p), "utf8");

describe("Paths sentence (§C10)", () => {
  it("has no digit literals in its string templates", () => {
    const files = [
      read("common/Trajectory/pathModel.js").split(
        "// ---------------------------------------------------------------- sentence",
      )[1] ?? "",
      read("experiment-detail/paths/PathSentence.jsx"),
    ];
    files.forEach((src) => {
      const code = src
        .replace(/\/\*[\s\S]*?\*\//g, "")
        .replace(/^\s*\/\/.*$/gm, "");
      const templates = code.match(/`[^`]*`/g) ?? [];
      templates.forEach((t) => {
        const digits = t.replace(/\$\{[^}]*\}/g, "").match(/\d+/g) ?? [];
        expect(digits.filter((d) => !["0", "1", "100"].includes(d))).toEqual(
          [],
        );
      });
    });
  });

  it("says when every try takes the same path", () => {
    const agg = {
      attempts: 2,
      node_source: "milestones",
      paths: [
        {
          id: "a",
          steps: [{ name: "a", repeat: 1 }],
          attempts: 2,
          failed: 0,
          share: 1,
          fail_share: 0,
        },
      ],
    };
    expect(sentenceFor(agg, { label: "#X" }).text).toBe(
      "#X: Every try takes the same path: a.",
    );
  });
});

describe("Paths table columns", () => {
  const base = {
    labels: { a: "#2B", b: "#3A" },
    colors: { a: "#00f", b: "#f0f" },
  };
  it("compare default = share per run, tries and fail of the run on screen", () => {
    expect(pathColumns({ ...base, compare: true }).map((c) => c.head)).toEqual([
      "Share #2B",
      "Share #3A",
      "Tries #2B",
      "Fail #2B",
    ]);
  });
  it("Columns adds Δ, the compared run's fail and examples", () => {
    expect(
      pathColumns({
        ...base,
        compare: true,
        optional: ["delta", "failEach", "examples"],
      }).map((c) => c.id),
    ).toEqual([
      "shareA",
      "shareB",
      "tries",
      "fail",
      "delta",
      "failB",
      "examples",
    ]);
    expect(pathColumns({ ...base, compare: false }).map((c) => c.id)).toEqual([
      "share",
      "tries",
      "fail",
    ]);
  });
});

describe("Paths graph data", () => {
  it("labels edges `18 · all fail` and heats failing nodes", () => {
    expect(edgeLabelOf({ trace_count: 18, error_count: 18 })).toBe(
      "18 · all fail",
    );
    expect(edgeLabelOf({ trace_count: 12, error_count: 3 })).toBe(
      "12 · 25% fail",
    );
    expect(edgeLabelOf({ trace_count: 40, error_count: 0 })).toBe("40");
    const node = (id, t, e) => ({
      id,
      name: id,
      type: "tool",
      span_count: t,
      trace_count: t,
      error_count: e,
    });
    const data = pathsGraphData(
      {
        graph: {
          nodes: [
            node("__start__", 2, 1),
            node("x", 2, 1),
            node("__end__", 2, 1),
          ],
          edges: [
            {
              source: "__start__",
              target: "x",
              transition_count: 2,
              trace_count: 2,
              error_count: 1,
              is_self_loop: false,
            },
            {
              source: "x",
              target: "x",
              transition_count: 1,
              trace_count: 1,
              error_count: 1,
              is_self_loop: true,
            },
          ],
          path_edges: [],
        },
      },
      { edgeLabels: true },
    );
    expect(data.nodes.find((n) => n.id === "x")).toMatchObject({
      _heat: 0.5,
      _loops: 2,
    });
    expect(data.edges).toHaveLength(1);
    expect(data.edges[0]._label).toBe("2 · 50% fail");
  });
});
