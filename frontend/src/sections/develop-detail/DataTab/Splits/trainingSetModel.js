// Export as training set (W9-5, DG-06): train / validation / test files
// from three splits of one saved version, checked for leakage first. Pure:
// rows of each split in (the table reads of common/export/datasetExport.js),
// the dialog's lines and the files out. Every file is written by
// common/export/writers.js (toOpenAiFineTune, exportFileName).
import { isSurfaceShown } from "src/launch/launchSwitch";
import {
  EXPORT_FORMATS,
  cellText,
  conversationTurns,
  exportFileName,
  hasExpected,
  openAiToolCalls,
  toOpenAiFineTune,
} from "src/sections/common/export/writers";

/** The three files, in priority order (a row kept in one goes to the first). */
export const TRAINING_PARTS = Object.freeze([
  Object.freeze({ key: "train", label: "Train", names: ["train"] }),
  Object.freeze({
    key: "validation",
    label: "Validation",
    names: ["val", "validation", "dev"],
  }),
  Object.freeze({ key: "test", label: "Test", names: ["test"] }),
]);

const PART_ORDER = TRAINING_PARTS.map((p) => p.key);

/**
 * Default split per file, guessed by split name (train; val / validation /
 * dev; test). A file with no matching split gets null (nothing preselected).
 * @param {{id: string, name: string}[]} splits
 * @returns {{train: string|null, validation: string|null, test: string|null}}
 */
export function guessSplitRoles(splits) {
  const out = {};
  const taken = new Set();
  TRAINING_PARTS.forEach((part) => {
    const hit = part.names
      .map((n) =>
        (splits ?? []).find(
          (s) =>
            !taken.has(s.id) &&
            String(s.name ?? "")
              .trim()
              .toLowerCase() === n,
        ),
      )
      .find(Boolean);
    out[part.key] = hit?.id ?? null;
    if (hit) taken.add(hit.id);
  });
  return out;
}

/** Text compared for leakage: trimmed, whitespace collapsed, lowercased. */
export function normalizeInput(value) {
  return String(value ?? "")
    .trim()
    .replace(/\s+/g, " ")
    .toLowerCase();
}

const textOf = (v) => {
  if (v === null || v === undefined) return "";
  if (typeof v === "string") return v;
  try {
    return JSON.stringify(v);
  } catch {
    return String(v);
  }
};

/**
 * A row's prompt as one comparable key: the earlier turns (when a turns
 * column is chosen) and the user message, each normalized. Two rows with
 * the same key send the model the same input.
 * @param {object} flat  `{columnId: value}` (messageRowOf)
 * @param {{user: string, turns?: string|null}} roles
 */
export function inputKeyOf(flat, roles) {
  const turnsOf = (v) =>
    (conversationTurns(v) ?? []).map(
      (t) => `${t.role}: ${normalizeInput(t.content)}`,
    );
  const prior = roles?.turns ? turnsOf(flat?.[roles.turns]) : [];
  const userValue = flat?.[roles?.user];
  const asTurns = conversationTurns(userValue);
  const user = asTurns
    ? turnsOf(userValue)
    : [`user: ${normalizeInput(textOf(userValue))}`];
  return [...prior, ...user].join("\n");
}

const cellValue = (row, columnId) => {
  const v = row?.[columnId]?.cell_value;
  return v === undefined || v === "" ? null : v;
};

/** `{columnId: value}` of the message columns of one table row. */
export function messageRowOf(row, roles) {
  const out = {};
  [roles?.user, roles?.turns, roles?.assistant, roles?.toolCalls].forEach(
    (id) => {
      if (id) out[id] = cellValue(row, id);
    },
  );
  return out;
}

/** The row number people see (`Row 12`), from the table row's order. */
export const rowNumberOf = (row) =>
  Number.isFinite(row?.order) ? row.order + 1 : null;

const rowIdOf = (row) => row?.row_id ?? row?.id ?? null;
const byNumber = (a, b) => (rowNumberOf(a) ?? 0) - (rowNumberOf(b) ?? 0);

