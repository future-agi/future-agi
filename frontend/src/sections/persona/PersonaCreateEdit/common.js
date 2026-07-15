import logger from "src/utils/logger";
import { getRandomId } from "src/utils/utils";
import { z } from "zod";

export const GenderOptions = [
  { label: "Male", value: "male" },
  { label: "Female", value: "female" },
];
export const AgeGroupOptions = [
  { label: "18-25", value: "18-25" },
  { label: "25-32", value: "25-32" },
  { label: "32-40", value: "32-40" },
  { label: "40-50", value: "40-50" },
  { label: "50-60", value: "50-60" },
  { label: "60+", value: "60+" },
];

export const ProfessionOptions = [
  { label: "Student", value: "Student" },
  { label: "Teacher", value: "Teacher" },
  { label: "Engineer", value: "Engineer" },
  { label: "Doctor", value: "Doctor" },
  { label: "Nurse", value: "nurse" },
  { label: "Business Owner", value: "Business Owner" },
  { label: "Manager", value: "manager" },
  { label: "Sales Representative", value: "Sales Representative" },
  { label: "Customer Service", value: "Customer Service" },
  { label: "Technician", value: "Technician" },
  { label: "Consultant", value: "Consultant" },
  { label: "Accountant", value: "Accountant" },
  { label: "Marketing Professional", value: "Marketing Professional" },
  { label: "Retired", value: "Retired" },
  { label: "Homemaker", value: "Homemaker" },
  { label: "Freelancer", value: "Freelancer" },
  { label: "Truck Driver", value: "Truck Driver" },
  { label: "Other", value: "Other" },
];

export const LocationOptions = [
  { label: "United States", value: "United States" },
  { label: "Canada", value: "Canada" },
  { label: "United Kingdom", value: "United Kingdom" },
  { label: "Australia", value: "Australia" },
  { label: "India", value: "India" },
];

export const PersonalityOptions = [
  { value: "Friendly and cooperative", label: "🤝 Friendly and cooperative" },
  { value: "Professional and formal", label: "💼 Professional and formal" },
  { value: "Cautious and skeptical", label: "🤔 Cautious and skeptical" },
  { value: "Impatient and direct", label: "⚡ Impatient and direct" },
  { value: "Detail-oriented", label: "🔍 Detail-oriented" },
  { value: "Easy-going", label: "😌 Easy-going" },
  { value: "Anxious", label: "😰 Anxious" },
  { value: "Confident", label: "💪 Confident" },
  { value: "Analytical", label: "📊 Analytical" },
  { value: "Emotional", label: "❤️ Emotional" },
  { value: "Reserved", label: "🤐 Reserved" },
  { value: "Talkative", label: "💬 Talkative" },
];

export const CommunicationStyleOptions = [
  { value: "Direct and concise", label: "Direct and concise" },
  { value: "Detailed and elaborate", label: "Detailed and elaborate" },
  { value: "Casual and friendly", label: "Casual and friendly" },
  { value: "Formal and polite", label: "Formal and polite" },
  { value: "Technical", label: "Technical" },
  { value: "Simple and clear", label: "Simple and clear" },
  { value: "Questioning", label: "Questioning" },
  { value: "Assertive", label: "Assertive" },
  { value: "Passive", label: "Passive" },
  { value: "Collaborative", label: "Collaborative" },
];

const accentList = [
  { value: "american", label: "American" },
  { value: "arabic", label: "Arabic" },
  { value: "australian", label: "Australian" },
  { value: "bengali", label: "Bengali" },
  { value: "brazilian", label: "Brazilian" },
  { value: "bulgarian", label: "Bulgarian" },
  { value: "canadian", label: "Canadian" },
  { value: "chinese", label: "Chinese" },
  { value: "croatian", label: "Croatian" },
  { value: "czech", label: "Czech" },
  { value: "danish", label: "Danish" },
  { value: "dutch", label: "Dutch" },
  { value: "filipino", label: "Filipino" },
  { value: "finnish", label: "Finnish" },
  { value: "french", label: "French" },
  { value: "georgian", label: "Georgian" },
  { value: "german", label: "German" },
  { value: "greek", label: "Greek" },
  { value: "gujarati", label: "Gujarati" },
  { value: "hebrew", label: "Hebrew" },
  { value: "hungarian", label: "Hungarian" },
  { value: "indian", label: "Indian" },
  { value: "indonesian", label: "Indonesian" },
  { value: "italian", label: "Italian" },
  { value: "japanese", label: "Japanese" },
  { value: "kannada", label: "Kannada" },
  { value: "korean", label: "Korean" },
  { value: "malay", label: "Malay" },
  { value: "malayalam", label: "Malayalam" },
  { value: "malaysian", label: "Malaysian" },
  { value: "mandarin", label: "Mandarin" },
  { value: "marathi", label: "Marathi" },
  { value: "neutral", label: "Neutral" },
  { value: "norwegian", label: "Norwegian" },
  { value: "polish", label: "Polish" },
  { value: "portuguese", label: "Portuguese" },
  { value: "punjabi", label: "Punjabi" },
  { value: "romanian", label: "Romanian" },
  { value: "russian", label: "Russian" },
  { value: "slovak", label: "Slovak" },
  { value: "south american", label: "South American" },
  { value: "southern", label: "Southern" },
  { value: "spanish", label: "Spanish" },
  { value: "swedish", label: "Swedish" },
  { value: "tagalog", label: "Tagalog" },
  { value: "tamil", label: "Tamil" },
  { value: "telugu", label: "Telugu" },
  { value: "thai", label: "Thai" },
  { value: "turkish", label: "Turkish" },
  { value: "ukrainian", label: "Ukrainian" },
  { value: "vietnamese", label: "Vietnamese" },
];

