import { describe, expect, it } from "vitest";
import { resolveColumns } from "src/sections/common/ux/oneview/columnLayout";
import {
  baselineMetric,
  bestRunTooltip,
  BASELINE_HELP,
  DATASET_LIST_COLUMNS,
  deleteConfirmCopy,
  highlightParts,
  runningTooltip,
  scoreNow,
  scoreRunPhrase,
  sortingFromParam,
  sortParamFrom,
} from "../datasetList";
import {
  cleanColumns,
  validateDatasetName,
} from "../AddDatasetDrawer/datasetName";
import { IMPORT_OPTIONS, ROW_IMPORT_OPTIONS } from "../importOptions";

const DS = {
  id: "d1",
  name: "Support agent - golden set",
  numberOfExperiments: 8,
  numberOfDatapoints: 248,
  latest_version: { number: 6 },
  baseline: {
    run_id: "a",
    label: "#1A",
    display: "79.4%",
    metric_name: "Correctness",
  },
  best_run: {
    run_id: "b",
    label: "#2B",
    display: "86.6%",
    significant: true,
    n_shared: 248,
  },
};

describe("dataset list helpers", () => {
  it("round-trips sort params", () => {
    expect(sortingFromParam("updated_at:desc")).toEqual([
      { id: "updatedAt", desc: true },
    ]);
    expect(sortingFromParam("number_of_experiments:asc")).toEqual([
      { id: "numberOfExperiments", desc: false },
    ]);
    expect(sortingFromParam("nope:asc")).toEqual([]);
    expect(sortParamFrom([{ id: "baseline", desc: true }])).toBe(
      "score_now:desc",
    );
    expect(sortParamFrom([])).toBe("none");
  });

  it("highlights case-insensitively", () => {
    expect(highlightParts("Refund policy QA", "refund")).toEqual([
      { text: "Refund", match: true },
      { text: " policy QA", match: false },
    ]);
    expect(highlightParts("abc", "")).toEqual([{ text: "abc", match: false }]);
    expect(highlightParts("aXa", "a").filter((p) => p.match)).toHaveLength(2);
  });

  it("names the metric and run under Score now (J1 numbers)", () => {
    expect(scoreNow(DS)).toEqual({
      display: "79.4%",
      run: "#1A",
      metric: "Correctness",
      pinned: true,
    });
    expect(scoreNow({ ...DS, baseline: null }).run).toBe("#2B");
    expect(scoreNow({})).toBeNull();
  });

  it("says baseline / best run in plain words and spells the id out", () => {
    expect(scoreRunPhrase(scoreNow(DS))).toEqual({
      word: "baseline",
      term: "baseline",
      extra:
        "Here: #1A is run A of experiment 1, the run new experiments are compared to.",
    });
    expect(scoreRunPhrase(scoreNow({ ...DS, baseline: null }))).toEqual({
      word: "best run",
      term: "run",
      extra: "Here: #2B is run B of experiment 2, the best score so far.",
    });
    expect(scoreRunPhrase({ display: "1", run: "", pinned: false }).extra).toBe(
      "Here: the best score so far.",
    );
    expect(scoreRunPhrase(null)).toBeNull();
  });

  it("calls a task suite's headline Success rate (F3)", () => {
    const ts = {
      baseline: {
        label: "#2B",
        display: "65.6%",
        metric_key: "eval:task_success",
        metric_name: "Task success",
      },
    };
    expect(scoreNow(ts)).toMatchObject({
      display: "65.6%",
      run: "#2B",
      metric: "Success rate",
    });
  });

  it("writes the best-run tooltip with the baseline and noise", () => {
    expect(bestRunTooltip(DS, "+7.2 pts")).toBe(
      "Best run #2B: 86.6% Correctness (+7.2 pts vs baseline #1A, 79.4%)",
    );
    expect(
      bestRunTooltip(
        { ...DS, best_run: { ...DS.best_run, significant: false } },
        "+1.0 pts",
      ),
    ).toContain("within noise (n=248)");
  });

  it("explains a running experiment with row counts", () => {
    const t = runningTooltip({ ordinal: 7, progress: 0.21 }, 248);
    expect(t).toBe(
      "Experiment #7 · 52 of 248 rows done (21%). Click to open it.",
    );
  });

  it("names deletes with counts and asks to type only when experiments use it", () => {
    const one = deleteConfirmCopy([DS]);
    expect(one.title).toBe('Delete "Support agent - golden set"?');
    expect(one.body).toBe(
      "This also removes 8 experiments and 6 versions. You can undo for 10 seconds.",
    );
    expect(one.typedConfirm).toBe(DS.name);
    const two = deleteConfirmCopy([
      DS,
      { name: "Empty", numberOfExperiments: 0 },
    ]);
    expect(two.confirmLabel).toBe("Delete 2");
    expect(two.typedConfirm).toBeUndefined();
  });
});

