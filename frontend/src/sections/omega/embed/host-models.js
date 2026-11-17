import axios from "src/utils/axios";

import { isEmbedDoc } from "./protocol";

// The model list the Console frame offers, read by Future AGI with the
// person's own session when the frame asks for it:
//
//   child  -> parent  embed.models.request {doc, model?}
//   parent -> child   embed.models {doc, models, attestation?,
//                                    runAuthorization?}
//
// - A request without `model` (the person opens the model choice) is answered
//   with a fresh list and nothing is minted.
// - A request with `model` (sent right before a start) is answered with a
//   fresh list and, when that model is one of the returned rows and the list
//   is attested, an authorization POST /agentcc/console-run/authorizations/
//   minted for exactly that model. Any refusal or failure leaves it out and
//   the list still goes.
// - models: GET /agentcc/console-models/ `result.models`, exactly as the
//   server returned them (the attestation is a digest over these rows, so
//   nothing here filters, sorts or renames them);
// - attestation: that answer's `result.attestation` when it has the compact
//   JWS shape and is within the length bound; otherwise omitted;
// - doc: the request's doc, echoed.
//
// The authorization lives in memory only: it is never logged, stored, put in
// a URL or reported in any event. The frame's bridge answers only the frame's
// current, open document and posts to the frame's exact origin.
//
// The Console publishes these message names and bounds in
// omega-embed-1.additions.json.
export const EMBED_MODELS_MESSAGE = "embed.models";
export const EMBED_MODELS_REQUEST_MESSAGE = "embed.models.request";
export const CONSOLE_MODELS_PATH = "/agentcc/console-models/";
export const RUN_AUTHORIZATIONS_PATH = "/agentcc/console-run/authorizations/";
// A listed model id is at most 200 characters.
export const MODEL_ID_MAX_LENGTH = 200;
// The Console reads the authorization strictly: a string without whitespace,
// at most 4096 characters.
export const RUN_AUTHORIZATION_MAX_LENGTH = 4096;

const REQUEST_FIELDS = new Set(["type", "doc", "requestId", "model"]);
// The frame names each request; every reply echoes it, so the frame can
// settle exactly the request it answers.
const REQUEST_ID = /^[A-Za-z0-9_-]{1,64}$/;
const isRequestId = (value) =>
  typeof value === "string" && REQUEST_ID.test(value);
const WHITESPACE = /\s/;
const ATTESTATION_PATTERN = /^[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+$/;
const ATTESTATION_MAX_LENGTH = 4096;

const isRecord = (value) =>
  value !== null && typeof value === "object" && !Array.isArray(value);

const isModelId = (value) =>
  typeof value === "string" &&
  value.length > 0 &&
  value.length <= MODEL_ID_MAX_LENGTH;

/**
 * The child's embed.models.request, strictly: only type, doc and an optional
 * model. Returns a clean copy, or null for anything else.
 */
export function readModelsRequest(data) {
  if (!isRecord(data) || data.type !== EMBED_MODELS_REQUEST_MESSAGE) {
    return null;
  }
  if (!Object.keys(data).every((key) => REQUEST_FIELDS.has(key))) return null;
  if (!isEmbedDoc(data.doc) || !isRequestId(data.requestId)) return null;
  const request = { type: data.type, doc: data.doc, requestId: data.requestId };
  if (data.model === undefined) return request;
  return isModelId(data.model) ? { ...request, model: data.model } : null;
}

/** Whether `value` is an authorization the Console would accept. */
export const isRunAuthorization = (value) =>
  typeof value === "string" &&
  value.length > 0 &&
  value.length <= RUN_AUTHORIZATION_MAX_LENGTH &&
  !WHITESPACE.test(value);

const readAttestation = (value) =>
  typeof value === "string" && value.length > 0 ? value : null;

// Whether the picked model is one of the rows, by its exact `model` id.
const listsModel = (models, model) =>
  isModelId(model) &&
  models.some((row) => isRecord(row) && row.model === model);

/**
 * The parent's embed.models. Only type, doc, requestId (the echo of the
 * request's), models, an optional attestation and, when it is one,
 * runAuthorization; nothing else from the server's answer crosses.
 */
export function modelsMessage({
  doc,
  requestId,
  models,
  attestation,
  runAuthorization,
}) {
  const message = {
    type: EMBED_MODELS_MESSAGE,
    doc,
    models,
  };
  // The whitespace check also excludes a final line break matched by `$`.
  if (
    typeof attestation === "string" &&
    attestation.length <= ATTESTATION_MAX_LENGTH &&
    ATTESTATION_PATTERN.test(attestation) &&
    !WHITESPACE.test(attestation)
  ) {
    message.attestation = attestation;
  }
  if (isRequestId(requestId)) message.requestId = requestId;
  if (isRunAuthorization(runAuthorization)) {
    message.runAuthorization = runAuthorization;
  }
  return message;
}

/**
 * @param {object} [options]
 * @param {(url: string, config: object) => Promise<{data: any}>} [options.get]
 * @param {(url: string, body: object, config: object) => Promise<{data: any}>} [options.post]
 * @returns {(context?: {signal?: AbortSignal, model?: string}) => Promise<null | {models: any[], attestation: string|null, runAuthorization?: string}>}
 *   null when there is no list to offer (a refusal, a failure, a malformed
 *   answer, or the document went away while it was read).
 */
export function createHostModelsLoader({
  get = (url, config) => axios.get(url, config),
  post = (url, body, config) => axios.post(url, body, config),
} = {}) {
  async function mintFor(model, signal) {
    try {
      const response = await post(
        RUN_AUTHORIZATIONS_PATH,
        { models: [model] },
        { signal },
      );
      const token = response?.data?.result?.run_authorization;
      return isRunAuthorization(token) ? token : undefined;
    } catch {
      // Not configured, refused, or no answer: the list goes without one.
      return undefined;
    }
  }

  return async function loadHostModels({ signal, model } = {}) {
    if (signal?.aborted) return null;
    let result;
    try {
      const response = await get(CONSOLE_MODELS_PATH, { signal });
      result = response?.data?.result;
    } catch {
      return null;
    }
    if (!isRecord(result) || !Array.isArray(result.models)) return null;
    if (signal?.aborted) return null;
    const { models } = result;
    const attestation = readAttestation(result.attestation);
    // The Console sends an authorization only beside an attested list, for
    // the model it starts with, and every mint registers a row: mint only for
    // a picked model this list carries, and only when the list is attested.
    const runAuthorization =
      attestation !== null && listsModel(models, model)
        ? await mintFor(model, signal)
        : undefined;
    if (signal?.aborted) return null;
    return runAuthorization === undefined
      ? { models, attestation }
      : { models, attestation, runAuthorization };
  };
}

export const loadHostModels = createHostModelsLoader();
