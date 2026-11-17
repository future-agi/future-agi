// L7c g7: what a wizard eval card shows and what its Test sends.
//
// cardChipsOf (wiz:card-chips): a card's chips from the card, else from its
// template (a card added from the picker, a group or a draft carries none of
// its own). A pass/fail eval shows no "Pass at ≥" bar (its verdict is the
// judge's pass or fail); its judge shows on its own.
//
// cardTestBodyOf (test:card): the eval-playground body of a card's Test on
// the sample row: the card's whole definition (bar, output type, prompt,
// N/A switch, error localization, few-shot examples, composite weights, the
// eval agent's options), so the one scorer scores what the run will.
import { isDecisionModel } from "src/sections/common/DecisionJudges/decisionModels";
import { isPrototypeMode } from "src/utils/prototype";

const isPassFail = (t) => /^pass[_/ ]?fail$/i.test(String(t ?? ""));

const versionLabel = (v) => {
  if (v == null || v === "") return null;
  const s = String(v);
  return /^v/i.test(s) ? `v${s.slice(1)}` : `v${s}`;
};

/**
 * @param {object} item     the card (form value)
 * @param {object} [detail] its template's detail (eval-templates/{id}/detail)
 * @returns {{outputType: string|null, passAt: number|null, judge: string|null,
 *   higherIsBetter: boolean|null, version: string|null}}
 */
export function cardChipsOf(item = {}, detail = null) {
  const outputType =
    item.outputType ?? item.output_type ?? detail?.output_type ?? null;
  const passFail = isPassFail(outputType);
  const bar =
    item.config?.run_config?.pass_threshold ??
    item.passThreshold ??
    item.config?.threshold ??
    item.config?.pass_threshold ??
    detail?.pass_threshold ??
    null;
  const model = item.model ?? null;
  return {
    outputType,
    passAt:
      passFail || bar == null || !Number.isFinite(Number(bar))
        ? null
        : Number(bar),
    judge: model && !isDecisionModel(model) ? model : null,
    higherIsBetter: item.higherIsBetter ?? null,
    version: item.version ?? versionLabel(detail?.current_version),
  };
}

/** The eval-playground body of a card's Test on one row. */
export function cardTestBodyOf({
  templateId,
  model,
  mapping,
  columnMapping,
  threshold,
  outputType,
  datasetId,
  rowId,
  output,
  decision,
  instructions,
  card = null,
}) {
  const cfg = card?.config ?? {};
  const runConfig = cfg.run_config ?? null;
  const errorLocalizer = Boolean(
    card?.errorLocalizer ??
      card?.error_localizer ??
      cfg.error_localizer_enabled ??
      cfg.error_localizer,
  );
  const fewShot = Array.isArray(cfg.few_shot_examples)
    ? cfg.few_shot_examples
    : [];
  return {
    template_id: templateId,
    model: model || "turing_large",
    error_localizer: errorLocalizer,
    config: {
      mapping: mapping ?? {},
      ...(threshold != null ? { threshold } : {}),
      ...(outputType ? { output_type: outputType } : {}),
      ...(decision ? { decision } : {}),
      ...(instructions ? { rule_prompt: instructions } : {}),
      ...(isPrototypeMode() && cfg.allow_na != null
        ? { allow_na: Boolean(cfg.allow_na) }
        : {}),
      ...(fewShot.length ? { few_shot_examples: fewShot } : {}),
      ...(cfg.composite_weight_overrides
        ? { composite_weight_overrides: cfg.composite_weight_overrides }
        : {}),
      ...(runConfig && Object.keys(runConfig).length
        ? { run_config: runConfig }
        : {}),
    },
    row_context: {
      source: "experiment_create",
      dataset_id: datasetId,
      row_id: rowId,
      output,
      ...(columnMapping ? { column_mapping: columnMapping } : {}),
    },
  };
}

const escapeRe = (s) => String(s).replace(/[.*+?^${}()|[\]\\]/g, "\\$&");

/**
 * L7c g7 (wiz:add-evaluation): the reference column an eval's definition
 * names ("Match between the tool calls made and expected_tool_calls"): a
 * dataset column whose name the description states and that holds a
 * reference (expected / reference / ground truth), else null.
 * @param {string} description
 * @param {{id: string, name: string}[]} columns
 */
export function namedReferenceColumnOf(description, columns = []) {
  const text = String(description ?? "");
  if (!text) return null;
  return (
    columns.find(
      (c) =>
        c?.name &&
        /expected|reference|ground_?truth/i.test(c.name) &&
        new RegExp(`(^|[^\\w])${escapeRe(c.name)}($|[^\\w])`).test(text),
    ) ?? null
  );
}

/**
 * L7c g7 (byok:settings-page): a refused Test in words: the server's
 * message without its machine code prefix ("provider_not_configured: Add
 * your OpenAI key ..." -> "Add your OpenAI key ...").
 * @param {string} message  the request's error message
 * @param {string|null} code the response's `code`
 */
export function refusalTextOf(message, code) {
  const text = String(message ?? "");
  if (!code) return text;
  const prefix = `${code}:`;
  return text.startsWith(prefix) ? text.slice(prefix.length).trim() : text;
}
