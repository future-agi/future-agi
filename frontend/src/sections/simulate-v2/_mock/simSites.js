/*
  Simulated websites (T47): sites we run for browser and computer-use
  environments. Each resets to its seed before every trial and records what
  the agent changed, so a trial can be checked against the site's state —
  not just against what the agent says it did. They are always allowed.
*/

export const SIM_SITES = {
  "env-browser": [
    {
      host: "app.taskly.dev",
      name: "Taskly",
      kind: "Todo app",
      pages: 6,
      records: ["todo_items", "sessions"],
    },
    {
      host: "play.2048.io",
      name: "2048",
      kind: "Game",
      pages: 1,
      records: ["game_states"],
    },
    {
      host: "app.acme-admin.com",
      name: "Acme Admin",
      kind: "Admin console",
      pages: 9,
      records: ["accounts", "exports"],
    },
  ],
};

/** The simulated sites a world or environment runs, by its template id. */
export const simSitesOf = (id) => SIM_SITES[id] || [];

/* The site each browser app in a trial is served from. */
const HOST_OF_APP = {
  todo: "app.taskly.dev",
  game: "play.2048.io",
  admin: "app.acme-admin.com",
};
export const siteHostOfApp = (app) => HOST_OF_APP[app] || null;

/**
 * What a trial changed on its simulated site, read off its actions:
 * [{ record, change }]. Nothing persists past the trial — the site resets.
 */
export const siteChangesOf = (app, steps = []) => {
  const typed = steps.filter((s) => s.action === "type").length;
  const ticked = steps.filter(
    (s) => s.action === "click" && /checkbox/i.test(s.target || ""),
  ).length;
  if (app === "todo")
    return [
      {
        record: "todo_items",
        change: `${Math.max(1, typed)} added · ${ticked} marked done`,
      },
    ];
  if (app === "game") {
    const moves = steps.filter(
      (s) => s.action === "press" || s.action === "key",
    ).length;
    return [
      {
        record: "game_states",
        change: `${moves || steps.length} moves · board saved`,
      },
    ];
  }
  const exported = steps.some((s) => /export/i.test(s.target || ""));
  return [
    { record: "accounts", change: `${Math.max(1, typed)} updated` },
    { record: "exports", change: exported ? "1 created" : "none" },
  ];
};
