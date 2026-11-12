// W9-5 C1: the training-set model. Counts per file, rows left out (no
// expected answer / no input), overlap (one row in two files), leakage
// (a test input equal to a train input after trimming, collapsing
// whitespace and lowercasing), the two fixes, and the files themselves.
import { describe, expect, it } from "vitest";
import {
  countsLine,
  emptyTrainLine,
  guessSplitRoles,
  inputKeyOf,
  leakAction,
  leakLine,
  leakPairs,
  leftOutLine,
  noInputLine,
  normalizeInput,
  overlapAction,
  overlapLines,
  planTrainingSet,
  rowNumberOf,
  splitNotFileLine,
  trainingFileName,
  trainingFiles,
} from "../trainingSetModel";

const ROLES = { user: "u", turns: "c", assistant: "e", toolCalls: "t" };

/** A table row: Row n, cells by column id. */
const row = (
  n,
  { u = `message ${n}`, c = "[]", e = `reply ${n}`, t = null } = {},
) => ({
  row_id: `r${n}`,
  order: n - 1,
  u: { cell_value: u },
  c: { cell_value: c },
  e: { cell_value: e },
  t: { cell_value: t },
});

const parts = (train, validation, test) =>
  [
    ["train", "train", train],
    ["validation", "val", validation],
    ["test", "test", test],
  ]
    .filter(([, , rows]) => rows)
    .map(([key, splitName, rows]) => ({
      key,
      splitName,
      splitId: `s-${splitName}`,
      rows,
    }));

describe("guessSplitRoles", () => {
  it("guesses train; val, validation or dev; test by name", () => {
    const splits = [
      { id: "a", name: "smoke" },
      { id: "b", name: "test" },
      { id: "c", name: "train" },
      { id: "d", name: "dev" },
    ];
    expect(guessSplitRoles(splits)).toEqual({
      train: "c",
      validation: "d",
      test: "b",
    });
    expect(guessSplitRoles([{ id: "v", name: "validation" }]).validation).toBe(
      "v",
    );
    expect(
      guessSplitRoles([
        { id: "v", name: "val" },
        { id: "w", name: "dev" },
      ]).validation,
    ).toBe("v");
  });

  it("preselects nothing for other names", () => {
    expect(
      guessSplitRoles([
        { id: "a", name: "training-v2" },
        { id: "b", name: "holdout" },
      ]),
    ).toEqual({
      train: null,
      validation: null,
      test: null,
    });
    expect(guessSplitRoles([])).toEqual({
      train: null,
      validation: null,
      test: null,
    });
  });
});

describe("leakage normalization", () => {
  it("trims, collapses whitespace and lowercases", () => {
    expect(normalizeInput("  Where IS   my\n\torder?  ")).toBe(
      "where is my order?",
    );
    expect(normalizeInput(null)).toBe("");
  });

  it("keys the whole prompt: earlier turns and the user message", () => {
    const turns = JSON.stringify([
      { role: "user", content: "Hi" },
      { role: "assistant", content: "Hello!" },
    ]);
    const a = inputKeyOf({ u: "Refund  please", c: turns }, ROLES);
    const b = inputKeyOf(
      { u: " refund please ", c: turns.replace("Hi", "  hi ") },
      ROLES,
    );
    expect(a).toBe(b);
    expect(inputKeyOf({ u: "refund please", c: "[]" }, ROLES)).not.toBe(a);
    // Without a turns column only the message counts.
    expect(
      inputKeyOf(
        { u: "Refund please", c: turns },
        { user: "u", assistant: "e" },
      ),
    ).toBe(inputKeyOf({ u: "refund   please" }, { user: "u", assistant: "e" }));
  });
});

