export function generateNMarks(min, max, step = (max - min) / 10) {
  const count = Math.round((max - min) / step) + 1;
  return Array.from({ length: count }, (_, i) => ({
    value: parseFloat((min + i * step).toFixed(6)), // prevent float errors
    label: "", // optional: add `${value}` if you want visible labels
  }));
}

export const DEFAULT_MODEL_PARAMS = [
  {
    label: "Temperature",
    id: "temperature",
    min: 0,
    max: 1,
    step: 0.1,
  },
  {
    label: "Top P",
    id: "topP",
    min: 0,
    max: 1,
    step: 0.1,
  },
  {
    label: "Max Tokens",
    id: "maxTokens",
    min: 1,
    max: 20000,
    step: 1,
  },
  {
    label: "Presence Penalty",
    id: "presencePenalty",
    min: 0,
    max: 2,
    step: 0.1,
  },
  {
    label: "Frequency Penalty",
    id: "frequencyPenalty",
    min: 0,
    max: 2,
    step: 0.1,
  },
];

export const TOOLTIP_OBJ = {
  temperature: "Controls randomness of responses",
  max_tokens: "Defines maximum response length",
  top_p: "Controls diversity in token selection",
  presence_penalty: "Adjusts topic diversity",
  frequency_penalty: "Reduces word/phrase repetition",
};
