import { bodyDefinitionOf, unknownFields } from "src/api/contracts/strictBody";

const STOP_PATH = "/model-hub/experiments/v2/{experiment_id}/stop/";

/** Capabilities belong to the route's request schema, not a shared serializer. */
export function supportsSelectedRunStop(prototype = false) {
  if (prototype) return true;
  const definition = bodyDefinitionOf("post", STOP_PATH);
  return (
    Boolean(definition) &&
    unknownFields({ run_ids: ["run"] }, definition).length === 0
  );
}

/** Only an omitted selection means stop the whole experiment. */
export function stopRequestBody(runIds, supported) {
  if (runIds === undefined) return {};
  if (
    !Array.isArray(runIds) ||
    runIds.length === 0 ||
    Array.from(runIds).some((id) => typeof id !== "string" || !id.trim())
  ) {
    throw new Error("Select at least one run to stop.");
  }
  if (!supported) {
    throw new Error(
      "Stopping individual runs is not available on this server.",
    );
  }
  return { run_ids: runIds };
}
