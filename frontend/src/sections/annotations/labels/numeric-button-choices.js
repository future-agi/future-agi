// A numeric label shown as buttons shows at most this many choices.
export const MAX_NUMERIC_BUTTON_CHOICES = 11;

export const NUMERIC_BUTTON_CHOICES_MESSAGE =
  "A button scale can show up to 11 choices. Use a larger step or the slider.";

// The choices a numeric label's button scale shows, counted exactly as the
// annotate screen draws the buttons (queues/annotate/label-input.jsx): from
// min in steps of step while not above max, each rounded to 6 decimal places,
// then max itself when the steps do not land on it. Values may be held as
// text; both numeric label forms count with this.
export const numericButtonChoices = (min, max, step) => {
  const choices = [];
  for (
    let option = Number(min);
    option <= Number(max) + Number.EPSILON && choices.length < 101;
    option += Number(step)
  ) {
    choices.push(Number(option.toFixed(6)));
  }
  if (!choices.includes(Number(max))) choices.push(Number(max));
  return choices;
};

export const hasTooManyButtonChoices = (min, max, step) =>
  numericButtonChoices(min, max, step).length > MAX_NUMERIC_BUTTON_CHOICES;
