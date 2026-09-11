import process from "node:process";

process.env.AI_PROVIDERS_ROUTE_MODE = "global";

await import("./settings-workspace-custom-models-crud-smoke.mjs");
