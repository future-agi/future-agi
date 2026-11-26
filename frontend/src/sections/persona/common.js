import _ from "lodash";
import { AGENT_TYPES } from "../agents/constants";

export const PERSONAS_DOCS_URL =
  "https://docs.futureagi.com/docs/simulation/concepts/personas";

const toArray = (val) => {
  if (val == null) return [];
  if (Array.isArray(val)) return val;
  return [val];
};

export const extractTagsFromPersona = (persona) => {
  const tags = [];
  tags.push(toArray(persona?.gender).map(_.capitalize).join(", "));
  tags.push(toArray(persona?.ageGroup).join(", "));
  tags.push(toArray(persona?.profession).join(", "));
  tags.push(toArray(persona?.accent).join(", "));
  tags.push(toArray(persona?.communicationStyle).join(", "));

  return tags?.filter(Boolean);
};

export const personaCategoriesOptions = [
  {
    label: "View All",
    value: "",
  },
  {
    label: "Custom Persona's",
    value: "custom",
  },
  {
    label: "Prebuilt Persona's",
    value: "prebuilt",
  },
];

export const personaCreationTypes = [
  {
    title: "Voice Type",
    description: "Create personas for your voice agent",
    icon: "/assets/icons/ic_voice.svg",
    value: "voice",
  },
  {
    title: "Chat Type",
    description: "Create personas for your chat agent",
    icon: "/assets/icons/ic_chat_single.svg",
    value: "text",
  },
];

export const extractGenderAgeLocationTagsFromPersona = (persona) => {
  const tags = [];
  tags.push(["Gender", toArray(persona?.gender).map(_.capitalize).join(", ")]);
  tags.push(["Age", toArray(persona?.ageGroup).join(", ")]);
  tags.push(["Location", toArray(persona?.location).join(", ")]);

  return tags.filter(([_label, value]) => Boolean(value));
};

export const extractPersonalityommunicationStyleAccentTagsFromPersona = (
  persona,
) => {
  const tags = [];
  tags.push(["Personality", toArray(persona?.personality).join(", ")]);
  tags.push([
    "Communication style ",
    toArray(persona?.communicationStyle).join(", "),
  ]);
  if (persona?.simulationType !== AGENT_TYPES.CHAT) {
    tags.push(["Accent", toArray(persona?.accent).join(", ")]);
  }

  return tags.filter(([_label, value]) => Boolean(value));
};
export const extractChatSettingsFromPersona = (persona) => {
  return {
    tone: persona?.tone ?? null,
    verbosity: persona?.verbosity ?? null,
    punctuation: persona?.punctuation ?? null,
    slangUsage: persona?.slangUsage ?? null,
    typosFrequency: persona?.typosFrequency ?? null,
    regionalMix: persona?.regionalMix ?? null,
    emojiUsage: persona?.emojiUsage ?? null,
  };
};
export const extractMultilingualityLanguagesConversationSpeedTagsFromPersona = (
  persona,
) => {
  const tags = [];
  tags.push([
    "Multilinguality",
    persona?.multilingual === false ? "OFF" : "ON",
  ]);
  tags.push(["Languages ", toArray(persona?.languages).join(", ")]);
  tags.push([
    "Conversation speed",
    toArray(persona?.conversationSpeed).join(", "),
  ]);

  return tags.filter(([_label, value]) => Boolean(value));
};

export const extractBackgroundNoiseFinishedSpeakingSensitivityInterruptSensitivityTagsFromPersona =
  (persona) => {
    const tags = [];
    tags.push([
      "Background noise",
      persona?.backgroundSound === false ? "No" : "Yes",
    ]);
    tags.push([
      "Finished speaking sensitivity",
      toArray(persona?.finishedSpeakingSensitivity).join(", "),
    ]);
    tags.push([
      "Interrupt sensitivity",
      toArray(persona?.interruptSensitivity).join(", "),
    ]);

    return tags.filter(([_label, value]) => Boolean(value));
  };
