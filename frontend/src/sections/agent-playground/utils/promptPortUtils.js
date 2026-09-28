import { PORT_DIRECTION, PORT_KEYS } from "./constants";

function getPromptConfiguration(config) {
  return config?.payload?.promptConfig?.[0]?.configuration || {};
}

function getSchemaObject(responseSchema) {
  if (!responseSchema || typeof responseSchema !== "object") return null;
  if (responseSchema.schema && typeof responseSchema.schema === "object") {
    return responseSchema.schema;
  }
  return responseSchema;
}

export function getPromptResponseDataSchema(config) {
  const configuration = getPromptConfiguration(config);
  const outputFormat =
    config?.outputFormat ||
    configuration?.outputFormat ||
    configuration?.output_format ||
    "string";

  if (outputFormat !== "json") {
    return { type: "string" };
  }

  return (
    getSchemaObject(
      config?.modelConfig?.responseSchema ||
        configuration?.responseSchema ||
        configuration?.response_schema,
    ) || { type: "object" }
  );
}

export function normalizePromptOutputPorts(ports, config) {
  if (!Array.isArray(ports)) return ports;

  const responseDataSchema = getPromptResponseDataSchema(config);

  return ports.map((port) => {
    const isResponseOutput =
      port?.direction === PORT_DIRECTION.OUTPUT &&
      (port?.key === PORT_KEYS.RESPONSE || port?.display_name === "response");

    if (!isResponseOutput) return port;

    return {
      ...port,
      data_schema: responseDataSchema,
    };
  });
}
