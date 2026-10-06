import logger from "src/utils/logger";
import { z } from "zod";

export const CreateUserFormValidation = z.object({
  userName: z
    .string({ required_error: "User Name is required." })
    .min(1, "User Name is required."),
  email: z
    .string({
      invalid_type_error: "Email is required.",
      required_error: "Email is required",
    })
    .email("Invalid email address"),
  organization_role: z.string({
    invalid_type_error: "Organization Role is required.",
    required_error: "Organization Role is required",
  }),
});

export const AddFundsFormValidation = z.object({
  amount: z.preprocess(
    (val) => {
      // Convert the input to a number
      const parsedValue = parseFloat(val);
      return isNaN(parsedValue) ? undefined : parsedValue; // Return undefined if not a number
    },
    z
      .number({ required_error: "Amount is required." })
      .min(1, "Amount must be at least 1."),
  ),
});

export const EditAutoReloadSettingsFormValidation = z
  .object({
    threshold: z.preprocess(
      (val) => {
        const parsedValue = parseFloat(val);
        return isNaN(parsedValue) ? undefined : parsedValue;
      },
      z
        .number({ required_error: "Threshold is required." })
        .min(1, "Threshold must be at least 1."),
    ),

    amount: z.preprocess(
      (val) => {
        const parsedValue = parseFloat(val);
        return isNaN(parsedValue) ? undefined : parsedValue;
      },
      z
        .number({ required_error: "Amount is required." })
        .min(1, "Amount must be at least 1."),
    ),
  })
  .refine(
    (data) => {
      logger.debug("Data:", data);
      if (data.amount <= data.threshold) {
        return false; // Validation fails
      }
      return true; // Validation passes
    },
    {
      message: "This value must be greater than the credit balance threshold.",
      path: ["amount"], // Specify the path to the field that has the issue
    },
  );

export const BillingInfoFormValidation = z.object({
  name: z.string().min(1, "Name is required"),
  email: z.string().min(1, "Email is required").email("Invalid email address"),
  company: z.string().optional().nullable(),
  billingAddress1: z.string().min(1, "Billing address is required"),
  billingAddress2: z.string().optional().nullable(),
  city: z.string().min(1, "City is required"),
  state: z.string().min(1, "State is required"),
  country: z.string().min(1, "Country is required"),
  postalCode: z.string().min(1, "Postal code is required"),
  taxId: z.string().min(1, "Tax ID is required"),
});

export const FullNameFormValidation = z.object({
  name: z
    .string({ required_error: "Full Name is required." })
    .min(1, "Full Name is required."),
});

const customConfigurationValidation = z.array(
  z.object({
    key: z.string().min(1, "Key is required"),
    value: z.string().min(1, "Value is required"),
  }),
);

const vertexJSONValidation = z
  .string()
  .optional()
  .refine(
    (val) => {
      if (!val) return true;
      try {
        JSON.parse(val);
        return true;
      } catch {
        return false;
      }
    },
    {
      message:
        "The JSON provided is incorrect. Please verify their structure, format, and content before resubmitting.",
    },
  )
  .transform((val) => {
    try {
      return JSON.parse(val);
    } catch {
      return val; // Return the original string if parsing fails
    }
  });

// 0 is a real price: self-hosted and local models cost nothing per token.
// An empty field reaches the schema as "" (or NaN), so the type error is the
// "required" case.
const tokenCostValidation = (label) =>
  z
    .number({
      required_error: `${label} is required`,
      invalid_type_error: `${label} is required`,
    })
    .min(0, `${label} cannot be negative`);

