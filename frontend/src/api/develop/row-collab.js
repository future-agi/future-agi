// W4: row comments (W4-1), row assignments (W4-2) and translation
// (W4-8). Paths: endpoints.develop.w4 (MOCK/CONTRACT.md "W4 ·
// Collaboration, review and access"). Every limit the UI shows (comment
// length, reactions, rows per assign) comes from these responses.
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import axios, { endpoints } from "src/utils/axios";
import { isPendingRowId } from "src/sections/develop-detail/DataTab/Versions/pendingRows";
import { asOfVersionParam, rowViewKeys } from "./row-view";
import {
  ROW_ASSIGNING_NOT_OFFERED,
  ROW_COMMENTS_NOT_OFFERED,
  rowAssigningOffered,
  rowCommentsOffered,
} from "src/sections/common/Collab/collabOffer";
import { notOfferedLine } from "src/sections/common/Collab/notOffered";

const W4 = () => endpoints.develop.w4;
const serverRow = (datasetId, rowId) =>
  Boolean(datasetId && rowId) && !isPendingRowId(rowId);
const resultOf = (res) => res?.data?.result ?? res?.data ?? null;

// Where the deployment does not offer comments or assignments
// (collabOffer.js), no read or write reaches their routes: reads stay off
// whatever the caller asks, fetches answer null, writes are refused with
// the sentence the page shows.
const refused = (sentence) => () => Promise.reject(new Error(sentence));

export const collabKeys = {
  all: (datasetId) => ["develop", "row-collab", datasetId],
  comments: (datasetId, rowId, runId) => [
    ...collabKeys.all(datasetId),
    "comments",
    rowId,
    runId ?? null,
  ],
  summary: (datasetId, params) => [
    ...collabKeys.all(datasetId),
    "summary",
    params ?? {},
  ],
  assignments: (datasetId, params) => [
    ...collabKeys.all(datasetId),
    "assignments",
    params ?? {},
  ],
  translate: (datasetId, rowId) => [
    ...collabKeys.all(datasetId),
    "translate",
    rowId ?? null,
  ],
};

/**
 * The server's error text, for toasts: the axios layer rejects with the
 * response body (`{status: false, result: "…"}` or an access denial
 * `{error, missing_permission}`) plus `statusCode`.
 */
export const collabError = (error, fallback) =>
  error?.error ??
  (typeof error?.result === "string" ? error.result : null) ??
  error?.message ??
  fallback;

// ------------------------------------------------------------ comments

/**
 * GET …/rows/{row_id}/comments/?run_id= →
 * `{threads: [{root, replies}], open, limits, access, me}`.
 */
export function useRowComments(
  datasetId,
  rowId,
  { runId = null, enabled, ...options } = {},
) {
  // integration: as of a version the row is read as of it (a row deleted later).
  const version = asOfVersionParam();
  return useQuery({
    queryKey: [
      ...collabKeys.comments(datasetId, rowId, runId),
      ...(version != null ? [{ version }] : []),
    ],
    queryFn: () =>
      axios.get(W4().rowComments(datasetId, rowId), {
        params: {
          ...(runId ? { run_id: runId } : {}),
          ...(version != null ? { version } : {}),
        },
      }),
    select: resultOf,
    meta: { errorHandled: true },
    ...options,
    enabled: rowCommentsOffered() && (enabled ?? serverRow(datasetId, rowId)),
  });
}

/**
 * GET …/comments/?status&mentions&run_id → `{rows, row_ids, total_open,
 * open_rows, mentions_of_me, mention_row_ids}`: `total_open` open threads
 * on `open_rows` rows; `mentions_of_me` comments that mention you, on the
 * rows in `mention_row_ids`.
 */
export function useDatasetComments(
  datasetId,
  params = {},
  { enabled, ...options } = {},
) {
  return useQuery({
    queryKey: collabKeys.summary(datasetId, params),
    queryFn: () => axios.get(W4().datasetComments(datasetId), { params }),
    select: resultOf,
    meta: { errorHandled: true },
    ...options,
    enabled: rowCommentsOffered() && (enabled ?? Boolean(datasetId)),
  });
}

/** Read the summary now (fresh ids for a `system:row_ids` view). */
export const fetchDatasetComments = (datasetId, params = {}) =>
  rowCommentsOffered()
    ? axios.get(W4().datasetComments(datasetId), { params }).then(resultOf)
    : Promise.resolve(null);

const useInvalidateCollab = (datasetId, rowId) => {
  const qc = useQueryClient();
  return () => {
    qc.invalidateQueries({ queryKey: collabKeys.all(datasetId) });
    if (rowId)
      qc.invalidateQueries({
        queryKey: rowViewKeys.activity(datasetId, rowId),
      });
  };
};

/** POST a comment or reply: `{body, anchor?, run_id?, parent_id?}`. */
export function usePostComment(datasetId, rowId) {
  const done = useInvalidateCollab(datasetId, rowId);
  return useMutation({
    mutationFn: rowCommentsOffered()
      ? (body) =>
          axios.post(W4().rowComments(datasetId, rowId), body).then(resultOf)
      : refused(ROW_COMMENTS_NOT_OFFERED),
    onSuccess: done,
    meta: { errorHandled: true },
  });
}

