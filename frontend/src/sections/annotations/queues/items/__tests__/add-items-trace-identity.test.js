import { runInNewContext } from "node:vm";
import { describe, expect, it, vi } from "vitest";
import dialogSource from "../add-items-dialog.jsx?raw";
import { addResultToast, summarizeAddResults } from "../add-items-results";

// Execute the actual submit callback with controlled selection state. The grid
// and project queries are outside this regression's boundary; no copy of the
// payload-building logic is used here.
const submitStart = dialogSource.indexOf(
  "  const handleSubmit = async () => {",
);
const submitEnd = dialogSource.indexOf(
  "  const handleBack = () => {",
  submitStart,
);
if (submitStart < 0 || submitEnd <= submitStart) {
  throw new Error("Could not locate AddItemsDialog's submit callback");
}
const submitSource = `${dialogSource.slice(submitStart, submitEnd)}\nhandleSubmit;`;

const projectId = "project-tracing";
const traceId = "trace-T";
const filters = [{ column_id: "status", operator: "equals", value: "success" }];

function createSubmit({
  mode = "manual",
  ids = [traceId],
  excludedIds = [],
  isVoice = false,
} = {}) {
  const scope = {
    queueId: "queue-traces",
    sourceType: "trace",
    selectionMode: mode,
    selectedIds: new Set(ids),
    selectAllInfo:
      mode === "selectAll"
        ? {
            projectId,
            totalCount: 41,
            filters,
            excludedIds: new Set(excludedIds),
          }
        : null,
    selectedProjectId: projectId,
    isVoiceTraceSelection: isVoice,
    datasetSelectionRevision: { current: 0 },
    // On the defective implementation, this resolves T to R and causes the
    // assertions below to observe an observation_span task instead of a trace.
    fetchRootSpans: vi.fn(async (selected) =>
      Object.fromEntries(
        selected.map((id) => [id, id === traceId ? "root-R" : `root-${id}`]),
      ),
    ),
    addItems: vi.fn((payload, { onSuccess }) => {
      onSuccess({
        data: {
          result: {
            added: payload.items?.length ?? 41,
            duplicates: 0,
            errors: [],
          },
        },
      });
    }),
    setIsResolving: vi.fn(),
    enqueueSnackbar: vi.fn(),
    resetSelection: vi.fn(),
    setSourceType: vi.fn(),
    onClose: vi.fn(),
    summarizeAddResults,
    addResultToast,
  };
  return {
    scope,
    submit: runInNewContext(submitSource, scope, {
      filename: "AddItemsDialog.handleSubmit.js",
    }),
  };
}

function payloads(scope) {
  return scope.addItems.mock.calls.map(([payload]) => payload);
}

// W10-ANNOTATIONS-L03: switching how the same From Traces selection is made
// must not switch the FK that the server uses for queue deduplication.
describe("From Traces canonical queue identity", () => {
  it.each([false, true])(
    "keeps the same trace identity in manual and all-matching modes (voice=%s)",
    async (isVoice) => {
      const manual = createSubmit({ isVoice });
      const allMatching = createSubmit({ mode: "selectAll", isVoice });

      await manual.submit();
      await allMatching.submit();

      expect(payloads(manual.scope)).toEqual([
        {
          queueId: "queue-traces",
          project_id: projectId,
          items: [{ source_type: "trace", source_id: traceId }],
        },
      ]);
      expect(payloads(allMatching.scope)).toEqual([
        {
          queueId: "queue-traces",
          selection: {
            mode: "filter",
            source_type: "trace",
            project_id: projectId,
            filter: filters,
            exclude_ids: [],
            ...(isVoice ? { is_voice_call: true } : {}),
          },
        },
      ]);
      expect(manual.scope.fetchRootSpans).not.toHaveBeenCalled();
      expect(allMatching.scope.fetchRootSpans).not.toHaveBeenCalled();
    },
  );

  it("keeps trace IDs and project scope across the 500-item batch boundary", async () => {
    const ids = Array.from({ length: 501 }, (_, index) => `trace-${index}`);
    const { scope, submit } = createSubmit({ ids });

    await submit();

    const requests = payloads(scope);
    expect(requests.map(({ items }) => items.length)).toEqual([500, 1]);
    expect(requests.every((request) => request.project_id === projectId)).toBe(
      true,
    );
    expect(
      requests.every((request) => request.queueId === "queue-traces"),
    ).toBe(true);
    expect(requests.flatMap(({ items }) => items)).toEqual(
      ids.map((id) => ({ source_type: "trace", source_id: id })),
    );
    expect(scope.fetchRootSpans).not.toHaveBeenCalled();
    expect(scope.onClose).toHaveBeenCalledOnce();
  });

  it("keeps all-matching exclusions in the same trace ID namespace", async () => {
    const { scope, submit } = createSubmit({
      mode: "selectAll",
      excludedIds: [traceId],
    });

    await submit();

    expect(payloads(scope)).toHaveLength(1);
    expect(payloads(scope)[0].selection).toMatchObject({
      source_type: "trace",
      project_id: projectId,
      filter: filters,
      exclude_ids: [traceId],
    });
    expect(scope.fetchRootSpans).not.toHaveBeenCalled();
  });
});
