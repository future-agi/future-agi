import { NumericLabelDisplayTypes } from "./common";

const addNumber = (fields, key, value) => {
  if (value === null || value === undefined || String(value).trim() === "")
    return;
  const number = Number(value);
  if (Number.isFinite(number)) fields[key] = number;
};

/** Returns the label type, description flag, and applicable settings measurements. */
export const annotationLabelEventFields = (values) => {
  const settings = values?.settings || {};
  const fields = {
    type: values?.type,
    has_description: Boolean(values?.description?.trim()),
  };

  switch (values?.type) {
    case "text":
      addNumber(fields, "min_length", settings.minLength);
      addNumber(fields, "max_length", settings.maxLength);
      if (
        typeof settings.placeholder === "string" &&
        settings.placeholder.trim()
      ) {
        fields.has_placeholder = true;
      }
      break;
    case "numeric":
      addNumber(fields, "min", settings.min);
      addNumber(fields, "max", settings.max);
      addNumber(fields, "step_size", settings.stepSize);
      if (
        NumericLabelDisplayTypes.some(
          ({ value }) => value === settings.displayType,
        )
      ) {
        fields.display_type = settings.displayType;
      }
      break;
    case "categorical":
      if (typeof settings.multiChoice === "boolean") {
        fields.multi_choice = settings.multiChoice;
      }
      if (Array.isArray(settings.options) && settings.options.length) {
        fields.option_count = settings.options.length;
      }
      break;
    case "star":
      addNumber(fields, "no_of_stars", settings.noOfStars);
      break;
    default:
      break;
  }

  return fields;
};
