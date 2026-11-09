import axios, { endpoints } from "src/utils/axios";
import { acceptedFields } from "src/api/contracts/strictBody";

export const appendVariantSupported = () =>
  acceptedFields("ExperimentUpdateV2").includes("append_only");

export function appendVariantBody(source, draft) {
  if (
    !source?.prompt_id ||
    !draft.promptId ||
    !draft.versionId ||
    !draft.model
  ) {
    throw new Error("Select a prompt, published version and model.");
  }
  const sameModel = draft.model === source.model;
  const samePrompt = draft.promptId === source.prompt_id;
  return {
    append_only: true,
    prompt_config: [
      {
        name: draft.name.trim(),
        prompt_id: draft.promptId,
        prompt_version: draft.versionId,
        model: sameModel
          ? {
              name: source.model,
              config: source.model_config ?? {},
              display_name: source.model_display_name ?? source.model,
            }
          : draft.model,
        model_params: sameModel ? source.model_params ?? {} : {},
        configuration: samePrompt ? source.configuration ?? {} : {},
        output_format: source.output_format ?? "string",
      },
    ],
  };
}

export async function appendExperimentVariant(experimentId, body) {
  if (!appendVariantSupported())
    throw new Error("Re-run with changes is unavailable on this server.");
  const response = await axios.put(
    endpoints.develop.experiment.getExperimentDetails(experimentId),
    body,
  );
  return response.data.result;
}
