// W6 C2 / C3: what the models in a run read, for the Evaluations step. The
// models list's `capabilities.inputs` (modelInputs, fetched once per page)
// and the judge-media rule (judgeMedia.js) give the step its refusal lines:
// "Correctness's judge gpt-4o-mini cannot read audio: call_audio is audio.
// Pick a judge that reads audio (gemini-2.5-pro) or map a transcript". The
// create path refuses the same list, so a run that would be judged on media
// its judge cannot read is never created.
import { useMemo } from "react";
import { useModelInputs } from "src/sections/develop-detail/Experiment/modelInputs";
import { judgeMediaWarnings } from "./judgeMedia";

/** True when any eval maps a column (so a judge could be sent media). */
const mapsColumns = (evals = []) =>
  (evals ?? []).some(
    (e) => Object.keys(e?.config?.mapping ?? e?.mapping ?? {}).length > 0,
  );

/**
 * The Evaluations step's judge-media warnings (loads the models list only
 * when an eval maps a column).
 */
export function useJudgeMediaWarnings(evals = [], columns = []) {
  const inputs = useModelInputs(mapsColumns(evals));
  return useMemo(
    () => (inputs ? judgeMediaWarnings(evals, columns, inputs) : []),
    [inputs, evals, columns],
  );
}

/**
 * Whether a model's parameter list (GET model_parameters) has anything the
 * "Model parameters" popover can set (sliders, booleans, dropdowns, response
 * formats). A TTS / STT / Image model answers empty lists, so its card shows
 * no parameters button; a list not loaded yet (or a model without one)
 * keeps the button.
 */
export function offersModelParams(params) {
  if (!params || typeof params !== "object") return true;
  return ["sliders", "booleans", "dropdowns", "responseFormat"].some(
    (k) => Array.isArray(params[k]) && params[k].length > 0,
  );
}
