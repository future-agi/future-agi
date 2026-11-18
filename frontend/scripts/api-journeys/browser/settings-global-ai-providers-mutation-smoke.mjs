import process from "node:process";

process.env.AI_PROVIDERS_ROUTE_MODE = "global";

await import("./settings-workspace-ai-providers-smoke.mjs");
