// What an Edit save changes of an experiment's run settings: its metadata,
// its error policy and its per-row limits, which the server saves for the
// experiment's next run (ExperimentUpdateV2; finished runs keep what they
// ran with). Pure.
//   - The Edit form opens with what the experiment holds
//     (withStoredRunSettings), and keeps it as `runSettingsBase`. A prompt
//     or agent run added on Edit starts with the stored limits
//     (cardAddedOnEdit).
//   - A setting travels only when the person changed it from that
//     (withEditRunSettings); a cleared one is a change too. serverBody.js
//     sends it where the edit contract declares it and blocks Save, with
//     the reason, where it does not.
//   - Each Edit control is open only where a change to its setting can be
//     sent (editSettingLocked).
//   - The rows an experiment runs on (its sample) are chosen when it is
//     created: the sample shows on Edit, read-only, and is never sent.
// A form without `runSettingsBase` (a create, a detail that carries no run
// settings) sends none of them on an edit, as before.
import { acceptedFields } from "src/api/contracts/strictBody";
import { UNAVAILABLE_ROW_LIMITS } from "src/sections/common/RunSettings/rowLimitsModel";
import { metadataObject } from "./experimentFormValues";

export const EDIT_RUN_SETTINGS = Object.freeze([
  "metadata",
  "error_policy",
  "limits",
]);

/** The server's words for a sample on an edit (SAMPLE_FIXED_MESSAGE). */
export const SAMPLE_FIXED_TEXT =
  "An experiment's rows are chosen when it is created, so its sample can't change. Create a new experiment to sample other rows.";

// Metadata keys the form does not show as rows: kept as stored on a save.
const HIDDEN_METADATA_KEYS = Object.freeze(["source"]);

const hiddenMetadataOf = (stored) =>
  Object.fromEntries(
    Object.entries(stored ?? {}).filter(([key]) =>
      HIDDEN_METADATA_KEYS.includes(key),
    ),
  );

const UPDATE_BODY = "ExperimentUpdateV2";

/** Whether an Edit save can change a run setting: the edit contract declares it. */
export function editSavesSetting(field) {
  return acceptedFields(UPDATE_BODY).includes(field);
}

/**
 * Whether a run setting's control is read-only: on an Edit that could not
 * send a change to it, because the edit contract does not declare it or the
 * form holds no stored value of it to compare with (`base`, the form's
 * runSettingsBase: absent when the detail carries no run settings; without
 * `limits` when the stored ones hold a limit this release refuses).
 */
export function editSettingLocked(
  field,
  { isEditing = false, base = null } = {},
) {
  if (!isEditing) return false;
  return !base || base[field] === undefined || !editSavesSetting(field);
}

const policyOf = (v) => (v === "zero" || v === "exclude" ? v : null);

const plainObject = (v) =>
  v && typeof v === "object" && !Array.isArray(v) ? { ...v } : {};

// JSON with sorted object keys, so two equal settings compare equal.
const keyOf = (value) =>
  JSON.stringify(value ?? null, (_, v) =>
    v && typeof v === "object" && !Array.isArray(v)
      ? Object.fromEntries(
          Object.entries(v).sort(([a], [b]) => a.localeCompare(b)),
        )
      : v,
  );

// The cards whose runs carry per-row limits (RunSettings/cardExtras).
const takesRowLimits = (card) => {
  const kind = card?.targetType ?? (card?.agentId ? "agent" : "prompt");
  return kind === "prompt" || kind === "agent";
};

/**
 * The Edit form with the run settings the experiment holds for its next run
 * (its detail's metadata, error_policy, limits and sample; an absent one is
 * the default), and those settings as `runSettingsBase`, as the form shows
 * them, so an Edit that changes nothing sends nothing. A detail that
 * carries no run settings (its server has them off) leaves the form as it
 * was. Stored limits that hold one this release refuses (from before it
 * refused them) stay as they are: they are not put on the runs, their
 * control stays read-only and an Edit never sends limits.
 * @param {object} values  the form values built from the detail
 * @param {object} detail  the experiment detail (GET experiments/v2/{id}/)
 */
