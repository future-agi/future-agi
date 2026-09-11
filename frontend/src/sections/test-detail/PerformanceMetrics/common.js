import { snakeCaseToTitleCase } from "../../../utils/utils";

// Keys are snake_case — the canonical form used by KPI responses and
// by `AGENT_METRICS` / `IGNORED_KEYS` in `test-detail/common.js`.
// `extractKpis` copies keys one by one so downstream objects only carry
// the canonical form.
const timeSystemMetrics = [
  "avg_response",
  "avg_agent_latency",
  "avg_stop_time_after_interruption",
];

const percentageSystemMetrics = ["calls_connected_percentage"];

const LABEL_MAP = {
  total_calls: "Total Calls",
  connected_calls: "Connected",
  failed_calls: "Failed",
  avg_agent_latency: "Agent Latency",
  avg_bot_wpm: "Agent WPM",
  agent_talk_percentage: "Agent Talk Percentage",
  customer_talk_percentage: "Customer Talk Percentage",
  avg_stop_time_after_interruption: "Agent Stop Latency",
  avg_toxicity: "Average Toxicity",
  avg_score: "Avg CSAT Score",
  calls_connected_percentage: "Calls Connected(%)",
};

const SUBTEXT_MAP = {
  talk_ratio: "agent/customer",
  avg_bot_wpm: "words/min",
};

const ICON_MAP = {
  total_calls: "/assets/icons/ic_phone_call.svg",
  connected_calls: "/assets/icons/components/ic_connected_call.svg",
  failed_calls: "/assets/icons/components/ic_end_call.svg",
  talk_ratio: "/assets/icons/ic_square_message.svg",
  avg_agent_latency: "/assets/icons/navbar/ic_new_clock.svg",
  avg_stop_time_after_interruption: "/assets/icons/navbar/ic_new_clock.svg",
  avg_bot_wpm: "/assets/icons/ic_user_speak.svg",
  avg_score: "/assets/icons/components/ic_avg_score.svg",
  avg_response: "/assets/icons/components/ic_avg_response.svg",
  calls_connected_percentage:
    "/assets/icons/components/ic_call_connected_percentage.svg",
  avg_user_interruption_count:
    "/assets/icons/components/ic_avg_user_interruption_count.svg",
  avg_user_interruption_rate:
    "/assets/icons/components/ic_avg_user_interruption_rate.svg",
  avg_ai_interruption_count:
    "/assets/icons/components/ic_avg_ai_interruption_count.svg",
  avg_ai_interruption_rate:
    "/assets/icons/components/ic_avg_ai_interruption_rate.svg",
  avg_user_wpm: "/assets/icons/components/ic_avg_user_wpm.svg",
  avg_talk_ratio: "/assets/icons/components/ic_avg_talk_ratio.svg",
  tokens: "/assets/icons/ic_tokens_header.svg",
  avg_turn_count: "/assets/icons/ic_turn_chat.svg",
  avg_input_tokens: "/assets/icons/ic_input_tokens.svg",
  avg_output_tokens: "/assets/icons/ic_output_tokens.svg",
};

const ICON_COLOR_MAP = {
  total_calls: "blue.500",
  connected_calls: "green.500",
  failed_calls: "red.500",
  avg_bot_wpm: "blue.500",
  talk_ratio: "green.500",
  avg_agent_latency: "orange.400",
  avg_stop_time_after_interruption: "pink.400",
  avg_score: "green.500",
  avg_response: "primary.main",
  calls_connected_percentage: "blue.500",
  avg_user_interruption_count: "red.500",
  avg_user_interruption_rate: "green.500",
  avg_ai_interruption_count: "blue.300",
  avg_ai_interruption_rate: "red.500",
  avg_user_wpm: "pink.500",
  avg_talk_ratio: "green.500",

  avg_total_tokens: "primary.main", // total tokens — informational
  avg_input_tokens: "blue.500", // input tokens — blue for inbound/input
  avg_output_tokens: "green.500", // output tokens — green for agent output
  avg_chat_latency_ms: "orange.400", // chat latency — warning-like color
  avg_turn_count: "pink.500", // conversation turns — distinct accent
  avg_csat_score: "green.500", // CSAT score — green for positive scoring
};