describe("planTrainingSet", () => {
  const train = [
    row(1),
    row(2),
    row(3, { e: "" }),
    row(4, { u: "Where is my ORDER?" }),
    row(7),
  ];
  const validation = [row(20), row(21, { e: "   " })];
  const test = [
    row(7), // also in train
    row(30, { u: "  where is   my order? " }), // same input as Row 4
    row(31, {
      e: "",
      t: JSON.stringify([{ name: "lookup_order", args: { id: 1 } }]),
    }), // tool calls only
    row(32, { e: "" }),
    row(33, { u: "" }),
    row(34),
  ];
  const plan = (opts = {}) =>
    planTrainingSet({
      parts: parts(train, validation, test),
      roles: ROLES,
      ...opts,
    });

  it("counts the rows each file gets and the rows left out", () => {
    const p = plan();
    expect(p.parts.map((x) => [x.key, x.written])).toEqual([
      ["train", 4],
      ["validation", 1],
      ["test", 4],
    ]);
    expect(p.total).toBe(9);
    expect(countsLine(p)).toBe("9 rows · train 4 · validation 1 · test 4");
    expect(leftOutLine(p)).toBe(
      "3 rows without an expected answer are left out (train 1, validation 1, test 1)",
    );
    expect(noInputLine(p)).toBe("1 row without an input is left out (test 1)");
  });

  it("finds the row in two files and keeps it in train only", () => {
    const p = plan();
    expect(p.overlap.map((o) => [rowNumberOf(o.row), o.parts])).toEqual([
      [7, ["train", "test"]],
    ]);
    expect(overlapLines(p)).toEqual(["Row 7 is in train and test"]);
    expect(overlapAction(p)).toBe("Keep it in train only");
    const fixed = plan({ keepOverlapInFirst: true });
    expect(fixed.parts.find((x) => x.key === "test").written).toBe(3);
    expect(fixed.parts.find((x) => x.key === "train").written).toBe(4);
    expect(overlapLines(fixed)).toEqual([
      "Row 7 is kept in train only (it is in train and test)",
    ]);
  });

  it("finds test inputs already in train after normalizing, not the shared row", () => {
    const p = plan();
    const t = p.parts.find((x) => x.key === "test");
    expect(
      t.leaks.map((l) => [rowNumberOf(l.row), rowNumberOf(l.trainRow)]),
    ).toEqual([[30, 4]]);
    expect(leakLine(p, "test")).toBe(
      "1 test row has the same input as a train row (Row 30)",
    );
    expect(leakAction(p, "test")).toBe("Leave it out of test");
    expect(leakPairs(p, "test")).toBe("Row 30 = train Row 4");
    expect(leakLine(p, "validation")).toBeNull();
    const out = plan({ leaveOutLeaked: { test: true } });
    expect(out.parts.find((x) => x.key === "test").written).toBe(3);
    expect(leakLine(out, "test")).toBe(
      "1 test row with the same input as a train row is left out (Row 30)",
    );
  });

  it("names several rows and both fixes together", () => {
    const many = planTrainingSet({
      parts: parts(
        [
          row(12, { u: "A" }),
          row(57, { u: "B" }),
          row(101, { u: "C" }),
          row(5),
        ],
        null,
        [
          row(200, { u: "a " }),
          row(201, { u: "b" }),
          row(202, { u: " C" }),
          row(5),
          row(203),
        ],
      ),
      roles: ROLES,
      keepOverlapInFirst: true,
      leaveOutLeaked: { test: true },
    });
    const notFixed = planTrainingSet({
      parts: parts(
        [row(12, { u: "A" }), row(57, { u: "B" }), row(101, { u: "C" })],
        null,
        [row(200, { u: "a " }), row(201, { u: "b" }), row(202, { u: " C" })],
      ),
      roles: ROLES,
    });
    expect(leakLine(notFixed, "test")).toBe(
      "3 test rows have the same input as a train row (Rows 200, 201, 202)",
    );
    expect(leakAction(notFixed, "test")).toBe("Leave them out of test");
    expect(countsLine(many)).toBe("5 rows · train 4 · test 1");
  });

  it("groups overlap by files and keeps each row in its first file", () => {
    const p = planTrainingSet({
      parts: parts(
        [row(1), row(2)],
        [row(1), row(9), row(10)],
        [row(9), row(10), row(2)],
      ),
      roles: ROLES,
    });
    expect(overlapLines(p)).toEqual([
      "Row 1 is in train and validation",
      "Row 2 is in train and test",
      "2 rows are in validation and test (Rows 9, 10)",
    ]);
    expect(overlapAction(p)).toBe("Keep each in its first file only");
    const fixed = planTrainingSet({
      parts: parts(
        [row(1), row(2)],
        [row(1), row(9), row(10)],
        [row(9), row(10), row(2)],
      ),
      roles: ROLES,
      keepOverlapInFirst: true,
    });
    expect(fixed.parts.map((x) => x.written)).toEqual([2, 2, 0]);
  });

  it("checks validation against train too", () => {
    const p = planTrainingSet({
      parts: parts([row(1, { u: "Same" })], [row(2, { u: "same" })], null),
      roles: ROLES,
    });
    expect(leakLine(p, "validation")).toBe(
      "1 validation row has the same input as a train row (Row 2)",
    );
    expect(leakAction(p, "validation")).toBe("Leave it out of validation");
  });
});