export function withStoredRunSettings(values, detail) {
  if (!values || !detail || !Object.hasOwn(detail, "metadata")) return values;
  const stored = plainObject(detail.metadata);
  const rows = Object.entries(stored)
    .filter(([key]) => !HIDDEN_METADATA_KEYS.includes(key))
    .map(([key, value]) => ({ key, value: String(value ?? "") }));
  const limits = plainObject(detail.limits);
  const keptAsStored = Object.keys(limits).some((key) =>
    UNAVAILABLE_ROW_LIMITS.includes(key),
  );
  const sample = detail.sample ?? detail.run_scope?.sample ?? null;
  return {
    ...values,
    metadata: rows,
    errorPolicy: policyOf(detail.error_policy),
    // One experiment-wide policy: every run of the form shows it.
    promptConfig: (values.promptConfig ?? []).map((card) =>
      !keptAsStored && takesRowLimits(card) && Object.keys(limits).length
        ? { ...card, rowLimits: { ...limits } }
        : card,
    ),
    ...(sample
      ? {
          sample: {
            on: true,
            n: sample.size ?? sample.n ?? null,
            seed: sample.seed ?? 0,
          },
        }
      : {}),
    runSettingsBase: {
      metadata: { ...hiddenMetadataOf(stored), ...metadataObject(rows) },
      error_policy: policyOf(detail.error_policy),
      ...(keptAsStored ? {} : { limits }),
    },
  };
}

/**
 * A run card added on Edit, with the per-row limits the experiment holds
 * (`base.limits`), so the new run carries the one experiment-wide policy
 * the runs already there carry, and Save is not held on limits the person
 * never set. Only a prompt or agent card that carries no limits of its own
 * takes them, as a copy; any other card, and a form whose base holds no
 * limits (a create, a detail without run settings, stored limits this
 * release refuses), gets the same card back. A limit cleared on the card
 * afterwards is cleared as on any run.
 * @param {object} card  the card being added
 * @param {object} base  the form's runSettingsBase
 */
export function cardAddedOnEdit(card, base) {
  if (!card || card.rowLimits != null || !takesRowLimits(card)) return card;
  const limits = plainObject(base?.limits);
  return Object.keys(limits).length ? { ...card, rowLimits: limits } : card;
}

/**
 * The one set of per-row limits every entry carries ({} when none does), or
 * undefined when the entries differ (the server has one policy) or there is
 * no entry to read them from.
 */
function sharedRowLimits(entries) {
  const all = entries.map((entry) => entry?.row_limits ?? {});
  if (!all.length) return undefined;
  return all.every((limits) => keyOf(limits) === keyOf(all[0]))
    ? { ...all[0] }
    : undefined;
}

/**
 * An Edit body with the run settings the person changed from
 * `values.runSettingsBase`: `metadata` (the rows, with the stored keys the
 * form does not show), `error_policy` (null returns it to the default) and
 * `limits` (the time limit on every run; {} clears it). Limits that differ
 * between runs stay on each run, where Save says why; otherwise no run
 * entry carries them. Never a sample. The body is returned as it was when
 * the form has no base.
 * @param {object} body    experimentBody()'s edit body
 * @param {object} values  the form values (getValues())
 */
export function withEditRunSettings(body, values) {
  const base = values?.runSettingsBase;
  if (!base) return body;
  const out = { ...body };
  const stored = plainObject(base.metadata);
  const metadata = {
    ...hiddenMetadataOf(stored),
    ...metadataObject(Array.isArray(values?.metadata) ? values.metadata : []),
  };
  if (keyOf(metadata) !== keyOf(stored)) out.metadata = metadata;
  const policy = policyOf(values?.errorPolicy);
  if (policy !== policyOf(base.error_policy)) out.error_policy = policy;
  // Stored limits this Edit cannot show stay as they are.
  if (base.limits === undefined) return out;
  const entries = Array.isArray(body?.prompt_config) ? body.prompt_config : [];
  const limits = sharedRowLimits(entries);
  if (limits === undefined) return out;
  if (keyOf(limits) !== keyOf(plainObject(base.limits))) out.limits = limits;
  out.prompt_config = entries.map(({ row_limits: _limits, ...entry }) => entry);
  return out;
}
