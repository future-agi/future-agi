import { Buffer } from "node:buffer";
import { createHash, randomUUID } from "node:crypto";
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";
import { emptyInputNote, serverSampleRows, sha256Hex } from "../scopePreview";

const reviewStep = readFileSync(
  join(
    dirname(fileURLToPath(import.meta.url)),
    "..",
    "StepperComponentExperiment",
    "ReviewStep.jsx",
  ),
  "utf8",
);
const nodeSha = (text) => createHash("sha256").update(text).digest("hex");

// The create endpoint's rule (experiment_run_scope.resolve_run_scope): rank
// the eligible rows by sha256(f"{seed}:{row_id}") and keep the first `size`.
function serverPick(ids, size, seed) {
  return new Set(
    [...ids]
      .sort((a, b) =>
        Buffer.compare(
          createHash("sha256").update(`${seed}:${a}`).digest(),
          createHash("sha256").update(`${seed}:${b}`).digest(),
        ),
      )
      .slice(0, size),
  );
}

describe("scope preview", () => {
  it("hashes exactly like SHA-256", () => {
    for (const text of [
      "",
      "abc",
      "17:4c5f2b0e-8d1a-4f52-9a3e-2f0d9b1c7e11",
      "x".repeat(55),
      "x".repeat(56),
      "x".repeat(64),
      "y".repeat(200),
      "naïve · 行",
    ])
      expect(sha256Hex(text)).toBe(nodeSha(text));
  });

  it("previews exactly the rows the server samples", () => {
    const rows = Array.from({ length: 40 }, (_, i) => ({
      row_id: randomUUID(),
      order: i,
    }));
    for (const [n, seed] of [
      [5, 17],
      [1, 0],
      [39, 123456],
    ]) {
      const picked = serverSampleRows(rows, { n, seed });
      expect(picked).toHaveLength(n);
      expect(new Set(picked.map((r) => r.row_id))).toEqual(
        serverPick(
          rows.map((r) => r.row_id),
          n,
          seed,
        ),
      );
      // Dataset order is kept, as the server keeps eligible order.
      expect(picked.map((r) => r.order)).toEqual(
        [...picked.map((r) => r.order)].sort((a, b) => a - b),
      );
    }
    expect(serverSampleRows(rows, null)).toBe(rows);
  });

  it("states what an empty mapped input really does", () => {
    const line = emptyInputNote(
      { evalName: "Correctness", column: "intent", missing: 1, total: 20 },
      "row",
      { selected: true },
    );
    expect(line).toBe(
      "Correctness needs intent · empty on 1 of 20 selected rows · those rows may show an error instead of a score",
    );
    expect(line).not.toMatch(/Skipped/);
  });

  it("is what the review step uses for the sample and the warning", () => {
    expect(reviewStep).not.toMatch(/sampleRowsOf|emptyInputLine/);
    expect(reviewStep).toMatch(/serverSampleRows\(fullScope, sampled\)/);
    expect(reviewStep).toMatch(
      /\{emptyInputNote\(w, rowNoun, \{ selected: scoped \}\)\}/,
    );
  });
});
