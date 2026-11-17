import { getRequestErrorMessage } from "src/utils/errorUtils";

export const CREATE_ADMISSION_KEY = ["experiment-create-admission"];
export const UNCONFIRMED_ADMISSION = "experiment_admission_unconfirmed";
export const RETRY_UNCHANGED =
  "Retry the unchanged configuration to confirm its execution before editing it.";
const UUID = "[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}";
const uuid = new RegExp(`^${UUID}$`, "i");
const legacyId = new RegExp(`experiment_id=(${UUID})(?:\\s|$)`, "i");

export function admissionCode(error) {
  return error?.code ?? error?.response?.data?.code;
}

// Axios flattens rejected API bodies, so detail is not always under response.
export function admissionError(error) {
  const detail = error?.detail ?? error?.response?.data?.detail;
  const message = getRequestErrorMessage(
    error,
    typeof detail === "string" && detail.trim()
      ? detail
      : "Could not start the experiment. Keep your draft and try again.",
  );
  return Object.assign(new Error(message), error, { message });
}

// A transport 200 alone must never close a draft or navigate to results.
export function confirmCreatedExperiment(response) {
  const payload = response?.data;
  const result = payload?.result;
  const id =
    typeof result === "string"
      ? legacyId.exec(result)?.[1]
      : result?.experiment_id;
  if (payload?.status !== true || !uuid.test(String(id ?? ""))) {
    throw admissionError({
      code: payload?.code,
      message:
        typeof payload?.detail === "string" && payload.detail.trim()
          ? payload.detail
          : "The server did not confirm this submission. Keep your draft and check the experiment before retrying.",
    });
  }
  return response;
}

export const snapshotSubmission = (body) => JSON.parse(JSON.stringify(body));
const canonical = (value) => {
  if (Array.isArray(value)) return value.map(canonical);
  if (value && typeof value === "object")
    return Object.fromEntries(
      Object.keys(value)
        .sort()
        .map((key) => [key, canonical(value[key])]),
    );
  return value;
};
export const sameSubmission = (left, right) =>
  JSON.stringify(canonical(left)) === JSON.stringify(canonical(right));

export const recoveryKey = (datasetId) => [
  "experiment-create-recovery",
  datasetId,
];
