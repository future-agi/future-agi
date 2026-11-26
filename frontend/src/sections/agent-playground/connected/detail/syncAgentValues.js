import { simulateAgentDefinitionsVersionsChangeCreateBodyPromptMax as PROMPT_MAX } from "src/generated/api-contracts/api.zod";
import { phoneNumberError } from "src/sections/simulate/environments/components/phoneNumber";

export { PROMPT_MAX };

const NAME_MAX = 512;

export const PROMPT_HELP = {
  phone:
    "The prompt your agent answers calls with. It only seeds scenarios; it never changes the live agent.",
  livekit:
    "LiveKit doesn't expose your agent's prompt, so paste it here. It only seeds scenarios; it never changes the live agent.",
};

export const promptProblem = (prompt) => {
  if (!prompt.trim()) return "Enter the prompt your agent runs with";
  if (prompt.length > PROMPT_MAX)
    return `Keep it under ${PROMPT_MAX.toLocaleString()} characters`;
  return null;
};

export const phoneProblem = (number) => {
  const raw = number.trim();
  if (!raw) return "Enter the number the platform calls";
  if (!raw.startsWith("+"))
    return "In international format, like +14155550100.";
  return phoneNumberError("", raw);
};

export const nameProblem = (name) => {
  if (!name.trim()) return "Enter the name your LiveKit worker registers with";
  if (name.trim().length > NAME_MAX)
    return `Keep it under ${NAME_MAX} characters`;
  return null;
};

export const initialValues = (config = {}) => ({
  prompt: config.description || "",
  phoneNumber: config.contact_number || "",
  agentName: config.livekit_agent_name || "",
  speaksFirst: config.target_speaks_first === true,
});

export const changeBody = (kind, start, values) => {
  const settings = {};
  if (kind === "phone" && values.phoneNumber.trim() !== start.phoneNumber) {
    settings.phone_number = values.phoneNumber.trim();
  }
  if (kind === "livekit" && values.agentName.trim() !== start.agentName) {
    settings.agent_name = values.agentName.trim();
  }
  if (values.speaksFirst !== start.speaksFirst) {
    settings.target_speaks_first = values.speaksFirst;
  }
  return {
    kind: "manual",
    ...(values.prompt.trim() !== start.prompt.trim()
      ? { prompt: values.prompt.trim() }
      : {}),
    ...(Object.keys(settings).length ? { settings } : {}),
  };
};
