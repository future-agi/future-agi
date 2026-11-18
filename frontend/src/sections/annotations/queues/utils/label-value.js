import { isNil, isEmpty } from "lodash";

// Values are per-type composites ({ rating }, { text }, ...); a cleared field
// keeps the key with an empty inner value, so check inner emptiness per type.
export const isLabelValueEmpty = (type, v) => {
  if (isNil(v)) return true;
  switch (type) {
    case "star":
      return !v.rating;
    case "categorical":
      return isEmpty(v.selected);
    case "text":
      return isEmpty(v.text?.trim());
    case "thumbs_up_down":
      return !v.value;
    case "numeric":
      return isNil(v.value);
    default:
      return false;
  }
};
