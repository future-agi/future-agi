// Vel's tool-fit sample: an agent version that calls tools the environment's
// world doesn't answer. Shown only with ?uiSample=toolfit; the API can't tell
// this yet.
export const SAMPLE_TOOL_FIT = {
  agent: "v4",
  environment: "v3",
  nextEnvironment: "v4",
  missing: ["refund_status", "loyalty_points"],
};