export const AccentOptions = accentList.sort((a, b) =>
  a.label.localeCompare(b.label),
);

const languagelist = [
  { value: "arabic", label: "Arabic" },
  { value: "bengali", label: "Bengali" },
  { value: "bulgarian", label: "Bulgarian" },
  { value: "chinese", label: "Chinese" },
  { value: "croatian", label: "Croatian" },
  { value: "czech", label: "Czech" },
  { value: "danish", label: "Danish" },
  { value: "dutch", label: "Dutch" },
  { value: "english", label: "English" },
  { value: "finnish", label: "Finnish" },
  { value: "filipino", label: "Filipino" },
  { value: "french", label: "French" },
  { value: "georgian", label: "Georgian" },
  { value: "german", label: "German" },
  { value: "greek", label: "Greek" },
  { value: "gujarati", label: "Gujarati" },
  { value: "hebrew", label: "Hebrew" },
  { value: "hindi", label: "Hindi" },
  { value: "hungarian", label: "Hungarian" },
  { value: "indonesian", label: "Indonesian" },
  { value: "italian", label: "Italian" },
  { value: "japanese", label: "Japanese" },
  { value: "kannada", label: "Kannada" },
  { value: "korean", label: "Korean" },
  { value: "malay", label: "Malay" },
  { value: "malayalam", label: "Malayalam" },
  { value: "mandarin", label: "Mandarin" },
  { value: "marathi", label: "Marathi" },
  { value: "norwegian", label: "Norwegian" },
  { value: "polish", label: "Polish" },
  { value: "portuguese", label: "Portuguese" },
  { value: "punjabi", label: "Punjabi" },
  { value: "romanian", label: "Romanian" },
  { value: "russian", label: "Russian" },
  { value: "slovak", label: "Slovak" },
  { value: "spanish", label: "Spanish" },
  { value: "swedish", label: "Swedish" },
  { value: "tagalog", label: "Tagalog" },
  { value: "tamil", label: "Tamil" },
  { value: "telugu", label: "Telugu" },
  { value: "thai", label: "Thai" },
  { value: "turkish", label: "Turkish" },
  { value: "ukrainian", label: "Ukrainian" },
  { value: "vietnamese", label: "Vietnamese" },
];

export const LanguageOptions = languagelist.sort((a, b) =>
  a.label.localeCompare(b.label),
);

export const ConversationSpeedOptions = [
  { value: "0.5", label: "🐢 0.5" },
  { value: "0.75", label: "🐌 0.75" },
  { value: "1.0", label: "🚶 1.0" },
  { value: "1.25", label: "🏃 1.25" },
  { value: "1.5", label: "🚀 1.5" },
];
export const toneOptions = [
  { value: "formal", label: "🤝 Formal" },
  { value: "neutral", label: "💬 Neutral" },
  { value: "casual", label: "😌 Casual" },
];

export const commonChattingOptions = [
  { value: "none", label: "🚫 None" },
  { value: "light", label: "💬 Light" },
  { value: "moderate", label: "☁️ Moderate" },
  { value: "heavy", label: "🗣️ Heavy" },
];

export const typoFrequencyOptions = [
  { value: "none", label: "🚫 None" },
  { value: "rare", label: "✨ Rare" },
  { value: "occasional", label: "⚠️ Occasional" },
  { value: "frequent", label: "❌ Frequent" },
];
export const punctuationUsage = [
  { value: "clean", label: "✨ Clean" },
  { value: "minimal", label: "▬ Minimal" },
  { value: "expressive", label: "💭 Expressive" },
  { value: "erratic", label: "❌ Erratic" },
];

