import { runInNewContext } from "node:vm";
import { describe, expect, it, vi } from "vitest";
import dialogSource from "../add-items-dialog.jsx?raw";
import { addResultToast, summarizeAddResults } from "../add-items-results";

// Run the dialog's real submit callback with controlled selection state, as
// add-items-trace-identity.test.js does. The grid and project queries are
// outside this regression's boundary.
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
const filters = [{ column_id: "status", operator: "equals", value: "success" }];

const duplicatesOnly = (payload) => ({
  data: {
    result: {
      added: 0,
      duplicates: payload.items?.length ?? 3,
      errors: [],
    },
  },
});
const errorsOnly = () => ({
  data: {
    result: {
      added: 0,
      duplicates: 0,
      errors: ["Not found: trace=trace-0"],
    },
  },
});
const allAdded = (payload) => ({
  data: {
    result: { added: payload.items?.length ?? 3, duplicates: 0, errors: [] },
  },
});

function createSubmit({ mode = "manual", ids = ["trace-0"], respond }) {
  const scope = {
    queueId: "queue-traces",
    sourceType: "trace",
    selectionMode: mode,
    selectedIds: new Set(ids),
    selectAllInfo:
      mode === "selectAll"
        ? {
            projectId,
            totalCount: 3,
            filters,
            excludedIds: new Set(),
          }
        : null,
    selectedProjectId: projectId,
    isVoiceTraceSelection: false,
    datasetSelectionRevision: { current: 0 },
    addItems: vi.fn((payload, { onSuccess }) => {
      onSuccess(respond(payload));
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

const submitPaths = [
  { path: "all-matching filter request", mode: "selectAll", ids: [] },
  { path: "single request", mode: "manual", ids: ["trace-0"] },
  {
    path: "batched requests",
    mode: "manual",
    ids: Array.from({ length: 501 }, (_, index) => `trace-${index}`),
  },
];

function expectSelectionKept(scope) {
  expect(scope.onClose).not.toHaveBeenCalled();
  expect(scope.resetSelection).not.toHaveBeenCalled();
  expect(scope.setSourceType).not.toHaveBeenCalled();
}

// W10-ANNOTATIONS-U08: when the server adds nothing, closing the drawer and
// clearing the selection throws away the user's work. Keep both and say why.
describe("W10-ANNOTATIONS-U08 Add Items when nothing was added", () => {
  describe.each(submitPaths)("$path", ({ mode, ids }) => {
    it("keeps the drawer and selection when every item is already queued", async () => {
      const { scope, submit } = createSubmit({
        mode,
        ids,
        respond: duplicatesOnly,
      });

      await submit();

      expect(scope.addItems).toHaveBeenCalled();
      expectSelectionKept(scope);
      expect(scope.enqueueSnackbar).toHaveBeenCalledExactlyOnceWith(
        expect.stringContaining("already in the queue"),
        { variant: "info" },
      );
    });

    it("keeps the drawer and selection when every item fails", async () => {
      const { scope, submit } = createSubmit({
        mode,
        ids,
        respond: errorsOnly,
      });

      await submit();

      expect(scope.addItems).toHaveBeenCalled();
      expectSelectionKept(scope);
      expect(scope.enqueueSnackbar).toHaveBeenCalledExactlyOnceWith(
        expect.stringContaining("Not found: trace=trace-0"),
        { variant: "error" },
      );
    });

    it("still closes and clears after items were added", async () => {
      const { scope, submit } = createSubmit({
        mode,
        ids,
        respond: allAdded,
      });

      await submit();

      expect(scope.onClose).toHaveBeenCalledOnce();
      expect(scope.resetSelection).toHaveBeenCalledOnce();
      expect(scope.setSourceType).toHaveBeenCalledWith(null);
    });
  });

  it("says why nothing was added when the server reports no matches", () => {
    const { message, variant } = addResultToast({
      added: 0,
      duplicates: 0,
      errors: [],
    });

    expect(variant).toBe("warning");
    expect(message).toMatch(/nothing in the selection matched/i);
    expect(message).toMatch(/change the selection and try again/i);
  });
});
