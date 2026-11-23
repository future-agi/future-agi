/*
  The starts as drawer items, so they open in the setup view like a way to
  connect does. None has its API yet: each is the design, on sample data.
*/
export const SCRATCH_ITEM = {
  id: "scratch",
  name: "Build from scratch",
  icon: "solar:document-add-linear",
  setup: {
    panel: "scratch",
    uiOnly: true,
    flush: true,
    subtitle:
      "Describe the world to test in. Connect the agent when it's ready.",
  },
};

export const FROM_CODE_ITEM = {
  id: "from-code",
  name: "From code",
  icon: "solar:code-linear",
  setup: { panel: "fromCode", uiOnly: true, flush: true },
};

export const MCP_ITEM = {
  id: "mcp-world",
  name: "MCP server",
  logo: "mcp",
  setup: { panel: "mcpWorld", uiOnly: true, flush: true },
};

export const SANDBOX_ITEM = {
  id: "saas-sandbox",
  name: "SaaS sandbox",
  icon: "solar:widget-4-linear",
  setup: {
    panel: "sandbox",
    uiOnly: true,
    subtitle:
      "Pick the apps your agent works in and the agent itself. Each app gets a sandbox, seeded to your description.",
  },
};

/** A ready environment, opened on one of its tabs (world, start or customize). */
export const worldItem = (world, initialTab = "world") => ({
  id: `world:${world.id}:${initialTab}`,
  name: world.name,
  tag: world.typeLabel,
  world,
  initialTab,
  setup: {
    panel: "ready",
    subtitle: `${world.scenarioCount} scenarios · ${world.tools} tools · ${world.rules.length} rules`,
  },
});
