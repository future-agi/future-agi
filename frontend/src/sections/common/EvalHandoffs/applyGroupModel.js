// HO D9 · apply an eval group to a dataset (Data tab) or an experiment
// (experiment page): POST eval-groups/apply-eval-group/ (backend
// services/eval_group.py apply; DATASET is atomic, EXPERIMENT adds each
// member and undoes all when one is refused). Pure; ApplyEvalGroupDialog
// renders it. And D9's chips: a member added on New experiment carries the
// same fields the EvalPicker's own add does (output type, the pinned version).
import { versionPinOf } from "src/sections/common/EvalPicker/allowNaModel";
import { getEvalRequiredKeys } from "src/sections/common/EvalPicker/evalPickerValue";
import { autoMapEvalKeys } from "src/sections/develop-detail/Experiment/variableMapping";
import { evaluatorModelLabel } from "src/sections/evals/components/evaluatorModels";
import { RESPONSE_CODES } from "src/utils/constants";
import { getInlineActionErrorMessage } from "src/utils/errorUtils";

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

/** A model by the name the model selector shows it, else by its id. */
const modelName = (model) => evaluatorModelLabel(model) ?? model;

// Where the server's refusal names a model: "its model, X, can't run", and
// for a built-in evaluation, "… uses X, which can't run here" (X taken after
// the last " uses " so an evaluation's own name is never read as a model).
const MODEL_IN_REFUSAL = [
  /(its model, )([^,]+)(, can't run)/g,
  /( uses )((?:(?! uses )[^,])+)(, which can't run here)/g,
];
const nameModel = (_, before, model, after) =>
  `${before}${modelName(model)}${after}`;

/**
 * The server's refusal text with each model it names shown by the model
 * selector's name (`jev-latest` as "Jev latest"); every other word is the
 * server's.
 */
export function withModelNames(text) {
  let named = String(text ?? "");
  for (const pattern of MODEL_IN_REFUSAL) {
    named = named.replace(pattern, nameModel);
  }
  return named;
}

// A refusal's code, sent beside its sentence: a built-in evaluation's own
// model ("built_in"), or the group's or the evaluation's model ("model"). A
// place tells them apart by the code, never by the sentence's words.
const BUILT_IN_REFUSAL = "built_in";

/**
 * The action the Apply dialog offers a built-in evaluation left out, after
 * its reason: before Apply under its row, and in the toast when every member
 * is refused (the dialog stays open). After a partial success nothing is
 * added anywhere: the rest was applied.
 */
export const IN_APPLY_DIALOG = Object.freeze({
  builtInAction: "Clear its checkbox, or choose another group.",
});

/**
 * A drawer's action for a built-in evaluation left out when every member is
 * refused (its form stays open), only while the member's chip shows its
 * remove; otherwise the reason alone.
 */
export function inDrawer(memberRemovable) {
  return {
    builtInAction: memberRemovable ? "Remove it from the group." : null,
  };
}

/**
 * The line under a member's name in the Apply dialog. The group read says,
 * per member, the model Apply uses when no model is sent (`apply_model`:
 * the template's own model, else Add evaluation's default), or why Apply
 * leaves the member out (`apply_refusal`: "Won't be added: …"); a built-in
 * evaluation's (`apply_refusal_code` "built_in") then gets the dialog's
 * action. Null when it says neither.
 */
export function memberModelLine(member) {
  if (member?.apply_refusal) {
    const line = withModelNames(member.apply_refusal);
    if (member.apply_refusal_code !== BUILT_IN_REFUSAL) return line;
    return `${line} ${IN_APPLY_DIALOG.builtInAction}`;
  }
  if (member?.apply_model) return `Model: ${modelName(member.apply_model)}`;
  return null;
}

/**
 * How New experiment's Add a group says to add a custom evaluation it left
 * out: that door has no Apply, so the read's own action is not shown there.
 */
export const ADD_GROUP_AGAIN =
  "Change the evaluation's model, then add the group again.";

/**
 * The line New experiment's Add a group shows for a member it leaves out:
 * the group read's first sentence, with its model by the model selector's
 * name, then ADD_GROUP_AGAIN for a custom evaluation; a built-in
 * evaluation's sentence alone (its model can't be changed on its page).
 * Null when the read leaves the member in.
 */
export function groupAddRefusalLine(member) {
  const line = member?.apply_refusal;
  if (!line) return null;
  // The first sentence ends at its first ". " (a model id keeps its dots).
  const end = line.indexOf(". ");
  const first = withModelNames(end === -1 ? line : line.slice(0, end + 1));
  if (member.apply_refusal_code === BUILT_IN_REFUSAL) return first;
  return `${first} ${ADD_GROUP_AGAIN}`;
}

/**
 * Add a group's lines for the members it left out, each after the member's
 * name; null when it left none out.
 */
export function groupAddRefusalText(members = []) {
  const lines = (members ?? [])
    .map((m) => {
      const line = groupAddRefusalLine(m);
      return line ? `${m.name} · ${line}` : null;
    })
    .filter(Boolean);
  return lines.length ? lines.join(" ") : null;
}

/**
 * The members an apply left out (the response's `refused`:
 * `[{eval_template_id, name, reason, code}]`), each by the server's own
 * sentence and nothing more: after a partial success the rest was applied.
 * Null when none was.
 */
export function refusedText(refused = []) {
  const lines = (refused ?? []).map((r) => r?.reason).filter(Boolean);
  return lines.length ? withModelNames(lines.join(" ")) : null;
}

// One refused member's sentence, with the place's action after a built-in
// evaluation's.
const refusalSentence = (entry, builtInAction) =>
  entry?.code === BUILT_IN_REFUSAL && builtInAction
    ? `${entry.reason} ${builtInAction}`
    : entry?.reason;

/**
 * The toast when an apply is refused, at every door. A 400's text is the
 * server's own (when every member is left out, each member's sentence,
 * joined), so it is shown in full, past the 240 characters after which the
 * app's error handler says only "Something went wrong". When that 400 lists
 * the members (`refused`, each with its code), each sentence is shown with
 * `builtInAction` after a built-in evaluation's: the action the place
 * offers (IN_APPLY_DIALOG, inDrawer). The app's other rules stay: text that
 * is multiline or reads like internal detail, or a failure that is not a
 * refusal, says "Couldn't apply the group", and a refusal the workspace
 * recovery explains, its one calm line.
 */
export function applyErrorText(error, { builtInAction = null } = {}) {
  const refused = Array.isArray(error?.refused) ? error.refused : [];
  const listed = Number(error?.statusCode) === 400 && refused.length > 0;
  const source = listed
    ? { ...error, result: refusalsText(refused, builtInAction) }
    : error;
  return withModelNames(
    getInlineActionErrorMessage(source, "Couldn't apply the group", {
      maxLength: Infinity,
    }),
  );
}

function refusalsText(refused, builtInAction) {
  return refused
    .map((entry) => refusalSentence(entry, builtInAction))
    .filter(Boolean)
    .join(" ");
}

/**
 * Every door's onError for the apply (with meta.errorHandled): one error
 * toast, `applyErrorText`'s, with the place's `options` (IN_APPLY_DIALOG,
 * inDrawer). A 429 adds none, as the app's handler adds none for it.
 */
export function notifyApplyError(error, notify, options) {
  if (Number(error?.statusCode) === RESPONSE_CODES.LIMIT_REACHED) return;
  notify(applyErrorText(error, options), { variant: "error" });
}

/**
 * Every door that applies a group: after a success, one warning toast lists
 * the members it left out (`response` is the axios response). The members
 * added are handled by the door as before. True when it showed the toast.
 */
export function notifyRefused(response, notify) {
  const text = refusedText(response?.data?.refused);
  if (text) notify(text, { variant: "warning" });
  return Boolean(text);
}

/**
 * The toast after an apply. On an experiment, `run` is what the apply did
 * (the response's result.run): "started" the experiment's run, found a run
 * already going ("run_going": nothing was started or stopped), or could not
 * start one ("not_started").
 */
export function appliedText(groupName, n, page, run = "started") {
  const what = `${n} evaluation${n === 1 ? "" : "s"}`;
  if (page !== "experiment")
    return `Added ${what} from ${groupName} to this dataset · Run them to score the rows`;
  if (run === "run_going")
    return `Added ${what} from ${groupName} · Run them when the current run finishes`;
  if (run === "not_started")
    return `Added ${what} from ${groupName} · Run them to score this experiment's rows`;
  return `Added ${what} from ${groupName} · scored on this experiment's runs`;
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