export const emojiUsage = [
  { value: "never", label: "🚫 Never" },
  { value: "light", label: "🙂 Light" },
  { value: "regular", label: "😄 Regular" },
  { value: "heavy", label: "🤩 Heavy" },
];

// writingStyle: "",
//   punctuation: "",
//   typosFrequency: "",
//   slangUsage: "",
//   regionalMix: "",
//   emojiUsage:""
//  "tone": ["formal", "neutral", "casual"],
//         "verbosity": ["brief", "balanced", "detailed"],
//         "regional_mix": ["none", "light", "moderate", "heavy"],
//         "slang_level": ["none", "light", "moderate", "heavy"],
//         "typo_level": ["none", "rare", "occasional", "frequent"],
//         "punctuation_style": ["clean", "minimal", "expressive", "erratic"],
//         "emoji_frequency": ["never", "light", "regular", "heavy"],

export const chatOptionSettings = [
  {
    title: "Tone",
    options: toneOptions,
    fieldName: "tone",
  },
  {
    title: "Verbosity",
    options: [
      { value: "brief", label: "🔹 Brief" },
      { value: "balanced", label: "⚖️ Balanced" },
      { value: "detailed", label: "📄 Detailed" },
    ],
    fieldName: "verbosity",
  },
  {
    title: "Regional Mix",
    options: commonChattingOptions,
    fieldName: "regionalMix",
  },
  {
    title: "Slang Level",
    options: commonChattingOptions,
    fieldName: "slangUsage",
  },
  {
    title: "Typo Level",
    options: typoFrequencyOptions,
    fieldName: "typosFrequency",
  },
  {
    title: "Punctuation Style",
    options: punctuationUsage,
    fieldName: "punctuation",
  },
  {
    title: "Emoji Frequency",
    options: emojiUsage,
    fieldName: "emojiUsage",
  },
];

export const getPersonaDefaultValues = (editPersona) => {
  logger.debug("editPersona", editPersona);
  if (editPersona) {
    return {
      name: editPersona?.name,
      description: editPersona?.description,
      gender: editPersona?.gender || [],
      ageGroup: editPersona?.ageGroup || [],
      location: editPersona?.location || [],
      profession: editPersona?.occupation || [],
      personality:
        editPersona?.personality !== null
          ? editPersona?.personality?.map((item) => ({ value: item }))
          : [],
      communicationStyle: editPersona?.communicationStyle || [],
      accent: editPersona?.accent || [],
      language: editPersona?.multilingual
        ? editPersona?.languages || []
        : editPersona?.languages?.[0],
      conversationSpeed:
        editPersona?.conversationSpeed !== null
          ? editPersona?.conversationSpeed?.map((item) => ({ value: item }))
          : [],
      backgroundSound:
        editPersona?.backgroundSound !== null
          ? editPersona?.backgroundSound
            ? "true"
            : "false"
          : null,
      finishedSpeakingSensitivity:
        editPersona?.finishedSpeakingSensitivity !== null
          ? parseInt(editPersona?.finishedSpeakingSensitivity?.[0])
          : null,
      interruptSensitivity:
        editPersona?.interruptSensitivity !== null
          ? parseInt(editPersona?.interruptSensitivity?.[0])
          : null,
      customProperties: Object.entries(editPersona?.metadata || {}).map(
        ([key, value]) => ({
          key,
          value,
          id: getRandomId(),
        }),
      ),
      additionalInstruction: editPersona?.additionalInstruction || null,
      multilingual: editPersona?.multilingual || false,
      simulationType: editPersona?.simulationType,

      punctuation: editPersona?.punctuation || "",
      typosFrequency: editPersona?.typosFrequency || "",
      slangUsage: editPersona?.slangUsage || "",
      regionalMix: editPersona?.regionalMix || "",
      emojiUsage: editPersona?.emojiUsage || "",
      tone: editPersona?.tone || "",
      verbosity: editPersona?.verbosity || "",
    };
  }
  return {
    name: "",
    description: "",
    gender: [],
    ageGroup: [],
    location: [],
    profession: [],
    personality: [],
    communicationStyle: [],
    accent: [],
    language: [],
    conversationSpeed: [],
    backgroundSound: null,
    finishedSpeakingSensitivity: null,
    interruptSensitivity: null,
    customProperties: [],
    additionalInstruction: null,
    multilingual: false,
    simulationType: "",
    tone: "",
    verbosity: "",
    punctuation: "",
    typosFrequency: "",
    slangUsage: "",
    regionalMix: "",
    emojiUsage: "",
  };
};

