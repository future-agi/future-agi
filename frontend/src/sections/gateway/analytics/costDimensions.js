import { REQUEST_DIMENSION_OPTIONS } from "../constants/requestTags";

// Only Cost supports caller attribution; do not offer these on Usage or Errors.
export const COST_DIMENSION_OPTIONS = [
  ...REQUEST_DIMENSION_OPTIONS,
  { value: "api_key_id", label: "API key" },
  { value: "user_id", label: "User" },
];
