// Falcon's Skill.icon API field is consumed by each of these components.
// Keep this explicit: new backend defaults must fail the coverage test until mapped.
const skillConsumers = [
  "src/sections/falcon-ai/components/CustomizePanel.jsx",
  "src/sections/falcon-ai/components/FalconAIHeader.jsx",
  "src/sections/falcon-ai/components/SkillEditorDialog.jsx",
  "src/sections/falcon-ai/components/SkillPicker.jsx",
  "src/sections/falcon-ai/components/SlashCommandPicker.jsx",
  "src/sections/falcon-ai/components/UserMessage.jsx",
];
export const backendIconConsumers = Object.fromEntries(
  [
    "mdi:bug-check-outline",
    "mdi:bug-outline",
    "mdi:clipboard-check-outline",
    "mdi:compare-arrows",
    "mdi:creation",
    "mdi:currency-usd",
    "mdi:database-plus",
    "mdi:star",
    "mdi:star-four-points",
    "mdi:text-box-edit-outline",
  ].map((name) => [name, skillConsumers]),
);