export const customModelValidation = (currentTab) =>
  z
    .object({
      modelProvider: z.string().min(1, "Model provider is required"),
      modelName: z.string().min(1, "Model name is required"),
      inputTokenCost: tokenCostValidation("Input token cost"),
      outputTokenCost: tokenCostValidation("Output token cost"),
      key: z.string().optional(),
      apiBaseUrl: z.string().optional(),
      awsAccessKeyId: z.string().optional(),
      awsSecretAccessKey: z.string().optional(),
      awsRegionName: z.string().optional(),
      vertexCredentialJson: vertexJSONValidation,
      vertexLocation: z.string().optional(),
      awsCredentialsJson: z.string().optional(),
      azureApiBase: z.string().optional(),
      azureApiVersion: z.string().optional(),
      apiKey: z.string().optional(),
      azureCredentialJson: z.string().optional(),
      azureEndpointType: z.string().optional(),
      openAiCredentialJson: z.string().optional(),
      customConfiguration: customConfigurationValidation.optional(),
      apiBase: z.string().optional(),
    })
    .superRefine((data, ctx) => {
      if (
        data.modelProvider === "bedrock" ||
        data.modelProvider === "sagemaker"
      ) {
        if (currentTab === "JSON") {
          if (!data.awsCredentialsJson) {
            ctx.addIssue({
              path: ["awsCredentialsJson"],
              message: "AWS Credentials JSON is required",
              code: "custom",
            });
          }
        } else {
          if (!data.awsAccessKeyId) {
            ctx.addIssue({
              path: ["awsAccessKeyId"],
              message: "AWS Access Key ID is required",
              code: "custom",
            });
          }
          if (!data.awsSecretAccessKey) {
            ctx.addIssue({
              path: ["awsSecretAccessKey"],
              message: "AWS Secret Access Key is required",
              code: "custom",
            });
          }
          if (!data.awsRegionName) {
            ctx.addIssue({
              path: ["awsRegionName"],
              message: "AWS Region Name is required",
              code: "custom",
            });
          }
        }
      }

      if (data.modelProvider === "vertex_ai") {
        if (!data.vertexCredentialJson) {
          ctx.addIssue({
            path: ["vertexCredentialJson"],
            message: "Vertex Credential JSON is required",
            code: "custom",
          });
        }
      }

      if (data.modelProvider === "vertex_ai") {
        if (!data.vertexCredentialJson) {
          ctx.addIssue({
            path: ["vertexCredentialJson"],
            message: "Vertex Credential JSON is required",
            code: "custom",
          });
        }
      }

      if (data.modelProvider === "openai") {
        if (currentTab === "JSON") {
          if (!data.openAiCredentialJson) {
            ctx.addIssue({
              path: ["openAiCredentialJson"],
              message: "OpenAI Credential JSON is required",
              code: "custom",
            });
          }
        } else {
          if (!data.key) {
            ctx.addIssue({
              path: ["key"],
              message: "OpenAI key is required",
              code: "custom",
            });
          }
        }
      }

      if (data.modelProvider === "azure") {
        if (currentTab === "JSON") {
          if (!data.azureCredentialJson) {
            ctx.addIssue({
              path: ["azureCredentialJson"],
              message: "Azure Credential JSON is required",
              code: "custom",
            });
          }
        } else {
          if (!data.azureApiBase) {
            ctx.addIssue({
              path: ["azureApiBase"],
              message: "Azure API Base is required",
              code: "custom",
            });
          }
          if (!data.azureEndpointType) {
            ctx.addIssue({
              path: ["azureEndpointType"],
              message: "Azure Endpoint Type is required",
              code: "custom",
            });
          }
          if (data.azureEndpointType !== "foundry" && !data.azureApiVersion) {
            ctx.addIssue({
              path: ["azureApiVersion"],
              message: "Azure API Version is required",
              code: "custom",
            });
          }
          if (!data.apiKey) {
            ctx.addIssue({
              path: ["apiKey"],
              message: "Azure API Key is required",
              code: "custom",
            });
          }
        }
      }

      if (data.modelProvider === "custom") {
        if (
          !data.customConfiguration ||
          data.customConfiguration.length === 0
        ) {
          ctx.addIssue({
            path: ["customConfiguration"],
            message: "At least one custom configuration is required",
            code: "custom",
          });
        }
      }
    });
