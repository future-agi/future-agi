// W6 C3 (W6-17): an LLM judge that cannot read the media its eval is mapped
// to is refused before the run (pure; the Evaluations step shows it and the
// create path refuses the same list). What a model reads is its
// `capabilities.inputs` from the models list (modelInputs `inputsByModel`);
// a judge whose inputs are unknown is not checked rather than guessed. The
// modality of a mapped column is the mapping's own rule (variableMapping
// `mappedModality`). Code evals have no judge and read no media.
import { mappedModality } from "src/sections/develop-detail/Experiment/variableMapping";
import { judgeModelOf } from "src/sections/develop-detail/Experiment/estimateInputs";

const READS = Object.freeze({
  image: "images",
  audio: "audio",
  video: "video",
  document: "documents",
});
const CODE_TYPES = new Set(["code", "function", "deterministic"]);

const nameOf = (e) =>
  e?.evalTemplateName ?? e?.name ?? e?.templateDetails?.name ?? "This eval";
const isCode = (e) =>
  CODE_TYPES.has(
    String(
      e?.templateType ??
        e?.template_type ??
        e?.templateDetails?.eval_type ??
        "",
    ).toLowerCase(),
  );
const mappingOf = (e) => e?.config?.mapping ?? e?.mapping ?? {};

/**
 * The judges that cannot read their mapped media.
 * @param {object[]} evals  the Evaluations step's eval entries
 * @param {object[]} columns  the dataset columns (`id`, `name`, `data_type`)
 * @param {Record<string, string[]>} inputsByName  `{model: inputs}`
 * @param {{defaultJudge?: string}} [opts]  the judge an eval without its
 *   own model is scored by
 * @returns {Array<{eval, judge, column, modality, suggest, text}>}
 */
export function judgeMediaWarnings(
  evals = [],
  columns = [],
  inputsByName = {},
  { defaultJudge = null } = {},
) {
  const out = [];
  const byName = inputsByName ?? {};
  (evals ?? []).forEach((e) => {
    if (!e || isCode(e)) return;
    const judge = judgeModelOf(e) ?? defaultJudge;
    const inputs = judge ? byName[judge] : null;
    if (!inputs?.length) return;
    Object.values(mappingOf(e)).forEach((value) => {
      const modality = mappedModality(value, columns);
      if (!modality || inputs.includes(modality)) return;
      const column =
        (columns ?? []).find((c) => c.id === String(value))?.name ?? value;
      const suggest =
        Object.entries(byName).find(([, ins]) => ins.includes(modality))?.[0] ??
        null;
      const pick = suggest
        ? `Pick a judge that reads ${READS[modality] ?? modality} (${suggest}) or map a transcript`
        : "Map a transcript";
      out.push({
        eval: nameOf(e),
        judge,
        column,
        modality,
        suggest,
        text: `${nameOf(e)}'s judge ${judge} cannot read ${modality}: ${column} is ${modality}. ${pick}`,
      });
    });
  });
  return out;
}

/** The refusal sentence the create path answers with (first warning). */
export const judgeMediaRefusal = (warnings = []) =>
  warnings.length ? warnings[0].text : null;
