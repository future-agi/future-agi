// API Calls > Response path on the real backend (L2 fix round 1). Dev's
// add-api-column/ and rerun-operation/ store the API column's config without
// a response path and keep the whole response in the cell: nothing in dev
// reads `response_path`. So a real build does not offer the field and the
// config it sends carries none; prototype mode keeps it (its mocks apply it).
import { isPrototypeMode } from "src/utils/prototype";

/** True where the backend applies a response path (prototype mode only). */
export const responsePathOffered = (mockMode = isPrototypeMode()) =>
  Boolean(mockMode);

/** The API column config to send: without response_path where it is not offered. */
export function apiCallConfigOf(config, mockMode = isPrototypeMode()) {
  if (!config || typeof config !== "object" || responsePathOffered(mockMode))
    return config;
  // eslint-disable-next-line no-unused-vars
  const { response_path, ...rest } = config;
  return rest;
}

/** The form values to send (config without response_path where not offered). */
export const apiCallFormOf = (values, mockMode = isPrototypeMode()) =>
  values && typeof values === "object"
    ? { ...values, config: apiCallConfigOf(values.config, mockMode) }
    : values;
