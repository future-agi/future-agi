export const ENTRY_TAB = {
  MY: "my-environments",
  PERSONAS: "personas",
};
export const DEFAULT_ENTRY_TAB = ENTRY_TAB.MY;
// The old Build tab's ?tab value. Links to it open the Build environment
// drawer over My Environments.
export const BUILD_DRAWER_TAB = "build";

// Matches the product tab rail (see HarnessDetail): the theme gives every tab a
// 40px right margin, which spreads short labels apart and breaks the row into
// separate boxes. Override it at the Tabs level so tabs sit flush and space
// themselves with padding — the indicator then spans a whole tab as one rail.
// The builder chat header shares ENV_TAB_RAIL_HEIGHT so the two line up.
export const ENV_TAB_RAIL_HEIGHT = 38;

// The environment workspace rail is Vel's: the product's own tabs, unstyled,
// 32px tall. The builder chat header and the build screen's rail match it.
export const WORKSPACE_RAIL_HEIGHT = 32;

// Vel's tab style (his theme's MuiTabs/MuiTab overrides), applied to the
// workspace rail only so the product's other tab strips are unchanged.
export const WORKSPACE_TABS_SX = {
  minHeight: WORKSPACE_RAIL_HEIGHT,
  "& .MuiTabs-indicator": {
    height: 2,
    borderRadius: 1,
    bgcolor: "secondary.main",
  },
  "& .MuiTab-root": {
    p: "0 6px",
    opacity: 1,
    minWidth: 0,
    minHeight: WORKSPACE_RAIL_HEIGHT,
    fontSize: 13,
    lineHeight: "20px",
    fontWeight: 500,
    textTransform: "none",
    "&:not(:last-of-type)": { mr: "8px" },
    "&.Mui-selected": { color: "text.primary" },
    "&:not(.Mui-selected)": {
      color: "text.subtitle",
      "&:hover": { color: "text.primary" },
    },
  },
};

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
    "An environment is the world your agent runs in: seeded state, tools, and rules. Pick how you want to bring your agent in and we take care of the rest.",
  connectHeading: "Or connect your own agent",
  connectSub: "We work with what you already have: no rewrite, no adapter.",
};
