import { PERMISSIONS, RolePermission } from "src/utils/rolePermissionMapping";

export function getClonedDatasetInfo(response) {
  const result = response?.data?.result || response?.result || {};
  return {
    datasetId: result.dataset_id || result.datasetId || result.id || null,
    datasetName:
      result.dataset_name || result.datasetName || result.name || null,
  };
}

const plural = (n, word) => `${n} ${word}${n === 1 ? "" : "s"}`;

/**
 * The workspaces a dataset can be copied or moved to: the ones whose role
 * may create datasets (RolePermission.DATASETS.create), as {id, name}.
 */
export function workspacesYouCanEdit(rows = []) {
  const canCreate = RolePermission.DATASETS[PERMISSIONS.CREATE];
  return (rows ?? [])
    .filter((w) => w?.id && canCreate[w.user_ws_role ?? w.role] === true)
    .map((w) => ({ id: w.id, name: w.display_name || w.name || w.id }));
}

/**
 * The clone-dataset form: today's model_type + new_dataset_name, and the
 * transfer keys (to_workspace_id, mode, history, include_experiments,
 * split_id, dry_run).
 */
export function transferForm({
  modelType,
  name,
  toWorkspaceId,
  mode = "copy",
  history = "latest",
  includeExperiments = false,
  splitId,
  dryRun = false,
} = {}) {
  const form = new FormData();
  if (modelType) form.append("model_type", modelType);
  if (name) form.append("new_dataset_name", name);
  if (toWorkspaceId) form.append("to_workspace_id", toWorkspaceId);
  form.append("mode", mode === "move" ? "move" : "copy");
  form.append("history", history === "all" ? "all" : "latest");
  form.append("include_experiments", includeExperiments ? "true" : "false");
  if (splitId) form.append("split_id", splitId);
  if (dryRun) form.append("dry_run", "true");
  return form;
}

/** `Will copy 6 versions · 248 rows · 4 splits · 10 runs into Billing Agents`. */
export function planSentence(result, { move = false, workspace } = {}) {
  const p = result?.plan;
  if (!p) return "";
  const parts = [
    plural(p.versions, "version"),
    plural(p.rows, "row"),
    p.splits ? plural(p.splits, "split") : null,
    p.runs ? plural(p.runs, "run") : null,
  ].filter(Boolean);
  return `Will ${move ? "move" : "copy"} ${parts.join(" · ")} into ${workspace}`;
}

/**
 * The runs a copy leaves behind because they had not ended (the plan's
 * runs_left_out, of them runs_waiting_left_out waiting for results), in one
 * sentence; [] when every run is copied.
 */
export function planNotes(result) {
  const p = result?.plan;
  const left = p?.runs_left_out ?? 0;
  if (!left) return [];
  const waiting = Math.min(left, p.runs_waiting_left_out ?? 0);
  const parts = [
    left - waiting ? `${plural(left - waiting, "run")} still running` : null,
    waiting ? `${plural(waiting, "run")} waiting for results` : null,
  ].filter(Boolean);
  const one = left === 1;
  return [
    `${parts.join(" and ")} ${one ? "is" : "are"} not copied. Copy again once ${one ? "it finishes" : "they finish"} to include ${one ? "it" : "them"}.`,
  ];
}

/** Every run the dataset has (the copied ones and the ones left behind). */
export const runsInDataset = (result) =>
  result?.plan?.runs == null
    ? null
    : result.plan.runs + (result.plan.runs_left_out ?? 0);
