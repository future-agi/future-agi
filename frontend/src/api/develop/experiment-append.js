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

/** A saved HTTP source of the experiment detail read (`http_configs[]`). */
export const isHttpSource = (source) =>
  source?.target_type === "http" && Boolean(source?.http);

/** The editable settings of a new HTTP variant, from its original source. */
export function httpVariantDraftOf(source) {
  const http = source?.http ?? {};
  return {
    name: `${source?.name || "HTTP endpoint"} variant`,
    url: http.url ?? "",
    method: String(http.method || "POST").toUpperCase(),
    bodyTemplate: http.body_template ?? "",
    outputPath: http.output_path ?? "",
  };
}

/** What is wrong with a variant's endpoint URL, or null (pure). */
export function httpVariantUrlProblem(url) {
  const raw = String(url ?? "").trim();
  if (!raw) return "Enter the endpoint URL.";
  try {
    const u = new URL(raw);
    if ((u.protocol === "https:" || u.protocol === "http:") && u.host)
      return null;
  } catch {
    // Reported below.
  }
  return "Use a full https:// URL, for example https://api.acme.ai/reply";
}

/** Whether a draft would send anything the original source does not. */
export function httpVariantChanged(source, draft) {
  const was = httpVariantDraftOf(source);
  const get = String(draft?.method ?? "").toUpperCase() === "GET";
  return (
    String(draft?.url ?? "").trim() !== String(was.url).trim() ||
    String(draft?.method ?? "").toUpperCase() !== was.method ||
    (!get && (draft?.bodyTemplate ?? "") !== was.bodyTemplate) ||
    String(draft?.outputPath ?? "").trim() !== String(was.outputPath).trim()
  );
}

/**
 * Re-run with changes for an HTTP source: one new HTTP variant on the same
 * snapshot, never the original's id. Headers (and their secret references),
 * timeout and the variable mapping are copied exactly from the original.
 */
export function appendHttpVariantBody(source, draft) {
  const url = String(draft?.url ?? "").trim();
  if (!isHttpSource(source) || !url) throw new Error("Enter the endpoint URL.");
  const http = JSON.parse(JSON.stringify(source.http));
  const method = String(draft?.method || http.method || "POST").toUpperCase();
  const name = String(draft?.name ?? "").trim();
  const mapping = source.variable_mapping ?? {};
  return {
    append_only: true,
    prompt_config: [
      {
        target_type: "http",
        ...(name ? { name } : {}),
        http: {
          ...http,
          url,
          method,
          body_template:
            method === "GET"
              ? ""
              : draft?.bodyTemplate ?? http.body_template ?? "",
          output_path: String(draft?.outputPath ?? http.output_path ?? "").trim(),
        },
        ...(Object.keys(mapping).length
          ? { variable_mapping: JSON.parse(JSON.stringify(mapping)) }
          : {}),
      },
    ],
  };
}

/**
 * HTTP Add new rows: the original dataset rows to capture, as the server's
 * preview named them. The server still skips rows the experiment already
 * holds and sends only the new ones.
 */
export function appendHttpRowsBody(rowIds = []) {
  const ids = [...new Set((rowIds ?? []).map(String).filter(Boolean))];
  if (!ids.length) throw new Error("The dataset has no rows to add.");
  return { append_only: true, row_ids: ids };
}

/**
 * HTTP Add new rows, read only: of these original dataset rows (the live
 * candidates, possibly none), which the experiment does not already hold, by
 * row identity, and how many requests adding them sends. Saves and sends
 * nothing, so it has no Idempotency-Key.
 * @returns {Promise<{preview: true, new_row_ids: string[],
 *   request_count: number, held_count: number, limit: number}>}
 */
export async function previewHttpAppendRows(experimentId, rowIds = []) {
  if (!acceptedFields("ExperimentUpdateV2").includes("preview"))
    throw new Error("Add new rows is unavailable on this server.");
  const ids = [...new Set((rowIds ?? []).map(String).filter(Boolean))];
  const response = await axios.put(
    endpoints.develop.experiment.getExperimentDetails(experimentId),
    { append_only: true, preview: true, row_ids: ids },
  );
  return response.data.result;
}

/**
 * PUT append_only. HTTP appends send an Idempotency-Key: a new key per
 * submission, the same key (and body) only to recover it after an unknown
 * outcome. Prompt variants send none.
 */
export async function appendExperimentVariant(
  experimentId,
  body,
  { idempotencyKey } = {},
) {
  if (!appendVariantSupported())
    throw new Error("Re-run with changes is unavailable on this server.");
  const response = await axios.put(
    endpoints.develop.experiment.getExperimentDetails(experimentId),
    body,
    ...(idempotencyKey
      ? [{ headers: { "Idempotency-Key": idempotencyKey } }]
      : []),
  );
  return response.data.result;
}
