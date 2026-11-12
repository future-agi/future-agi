import { metadataObject } from "./experimentFormValues";

/**
 * The review line for a run's metadata: exactly the entries the create
 * request sends and the server stores. Returns null when there are none.
 * @param {{key?: string, value?: string}[]} rows
 */
export function metadataLine(rows = []) {
  const entries = Object.entries(metadataObject(rows ?? []));
  if (!entries.length) return null;
  return entries.map(([key, value]) => `${key}: ${value}`).join(" · ");
}
