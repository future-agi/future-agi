import axios, { endpoints } from "src/utils/axios";

import { boundedHandoffRequest, workbenchModelFor } from "./handoff";

// The model-list API's name filter returns at most one exact-name row, unlike
// its paginated substring search. Check the full ID before the provider/bare
// alias, so availability ordering cannot hide a model on a later search page.
// null means confirmed absence; failed or malformed lookups must reject.
export async function resolvePromptsModel(modelId) {
  if (!modelId) return null;
  const slash = modelId.indexOf("/");
  const names = slash < 0 ? [modelId] : [modelId, modelId.slice(slash + 1)];

  for (const name of names) {
    const { data } = await boundedHandoffRequest((options) =>
      axios.get(endpoints.develop.modelList, { ...options, params: { name } }),
    );
    if (!Array.isArray(data?.results) || data?.status === false) {
      throw new Error("The Prompts model list could not be checked.");
    }
    const row = workbenchModelFor(modelId, data.results);
    if (row) return row;
  }
  return null;
}
