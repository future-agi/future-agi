import { z } from "zod";

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
      embeddingConfig: z
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
            }
            if (!data.type?.length) {
              ctx.addIssue({
                code: z.ZodIssueCode.custom,
                message: "Type is required",
                path: ["type"],
              });
            }
          }
        }),
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
      embeddingConfig: z
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
            }
            if (!data.type?.length) {
              ctx.addIssue({
                code: z.ZodIssueCode.custom,
                message: "Type is required",
                path: ["type"],
              });
            }
          }
        }),
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
      embeddingConfig: z
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
            }
            if (!data.type?.length) {
              ctx.addIssue({
                code: z.ZodIssueCode.custom,
                message: "Type is required",
                path: ["type"],
              });
            }
          }
        }),
      concurrency: z
        .number()
        .positive("Please enter number of chunks to fetch"),
      url: z.string().url().min(1, "Please enter a URL"),
      collectionName: z.string().min(1, "Please enter a collection name"),
      searchType: z.string().min(1, "Please enter a search type"),
      key: z.string().min(1, "Please enter a key"),
      vectorLength: z.number().positive("Please enter vector length"),
    }),
    ...["chroma", "milvus", "pgvector"].map((subType) =>
      z.object({
        subType: z.literal(subType),
        newColumnName:
          isConditionalNodez || isEdit
            ? z.string().optional()
            : z.string().min(1, "Column name is required"),
        columnId: z.string().min(1, "Please select a column"),
        apiKey: z.string().optional(),
        url: z.string().min(1, "Please enter a connection URL"),
        collectionName: z.string().min(1, "Please enter a collection name"),
        topK: z.number().positive("Please enter number of chunks to fetch"),
        queryKey: z.string().optional(),
        key: z.string().optional(),
        embeddingConfig: z.object({
          model: z.string().min(1, "Model is required"),
          type: z.string().min(1, "Type is required"),
        }),
        concurrency: z.number().positive("Please enter a positive concurrency value"),
        vectorLength: z.number().positive("Please enter vector length"),
      }),
    ),
  ]);
};
