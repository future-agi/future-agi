import process from "node:process";

process.env.INTEGRATIONS_LIFECYCLE_ROUTE = "workspace";

await import("./settings-global-integrations-lifecycle-smoke.mjs");