const TOOLTIP_MAP = {
  avg_agent_latency:
    "Average time (ms) for the agent to start a response after the user finishes speaking",
  avg_bot_wpm: "Agent's average speaking rate, measuring conversational pace",
  talk_ratio: "Ratio of time the Agent spoke versus the simulator spoke",
  avg_stop_time_after_interruption:
    "Average time (ms) the agent takes to react to, and stop speaking, when interrupted by the user",
  avg_user_wpm:
    "Calculate words per minute for the user — represents how fast the user speaks",

  avg_talk_ratio:
    "Calculate talk ratio (bot speaking time / user speaking time). Returns None when user didn't speak to avoid infinity values.",

  avg_user_interruption_count:
    "Calculate user interruptions — number of times the user interrupted the agent",

  avg_ai_interruption_count:
    "Calculate AI interruptions — number of times the agent interrupted the user",
};

export const getTooltipMessage = (key) => {
  return TOOLTIP_MAP[key] || "";
};

export const getLabel = (key) => {
  const label = LABEL_MAP[key] || snakeCaseToTitleCase(key);
  return label.replace(/\bai\b/gi, "AI");
};

export const getSuffix = (key) => {
  if (timeSystemMetrics?.includes(key)) {
    return "ms";
  } else if (percentageSystemMetrics?.includes(key)) {
    return "%";
  }
  return "";
};

export const getSubtext = (key) => {
  return SUBTEXT_MAP[key] || "";
};

export const getIcon = (key) => {
  if (key === "avg_csat_score") {
    return ICON_MAP["avg_score"];
  } else if (key === "avg_chat_latency_ms") {
    return ICON_MAP["avg_agent_latency"];
  } else if (key === "avg_total_tokens") {
    return ICON_MAP["tokens"];
  }
  return ICON_MAP[key];
};

export const getIconColor = (key) => {
  if (key === "avg_csat_score") {
    return ICON_COLOR_MAP["avg_csat_score"] || ICON_COLOR_MAP["avg_score"];
  } else if (key === "avg_chat_latency_ms") {
    return (
      ICON_COLOR_MAP["avg_chat_latency_ms"] ||
      ICON_COLOR_MAP["avg_agent_latency"]
    );
  }
  return ICON_COLOR_MAP[key] || "text.primary";
};

export const getColor = (value) => {
  if (value < 40) return "red.400"; // Low score
  if (value < 70) return "orange.300"; // Medium score
  return "green.400"; // High score
};

// utils/colorUtils.js or wherever you keep utility functions

/**
 * Generates colors from theme palette with progressive shades
 * @param {string} colorFamily - Color family name (e.g., 'pink', 'blue', 'purple')
 * @param {number} count - Number of colors needed
 * @param {object} theme - MUI theme object
 * @returns {string[]} Array of color hex values
 */
export const generateColorShades = (colorFamily, count, theme) => {
  const availableShades = [800, 700, 600, 500, 400, 300, 200, 100];
  const palette = theme.palette[colorFamily];

  if (!palette) {
    return [];
  }

  // Calculate step size to distribute colors evenly
  const step = Math.max(1, Math.floor(availableShades.length / count));
  return Array.from({ length: count }, (_, i) => {
    const shadeIndex = Math.min(i * step, availableShades.length - 1);
    const shade = availableShades[shadeIndex];
    return palette[shade] || palette[500]; // Fallback to main color
  });
};

/**
 * Gets a random color family from available theme colors
 * @returns {string} Random color family name
 */
export const getRandomColorFamily = () => {
  const colorFamilies = [
    "purple",
    "pink",
    "blue",
    "yellow",
    "orange",
    "green",
    "grey",
  ];
  return colorFamilies[Math.floor(Math.random() * colorFamilies.length)];
};

/**
 * Adds colors to deterministic evaluation data
 * @param {Array} data - Array of data items
 * @param {object} theme - MUI theme object
 * @param {string} colorFamily - Optional specific color family, random if not provided
 * @returns {Array} Data with colors added
 */
export const addColorsToData = (data, theme, colorFamily = null) => {
  const family = colorFamily || getRandomColorFamily();
  const colors = generateColorShades(family, data.length, theme);

  return data.map((item, index) => ({
    ...item,
    color: colors[index],
  }));
};

export const getChatOverrides = {
  total_calls: {
    label: "Total Chats",
    icon: "/assets/icons/ic_square_message.svg",
    iconColor: "blue.500",
    suffix: "",
  },
  connected_calls: {
    label: "Completed",
    icon: "/assets/icons/ic_message.svg",
    iconColor: "green.500",
    suffix: "",
  },
  failed_calls: {
    label: "Failed",
    icon: "/assets/icons/components/ic_end_call.svg",
    iconColor: "red.500",
    suffix: "",
  },
  calls_connected_percentage: {
    label: "Completion(%)",
    icon: "/assets/icons/components/ic_call_connected_percentage.svg",
    iconColor: "blue.500",
    suffix: "%",
  },
};
