import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { describe, expect, it, vi } from "vitest";
import { PREVIEW_ROW_LIMIT, readAllTableRows } from "../scopeRows";

const here = dirname(fileURLToPath(import.meta.url));
const read = (...parts) => readFileSync(join(here, "..", ...parts), "utf8");

// A dataset table endpoint that clamps every page, like the legacy grid
// contract (page_size is clamped to the dataset page maximum).
function tableOf(total, clamp, { withTotal = true } = {}) {
  const all = Array.from({ length: total }, (_, i) => ({ row_id: `r${i}` }));
  return vi.fn(async (_url, { params }) => {
    const size = Math.min(params.page_size, clamp);
    const start = params.current_page_index * size;
    return {
      data: {
        result: {
          table: all.slice(start, start + size),
          ...(withTotal ? { metadata: { total_rows: total } } : {}),
        },
      },
    };
  });
}

describe("reading every row in scope", () => {
  it.each([
    [1203, 500],
    [1203, 100],
    [500, 500],
    [0, 500],
  ])("reads %i rows through a %i-row page clamp", async (total, clamp) => {
    const get = tableOf(total, clamp);
    const out = await readAllTableRows("/t", { filters: "[]" }, { get });
    expect(out.complete).toBe(true);
    expect(out.rows.map((r) => r.row_id)).toEqual(
      Array.from({ length: total }, (_, i) => `r${i}`),
    );
    for (const [, { params }] of get.mock.calls)
      expect(params.filters).toBe("[]");
  });

  it("stops at an empty page when the total is not reported", async () => {
    const get = tableOf(730, 500, { withTotal: false });
    const out = await readAllTableRows("/t", {}, { get });
    expect(out).toMatchObject({ complete: true });
    expect(out.rows).toHaveLength(730);
  });

  it("says when a bounded read is incomplete", async () => {
    const get = tableOf(PREVIEW_ROW_LIMIT + 1, 500);
    const out = await readAllTableRows(
      "/t",
      {},
      {
        get,
        limit: PREVIEW_ROW_LIMIT,
      },
    );
    expect(out.complete).toBe(false);
    expect(out.rows.length).toBeGreaterThanOrEqual(PREVIEW_ROW_LIMIT);
  });

  it("is how review and deep links read rows", () => {
    const review = read("StepperComponentExperiment", "ReviewStep.jsx");
    const wizard = read("RunExperiment.jsx");
    // No single capped page stands for a whole scope any more.
    expect(review).not.toMatch(/page_size: Math\.max\(ids\.length, 1\)/);
    expect(review).not.toMatch(/useVersionScopeRows/);
    expect(review).toMatch(/useScopePreviewRows\(/);
    expect(review).toMatch(/readAllTableRows\(/);
    expect(wizard).not.toMatch(/page_size: total, current_page_index: 0/);
    expect(wizard).toMatch(/readAllTableRows\(url, params\)/);
  });

  it("never stands in column statistics the table does not send", () => {
    const review = read("StepperComponentExperiment", "ReviewStep.jsx");
    // Every run that is not on picked rows reads its rows; an oversized
    // scope says it was not checked instead of showing nothing.
    expect(review).toMatch(/all: !values\.rowIds\?\.length,/);
    expect(review).toMatch(/useStats: false/);
    expect(review).not.toMatch(/useStats: !scoped/);
    expect(review).toMatch(/data-testid="empty-input-unchecked"/);
    expect(review).toMatch(/Empty inputs not checked: more than/);
  });

  it("never widens a deep link when its rows cannot be read", () => {
    const wizard = read("RunExperiment.jsx");
    expect(wizard).not.toMatch(/\.catch\(\(\) => apply\(sample\.rows\)\)/);
    expect(wizard).toMatch(/keepUnverified\(\)/);
  });
});