/**
 * Plan the files: which rows each file gets and why the others are left out.
 *
 * 1. A row needs a user message and an expected answer (a reply, or tool
 *    calls) to be an example; the others are left out and counted.
 * 2. Overlap: the same row in two files. With `keepOverlapInFirst` it stays
 *    only in the first of train, validation, test.
 * 3. Leakage: a validation or test row whose input equals a train row's
 *    (inputKeyOf). With `leaveOutLeaked[part]` those rows leave that file.
 *
 * @param {{parts: {key: string, splitId?: string, splitName: string,
 *   rows: object[]}[], roles: {user: string, assistant: string,
 *   turns?: string|null, toolCalls?: string|null},
 *   keepOverlapInFirst?: boolean,
 *   leaveOutLeaked?: {validation?: boolean, test?: boolean}}} p
 */
export function planTrainingSet({
  parts,
  roles,
  keepOverlapInFirst = false,
  leaveOutLeaked = {},
}) {
  const chosen = PART_ORDER.map((key) =>
    (parts ?? []).find((p) => p?.key === key),
  ).filter(Boolean);
  const keyOf = new Map();
  const eligible = {};
  const missing = {};
  chosen.forEach((p) => {
    missing[p.key] = { expected: [], input: [] };
    eligible[p.key] = [];
    (p.rows ?? []).forEach((row) => {
      const flat = messageRowOf(row, roles);
      if (
        roles?.user &&
        roles?.assistant &&
        hasExpected(flat, {
          user: roles.user,
          assistant: roles.assistant,
          toolCalls: roles.toolCalls ?? undefined,
        })
      ) {
        eligible[p.key].push(row);
        keyOf.set(row, inputKeyOf(flat, roles));
        return;
      }
      // Left out: say why (no expected answer first, else no input).
      const hasAnswer =
        cellText(flat[roles?.assistant]).trim() !== "" ||
        Boolean(roles?.toolCalls && openAiToolCalls(flat[roles.toolCalls]));
      missing[p.key][hasAnswer ? "input" : "expected"].push(row);
    });
  });

  // 2. Overlap: rows (by id) in more than one file.
  const partsOfRow = new Map();
  chosen.forEach((p) =>
    eligible[p.key].forEach((row) => {
      const id = rowIdOf(row);
      if (!partsOfRow.has(id)) partsOfRow.set(id, { row, parts: [] });
      partsOfRow.get(id).parts.push(p.key);
    }),
  );
  const overlap = [...partsOfRow.values()]
    .filter((o) => o.parts.length > 1)
    .sort((a, b) => byNumber(a.row, b.row));
  const overlapDropped = {};
  const afterOverlap = {};
  chosen.forEach((p) => {
    overlapDropped[p.key] = [];
    afterOverlap[p.key] = eligible[p.key].filter((row) => {
      const o = partsOfRow.get(rowIdOf(row));
      const drop =
        keepOverlapInFirst && o.parts.length > 1 && o.parts[0] !== p.key;
      if (drop) overlapDropped[p.key].push(row);
      return !drop;
    });
  });

  // 3. Leakage against train (the rows train keeps).
  const trainRows = afterOverlap.train ?? [];
  const trainByKey = new Map();
  trainRows.forEach((row) => {
    const k = keyOf.get(row);
    if (!trainByKey.has(k)) trainByKey.set(k, row);
  });
  const trainIds = new Set(trainRows.map(rowIdOf));
  const leaks = {};
  const finalRows = {};
  chosen.forEach((p) => {
    leaks[p.key] = [];
    if (p.key === "train") {
      finalRows[p.key] = trainRows;
      return;
    }
    afterOverlap[p.key].forEach((row) => {
      if (trainIds.has(rowIdOf(row))) return; // overlap, reported above
      const match = trainByKey.get(keyOf.get(row));
      if (match) leaks[p.key].push({ row, trainRow: match });
    });
    leaks[p.key].sort((a, b) => byNumber(a.row, b.row));
    const leaked = new Set(leaks[p.key].map((l) => rowIdOf(l.row)));
    finalRows[p.key] = leaveOutLeaked[p.key]
      ? afterOverlap[p.key].filter((row) => !leaked.has(rowIdOf(row)))
      : afterOverlap[p.key];
  });

  const out = chosen.map((p) => ({
    key: p.key,
    splitId: p.splitId ?? null,
    splitName: p.splitName,
    read: (p.rows ?? []).length,
    rows: finalRows[p.key],
    written: finalRows[p.key].length,
    missingExpected: missing[p.key].expected.length,
    missingInput: missing[p.key].input.length,
    overlapDropped: overlapDropped[p.key],
    leaks: leaks[p.key],
    leakedOut: Boolean(leaveOutLeaked[p.key]) && leaks[p.key].length > 0,
  }));
  return {
    parts: out,
    total: out.reduce((n, p) => n + p.written, 0),
    overlap,
    keepOverlapInFirst: Boolean(keepOverlapInFirst) && overlap.length > 0,
  };
}

