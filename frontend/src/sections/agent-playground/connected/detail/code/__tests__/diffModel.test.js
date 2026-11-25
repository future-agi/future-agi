import { createHash } from "node:crypto";
import { describe, expect, it } from "vitest";
import {
  gapRows,
  githubCompareUrl,
  githubFileAnchor,
  hunkRows,
  lineCount,
  olderFirst,
  planBatches,
  segments,
  splitRows,
} from "../diffModel";

const hunk = {
  old_start: 7,
  old_lines: 7,
  new_start: 7,
  new_lines: 8,
  lines: [
    " line 7",
    " line 8",
    " line 9",
    "-line 10",
    "+line ten",
    "+line ten and a half",
    " line 11",
    " line 12",
    " line 13",
  ],
};

describe("hunkRows", () => {
  it("numbers each side from the hunk's starts", () => {
    const rows = hunkRows(hunk);
    expect(rows[0]).toEqual({
      type: "context",
      oldNo: 7,
      newNo: 7,
      text: "line 7",
    });
    expect(rows[3]).toEqual({
      type: "del",
      oldNo: 10,
      newNo: null,
      text: "line 10",
    });
    expect(rows[5]).toEqual({
      type: "add",
      oldNo: null,
      newNo: 11,
      text: "line ten and a half",
    });
    expect(rows[8]).toEqual({
      type: "context",
      oldNo: 13,
      newNo: 14,
      text: "line 13",
    });
  });

  it("starts a side with no lines at line one", () => {
    const rows = hunkRows({
      old_start: 1,
      old_lines: 1,
      new_start: 0,
      new_lines: 0,
      lines: ["-x"],
    });
    expect(rows).toEqual([{ type: "del", oldNo: 1, newNo: null, text: "x" }]);
  });
});

describe("segments", () => {
  it("puts the unchanged lines before, between and after hunks in gaps", () => {
    const second = {
      old_start: 30,
      old_lines: 3,
      new_start: 31,
      new_lines: 3,
      lines: [" a", " b", " c"],
    };
    const parts = segments([hunk, second], 40);
    expect(parts.map((part) => part.kind)).toEqual([
      "gap",
      "hunk",
      "gap",
      "hunk",
      "gap",
    ]);
    expect(parts[0]).toMatchObject({ from: 1, to: 6, offset: 0 });
    expect(parts[2]).toMatchObject({ from: 15, to: 30, offset: -1 });
    expect(parts[4]).toMatchObject({ from: 34, to: 40, offset: -1 });
  });

  it("leaves the end open until the file's length is known", () => {
    const parts = segments([hunk]);
    expect(parts.at(-1)).toMatchObject({ kind: "gap", from: 15, to: null });
  });

  it("has no gaps for a file with no hunks", () => {
    expect(segments([], 10)).toEqual([]);
  });
});

describe("gapRows", () => {
  it("reads the unchanged lines with both sides' numbers", () => {
    const content = Array.from({ length: 20 }, (_, i) => `l${i + 1}`).join(
      "\n",
    );
    const rows = gapRows({ from: 15, to: 16, offset: -1 }, content);
    expect(rows).toEqual([
      { type: "context", oldNo: 14, newNo: 15, text: "l15" },
      { type: "context", oldNo: 15, newNo: 16, text: "l16" },
    ]);
    expect(gapRows({ from: 19, to: null, offset: 0 }, content)).toHaveLength(2);
  });

  it("counts lines without the final newline", () => {
    expect(lineCount("a\nb\n")).toBe(2);
    expect(lineCount("a\nb")).toBe(2);
    expect(lineCount("")).toBe(0);
  });
});

describe("splitRows", () => {
  it("pairs removed lines with the added lines that replace them", () => {
    const pairs = splitRows(hunkRows(hunk));
    expect(pairs[3].left.text).toBe("line 10");
    expect(pairs[3].right.text).toBe("line ten");
    expect(pairs[4].left).toBeNull();
    expect(pairs[4].right.text).toBe("line ten and a half");
    expect(pairs[5].left).toBe(pairs[5].right);
  });
});

describe("planBatches", () => {
  it("groups the files that load by themselves, skipping big and binary ones", () => {
    const files = [
      ...Array.from({ length: 30 }, (_, i) => ({
        path: `f${i}.py`,
        kind: "text",
        collapsed: false,
      })),
      { path: "big.py", kind: "text", collapsed: true },
      { path: "model.bin", kind: "binary", collapsed: false },
    ];
    const { batches, batchOf } = planBatches(files);
    expect(batches.map((batch) => batch.length)).toEqual([25, 5]);
    expect(batchOf["f26.py"]).toBe(1);
    expect(batchOf["big.py"]).toBeUndefined();
    expect(batchOf["model.bin"]).toBeUndefined();
  });
});

describe("olderFirst", () => {
  it("always puts the lower version number on the left", () => {
    const versions = [
      { id: "a", number: 2 },
      { id: "b", number: 5 },
    ];
    expect(olderFirst(versions, "b", "a")).toEqual(["a", "b"]);
    expect(olderFirst(versions, "a", "b")).toEqual(["a", "b"]);
  });
});

describe("githubCompareUrl", () => {
  const at = (commit, repository = "Acme/Agent") => ({
    source: { kind: "github", repository, commit_sha: commit },
  });

  it("links two commits of the same repository", () => {
    expect(githubCompareUrl(at("aaa"), at("bbb", "acme/agent"))).toBe(
      "https://github.com/acme/agent/compare/aaa...bbb",
    );
  });

  it("has no link for the same commit, another repository or an upload", () => {
    expect(githubCompareUrl(at("aaa"), at("aaa"))).toBeNull();
    expect(githubCompareUrl(at("aaa"), at("bbb", "other/repo"))).toBeNull();
    expect(
      githubCompareUrl({ source: { kind: "archive" } }, at("bbb")),
    ).toBeNull();
  });

  it("anchors a file the way GitHub's compare page does", async () => {
    const expected = createHash("sha256")
      .update("agent/tools.py")
      .digest("hex");
    expect(await githubFileAnchor("agent/tools.py")).toBe(`#diff-${expected}`);
  });
});
