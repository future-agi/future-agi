export const AZURE_ENDPOINT_TYPES = {
  FOUNDRY: "foundry",
  LEGACY: "legacy",
};

export const AzureEndpointOptions = [
  {
    label: "Azure AI Foundry / AI Studio",
    value: AZURE_ENDPOINT_TYPES.FOUNDRY,
  },
  {
    label: "Azure OpenAI (Legacy Deployments)",
    value: AZURE_ENDPOINT_TYPES.LEGACY,
  },
];

export const defaultValues = {
  modelProvider: "",
  modelName: "",
  inputTokenCost: "",
  outputTokenCost: "",
  key: "",
  apiBaseUrl: "",
  awsAccessKeyId: "",
  awsSecretAccessKey: "",
  awsRegionName: "",
  awsCredentialsJson: "",
  vertexCredentialJson: "",
  vertexLocation: "",
  azureApiBase: "",
  azureApiVersion: "",
  apiKey: "",
  azureCredentialJson: "",
  openAiCredentialJson: "",
  customConfiguration: [],
  azureEndpointType: AZURE_ENDPOINT_TYPES.FOUNDRY,
};

export const MODEL_PROVIDERS = {
  AZURE: "azure",
  OPENAI: "openai",
  VERTEX_AI: "vertex_ai",
  CUSTOM: "custom",
  BEDROCK: "bedrock",
  SAGEMAKER: "sagemaker",
};

export const TABS = {
  FORM: "Form",
  JSON: "JSON",
};

export const RADIO_VALUES = {
  CUSTOM_PROVIDER: "custom-provider",
  CONFIGURE_CUSTOM_MODEL: "configure-custom-model",
};

export const FIELD_NAMES = {
  AWS_CREDENTIALS_JSON: "awsCredentialsJson",
  AZURE_CREDENTIALS_JSON: "azureCredentialJson",
  OPENAI_CREDENTIALS_JSON: "openAiCredentialJson",
  VERTEX_CREDENTIAL_JSON: "vertexCredentialJson",
};

export const CustomModalOptions = [
  {
    label: "Open AI",
    value: "openai",
    logo: "/icons/settings/openai.png",
  },
  {
    label: "AWS Bedrock",
    value: "bedrock",
    logo: "/assets/icons/settings/ic_aws.svg",
  },
  {
    label: "AWS Sagemaker",
    value: "sagemaker",
    logo: "/assets/icons/settings/ic_aws.svg",
  },
  {
    label: "Vertex AI",
    value: "vertex_ai",
    logo: "/icons/settings/google.png",
  },
  {
    label: "Azure",
    value: "azure",
    logo: "/icons/settings/azure.png",
  },
  // { label: "Custom", value: "custom" },
];
