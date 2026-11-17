import {
  NUMERIC_BUTTON_CHOICES_MESSAGE,
  hasTooManyButtonChoices,
} from "src/sections/annotations/labels/numeric-button-choices";
import { getNumberValidation } from "src/utils/validation";
import { z } from "zod";

const createTextAnnotationSettingsSchema = (
  originalMin,
  originalMax,
  storedPlaceholderEmpty = false,
) =>
  z
    .object({
      // A label stored with an empty placeholder can be saved with it empty;
      // a new label, or one stored with a placeholder, needs one.
      placeholder: storedPlaceholderEmpty
        ? z.string()
        : z.string().min(1, "Placeholder is required"),
      // Max has no upper limit of its own, as the server sets none.
      maxLength: getNumberValidation("Max length is required", {
        integerOnly: true,
        positive: true,
      }),
      // Min may be 0.
      minLength: getNumberValidation("Min length is required", {
        integerOnly: true,
        zeroAndPositive: true,
      }),
    })
    .superRefine((data, ctx) => {
      if (
        data.minLength &&
        data.maxLength &&
        Number(data.minLength) > Number(data.maxLength)
      ) {
        ctx.addIssue({
          code: z.ZodIssueCode.custom,
          message: "Min length cannot be greater than max length",
          path: ["minLength"],
        });
      }

      if (
        data.minLength &&
        data.maxLength &&
        Number(data.maxLength) < Number(data.minLength)
      ) {
        ctx.addIssue({
          code: z.ZodIssueCode.custom,
          message: "Max length cannot be less than min length",
          path: ["maxLength"],
        });
      }

      if (
        data.minLength &&
        data.maxLength &&
        Number(data.minLength) === Number(data.maxLength)
      ) {
        ctx.addIssue({
          code: z.ZodIssueCode.custom,
          message: "Min length cannot be equal to max length",
          path: ["minLength"],
        });
        ctx.addIssue({
          code: z.ZodIssueCode.custom,
          message: "Max length cannot be equal to min length",
          path: ["maxLength"],
        });
      }

      if (originalMin !== undefined && Number(data.minLength) > originalMin) {
        ctx.addIssue({
          code: z.ZodIssueCode.custom,
          message:
            "Min cannot be increased while editing — existing annotations may be affected.",
          path: ["minLength"],
        });
      }

      if (originalMax !== undefined && Number(data.maxLength) < originalMax) {
        ctx.addIssue({
          code: z.ZodIssueCode.custom,
          message:
            "Max cannot be decreased while editing — existing annotations may be affected.",
          path: ["maxLength"],
        });
      }
    });

const TextAnnotationSettingsSchema = createTextAnnotationSettingsSchema();

const createNumericAnnotationSettingsSchema = (originalMin, originalMax) =>
  z
    .object({
      // The same words and rules as the other numeric label form: decimals
      // are allowed, as the server allows them.
      min: getNumberValidation("Min is required", {
        zeroAndPositive: true,
        zeroAndPositiveMessage: "Enter a value of 0 or more",
      }),
      max: getNumberValidation("Max is required", {
        positive: true,
        positiveMessage: "Enter a value above 0",
      }),
      stepSize: getNumberValidation("Step size is required", {
        positive: true,
        positiveMessage: "Enter a value above 0",
      }),
      displayType: z.string().min(1, "Display type is required"),
    })
    .superRefine((data, ctx) => {
      if (data.stepSize > data.max) {
        ctx.addIssue({
          code: z.ZodIssueCode.custom,
          message: "Step size cannot be greater than max value",
          path: ["stepSize"],
        });
      }

      // A button scale shows at most 11 choices; a label saved earlier with
      // more still draws, but an edit to it is refused until the step or the
      // display type changes.
      if (
        data.displayType === "button" &&
        Number(data.stepSize) > 0 &&
        hasTooManyButtonChoices(data.min, data.max, data.stepSize)
      ) {
        ctx.addIssue({
          code: z.ZodIssueCode.custom,
          message: NUMERIC_BUTTON_CHOICES_MESSAGE,
          path: ["stepSize"],
        });
      }

      if (data.min && data.max && Number(data.min) > Number(data.max)) {
        ctx.addIssue({
          code: z.ZodIssueCode.custom,
          message: "Min value cannot be greater than max value",
          path: ["min"],
        });
      }

      if (data.min && data.max && Number(data.max) < Number(data.min)) {
        ctx.addIssue({
          code: z.ZodIssueCode.custom,
          message: "Max value cannot be less than min value",
          path: ["max"],
        });
      }

      if (data.min && data.max && Number(data.min) === Number(data.max)) {
        ctx.addIssue({
          code: z.ZodIssueCode.custom,
          message: "Min value cannot be equal to max value",
          path: ["min"],
        });
        ctx.addIssue({
          code: z.ZodIssueCode.custom,
          message: "Max value cannot be equal to min value",
          path: ["max"],
        });
      }

      if (originalMin !== undefined && Number(data.min) > originalMin) {
        ctx.addIssue({
          code: z.ZodIssueCode.custom,
          message:
            "Min cannot be increased while editing — existing annotations may be affected.",
          path: ["min"],
        });
      }

      if (originalMax !== undefined && Number(data.max) < originalMax) {
        ctx.addIssue({
          code: z.ZodIssueCode.custom,
          message:
            "Max cannot be decreased while editing — existing annotations may be affected.",
          path: ["max"],
        });
      }
    });

