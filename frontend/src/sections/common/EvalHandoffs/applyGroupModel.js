// HO D9 · apply an eval group to a dataset (Data tab) or an experiment
// (experiment page): POST eval-groups/apply-eval-group/ (backend
// services/eval_group.py apply; DATASET is atomic, EXPERIMENT adds each
// member and undoes all when one is refused). Pure; ApplyEvalGroupDialog
// renders it. And D9's chips: a member added on New experiment carries the
// same fields the EvalPicker's own add does (output type, the pinned version).
import { versionPinOf } from "src/sections/common/EvalPicker/allowNaModel";
import { getEvalRequiredKeys } from "src/sections/common/EvalPicker/evalPickerValue";
import { autoMapEvalKeys } from "src/sections/develop-detail/Experiment/variableMapping";

export const APPLY_PAGE = Object.freeze({
  dataset: "DATASET",
  experiment: "EXPERIMENT",
});

// The mapping value an experiment eval reads each run's stored output by
// (New experiment's own value; its label there is "Run output").
export const RUN_OUTPUT = "output";
export const RUN_OUTPUT_LABEL = "Run output";

/**
 * On the experiment page, the keys that read each run's stored output, fixed
 * to it (`{key: "output"}`): the experiment step's own rule
 * (variableMapping.autoMapEvalKeys maps output and its synonyms to it). On a
 * dataset nothing is fixed; every key maps to a column.
 */
export function runOutputMapping(keys = [], page) {
  if (page !== "experiment") return {};
  return Object.fromEntries(
    (keys ?? [])
      .filter((k) => autoMapEvalKeys([k], [])[k] === RUN_OUTPUT)
      .map((k) => [k, RUN_OUTPUT]),
  );
}

const uniq = (xs) => [...new Set(xs)];

/** The keys the selected members read: required (any member) and optional. */
export function groupKeys(members = [], deselected = []) {
  const off = new Set(deselected);
  const on = (members ?? []).filter((m) => !off.has(m.eval_template_id));
  const required = uniq(on.flatMap((m) => m.required_keys ?? []));
  const optional = uniq(on.flatMap((m) => m.optional_keys ?? [])).filter(
    (k) => !required.includes(k),
  );
  return { required, optional };
}

// A key's column by its role when no saved eval maps it.
const ROLE_OF_KEY = [
  [/^(output|response|answer|completion|generated)/i, "output"],
  [/^(expected|reference|ground_?truth|target|label)/i, "expected"],
  [/^(input|query|question|prompt|message)/i, "input"],
];

/**
 * The mapping the dialog starts from: what the dataset's saved evals map
 * each key to (one mapping per dataset), else the one column of the key's
 * role. Values are column ids; a key nothing fits stays unmapped.
 * @param {string[]} keys
 * @param {Array<{id, name, role}>} columns
 * @param {Array<{mapping}>} savedEvals  get_evals_list items (ids or names)
 */
export function autoMapping(keys = [], columns = [], savedEvals = []) {
  const idOf = (v) =>
    columns.find((c) => c.id === v)?.id ??
    columns.find((c) => c.name === v)?.id ??
    null;
  const out = {};
  keys.forEach((k) => {
    for (const e of savedEvals ?? []) {
      const id = idOf(e?.mapping?.[k]);
      if (id) {
        out[k] = id;
        return;
      }
    }
    const role = ROLE_OF_KEY.find(([re]) => re.test(k))?.[1];
    const ofRole = role ? columns.filter((c) => c.role === role) : [];
    if (ofRole.length === 1) out[k] = ofRole[0].id;
  });
  return out;
}

/** Why Apply is off, or null. */
export function applyBlocked({ members = [], deselected = [], mapping = {} }) {
  const off = new Set(deselected);
  if (!members.some((m) => !off.has(m.eval_template_id)))
    return "Select at least one evaluation in the group to apply.";
  const missing = groupKeys(members, deselected).required.filter(
    (k) => !mapping[k],
  );
  if (missing.length)
    return `Map ${missing.join(", ")} to ${missing.length === 1 ? "a column" : "columns"}.`;
  return null;
}

/** The apply-eval-group body. */
export function applyBody({
  groupId,
  page,
  datasetId,
  experimentId,
  mapping = {},
  deselected = [],
}) {
  const pageId = APPLY_PAGE[page];
  return {
    eval_group_id: groupId,
    page_id: pageId,
    filters:
      pageId === "EXPERIMENT"
        ? { experiment_id: experimentId }
        : { dataset_id: datasetId },
    mapping: Object.fromEntries(
      Object.entries(mapping).filter(([, v]) => Boolean(v)),
    ),
    deselected_evals: deselected,
  };
}

/** The toast after an apply. */
export function appliedText(groupName, n, page) {
  const what = `${n} evaluation${n === 1 ? "" : "s"}`;
  return page === "experiment"
    ? `Added ${what} from ${groupName} · scored on this experiment's runs`
    : `Added ${what} from ${groupName} to this dataset · Run them to score the rows`;
}

/**
 * D9 · the New experiment step's add for one group member: the EvalPicker's
 * own payload (EvalPickerConfigFull onSave) so the card carries the same
 * chips: the raw template (output type), its keys, and the version the
 * group add used, pinned (W1-3 `versionPinLabel`, `config.version_pin`).
 * @param {object} tpl  the template as the picker reads it (evalDetailQuery)
 * @param {Array<{id, version_number, is_default}>} versions  newest first
 */
export function pickerAddOf(tpl, versions = []) {
  const current =
    (versions ?? []).find((v) => v.is_default) ?? versions?.[0] ?? null;
  const pin = current ? versionPinOf(current.id, versions) : {};
  const templateId = tpl?.id ?? tpl?.template_id ?? null;
  return {
    templateId,
    evalTemplateId: templateId,
    name: tpl?.name ?? "",
    mapping: {},
    evalTemplate: tpl,
    evalType: tpl?.eval_type ?? tpl?.evalType ?? "llm",
    templateType: tpl?.template_type ?? tpl?.templateType ?? "single",
    outputType: tpl?.output_type ?? tpl?.outputType ?? null,
    requiredKeys: getEvalRequiredKeys(tpl),
    ...(tpl?.model ? { model: tpl.model } : {}),
    ...pin,
    config: {
      ...(tpl?.config ?? {}),
      ...(pin.versionPin ? { version_pin: pin.versionPin } : {}),
    },
    versionId: current?.id ?? null,
  };
}
