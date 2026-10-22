import { trackEvent, Events } from "src/utils/Mixpanel";

export const extractApiVersionFromUrl = (url) => {
  if (!url) return null;

  try {
    const urlObj = new URL(url);
    const apiVersionParam = urlObj.searchParams.get("api-version");
    return apiVersionParam;
  } catch (error) {
    const match = url.match(/api-version=([^&]*)/);
    return match ? match[1] : null;
  }
};

export const inferAzureEndpointType = (url) => {
  if (!url) return null;
  const lower = url.toLowerCase();
  if (
    lower.includes(".openai.azure.com") ||
    lower.includes("/openai/deployments/")
  ) {
    return "legacy";
  }
  if (
    lower.includes(".services.ai.azure.com") ||
    lower.includes(".models.ai.azure.com") ||
    lower.includes(".inference.ai.azure.com") ||
    lower.includes(".cognitiveservices.azure.com")
  ) {
    return "foundry";
  }
  return null;
};

export const extractAzureDeploymentFromUrl = (url) => {
  if (!url) return null;
  const match = url.match(/\/openai\/deployments\/([^/]+)/i);
  return match ? match[1] : null;
};

export const updateApiVersionInUrl = (url, newVersion) => {
  if (!url || !newVersion) return url;

  try {
    const urlObj = new URL(url);
    urlObj.searchParams.set("api-version", newVersion);
    return urlObj.toString();
  } catch (error) {
    if (url.includes("api-version=")) {
      return url.replace(/api-version=[^&]*/, `api-version=${newVersion}`);
    } else {
      const separator = url.includes("?") ? "&" : "?";
      return `${url}${separator}api-version=${newVersion}`;
    }
  }
};

// You’ll need to pass in setValue and clearedFieldsRef when calling this
export const createClearOnFocus =
  (setValue, clearedFieldsRef) => (fieldName) => () => {
    if (clearedFieldsRef.current[fieldName]) return;
    setValue(fieldName, "");
    clearedFieldsRef.current[fieldName] = true;
  };

// You’ll also need to pass in modelProvider, setValue, getValues
export const createHandleApiBaseChange =
  ({ modelProvider, setValue, getValues }) =>
  (e) => {
    const newUrl = e.target.value;
    setValue("azureApiBase", newUrl, { shouldValidate: true });

    if (modelProvider === "azure") {
      // Extract and auto-fill API version from URL
      const extractedVersion = extractApiVersionFromUrl(newUrl);
      if (
        extractedVersion &&
        extractedVersion !== getValues("azureApiVersion")
      ) {
        setValue("azureApiVersion", extractedVersion, { shouldValidate: true });
      }

      // Infer and auto-fill endpoint type from URL
      const inferred = inferAzureEndpointType(newUrl);
      if (inferred && inferred !== getValues("azureEndpointType")) {
        setValue("azureEndpointType", inferred, { shouldValidate: true });
      }

      // Extract deployment name from legacy URL and auto-fill model name if empty
      const deployment = extractAzureDeploymentFromUrl(newUrl);
      if (deployment && !getValues("modelName")) {
        setValue("modelName", deployment, { shouldValidate: true });
      }
    }
  };

export const createHandleApiVersionChange =
  ({ modelProvider, setValue, getValues }) =>
  (e) => {
    const newVersion = e.target.value;
    setValue("azureApiVersion", newVersion, { shouldValidate: true });

    if (modelProvider === "azure") {
      const currentUrl = getValues("azureApiBase");
      if (currentUrl) {
        const updatedUrl = updateApiVersionInUrl(currentUrl, newVersion);
        if (updatedUrl !== currentUrl) {
          setValue("azureApiBase", updatedUrl, { shouldValidate: true });
        }
      }
    }
  };

