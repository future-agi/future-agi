import {
  CATALOG_ITEMS,
  FRAMEWORK_ITEMS,
} from "src/sections/simulate/environments/buildDrawer/buildDrawerCatalog";

export const GITHUB_SOURCE = "github";
export const UPLOAD_SOURCE = "upload";

// Hosted platforms and other ways that save a voice agent to Agents.
export const PLATFORM_SOURCES = ["vapi", "retell", "livekit", "phone"];

// What each row says it brings: on this page every platform way saves a voice
// agent, so none promises chat.
const PAGE_LINES = {
  vapi: "Add your Vapi API key and the assistant · Voice",
  retell: "Add your Retell AI API key and the agent · Voice",
};

// The SDK frameworks traceAI instruments, none connectable from here yet:
// the build drawer's list without its setup, so they show as Soon.
export const SOON_FRAMEWORKS = FRAMEWORK_ITEMS.map(
  ({ setup: _setup, ...item }) => item,
);

/**
 * The ways in on the New agent page: the build drawer's live ones, plus the
 * SDK frameworks that aren't connectable yet.
 */
export const AGENT_CONNECT_ITEMS = [
  ...CATALOG_ITEMS.map((item) =>
    PAGE_LINES[item.id] ? { ...item, next: PAGE_LINES[item.id] } : item,
  ),
  ...SOON_FRAMEWORKS,
];

export const connectItemOf = (id) =>
  AGENT_CONNECT_ITEMS.find((item) => item.id === id) || null;
