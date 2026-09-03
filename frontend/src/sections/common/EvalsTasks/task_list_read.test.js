import { describe, expect, it, vi } from "vitest";

import { INTERACTIVE_REQUEST_TIMEOUT_MS } from "src/config/runtime_limits";

import {
  EVAL_TASK_LIST_REQUEST_TIMEOUT_MS,
  readEvalTaskListPage,
} from "./task_list_read";

describe("taskListRead", () => {
  it("returns only a bounded valid task page", async () => {
    const requestPage = vi.fn().mockResolvedValue({
      data: {
        result: {
          table: [{ id: "task-1" }],
          metadata: { total_rows: 1 },
        },
      },
    });

    await expect(readEvalTaskListPage(requestPage)).resolves.toMatchObject({
      table: [{ id: "task-1" }],
      totalRows: 1,
    });
    expect(requestPage).toHaveBeenCalledWith(
      expect.objectContaining({
        signal: expect.any(AbortSignal),
        timeout: EVAL_TASK_LIST_REQUEST_TIMEOUT_MS,
      }),
    );
    expect(EVAL_TASK_LIST_REQUEST_TIMEOUT_MS).toBe(
      INTERACTIVE_REQUEST_TIMEOUT_MS,
    );
  });

  it.each([
    undefined,
    { table: [], metadata: {} },
    { table: [{ id: "task-1" }], metadata: { total_rows: 0 } },
  ])(
    "fails malformed pages instead of reporting an empty list",
    async (result) => {
      await expect(
        readEvalTaskListPage(() =>
          Promise.resolve({ data: result === undefined ? {} : { result } }),
        ),
      ).rejects.toMatchObject({ code: "eval_task_list_invalid_page" });
    },
  );
});