const PersonCreateBaseValidationSchema = z.object({
  simulationType: z.enum(["voice", "text"]),
  name: z.string().min(1, "Name is required"),
  description: z.string().min(1, "Description is required"),
  gender: z.array(z.string()).transform((val) => (val.length ? val : null)),
  ageGroup: z.array(z.string()).transform((val) => (val.length ? val : null)),
  location: z.array(z.string()).transform((val) => (val.length ? val : null)),
  profession: z.array(z.string()).transform((val) => (val.length ? val : null)),
  personality: z
    .array(z.object({ value: z.string() }))
    .min(1, "Select at least one personality")
    .transform((val) => val.map((item) => item.value)),
  communicationStyle: z
    .array(z.string())
    .min(1, "Select at least one communication style")
    .transform((val) => val),
  accent: z.array(z.string()).transform((val) => (val.length ? val : null)),
  conversationSpeed: z
    .array(z.object({ value: z.string() }))
    .optional()
    .transform((val) => (val?.length ? val.map((i) => i.value) : null)),

  backgroundSound: z.enum(["true", "false"]).optional().nullable(),

  finishedSpeakingSensitivity: z.preprocess(
    (val) => (Number.isNaN(val) ? null : val),
    z
      .number()
      .nullable()
      .optional()
      .transform((val) => (val != null ? [val] : null)),
  ),

  interruptSensitivity: z.preprocess(
    (val) => (Number.isNaN(val) ? null : val),
    z
      .number()
      .nullable()
      .optional()
      .transform((val) => (val != null ? [val] : null)),
  ),

  customProperties: z.array(
    z.object({
      key: z.string().min(1, "Key is required"),
      value: z.string().min(1, "Value is required"),
    }),
  ),
  additionalInstruction: z.string().nullable(),
  tone: z.string().optional(),
  verbosity: z.string().optional(),
  punctuation: z.string().optional(),
  typosFrequency: z.string().optional(),
  slangUsage: z.string().optional(),
  regionalMix: z.string().optional(),
  emojiUsage: z.string().optional(),
});

export const PersonCreateValidationSchema = z
  .discriminatedUnion("multilingual", [
    z.object({
      multilingual: z.literal(true),
      language: z.array(z.string()).min(1, "Language is required"),
      ...PersonCreateBaseValidationSchema.shape,
    }),
    z.object({
      language: z
        .string()
        .min(1, "Language is required")
        .transform((val) => [val]),
      multilingual: z.literal(false),
      ...PersonCreateBaseValidationSchema.shape,
    }),
  ])
  .transform((data) => {
    return {
      ...data,
      customProperties: data?.customProperties?.reduce((acc, curr) => {
        acc[curr.key] = curr.value;
        return acc;
      }, {}),
    };
  });

import { useState, useCallback, useEffect } from "react";

export const usePersonaSelection = (initialAgentType = null) => {
  const [selectedPersonaType, setSelectedPersonaType] = useState(
    initialAgentType || null,
  );
  const [selectedPersonas, setSelectedPersonas] = useState([]);
  const [selectionSource, setSelectionSource] = useState(null);

  const handleTogglePersona = useCallback(
    (persona, isSelected) => {
      if (isSelected) {
        if (!selectedPersonaType) {
          setSelectedPersonaType(persona.simulationType);
          setSelectionSource("persona");
        }

        if (
          !selectedPersonaType ||
          selectedPersonaType === persona.simulationType
        ) {
          setSelectedPersonas((prev) => {
            const exists = prev.some((p) => p.id === persona.id);
            return exists ? prev : [...prev, persona];
          });
        }
      } else {
        const updated = selectedPersonas.filter((p) => p.id !== persona.id);
        setSelectedPersonas(updated);

        if (updated.length === 0 && selectionSource === "persona") {
          setSelectedPersonaType(null);
          setSelectionSource(null);
        }
      }
    },
    [selectedPersonaType, selectedPersonas, selectionSource],
  );

  const reset = useCallback(() => {
    setSelectedPersonaType(null);
    setSelectedPersonas([]);
    setSelectionSource(null);
  }, []);

  const lockTypeByAgent = useCallback((agentType) => {
    if (agentType) {
      setSelectedPersonaType(agentType);
      setSelectionSource("agent");
      setSelectedPersonas((prev) =>
        prev.filter((p) => p.simulationType === agentType),
      );
    }
  }, []);

  // Update initial agent type when it changes
  useEffect(() => {
    if (initialAgentType) {
      lockTypeByAgent(initialAgentType);
    }
  }, [initialAgentType, lockTypeByAgent]);

  return {
    selectedPersonaType,
    selectedPersonas,
    selectionSource,
    handleTogglePersona,
    reset,
    lockTypeByAgent,
  };
};
