const GENDER_CHOICES = ["male", "female"];
const AGE_GROUP_CHOICES = ["18-25", "25-32", "32-40", "40-50", "50-60", "60+"];
const LOCATION_CHOICES = [
  "United States",
  "Canada",
  "United Kingdom",
  "Australia",
  "India",
];
const OCCUPATION_CHOICES = [
  "Student",
  "Teacher",
  "Engineer",
  "Doctor",
  "Nurse",
  "Business Owner",
  "Manager",
  "Sales Representative",
  "Customer Service",
  "Technician",
  "Consultant",
  "Accountant",
  "Marketing Professional",
  "Retired",
  "Homemaker",
  "Freelancer",
  "Other",
];
const PERSONALITY_CHOICES = [
  "Friendly and cooperative",
  "Professional and formal",
  "Cautious and skeptical",
  "Impatient and direct",
  "Detail-oriented",
  "Easy-going",
  "Anxious",
  "Confident",
  "Analytical",
  "Emotional",
  "Reserved",
  "Talkative",
];
const COMMUNICATION_STYLE_CHOICES = [
  "Direct and concise",
  "Detailed and elaborate",
  "Casual and friendly",
  "Formal and polite",
  "Technical",
  "Simple and clear",
  "Questioning",
  "Assertive",
  "Passive",
  "Collaborative",
];
const LANGUAGE_CHOICES = ["English", "Hindi"];
const ACCENT_CHOICES = [
  "American",
  "Australian",
  "Indian",
  "Canadian",
  "Neutral",
];
const CONVERSATION_SPEED_CHOICES = ["0.5", "0.75", "1.0", "1.25", "1.5"];
const SENSITIVITY_CHOICES = ["1", "2", "3", "4", "5", "6", "7", "8", "9", "10"];
const STANDARD_USAGE_CHOICES = ["none", "light", "moderate", "heavy"];

export const SIMULATION_PERSONA_FILTER_FIELDS = [
  { id: "persona.name", name: "Persona Name", type: "text" },
  { id: "persona.description", name: "Description", type: "text" },
  {
    id: "persona.gender",
    name: "Gender",
    type: "categorical",
    choices: GENDER_CHOICES,
  },
  {
    id: "persona.age_group",
    name: "Age Group",
    type: "categorical",
    choices: AGE_GROUP_CHOICES,
  },
  {
    id: "persona.occupation",
    name: "Occupation",
    type: "categorical",
    choices: OCCUPATION_CHOICES,
  },
  {
    id: "persona.location",
    name: "Location",
    type: "categorical",
    choices: LOCATION_CHOICES,
  },
  {
    id: "persona.personality",
    name: "Personality",
    type: "categorical",
    choices: PERSONALITY_CHOICES,
  },
  {
    id: "persona.communication_style",
    name: "Communication Style",
    type: "categorical",
    choices: COMMUNICATION_STYLE_CHOICES,
  },
  {
    id: "persona.language",
    name: "Language",
    type: "categorical",
    choices: LANGUAGE_CHOICES,
  },
  {
    id: "persona.languages",
    name: "Languages",
    type: "categorical",
    choices: LANGUAGE_CHOICES,
  },
  {
    id: "persona.accent",
    name: "Accent",
    type: "categorical",
    choices: ACCENT_CHOICES,
  },
  {
    id: "persona.conversation_speed",
    name: "Conversation Speed",
    type: "categorical",
    choices: CONVERSATION_SPEED_CHOICES,
  },
  { id: "persona.multilingual", name: "Multilingual", type: "boolean" },
  { id: "persona.background_sound", name: "Background Sound", type: "boolean" },
  {
    id: "persona.finished_speaking_sensitivity",
    name: "Finished Speaking Sensitivity",
    type: "categorical",
    choices: SENSITIVITY_CHOICES,
  },
  {
    id: "persona.interrupt_sensitivity",
    name: "Interrupt Sensitivity",
    type: "categorical",
    choices: SENSITIVITY_CHOICES,
  },
  { id: "persona.keywords", name: "Keywords", type: "text" },
  {
    id: "persona.tone",
    name: "Tone",
    type: "categorical",
    choices: ["formal", "casual", "neutral"],
  },
  {
    id: "persona.verbosity",
    name: "Verbosity",
    type: "categorical",
    choices: ["brief", "balanced", "detailed"],
  },
  {
    id: "persona.punctuation",
    name: "Punctuation",
    type: "categorical",
    choices: ["clean", "minimal", "expressive", "erratic"],
  },
  {
    id: "persona.slang_usage",
    name: "Slang Usage",
    type: "categorical",
    choices: STANDARD_USAGE_CHOICES,
  },
  {
    id: "persona.typos_frequency",
    name: "Typos Frequency",
    type: "categorical",
    choices: ["none", "rare", "occasional", "frequent"],
  },
  {
    id: "persona.regional_mix",
    name: "Regional Mix",
    type: "categorical",
    choices: STANDARD_USAGE_CHOICES,
  },
  {
    id: "persona.emoji_usage",
    name: "Emoji Usage",
    type: "categorical",
    choices: ["never", "light", "regular", "heavy"],
  },
  {
    id: "persona.additional_instruction",
    name: "Additional Instruction",
    type: "text",
  },
].map((field) => ({ ...field, category: "persona" }));