const NumericAnnotationSettingsSchema = createNumericAnnotationSettingsSchema();

// A new label has 5 stars. A stored label keeps the count it was saved with,
// a whole number from 1 as the server allows; this form has no field for it.
const StarAnnotationSettingsSchema = z.object({
  noOfStars: z.number().int().min(1),
});

const CategoricalAnnotationSettingsSchema = z.object({
  options: z
    .array(z.object({ id: z.string(), value: z.string() }))
    .min(1, "At least one option is required")
    .transform((v) => v.map((o) => ({ label: o.value }))),
  multiChoice: z.boolean(),
});

const buildDiscriminatedUnion = (textSettings, numericSettings) =>
  z.discriminatedUnion("type", [
    z.object({
      name: z.string().min(1, "Name is required"),
      type: z.literal("text"),
      description: z.string().optional(),
      settings: textSettings,
    }),
    z.object({
      name: z.string().min(1, "Name is required"),
      type: z.literal("numeric"),
      description: z.string().optional(),
      settings: numericSettings,
    }),
    z.object({
      name: z.string().min(1, "Name is required"),
      type: z.literal("categorical"),
      description: z.string().optional(),
      settings: CategoricalAnnotationSettingsSchema,
    }),
    z.object({
      name: z.string().min(1, "Name is required"),
      type: z.literal("star"),
      description: z.string().optional(),
      settings: StarAnnotationSettingsSchema,
    }),
    z.object({
      name: z.string().min(1, "Name is required"),
      type: z.literal("thumbs_up_down"),
      description: z.string().optional(),
      settings: z.object({}),
    }),
  ]);

export const createCreateEditLabelValidationSchema = (editData) => {
  if (!editData)
    return buildDiscriminatedUnion(
      TextAnnotationSettingsSchema,
      NumericAnnotationSettingsSchema,
    );

  if (editData.type === "text") {
    return buildDiscriminatedUnion(
      createTextAnnotationSettingsSchema(
        // A Min stored below 0 can be raised to 0, the lowest Min this form
        // takes. A label stored without a Min keeps no Min check.
        Math.max(0, Number(editData.settings?.minLength)),
        Number(editData.settings?.maxLength),
        editData.settings?.placeholder === "",
      ),
      NumericAnnotationSettingsSchema,
    );
  }

  if (editData.type === "numeric") {
    return buildDiscriminatedUnion(
      TextAnnotationSettingsSchema,
      createNumericAnnotationSettingsSchema(
        Number(editData.settings?.min),
        Number(editData.settings?.max),
      ),
    );
  }

  return buildDiscriminatedUnion(
    TextAnnotationSettingsSchema,
    NumericAnnotationSettingsSchema,
  );
};

export const CreateEditLabelValidationSchema = z.discriminatedUnion("type", [
  z.object({
    name: z.string().min(1, "Name is required"),
    type: z.literal("text"),
    description: z.string().optional(),
    settings: TextAnnotationSettingsSchema,
  }),
  z.object({
    name: z.string().min(1, "Name is required"),
    type: z.literal("numeric"),
    description: z.string().optional(),
    settings: NumericAnnotationSettingsSchema,
  }),
  z.object({
    name: z.string().min(1, "Name is required"),
    type: z.literal("categorical"),
    description: z.string().optional(),
    settings: CategoricalAnnotationSettingsSchema,
  }),
  z.object({
    name: z.string().min(1, "Name is required"),
    type: z.literal("star"),
    description: z.string().optional(),
    settings: StarAnnotationSettingsSchema,
  }),
  z.object({
    name: z.string().min(1, "Name is required"),
    type: z.literal("thumbs_up_down"),
    description: z.string().optional(),
    settings: z.object({}),
  }),
]);