const rowsWord = (n) => (n === 1 ? "row" : "rows");
const listRows = (rows) => {
  const nums = rows.map(rowNumberOf).filter((n) => n != null);
  return nums.length === 1 ? `Row ${nums[0]}` : `Rows ${nums.join(", ")}`;
};
const joinWords = (words) =>
  words.length <= 1
    ? words.join("")
    : `${words.slice(0, -1).join(", ")} and ${words[words.length - 1]}`;

/** `248 rows · train 180 · validation 30 · test 38` */
export function countsLine(plan) {
  return [
    `${plan.total} ${rowsWord(plan.total)}`,
    ...plan.parts.map((p) => `${p.key} ${p.written}`),
  ].join(" · ");
}

const leftOut = (plan, field, what) => {
  const parts = plan.parts.filter((p) => p[field] > 0);
  const n = parts.reduce((s, p) => s + p[field], 0);
  if (!n) return null;
  const by = parts.map((p) => `${p.key} ${p[field]}`).join(", ");
  return `${n} ${rowsWord(n)} without ${what} ${n === 1 ? "is" : "are"} left out (${by})`;
};

/** `12 rows without an expected answer are left out (train 8, …)`, or null. */
export const leftOutLine = (plan) =>
  leftOut(plan, "missingExpected", "an expected answer");

/** `2 rows without an input are left out (test 2)`, or null. */
export const noInputLine = (plan) => leftOut(plan, "missingInput", "an input");

/**
 * Why there is nothing to train on (Download needs a train file), or null:
 * `Split train has no rows in v6. …` / `No row of train can be written: …`
 * (the left-out lines say why each row was left out).
 */
export function emptyTrainLine(plan, versionNumber = null) {
  const train = plan.parts.find((p) => p.key === "train");
  if (!train || train.written > 0) return null;
  if (
    !train.read &&
    (versionNumber == null || !isSurfaceShown("dataset.versions"))
  )
    return `Split ${train.splitName} has no current saved rows. Pick another split to train on.`;
  const where = `v${versionNumber}`;
  if (!train.read)
    return `Split ${train.splitName} has no rows in ${where}. Pick another split or version to train on.`;
  return `No row of ${train.splitName} can be written: each one is left out above.`;
}

/**
 * `3 test rows have the same input as a train row (Rows 12, 57, 101)`, or
 * null when that file has none. Once left out the line says so.
 */
export function leakLine(plan, key) {
  const part = plan.parts.find((p) => p.key === key);
  const n = part?.leaks.length ?? 0;
  if (!n) return null;
  const rows = listRows(part.leaks.map((l) => l.row));
  if (part.leakedOut)
    return `${n} ${key} ${rowsWord(n)} with the same input as a train row ${n === 1 ? "is" : "are"} left out (${rows})`;
  return `${n} ${key} ${rowsWord(n)} ${n === 1 ? "has" : "have"} the same input as a train row (${rows})`;
}

/** Which train row each leaked row matches: `Row 30 = train Row 4 · …`. */
export function leakPairs(plan, key) {
  const part = plan.parts.find((p) => p.key === key);
  return (part?.leaks ?? [])
    .map(
      (l) => `Row ${rowNumberOf(l.row)} = train Row ${rowNumberOf(l.trainRow)}`,
    )
    .join(" · ");
}

