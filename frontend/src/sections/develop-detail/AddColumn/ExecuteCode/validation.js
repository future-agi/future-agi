import { z } from "zod";

const ExecuteCodeValidation = (isConditionalNode = false, isEdit = false) => {
  return z.object({
    code: z.string().min(1, "Code is required"),
    new_column_name:
      isConditionalNode || isEdit
        ? z.string().optional()
        : z.string().min(1, "Column name is required"),
    concurrency: z.number().positive("Concurrency must be a positive integer"),
  });
};

export default ExecuteCodeValidation;
