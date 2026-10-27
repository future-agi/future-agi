const toArray = (value) => {
  if (value == null) return [];
  return Array.isArray(value) ? value : [value];
};

export const normalizePersona = (persona) => {
  if (!persona || typeof persona !== "object") return persona;

  return {
    ...persona,
    personaType: persona.personaType ?? persona.persona_type,
    personaTypeDisplay:
      persona.personaTypeDisplay ?? persona.persona_type_display,
    ageGroup: toArray(persona.ageGroup ?? persona.age_group),
    profession: toArray(persona.profession ?? persona.occupation),
    communicationStyle: toArray(
      persona.communicationStyle ?? persona.communication_style,
    ),
    conversationSpeed: toArray(
      persona.conversationSpeed ?? persona.conversation_speed,
    ),
    backgroundSound: persona.backgroundSound ?? persona.background_sound,
    finishedSpeakingSensitivity: toArray(
      persona.finishedSpeakingSensitivity ??
        persona.finished_speaking_sensitivity,
    ),
    interruptSensitivity: toArray(
      persona.interruptSensitivity ?? persona.interrupt_sensitivity,
    ),
    additionalInstruction:
      persona.additionalInstruction ?? persona.additional_instruction,
    customProperties: persona.customProperties ?? persona.metadata ?? {},
    simulationType: persona.simulationType ?? persona.simulation_type,
    isDefault: persona.isDefault ?? persona.is_default,
    createdAt: persona.createdAt ?? persona.created_at,
    updatedAt: persona.updatedAt ?? persona.updated_at,
    slangUsage: persona.slangUsage ?? persona.slang_usage,
    typosFrequency: persona.typosFrequency ?? persona.typos_frequency,
    regionalMix: persona.regionalMix ?? persona.regional_mix,
    emojiUsage: persona.emojiUsage ?? persona.emoji_usage,
  };
};

export const normalizePersonas = (personas) =>
  Array.isArray(personas) ? personas.map(normalizePersona) : [];
