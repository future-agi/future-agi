import { z } from "zod";
import { transformLabelObject } from "src/sections/develop-detail/Annotations/CreateEditLabel/common";

export const createTextFieldSchema = (
  settings,
  isAlreadyAnnotated = false,
  existingValue = undefined,
) => {
  const minLength = settings?.minLength ? parseInt(settings.minLength, 10) : 0;
  const maxLength = settings?.maxLength
    ? parseInt(settings.maxLength, 10)
    : 100;

  let schema = z.string().trim();

  if (minLength > 0) {
    schema = schema.min(minLength, `Minimum ${minLength} characters required`);
  }

  if (maxLength > 0) {
    schema = schema.max(maxLength, `Maximum ${maxLength} characters allowed`);
  }

  if (isAlreadyAnnotated) {
    const filled = schema.min(1, "Value cannot be empty once annotated");
    // The saved answer, left as it is, is accepted even when the label's
    // limits changed since it was saved. The limits come first so a changed
    // answer is refused with their words.
    if (typeof existingValue === "string") {
      return z.union([filled, z.literal(existingValue)]);
    }
    return filled;
  }

  return z.union([z.null(), z.undefined(), z.literal(""), schema]);
};

export const createFormSchema = (labels, defaultValues = {}) => {
  const schemaObject = {};

  labels?.forEach((item) => {
    const transformedItem = transformLabelObject(item);

    if (transformedItem.type === "text") {
      const existingValue = defaultValues[transformedItem.id];
      const isAlreadyAnnotated = existingValue != null && existingValue !== "";
      schemaObject[transformedItem.id] = createTextFieldSchema(
        transformedItem.settings,
        isAlreadyAnnotated,
        existingValue,
      );
    } else {
      // For non-text fields, use a more permissive schema
      schemaObject[transformedItem.id] = z.any().optional();
    }
  });

  return z.object({
    ...schemaObject,
    notes: z.string().optional(),
  });
};
