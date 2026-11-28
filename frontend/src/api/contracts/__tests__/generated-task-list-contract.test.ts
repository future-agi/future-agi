import { describe, expect, it } from "vitest";
import {
  TracerEvalTaskListEvalTasksResponse,
  TracerEvalTaskListEvalTasksWithProjectNameResponse,
} from "src/generated/api-contracts/api.zod";

const row = {
  id: "11111111-1111-4111-8111-111111111111",
  name: "Historical evaluation",
  project_name: "Example project",
  run_type: "historical",
  row_type: "spans",
  filters_applied: {},
  created_at: "2026-10-03T00:00:00Z",
  evals_applied: [],
  sampling_rate: 100,
  last_run: "2026-10-03T00:00:00Z",
};

describe("generated task-list contracts", () => {
  it.each([
    { scope: "project", schema: TracerEvalTaskListEvalTasksResponse },
    {
      scope: "cross-project",
      schema: TracerEvalTaskListEvalTasksWithProjectNameResponse,
    },
  ])("preserves failure reasons on $scope rows", ({ schema }) => {
    const table = [
      { ...row, status: "failed", failure_reason: "Narrow the time range." },
      { ...row, status: "completed", failure_reason: null },
    ];
    const parsed = schema.parse({
      status: true,
      result: { table, config: [], metadata: {} },
    });
    expect(parsed.result.table).toEqual(table);
  });
});