describe("dataset list default layout (one view, A1-01/A1-07)", () => {
  const resolved = resolveColumns(DATASET_LIST_COLUMNS, {});
  const visible = resolved.filter((c) => !c.hidden).map((c) => c.id);
  const hidden = resolved.filter((c) => c.hidden).map((c) => c.id);

  it("opens on the columns that answer the list's question", () => {
    expect(visible).toEqual([
      "name",
      "numberOfDatapoints",
      "lastExperiment",
      "baseline",
      "bestRun",
      "updatedAt",
    ]);
  });

  it("keeps the rest one click away (`6 hidden`), none dropped", () => {
    expect(hidden).toEqual([
      "version",
      "numberOfExperiments",
      "createdAt",
      "owner",
      "numberOfOptimisations",
      "source",
    ]);
  });

  it("freezes and locks Name", () => {
    const name = resolved.find((c) => c.id === "name");
    expect(name.locked).toBe(true);
    expect(name.pinned).toBe("left");
  });

  it("retires Score now by folding it into Baseline", () => {
    expect(resolved.some((c) => c.id === "scoreNow")).toBe(false);
    const base = DATASET_LIST_COLUMNS.find((c) => c.id === "baseline");
    expect(base.description).toBe(BASELINE_HELP);
    expect(BASELINE_HELP).toBe(
      "The pinned baseline's score on this dataset. The metric is named under it.",
    );
    expect(
      baselineMetric({
        baseline: { metric_key: "eval:task_success", metric_name: "x" },
      }),
    ).toBe("Success rate");
    expect(baselineMetric({ baseline: { metric_name: "Faithfulness" } })).toBe(
      "Faithfulness",
    );
    expect(baselineMetric({ baseline: null })).toBeNull();
    // Old `sort=score_now` links still sort by the baseline's score.
    expect(sortingFromParam("score_now:desc")).toEqual([
      { id: "baseline", desc: true },
    ]);
    expect(sortParamFrom([{ id: "baseline", desc: false }])).toBe(
      "score_now:asc",
    );
  });

  it("shows a hidden column at its catalog place", () => {
    const shown = resolveColumns(DATASET_LIST_COLUMNS, {
      visible: { version: true },
    })
      .filter((c) => !c.hidden)
      .map((c) => c.id);
    expect(shown.slice(0, 4)).toEqual([
      "name",
      "numberOfDatapoints",
      "version",
      "lastExperiment",
    ]);
  });
});

describe("dataset name validation", () => {
  const existing = [{ id: "x", name: "Refund policy QA" }];
  it("flags a clash with a link target", () => {
    const r = validateDatasetName("refund policy qa ", existing);
    expect(r.ok).toBe(false);
    expect(r.error).toBe('A dataset named "Refund policy QA" already exists.');
    expect(r.clash.id).toBe("x");
  });
  it("shows a counter from 60 chars and rejects over 80", () => {
    expect(validateDatasetName("a".repeat(60), []).counter).toBe("60/80");
    expect(validateDatasetName("a".repeat(81), []).ok).toBe(false);
    expect(validateDatasetName("Fine", []).ok).toBe(true);
  });
  it("cleans columns", () => {
    expect(
      cleanColumns([{ name: " input " }, { name: "INPUT" }, { name: "" }]),
    ).toEqual([{ name: "input" }]);
  });
});

describe("import options", () => {
  it("never repeats a subtitle and shares order with the row drawer", () => {
    const subs = IMPORT_OPTIONS.map((o) => o.subTitle);
    expect(new Set(subs).size).toBe(subs.length);
    const rowIds = ROW_IMPORT_OPTIONS.map((o) => o.id).filter(
      (id) => id !== "empty-row",
    );
    expect(rowIds).toEqual(
      IMPORT_OPTIONS.filter((o) => !o.datasetOnly).map((o) => o.id),
    );
  });
});

import {
  fileProblem,
  guessRole,
  importBlockReason,
  parseImport,
  SAMPLE_CSV,
  splitCsvLine,
} from "../AddDatasetDrawer/importFile";

