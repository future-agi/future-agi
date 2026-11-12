// Hugging Face imports write rows immediately and load their values asynchronously.
// With the `ml` extra, configuration returns an immutable revision for create
// and add requests; without it, an import that names no revision is unpinned.
import { isPrototypeMode } from "src/utils/prototype";

/** The IM1 import preview exists only in prototype builds. */
export const hfPreviewAvailable = (prototype = isPrototypeMode()) =>
  Boolean(prototype);

/**
 * The revisions the Revision control may offer: the config answer's list in
 * a prototype build; none on the real backend (no route takes a revision).
 */
export function hfRevisionChoices(config, prototype = isPrototypeMode()) {
  if (!prototype) return [];
  return Array.isArray(config?.revisions) ? config.revisions : [];
}

/** The config request body: `revision` only where a route reads it. */
export function hfConfigBody(datasetPath, revision) {
  return {
    dataset_path: datasetPath,
    ...(revision ? { revision } : {}),
  };
}

/** Use the resolved commit, not a branch that could move while work is queued. */
export const hfRequestRevision = (config, prototype = isPrototypeMode()) =>
  prototype ? config?.revision : config?.dataset_info?.revision;

/**
 * Whether a create / add may be sent for this config answer. A real answer
 * pins a resolved commit when the `ml` extra is installed. Without it, a
 * request that names no revision gets the base's HTTP-only answer (splits,
 * no revision), and the import runs without a revision, as at the base.
 */
export function hfRequestReady(
  config,
  requestedRevision,
  prototype = isPrototypeMode(),
) {
  if (prototype || config?.dataset_info?.revision) return true;
  const requested = String(requestedRevision ?? "").trim();
  return Boolean(config?.dataset_info) && !requested;
}

/** The revision fields of a create / add request; none when unpinned. */
export function hfRequestFields(config, prototype = isPrototypeMode()) {
  const revision = hfRequestRevision(config, prototype);
  if (prototype) return { huggingface_dataset_revision: revision };
  return revision ? { revision } : {};
}

/** The submit button of the drawer. */
export function hfSubmitLabel(targetDatasetId, prototype = isPrototypeMode()) {
  if (!targetDatasetId) return "Create dataset";
  return prototype ? "Add to pending changes" : "Add rows";
}

const plural = (n) =>
  `${n.toLocaleString("en-US")} ${n === 1 ? "row" : "rows"}`;

/** The row count a real add answered (`"3 Row(s) imported ..."`), or null. */
export function hfImportedCount(result) {
  const m = /^\s*(\d+)\s+row/i.exec(String(result?.message ?? ""));
  return m ? Number(m[1]) : null;
}

/**
 * What the drawer says after a real (non-prototype) add: the rows are in
 * the dataset now and their values load in the background. Never "pending
 * changes": nothing is pending, there is no Save version on this path.
 */
export function hfDirectAddToast(result, requested) {
  const n = hfImportedCount(result) ?? (Number(requested) || null);
  return n
    ? `${plural(n)} added to the dataset; their values load in the background`
    : "Rows added to the dataset; their values load in the background";
}

/** The line under an add on the real backend (no preview to read there). */
export const hfDirectAddHint =
  "The rows are added to this dataset now; their values load in the background.";