export const getModelFields = () => [
  {
    fieldName: "modelName",
    label: "Model Name",
    placeholder: "Enter model name",
    onChange: () => trackEvent(Events.modelNameEntered),
  },
  {
    fieldName: "inputTokenCost",
    label: "Input Token Cost Per Million Tokens",
    placeholder: "Enter input token cost per million tokens",
    type: "number",
    fieldType: "number",
    inputProps: { min: 0, max: 100000, step: "any" },
    onChange: () => trackEvent(Events.inputTokenCostEntered),
  },
  {
    fieldName: "outputTokenCost",
    label: "Output Token Cost Per Million Tokens",
    placeholder: "Enter output token cost per million tokens",
    type: "number",
    fieldType: "number",
    inputProps: { min: 0, max: 100000, step: "any" },
    onChange: () => trackEvent(Events.outputTokenCostEntered),
  },
];

export const getCustomModelFields = () => [
  {
    fieldName: "modelName",
    label: "Model Name",
    placeholder: "Enter model name",
    onChange: () => trackEvent(Events.modelNameEntered),
    required: true,
  },
  {
    fieldName: "inputTokenCost",
    label: "Input Token Cost Per Million Tokens",
    placeholder: "Enter input token cost per million tokens",
    type: "number",
    fieldType: "number",
    inputProps: { min: 0, max: 100000, step: "any" },
    onChange: () => trackEvent(Events.inputTokenCostEntered),
    required: true,
  },
  {
    fieldName: "outputTokenCost",
    label: "Output Token Cost Per Million Tokens",
    placeholder: "Enter output token cost per million tokens",
    type: "number",
    fieldType: "number",
    inputProps: { min: 0, max: 100000, step: "any" },
    onChange: () => trackEvent(Events.outputTokenCostEntered),
    required: true,
  },
  {
    fieldName: "apiBase",
    label: "API Base URL",
    placeholder: "e.g. https://your-host/v1/chat/completions",
    // The check and every model call POST to this URL as given.
    helperText: "The full chat-completions URL, not just the /v1 base",
    required: true,
  },
];

export const getAwsCredentialFields = (clearOnFocus) => [
  {
    fieldName: "awsAccessKeyId",
    label: "Access Key ID",
    placeholder: "Enter access key ID",
    onFocus: clearOnFocus("awsAccessKeyId"),
    onChange: () => trackEvent(Events.accessKeyEntered),
  },
  {
    fieldName: "awsSecretAccessKey",
    label: "Secret Access Key",
    placeholder: "Enter secret access key",
    onFocus: clearOnFocus("awsSecretAccessKey"),
    onChange: () => trackEvent(Events.secretKeyEntered),
  },
  {
    fieldName: "awsRegionName",
    label: "Region Name",
    placeholder: "Enter region name",
    onFocus: clearOnFocus("awsRegionName"),
    onChange: () => trackEvent(Events.regionNameEntered),
  },
];

export const getOpenAiKeyFields = (clearOnFocus) => [
  {
    fieldName: "key",
    label: "API Key",
    placeholder: "Enter Open AI Key",
    onFocus: clearOnFocus("key"),
  },
  {
    fieldName: "apiBaseUrl",
    label: "Enter Base URL (optional)",
    placeholder: "Enter Open AI Base URL",
    onFocus: clearOnFocus("apiBaseUrl"),
  },
];

export const getVertexKeyFields = (clearOnFocus) => [
  {
    fieldName: "vertexCredentialJson",
    label: "Vertex Credentials",
    placeholder: "Enter Vertex credentials in JSON format",
    isJsonKey: true,
    onFocus: clearOnFocus("vertexCredentialJson"),
  },
  {
    fieldName: "vertexLocation",
    label: "Location (optional)",
    placeholder: "e.g. us-central1",
    onFocus: clearOnFocus("vertexLocation"),
  },
];

export const getAzureKeyFields = (
  clearOnFocus,
  handleApiBaseChange,
  handleApiVersionChange,
) => [
  {
    fieldName: "azureApiBase",
    label: "API Base",
    placeholder: "Enter Azure API base",
    onChange: handleApiBaseChange,
    onFocus: clearOnFocus("azureApiBase"),
  },
  {
    fieldName: "apiKey",
    label: "API Key",
    placeholder: "Enter Azure API Key",
    onFocus: clearOnFocus("apiKey"),
  },
  {
    fieldName: "azureApiVersion",
    label: "API Version",
    placeholder: "Enter Azure API version",
    onChange: handleApiVersionChange,
    onFocus: clearOnFocus("azureApiVersion"),
  },
];
