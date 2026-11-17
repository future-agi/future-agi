import { apiPath } from "src/api/contracts/api-surface";
import axios from "src/utils/axios";

const plural = (n, word) => `${n} ${word}${n === 1 ? "" : "s"}`;

// Workspace Member (3) and above may add datasets; the list reports the
// caller's level in each workspace as user_ws_level.
const WORKSPACE_MEMBER_LEVEL = 3;

/** The workspaces the caller can add datasets to, as {id, name}. */
export function writableWorkspaces(rows = []) {
  return (rows ?? [])
    .filter((w) => w?.id && (w.user_ws_level ?? 0) >= WORKSPACE_MEMBER_LEVEL)
    .map((w) => ({ id: w.id, name: w.display_name || w.name || w.id }));
}

export const datasetTransferUrl = (datasetId) =>
  apiPath("/model-hub/datasets/{dataset_id}/transfer/", {
    dataset_id: datasetId,
  });

/** The copy request, which may carry a name. The backend refuses moves. */
export function datasetTransferBody({ toWorkspaceId, name, dryRun }) {
  const body = {
    to_workspace_id: toWorkspaceId,
    mode: "copy",
    dry_run: Boolean(dryRun),
  };
  const trimmed = typeof name === "string" ? name.trim() : "";
  if (trimmed) body.new_dataset_name = trimmed;
  return body;
}

/** POST the copy (or its dry run); resolves with the result. */
export const postDatasetTransfer = (datasetId, options) =>
  axios
    .post(datasetTransferUrl(datasetId), datasetTransferBody(options))
    .then((res) => res.data?.result ?? null);

/** `Will copy 2 rows · 1 column into Billing as “Copy of Support”`. */
export function transferPlanSentence(result) {
  const plan = result?.plan;
  if (!plan) return "";
  const where = result.to_workspace?.name ?? "the other workspace";
  const what = `${plural(plan.rows, "row")} · ${plural(plan.columns, "column")}`;
  return `Will copy ${what} into ${where} as “${result.dataset_name}”.`;
}

/** What the preview leaves out, one sentence each; [] when nothing is. */
export function transferPlanNotes(result) {
  const plan = result?.plan;
  if (!plan) return [];
  const notes = [];
  if (plan.columns_left_out) {
    const one = plan.columns_left_out === 1;
    notes.push(
      `${plural(plan.columns_left_out, "generated column")} ${one ? "is" : "are"} not copied.`,
    );
  }
  return notes;
}