describe("splitNotFileLine", () => {
  const train = [row(1), row(4, { u: "Where is my order?" }), row(7)];
  const test = [row(7), row(30, { u: " WHERE is my  order?" }), row(34)];
  const planWith = (opts) =>
    planTrainingSet({
      parts: parts(train, null, test),
      roles: ROLES,
      ...opts,
    });

  it("is null while the test file holds every eligible test row", () => {
    expect(splitNotFileLine(planWith({}))).toBeNull();
  });

  it("names the rows the fixes took out of the file but not the split", () => {
    expect(splitNotFileLine(planWith({ leaveOutLeaked: { test: true } }))).toBe(
      "New experiment on test runs on the whole split, including Row 30, which the test file leaves out.",
    );
    expect(
      splitNotFileLine(
        planWith({ keepOverlapInFirst: true, leaveOutLeaked: { test: true } }),
      ),
    ).toBe(
      "New experiment on test runs on the whole split, including Rows 7, 30, which the test file leaves out.",
    );
  });
});

describe("emptyTrainLine", () => {
  const planOf = (train) =>
    planTrainingSet({
      parts: parts(train, null, [row(40)]),
      roles: ROLES,
    });

  it("is null while train has a row to write", () => {
    expect(emptyTrainLine(planOf([row(1)]), 6)).toBeNull();
  });

  it("names the version when the train split has no rows in it", () => {
    expect(emptyTrainLine(planOf([]), 6)).toBe(
      "Split train has no rows in v6. Pick another split or version to train on.",
    );
    expect(emptyTrainLine(planOf([]))).toBe(
      "Split train has no current saved rows. Pick another split to train on.",
    );
  });

  it("points at the left-out lines when every train row is left out", () => {
    const p = planOf([row(1, { e: "" }), row(2, { u: "" })]);
    expect(emptyTrainLine(p, 6)).toBe(
      "No row of train can be written: each one is left out above.",
    );
    expect(leftOutLine(p)).toBe(
      "1 row without an expected answer is left out (train 1)",
    );
  });
});

describe("trainingFiles", () => {
  it("names the files by exportFileName and writes OpenAI chat lines", () => {
    expect(
      trainingFileName({
        datasetName: "Support agent - golden set",
        versionNumber: 6,
        key: "train",
      }),
    ).toBe("support-agent-golden-set-v6-train.jsonl");
    const turns = JSON.stringify([
      { role: "system", content: "Be brief." },
      { role: "user", content: "Hi" },
      { role: "assistant", content: "Hello!" },
    ]);
    const plan = planTrainingSet({
      parts: parts(
        [
          row(1, { c: turns }),
          row(2, {
            e: "",
            t: JSON.stringify([{ name: "lookup_order", args: { id: 7 } }]),
          }),
        ],
        [row(3)],
        [row(4), row(5, { e: "" })],
      ),
      roles: ROLES,
    });
    const files = trainingFiles({
      plan,
      roles: ROLES,
      datasetName: "Support agent - golden set",
      versionNumber: 6,
      system: "You are the Acme support agent.",
    });
    expect(files.map((f) => [f.name, f.lines])).toEqual([
      ["support-agent-golden-set-v6-train.jsonl", 2],
      ["support-agent-golden-set-v6-validation.jsonl", 1],
      ["support-agent-golden-set-v6-test.jsonl", 1],
    ]);
    expect(files.every((f) => f.mime === "application/x-ndjson")).toBe(true);
    const lines = files[0].data
      .trim()
      .split("\n")
      .map((l) => JSON.parse(l));
    expect(lines[0].messages.map((m) => m.role)).toEqual([
      "system",
      "system",
      "user",
      "assistant",
      "user",
      "assistant",
    ]);
    expect(lines[0].messages[0].content).toBe(
      "You are the Acme support agent.",
    );
    expect(lines[0].messages.at(-1)).toEqual({
      role: "assistant",
      content: "reply 1",
    });
    expect(lines[1].messages.at(-1).tool_calls[0].function).toEqual({
      name: "lookup_order",
      arguments: JSON.stringify({ id: 7 }),
    });
    plan.parts.forEach((p, i) => expect(files[i].lines).toBe(p.written));
  });

  it("writes no file for a split with no rows left", () => {
    const plan = planTrainingSet({
      parts: parts([row(1)], [row(2, { e: "" })], null),
      roles: ROLES,
    });
    expect(
      trainingFiles({
        plan,
        roles: ROLES,
        datasetName: "QA",
        versionNumber: 2,
      }).map((f) => f.name),
    ).toEqual(["qa-v2-train.jsonl"]);
  });
});
