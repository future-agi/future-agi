// MOCK_RUNS — a QA-only fixture for the Runs tab, surfaced behind the
// `?mockRuns=1` switch so the populated run-history state can be exercised
// without a real completed harness job. Real runs come from the executions API
// (useEnvironmentRuns / executionToRun); this matches that same run shape
// (id, label, status, startedAt, finishedAt, total, passed, failed,
// agentVersion) so RunHistoryRow renders it identically. `executionId` is the
// id a row click routes into the reused product execution detail.
//
// Fixed ISO timestamps on purpose — nothing else in these fixtures is
// clock-dependent, so a snapshot stays stable.
export const MOCK_RUNS = [
  {
    id: "run-mock-3",
    executionId: "run-mock-3",
    label: "Run 3",
    status: "running",
    startedAt: "2026-01-14T09:12:00.000Z",
    finishedAt: null,
    total: 12,
    passed: 4,
    failed: 0,
    agentVersion: "v2",
  },
  {
    id: "run-mock-2",
    executionId: "run-mock-2",
    label: "Run 2",
    status: "passed",
    startedAt: "2026-01-13T16:40:00.000Z",
    finishedAt: "2026-01-13T16:42:10.000Z",
    total: 12,
    passed: 11,
    failed: 1,
    agentVersion: "v2",
  },
  {
    id: "run-mock-1",
    executionId: "run-mock-1",
    label: "Run 1",
    status: "failed",
    startedAt: "2026-01-12T11:05:00.000Z",
    finishedAt: "2026-01-12T11:07:30.000Z",
    total: 12,
    passed: 7,
    failed: 5,
    agentVersion: "v1",
  },
];