describe("file import parse (DL#1, DL#2)", () => {
  it("rejects wrong types and big files with a next step", () => {
    expect(fileProblem({ name: "a.xlsx", size: 10 })).toBe(
      ".xlsx isn't supported. Use CSV, JSON or JSONL.",
    );
    expect(fileProblem({ name: "a.csv", size: 31 * 1024 * 1024 })).toBe(
      "This file is 31.0 MB. The limit is 25 MB. Split it into files under 25 MB.",
    );
    expect(fileProblem({ name: "a.jsonl", size: 10 })).toBeNull();
  });
  it("parses quoted CSV and reports bad lines by number", () => {
    expect(splitCsvLine('a,"b, c","say ""hi"""')).toEqual([
      "a",
      "b, c",
      'say "hi"',
    ]);
    const p = parseImport("question,answer\nhi,there\nbad\nq2,a2\n", "csv");
    expect(p.columns).toEqual(["question", "answer"]);
    expect(p.total).toBe(2);
    expect(p.errors).toEqual([
      { line: 3, message: "Expected 2 values, found 1" },
    ]);
  });
  it("parses JSONL with nested values and guesses roles", () => {
    const p = parseImport('{"input":"x","meta":{"a":1}}\nnope\n', "jsonl");
    expect(p.rows[0]).toEqual(["x", '{"a":1}']);
    expect(p.errors[0].line).toBe(2);
    expect(
      ["question", "expected_answer", "id", "lang"].map(guessRole),
    ).toEqual(["input", "expected", "ignore", "metadata"]);
  });
});

describe("upload modal helpers", () => {
  it("ships a sample CSV the importer maps by itself", () => {
    const p = parseImport(SAMPLE_CSV, "csv");
    expect(p.errors).toEqual([]);
    expect(p.total).toBe(3);
    expect(p.columns).toEqual([
      "customer_message",
      "expected_reply",
      "intent",
      "language",
    ]);
    expect(p.columns.map(guessRole)).toEqual([
      "input",
      "expected",
      "metadata",
      "metadata",
    ]);
  });

  it("says why Import is off, in the order you fix it", () => {
    const ok = { name: "Refunds", hasFile: true, parsed: {}, keptColumns: 2 };
    expect(importBlockReason({ ...ok, hasFile: false })).toBe(
      "Choose a file first",
    );
    expect(importBlockReason({ ...ok, problem: "Too big" })).toBe(
      "Fix the file problem shown above first",
    );
    expect(importBlockReason({ ...ok, parsed: null })).toBe(
      "Reading the file…",
    );
    expect(importBlockReason({ ...ok, name: "  " })).toBe(
      "Name the dataset first",
    );
    expect(importBlockReason({ ...ok, keptColumns: 0 })).toMatch(
      /^Keep at least one column/,
    );
    expect(importBlockReason({ ...ok, busy: true })).toBe(
      "Creating the dataset…",
    );
    expect(importBlockReason(ok)).toBeNull();
  });
});

// IT-R1 G2 P7: one line under each name saying what the dataset tests.
import { datasetKindLine } from "../datasetList";

describe("datasetKindLine", () => {
  const col = (name, data_type) => ({ name, data_type });
  it("names a task suite, a voice set and production sessions", () => {
    expect(
      datasetKindLine({ kind: "task_suite", number_of_datapoints: 30 }),
    ).toBe("Task suite · 30 tasks with end-state checks");
    expect(
      datasetKindLine({ kind: "scenarios", number_of_datapoints: 24 }),
    ).toBe("Voice · 24 simulated callers");
    expect(
      datasetKindLine({ kind: "conversations", number_of_datapoints: 18 }),
    ).toBe("Sessions · 18 production conversations");
  });
  it("derives rows datasets from their columns", () => {
    const row = (n) => ({ kind: "rows", number_of_datapoints: n });
    expect(
      datasetKindLine(row(40), [
        col("conversation", "json"),
        col("screenshot", "image"),
        col("call_audio", "audio"),
        col("contract", "document"),
      ]),
    ).toBe("Multimodal · chat, image, audio, docs");
    expect(
      datasetKindLine(row(248), [
        col("conversation", "json"),
        col("expected_tool_calls", "json"),
      ]),
    ).toBe("Conversations · 248 rows");
    expect(
      datasetKindLine(row(312), [col("expected_tool_calls", "json")]),
    ).toBe("Tool calls · 312 rows with expected calls");
    expect(datasetKindLine(row(120), [col("expected_answer", "text")])).toBe(
      "Q&A · 120 rows with expected answers",
    );
    expect(datasetKindLine(row(5), [])).toBe("Rows · 5 rows");
  });
});
