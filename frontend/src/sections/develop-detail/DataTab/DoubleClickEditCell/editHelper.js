import { AGENT_TYPES } from "src/sections/agents/constants";
import { z } from "zod";

export const NOT_A_WEB_ADDRESS_MESSAGE = "The value is not a web address.";

export const isDocumentWebAddress = (value) => {
  if (typeof value !== "string" || !value.trim()) {
    return false;
  }
  try {
    const parsed = new URL(value.trim());
    return parsed.protocol === "http:" || parsed.protocol === "https:";
  } catch {
    return false;
  }
};

export const getPopperDimensions = (dataType) => {
  const dimensions = {
    "date and time": { width: 700, height: 600 },
    image: { width: 450, height: 400 },
    images: { width: 550, height: 500 },
    json: { width: 600, height: 500 },
    array: { width: 400, height: 300 },
    text: { width: 400, height: 300 },
    boolean: { width: 400, height: 300 },
    integer: { width: 400, height: 300 },
    float: { width: 400, height: 300 },
    audio: { width: 350, height: 300 },
  };

  return dimensions[dataType] || { width: 400, height: 300 };
};

export const getPopperStyle = (position, dataType) => {
  return {
    zIndex: 9999,
    position: "absolute",
    transform: `translate(${position.x}px, ${position.y}px)`,
    backgroundColor: "var(--bg-paper)",
    borderRadius: "12px",
    boxShadow: "0px 0px 10px rgba(240, 240, 241, 1)",
    width: "auto",
    minWidth: "200px",
    ...(dataType === "date and time" && {
      height: "553px",
      borderRadius: "12px",
    }),
  };
};

export const personEditDefaultValue = (persona) => {
  const language =
    typeof persona?.language === "object"
      ? Object.values(persona?.language)?.[0]
      : persona?.language;
  return {
    ...persona,
    language,
    conversationSpeed: persona?.conversationSpeed?.toString() ?? "",
    finishedSpeakingSensitivity: isNaN(
      parseInt(persona?.finishedSpeakingSensitivity),
    )
      ? null
      : parseInt(persona?.finishedSpeakingSensitivity),
    interruptSensitivity: isNaN(parseInt(persona?.interruptSensitivity))
      ? null
      : parseInt(persona?.interruptSensitivity),
  };
};
export const PersonEditValidationSchema = z
  .object({
    simulationType: z.enum([AGENT_TYPES.VOICE, AGENT_TYPES.CHAT]),
    gender: z.string().min(1, "Gender is required"),
    ageGroup: z.string().min(1, "Age Group is required"),
    location: z.string().min(1, "Location is required"),
    profession: z.string().min(1, "Profession is required"),

    personality: z
      .union([z.string(), z.array(z.string())])
      .optional()
      .transform((val) => {
        if (!val) return "";
        return Array.isArray(val) ? val[0] ?? "" : val;
      }),

    communicationStyle: z.string().min(1, "Communication Style is required"),

    accent: z.string().optional(),

    backgroundSound: z.string().optional(),

    conversationSpeed: z
      .union([z.string(), z.array(z.string())])
      .optional()
      .transform((val) => {
        if (!val) return "";
        return Array.isArray(val) ? val[0] ?? "" : val;
      }),

    finishedSpeakingSensitivity: z
      .union([z.string(), z.number()])
      .transform((val) => val.toString())
      .nullable(),

    interruptSensitivity: z
      .union([z.string(), z.number()])
      .transform((val) => val.toString())
      .nullable(),

    language: z.string().min(1, "Language is required"),
    tone: z.string().optional(),
    punctuation: z.string().optional(),
    typosFrequency: z.string().optional(),
    slangUsage: z.string().optional(),
    regionalMix: z.string().optional(),
    emojiUsage: z.string().optional(),
    verbosity: z.string().optional(),
  })
  .superRefine((data, ctx) => {
    if (data.simulationType === AGENT_TYPES.VOICE && !data.accent?.trim()) {
      ctx.addIssue({
        path: ["accent"],
        message: "Accent is required for voice simulation",
        code: z.ZodIssueCode.custom,
      });
    }
  });
