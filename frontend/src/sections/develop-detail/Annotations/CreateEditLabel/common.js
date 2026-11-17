// The API sends a label's settings in snake_case; this form reads camelCase.
// A camelCase value already present is kept.
const toNumericFormSettings = ({ step_size, display_type, ...settings }) => ({
  ...settings,
  stepSize: settings.stepSize ?? step_size,
  displayType: settings.displayType ?? display_type,
});

const toTextFormSettings = ({ min_length, max_length, ...settings }) => ({
  ...settings,
  minLength: settings.minLength ?? min_length,
  maxLength: settings.maxLength ?? max_length,
});

const toCategoricalFormSettings = ({ multi_choice, ...settings }) => ({
  ...settings,
  multiChoice: settings.multiChoice ?? multi_choice,
});

const toStarFormSettings = ({ no_of_stars, ...settings }) => ({
  ...settings,
  noOfStars: settings.noOfStars ?? no_of_stars,
});

const toFormSettings = {
  text: toTextFormSettings,
  numeric: toNumericFormSettings,
  categorical: toCategoricalFormSettings,
  star: toStarFormSettings,
};

export const getDefaultCreateLabelFormValues = (editData) => {
  let settings = editData?.settings || {};
  const toForm = toFormSettings[editData?.type];
  if (toForm) {
    settings = toForm(settings);
  }
  if (editData?.type === "categorical" && settings?.options) {
    settings = {
      ...settings,
      options: settings.options.map((opt, idx) => ({
        id: `${idx}-${opt.label}`,
        value: opt.label,
      })),
    };
  }

  return {
    name: editData?.name || "",
    type: editData?.type || "",
    description: editData?.description || "",
    settings,
  };
};

export const AnnotationTypes = [
  { label: "Text", value: "text" },
  { label: "Numeric", value: "numeric" },
  { label: "Categorical", value: "categorical" },
  { label: "Star", value: "star" },
  { label: "Thumbs Up & Down", value: "thumbs_up_down" },
];

export const AnnotationFormDefaultSettings = {
  text: {
    placeholder: "",
    maxLength: "",
    minLength: "",
  },
  numeric: {
    min: "",
    max: "",
    stepSize: "",
    displayType: "",
  },
  categorical: {
    options: [],
    multiChoice: false,
  },
  star: {
    noOfStars: 5,
  },
  thumbs_up_down: {},
};

export const transformLabelObject = (label) => {
  if (label.type === "categorical") {
    return {
      ...label,
      settings: {
        ...label.settings,
        options: label.settings.options.map((option) => ({
          label: option.label,
          value: option.label,
        })),
      },
    };
  }
  return label;
};

export const NumericLabelDisplayTypes = [{ label: "Slider", value: "slider" }];

// A new label is offered the slider only. A label stored as buttons keeps that
// display when it is edited, so it can be saved as it is; "Buttons" is the
// word the other numeric label form uses for it.
export const getNumericLabelDisplayTypes = (storedDisplayType) =>
  storedDisplayType === "button"
    ? [...NumericLabelDisplayTypes, { label: "Buttons", value: "button" }]
    : NumericLabelDisplayTypes;
