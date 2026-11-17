// The keymap surface a route shows when no screen registered one yet
// (R3 G0): `?` opens the sheet with this surface first. Screens that call
// useShortcuts(surface) always win (they sit on the surface stack).
const UUID = "[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}";
const RULES = [
  [
    new RegExp(`^/dashboard/develop/${UUID}/compare/tasks`),
    () => "taskCompare",
  ],
  [new RegExp(`^/dashboard/develop/${UUID}/compare/matrix`), () => "matrix"],
  [new RegExp(`^/dashboard/develop/${UUID}/compare/summary`), () => "summary"],
  [new RegExp(`^/dashboard/develop/${UUID}/compare`), () => "results"],
  [new RegExp(`^/dashboard/develop/${UUID}/review/`), () => "review"],
  [/^\/dashboard\/develop\/experiment\/[^/]+\/matrix/, () => "matrix"],
  [/^\/dashboard\/develop\/experiment\/[^/]+\/summary/, () => "summary"],
  [/^\/dashboard\/develop\/experiment\//, () => "results"],
  [/^\/dashboard\/annotations\/queues\/[^/]+\/annotate/, () => "review"],
  [
    new RegExp(`^/dashboard/develop/${UUID}/?$`),
    (sp) => {
      const tab = sp.get("tab");
      if (sp.get("row")) return "row";
      if (tab === "experiments") return "leaderboard";
      if (tab === "summary") return "summary";
      return "grid";
    },
  ],
  [/^\/dashboard\/develop\/?$/, () => "list"],
];

/** `grid`, `matrix`, `leaderboard`, … for a pathname + search, or null. */
export function surfaceForRoute(pathname, search = "") {
  const sp = new URLSearchParams(search ?? "");
  const hit = RULES.find(([re]) => re.test(pathname ?? ""));
  return hit ? hit[1](sp) : null;
}
