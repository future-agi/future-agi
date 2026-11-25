export const ENTRY_TAB = {
  MY: "my-environments",
  PERSONAS: "personas",
};
export const DEFAULT_ENTRY_TAB = ENTRY_TAB.MY;
// The old Build tab's ?tab value. Links to it open the Build environment
// drawer over My Environments.
export const BUILD_DRAWER_TAB = "build";

// The product tab rail: the theme gives every tab a
// 40px right margin, which spreads short labels apart and breaks the row into
// separate boxes. Override it at the Tabs level so tabs sit flush and space
// themselves with padding — the indicator then spans a whole tab as one rail.
// The builder chat header shares ENV_TAB_RAIL_HEIGHT so the two line up.
export const ENV_TAB_RAIL_HEIGHT = 32;

export const ENV_TABS_SX = {
  minHeight: ENV_TAB_RAIL_HEIGHT,
  "& .MuiTab-root:not(.Mui-selected)": { color: "text.subtitle" },
  "& .MuiTab-root": {
    minHeight: ENV_TAB_RAIL_HEIGHT,
    minWidth: "auto",
    textTransform: "none",
    pl: 1.5,
    pr: 2.25,
    "&:not(:last-of-type)": { mr: 0 },
  },
};

export const ENVIRONMENTS_HEADER = {
  title: "Environments",
  subtitle:
    "Build a simulated world for your agent, then run callers against it and read the failures.",
  personasSubtitle:
    "Reusable callers for voice and chat agents. Use writes an environment's tasks in a persona's voice.",
  connectHeading: "Or connect your own agent",
  connectSub: "We work with what you already have: no rewrite, no adapter.",
};
