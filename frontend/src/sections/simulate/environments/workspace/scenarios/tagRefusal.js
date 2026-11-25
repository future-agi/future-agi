import { refusalText } from "../evals/refusalText";
import { SCENARIO_TAGS_COPY } from "./scenarios.constants";

export const tagRefusal = (error) => {
  const status = error?.statusCode;
  if (status == null || status >= 500) return SCENARIO_TAGS_COPY.failed;
  return refusalText(error, SCENARIO_TAGS_COPY.refused);
};