/** PATCH `{body}` | `{resolved}` | `{react}` on one comment. */
export function usePatchComment(datasetId, rowId) {
  const done = useInvalidateCollab(datasetId, rowId);
  return useMutation({
    mutationFn: rowCommentsOffered()
      ? ({ commentId, patch }) =>
          axios
            .patch(W4().rowComment(datasetId, rowId, commentId), patch)
            .then(resultOf)
      : refused(ROW_COMMENTS_NOT_OFFERED),
    onSuccess: done,
    meta: { errorHandled: true },
  });
}

/** DELETE one comment. */
export function useDeleteComment(datasetId, rowId) {
  const done = useInvalidateCollab(datasetId, rowId);
  return useMutation({
    mutationFn: rowCommentsOffered()
      ? (commentId) =>
          axios
            .delete(W4().rowComment(datasetId, rowId, commentId))
            .then(resultOf)
      : refused(ROW_COMMENTS_NOT_OFFERED),
    onSuccess: done,
    meta: { errorHandled: true },
  });
}

// --------------------------------------------------------- assignments

/**
 * GET …/assignments/?assignee&status&run_id → `{assignments, row_ids,
 * counts: {open, overdue, done, unassigned}, open_rows, people: [{id, name,
 * open, rows}], limits, members, me}`: `counts.open` assignments (a row and
 * each of its result rows count one each) on `open_rows` rows.
 */
export function useAssignments(
  datasetId,
  params = {},
  { enabled, ...options } = {},
) {
  return useQuery({
    queryKey: collabKeys.assignments(datasetId, params),
    queryFn: () => axios.get(W4().assignments(datasetId), { params }),
    select: resultOf,
    meta: { errorHandled: true },
    ...options,
    enabled: rowAssigningOffered() && (enabled ?? Boolean(datasetId)),
  });
}

export const fetchAssignments = (datasetId, params = {}) =>
  rowAssigningOffered()
    ? axios.get(W4().assignments(datasetId), { params }).then(resultOf)
    : Promise.resolve(null);

/** POST `{row_ids, run_id?, assignee_id, note?, due_at?}`. */
export function useCreateAssignments(datasetId) {
  const done = useInvalidateCollab(datasetId);
  return useMutation({
    mutationFn: rowAssigningOffered()
      ? (body) => axios.post(W4().assignments(datasetId), body).then(resultOf)
      : refused(ROW_ASSIGNING_NOT_OFFERED),
    onSuccess: done,
    meta: { errorHandled: true },
  });
}

/** PATCH `{status}` | `{assignee_id}` | `{due_at}` | `{unassign: true}`. */
export function usePatchAssignment(datasetId) {
  const done = useInvalidateCollab(datasetId);
  return useMutation({
    mutationFn: rowAssigningOffered()
      ? ({ assignmentId, patch }) =>
          axios
            .patch(W4().assignment(datasetId, assignmentId), patch)
            .then(resultOf)
      : refused(ROW_ASSIGNING_NOT_OFFERED),
    onSuccess: done,
    meta: { errorHandled: true },
  });
}

// ----------------------------------------------------------- translate

// Where the deployment does not offer translation (notOffered.js), the
// translate read stays off whatever the caller asks and a translate is
// refused with the shared line, without a request.
const TRANSLATE = "dataset.collab.translate";

/**
 * GET …/translate/?row_id= → `{dataset_language, dataset_language_name,
 * fields: [{column_id, name, role, language, language_name, available}],
 * other_language_rows: {count, row_ids}, languages, model}`.
 */
export function useTranslateInfo(
  datasetId,
  rowId,
  { enabled, ...options } = {},
) {
  // integration review: as of a version a row's read names it (a row not in the
  // latest version answers with no fields, not a 404).
  const version = rowId ? asOfVersionParam() : null;
  return useQuery({
    queryKey: [
      ...collabKeys.translate(datasetId, rowId),
      ...(version != null ? [version] : []),
    ],
    queryFn: () =>
      axios.get(W4().translate(datasetId), {
        params: rowId
          ? { row_id: rowId, ...(version != null && { version }) }
          : {},
      }),
    select: resultOf,
    meta: { errorHandled: true },
    ...options,
    enabled:
      !notOfferedLine(TRANSLATE) &&
      (enabled ??
        (Boolean(datasetId) && (!rowId || serverRow(datasetId, rowId)))),
  });
}

/** POST `{row_id, column_ids, target}` → `{target, model, translations, cost_usd}`. */
export function useTranslate(datasetId) {
  const off = notOfferedLine(TRANSLATE);
  return useMutation({
    mutationFn: off
      ? refused(off)
      : (body) => axios.post(W4().translate(datasetId), body).then(resultOf),
    meta: { errorHandled: true },
  });
}
