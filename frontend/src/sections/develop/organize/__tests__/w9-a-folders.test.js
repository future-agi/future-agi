// W9 A6 (W9-SCOPE §5): virtual folders: normalize, validate, tree counts,
// prefix rename, the cycle refusal and remove.
import { describe, expect, it } from "vitest";
import {
  CYCLE_SENTENCE,
  ancestorsOf,
  breadcrumbOf,
  folderTree,
  isUnder,
  moveFolderProblem,
  noFolderCount,
  parentOf,
  planFolderMove,
  planFolderRemove,
  removeFolderCopy,
  rewritePrefix,
  undoChanges,
} from "../folderTree";
import { folderProblem, normalizeFolder } from "../orgRules";

describe("one path", () => {
  it("normalizes slashes and spaces", () => {
    expect(normalizeFolder(" /support/billing/ ")).toBe("support/billing");
    expect(normalizeFolder("support / billing")).toBe("support/billing");
    expect(normalizeFolder("")).toBe("");
    expect(normalizeFolder(null)).toBe("");
  });

  it("validates segments and depth with the contract sentences", () => {
    expect(folderProblem("support/billing")).toBeNull();
    expect(folderProblem("")).toBeNull();
    expect(folderProblem("support//billing")).toBe(
      "A folder name can't be empty or contain “/”.",
    );
    expect(folderProblem("a/b/c/d/e")).toBeNull();
    expect(folderProblem("a/b/c/d/e/f")).toBe(
      "Folders go 5 levels deep at most.",
    );
    expect(folderProblem("x".repeat(65))).toBe(
      "A folder name can be 64 characters at most.",
    );
    expect(folderProblem(3)).toBe(
      "A folder must be a path like support/billing.",
    );
  });

  it("knows parents, ancestors and subtrees", () => {
    expect(parentOf("support/billing")).toBe("support");
    expect(parentOf("support")).toBe("");
    expect(ancestorsOf("a/b/c")).toEqual(["a", "a/b", "a/b/c"]);
    expect(isUnder("support/billing", "support")).toBe(true);
    expect(isUnder("support", "support")).toBe(true);
    expect(isUnder("supportx", "support")).toBe(false);
    expect(isUnder("anything", "")).toBe(true);
    expect(breadcrumbOf("support/billing").map((c) => c.label)).toEqual([
      "All datasets",
      "support",
      "billing",
    ]);
  });
});

describe("the tree", () => {
  const paths = ["support/billing", "support", "", "", "eval/tools", ""];

  it("counts own and subtree datasets per folder", () => {
    expect(folderTree(paths)).toEqual([
      { path: "eval", name: "eval", depth: 1, count: 0, total: 1 },
      { path: "eval/tools", name: "tools", depth: 2, count: 1, total: 1 },
      { path: "support", name: "support", depth: 1, count: 1, total: 2 },
      {
        path: "support/billing",
        name: "billing",
        depth: 2,
        count: 1,
        total: 1,
      },
    ]);
    expect(noFolderCount(paths)).toBe(3);
  });

  it("a folder exists only while it holds a dataset", () => {
    expect(folderTree(["", ""])).toEqual([]);
  });
});

describe("rename, move and remove", () => {
  const datasets = [
    { id: "gs", folder: "support/billing" },
    { id: "ts", folder: "support" },
    { id: "rp", folder: "" },
    { id: "x", folder: "supportx" },
  ];

  it("rewrites the prefix of every dataset in the subtree only", () => {
    expect(rewritePrefix("support/billing", "support", "help")).toBe(
      "help/billing",
    );
    expect(rewritePrefix("supportx", "support", "help")).toBe("supportx");
    const { changes, problem } = planFolderMove(datasets, "support", "help");
    expect(problem).toBeNull();
    expect(changes).toEqual([
      { id: "gs", from: "support/billing", to: "help/billing" },
      { id: "ts", from: "support", to: "help" },
    ]);
    expect(undoChanges(changes)).toEqual([
      { id: "gs", from: "help/billing", to: "support/billing" },
      { id: "ts", from: "help", to: "support" },
    ]);
  });

  it("moves a folder under another one", () => {
    const { changes } = planFolderMove(
      datasets,
      "support/billing",
      "finance/billing",
    );
    expect(changes).toEqual([
      { id: "gs", from: "support/billing", to: "finance/billing" },
    ]);
  });

  it("refuses a move into its own subfolder", () => {
    expect(moveFolderProblem("support", "support/billing/x")).toBe(
      CYCLE_SENTENCE,
    );
    expect(planFolderMove(datasets, "support", "support/inner")).toEqual({
      changes: [],
      problem: CYCLE_SENTENCE,
    });
  });

  it("refuses a move that would go deeper than 5 levels", () => {
    // support/billing would become a/b/c/d/e/billing (6 levels).
    expect(planFolderMove(datasets, "support", "a/b/c/d").problem).toBeNull();
    expect(planFolderMove(datasets, "support", "a/b/c/d/e").problem).toBe(
      "Folders go 5 levels deep at most.",
    );
  });

  it("removing a folder moves its datasets to its parent", () => {
    expect(planFolderRemove(datasets, "support/billing")).toEqual({
      changes: [{ id: "gs", from: "support/billing", to: "support" }],
      to: "support",
    });
    expect(removeFolderCopy(datasets, "support/billing")).toBe(
      "Its dataset moves to support.",
    );
    expect(removeFolderCopy(datasets, "support")).toBe(
      "Its dataset moves out of any folder. Its subfolders move up one level with their dataset.",
    );
    expect(
      removeFolderCopy(
        [...datasets, { id: "y", folder: "support/billing" }],
        "support/billing",
      ),
    ).toBe("Its 2 datasets move to support.");
    expect(planFolderRemove(datasets, "support").changes).toEqual([
      { id: "gs", from: "support/billing", to: "billing" },
      { id: "ts", from: "support", to: "" },
    ]);
  });
});