/**
 * The fixes change the files, not the split: `New experiment on test runs on
 * the whole split, including Rows 1, 7, which the test file leaves out.`, or
 * null when the file holds every eligible row of the split.
 */
export function splitNotFileLine(plan, key = "test") {
  const part = plan?.parts.find((p) => p.key === key);
  if (!part) return null;
  const out = [
    ...part.overlapDropped,
    ...(part.leakedOut ? part.leaks.map((l) => l.row) : []),
  ].sort(byNumber);
  if (!out.length) return null;
  return `New experiment on ${part.splitName} runs on the whole split, including ${listRows(out)}, which the ${key} file leaves out.`;
}

/** `Leave them out of test` / `Leave it out of test`. */
export const leakAction = (plan, key) => {
  const n = plan.parts.find((p) => p.key === key)?.leaks.length ?? 0;
  return `Leave ${n === 1 ? "it" : "them"} out of ${key}`;
};

/**
 * Overlap lines, one per set of files: `Row 7 is in train and test`,
 * `2 rows are in validation and test (Rows 9, 10)`; once kept in the first
 * file only: `Row 7 is kept in train only (it is in train and test)`.
 */
export function overlapLines(plan) {
  const groups = new Map();
  plan.overlap.forEach((o) => {
    const k = o.parts.join("|");
    if (!groups.has(k)) groups.set(k, { parts: o.parts, rows: [] });
    groups.get(k).rows.push(o.row);
  });
  return [...groups.values()].map(({ parts, rows }) => {
    const where = joinWords(parts);
    const one = rows.length === 1;
    if (plan.keepOverlapInFirst)
      return one
        ? `${listRows(rows)} is kept in ${parts[0]} only (it is in ${where})`
        : `${rows.length} rows are kept in ${parts[0]} only (${listRows(rows)}; each is in ${where})`;
    if (one) return `${listRows(rows)} is in ${where}`;
    return `${rows.length} rows are in ${where} (${listRows(rows)})`;
  });
}

/** `Keep it in train only`; mixed first files: `Keep each in its first file only`. */
export function overlapAction(plan) {
  const firsts = [...new Set(plan.overlap.map((o) => o.parts[0]))];
  const it = plan.overlap.length === 1 ? "it" : "them";
  return firsts.length === 1
    ? `Keep ${it} in ${firsts[0]} only`
    : "Keep each in its first file only";
}

/** `support-agent-golden-set-v6-validation.jsonl` (exportFileName). */
export const trainingFileName = ({ datasetName, versionNumber = null, key }) =>
  exportFileName(
    [datasetName, versionNumber != null ? `v${versionNumber}` : null, key],
    "jsonl",
  );

/**
 * The files: `[{key, splitName, name, mime, data, lines}]`, one per chosen split
 * with rows, written by toOpenAiFineTune and named by exportFileName
 * (`support-agent-golden-set-v6-train.jsonl`).
 * @param {{plan: object, roles: object, system?: string, datasetName: string,
 *   versionNumber?: number|null}} p
 */
export function trainingFiles({
  plan,
  roles,
  system = "",
  datasetName,
  versionNumber = null,
}) {
  const messageRoles = {
    system: system?.trim() ? system : undefined,
    user: roles.user,
    assistant: roles.assistant,
    turns: roles.turns ?? undefined,
    toolCalls: roles.toolCalls ?? undefined,
  };
  return plan.parts
    .filter((p) => p.written > 0)
    .map((p) => {
      const data = toOpenAiFineTune(
        p.rows.map((r) => messageRowOf(r, roles)),
        messageRoles,
      );
      return {
        key: p.key,
        splitName: p.splitName,
        name: trainingFileName({ datasetName, versionNumber, key: p.key }),
        mime: EXPORT_FORMATS.jsonl.mime,
        data,
        lines: data ? data.split("\n").filter(Boolean).length : 0,
      };
    });
}
