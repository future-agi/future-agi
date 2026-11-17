import { z } from "zod";

// Hugging Face embedding models model serving will load (its allowlist,
// model_serving/app/config/settings.py APPROVED_HUGGINGFACE_MODELS, mirrored
// by futureagi/model_hub/serializers/contracts.py). Any other ID is refused
// by add_vector_db_column and at run time, so the form refuses it first,
// with the server's message. Sorted as the server sorts them.
export const APPROVED_HUGGINGFACE_EMBEDDING_MODELS = Object.freeze([
  "all-MiniLM-L6-v2",
  "all-mpnet-base-v2",
  "sentence-transformers/all-MiniLM-L6-v2",
  "sentence-transformers/all-mpnet-base-v2",
]);
// Embedding types whose model is loaded from Hugging Face.
export const HUGGINGFACE_EMBEDDING_TYPES = Object.freeze([
  "huggingface",
  "sentence_transformers",
]);
export const HUGGINGFACE_EMBEDDING_MODEL_NOT_APPROVED = `Hugging Face embedding model is not approved. Approved models: ${APPROVED_HUGGINGFACE_EMBEDDING_MODELS.join(", ")}.`;

// Shared by every vector DB: both fields or neither, and a Hugging Face
// model must be approved. OpenAI model names are remote API names, free.
const embeddingConfigSchema = z
  .object({
    model: z.string().optional(),
    type: z.string().optional(),
  })
  .superRefine((data, ctx) => {
    if (data.model?.length > 0 || data.type?.length > 0) {
      if (!data.model?.length) {
        ctx.addIssue({
          code: z.ZodIssueCode.custom,
          message: "Model is required",
          path: ["model"],
        });
      } else if (
        HUGGINGFACE_EMBEDDING_TYPES.includes(data.type) &&
        !APPROVED_HUGGINGFACE_EMBEDDING_MODELS.includes(data.model)
      ) {
        ctx.addIssue({
          code: z.ZodIssueCode.custom,
          message: HUGGINGFACE_EMBEDDING_MODEL_NOT_APPROVED,
          path: ["model"],
        });
      }
      if (!data.type?.length) {
        ctx.addIssue({
          code: z.ZodIssueCode.custom,
          message: "Type is required",
          path: ["type"],
        });
      }
    }
  });

// const stringToIntSchema = (errorMessage) => {
//   return z
//     .string({
//       required_error: errorMessage,
//     })
//     .transform((val) => parseInt(val))
//     .refine((val) => !isNaN(val) && val >= 1, {
//       message: errorMessage,
//     });
// };

export const RetrievalValidationSchema = (
  isConditionalNodez = false,
  isEdit = false,
) => {
  return z.discriminatedUnion("subType", [
    z.object({
      subType: z.literal("pinecone"),
      newColumnName:
        isConditionalNodez || isEdit
          ? z.string().optional()
          : z.string().min(1, "Column name is required"),
      columnId: z.string().min(1, "Please select a column"),
      apiKey: z.string().min(1, "Please enter your API key"),
      indexName: z.string().min(1, "Please enter an index name"),
      namespace: z.string().min(1, "Please enter a namespace"),
      topK: z.number().positive("Please enter number of chunks to fetch"),
      queryKey: z.string().min(1, "Please enter a query key"),
      embeddingConfig: embeddingConfigSchema,
      key: z.string().min(1, "Please enter a key"),
      concurrency: z
        .number()
        .positive("Please enter number of chunks to fetch"),
      vectorLength: z.number().positive("Please enter vector length"),
    }),
    z.object({
      subType: z.literal("qdrant"),
      newColumnName: z.string().min(1, "Column name is required"),
      columnId: z.string().min(1, "Please select a column"),
      apiKey: z.string().min(1, "Please enter your API key"),
      topK: z.number().positive("Please enter number of chunks to fetch"),
      embeddingConfig: embeddingConfigSchema,
      concurrency: z
        .number()
        .positive("Please enter number of chunks to fetch"),
      url: z.string().url().min(1, "Please enter a URL"),
      collectionName: z.string().min(1, "Please enter a collection name"),
      key: z.string().min(1, "Please enter a key"),
      vectorLength: z.number().positive("Please enter vector length"),
    }),
    z.object({
      subType: z.literal("weaviate"),
      newColumnName: z.string().min(1, "Column name is required"),
      columnId: z.string().min(1, "Please select a column"),
      topK: z.number().positive("Please enter number of chunks to fetch"),
      apiKey: z.string().min(1, "Please enter your API key"),
      embeddingConfig: embeddingConfigSchema,
      concurrency: z
        .number()
        .positive("Please enter number of chunks to fetch"),
      url: z.string().url().min(1, "Please enter a URL"),
      collectionName: z.string().min(1, "Please enter a collection name"),
      // Weaviate's Search Type: the vector_db column runs a hybrid query
      // when search_type is "hybrid", a near-vector query otherwise.
      searchType: z.string().min(1, "Please enter a search type"),
      key: z.string().min(1, "Please enter a key"),
      vectorLength: z.number().positive("Please enter vector length"),
    }),
  ]);
};
