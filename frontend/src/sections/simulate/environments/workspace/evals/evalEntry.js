// One eval entry exactly as the API sends it (contract §1: name, description,
// source, tags, required_keys, agent_type, modality, credits_per_run,
// charges_judge_tokens, inputs[]).
//
// Presentation only. F1: the frontend never computes which source fills a key.
// `inputs` is read as the API built it — one row per required key, sorted by
// key — and `label` is the only text shown for a source ("Call recording",
// "Transcript", "Agent instructions", "Scenario situation"). `required_keys`
// keeps the template's stored order and is NOT aligned with `inputs` (P1), so
// nothing here pairs the two by position; it stays in the shape because the
// sandbox's authoring prompt reads it (design D6).
import PropTypes from "prop-types";

export const INPUT_ROW_SHAPE = PropTypes.shape({
  key: PropTypes.string,
  source: PropTypes.string,
  label: PropTypes.string,
});

const ENTRY_FIELDS = {
  name: PropTypes.string,
  description: PropTypes.string,
  source: PropTypes.oneOf(["system", "custom"]),
  tags: PropTypes.arrayOf(PropTypes.string),
  required_keys: PropTypes.arrayOf(PropTypes.string),
  agent_type: PropTypes.string,
  modality: PropTypes.string,
  credits_per_run: PropTypes.number,
  charges_judge_tokens: PropTypes.bool,
  inputs: PropTypes.arrayOf(INPUT_ROW_SHAPE),
};

export const EVAL_ENTRY_SHAPE = PropTypes.shape(ENTRY_FIELDS);

// An entry on `evaluations.selected[]` — the same entry plus the config id the
// remove endpoint takes and `runnable` (§5 P16).
export const SELECTED_EVAL_SHAPE = PropTypes.shape({
  ...ENTRY_FIELDS,
  id: PropTypes.string,
  runnable: PropTypes.bool,
});

// P4/P25: "system" is a built-in from the library, anything else is the
// organisation's own. The words on screen are the product's; the value is the
// API's — never inferred from the name, the owner or the tags.
export const sourceLabel = (entry) => (entry?.source === "custom" ? "Custom" : "Library");

// P4/P25: nothing is free — every eval run costs `credits_per_run` (always 0.5)
// credits, and an AI-judged eval additionally charges the judge's tokens
// (`charges_judge_tokens`). Both numbers are the API's own; never derived from
// `eval_type` or the eval's name.
//
// `runMode` (P25, owner's rule 2026-09-23 night): the picker opened from inside
// a run reads "run" as the simulation run, not one eval run — "0.5 credits per
// run" there would misread as 0.5 credits total. In run mode the chip instead
// reads "0.5 credits per call graded" / "… + judge tokens", naming what it
// actually charges per (one queued call), never renamed or reworded beyond
// these two exact strings.
export const costLabel = (entry, runMode = false) => {
  const credits = entry?.credits_per_run ?? 0.5;
  const suffix = entry?.charges_judge_tokens ? " + judge tokens" : "";
  // L8 (round 2): `credits` is read off the API, never hard-coded (P25) — P4
  // pins it at 0.5 today, but the noun still has to agree in number if that
  // ever changes ("1 credit", not "1 credits").
  const noun = credits === 1 ? "credit" : "credits";
  return runMode ? `${credits} ${noun} per call graded${suffix}` : `${credits} ${noun} per run${suffix}`;
};

// The arrows a row draws, as given — order included.
export const inputRowsOf = (entry) => (Array.isArray(entry?.inputs) ? entry.inputs : []);
