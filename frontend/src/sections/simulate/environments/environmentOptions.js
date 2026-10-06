export const OPTION_STATUS = { LIVE: "live", COMING_SOON: "coming_soon" };

export const OPTION_ID = {
  TEMPLATES: "templates",
  WEB: "web",
  SOURCE: "source",
  HOSTED: "hosted",
  MCP: "mcp",
  UPLOAD: "upload",
  LOCAL: "local",
};

export const OPTIONS = [
  {
    id: "templates",
    group: "bring",
    title: "Prebuilt Environments",
    icon: "solar:widget-linear",
    blurb:
      "Skip world setup: pick an environment the harness already built, then run your own copy.",
    setupSubtitle:
      "Ready-made environments shared by everyone. Pick one to get your own copy.",
    status: OPTION_STATUS.LIVE,
  },
  {
    id: "web",
    group: "hero",
    title: "Web Environments",
    icon: "solar:copy-linear",
    status: OPTION_STATUS.COMING_SOON,
  },
  {
    id: "source",
    group: "bring",
    title: "Source repository",
    icon: "solar:code-linear",
    blurb:
      "Read the code straight from a git host. Scenarios and tools stay in sync with the real code as it changes. No manual updates when your agent evolves.",
    preview: ["GitHub", "GitLab", "Bitbucket"],
    setupSubtitle:
      "We read the code so scenarios stay in sync with your actual tools.",
    status: OPTION_STATUS.LIVE,
  },
  {
    id: "hosted",
    group: "bring",
    title: "Hosted platform",
    icon: "solar:cloud-linear",
    blurb:
      "Connect an agent living on a managed voice or chat platform by its ID. We handle the auth and route every simulated turn through the platform's own API.",
    preview: ["Vapi", "Retell", "Bland", "ElevenLabs", "LiveKit"],
    setupSubtitle: "Point at an agent living on a managed platform.",
    status: OPTION_STATUS.LIVE,
  },
  {
    id: "mcp",
    group: "bring",
    title: "MCP server",
    icon: "solar:plug-circle-linear",
    blurb:
      "Any MCP server can back the environment. Its tools become the action space your agent operates against, one-to-one. No adapter code required.",
    preview: ["HTTP", "SSE", "stdio"],
    setupSubtitle:
      "The MCP server exposes tools; those tools become the environment's action space.",
    status: OPTION_STATUS.COMING_SOON,
  },
  {
    id: "upload",
    group: "bring",
    title: "Code upload",
    icon: "solar:cloud-upload-linear",
    blurb:
      "Upload a local folder when you can't push to a git host. Good for prototypes, private research code, or one-off experiments you want to spin up fast.",
    preview: [".py", ".ts", ".js"],
    setupSubtitle: "Upload your agent code and we'll analyze it in place.",
    status: OPTION_STATUS.LIVE,
  },
  {
    id: "local",
    group: "bring",
    title: "Build locally",
    icon: "solar:laptop-2-linear",
    blurb:
      "Run the environment on your own machine over a secure tunnel. Nothing leaves your laptop. Great for regulated code, air-gapped setups, or offline dev.",
    preview: ["macOS", "Linux", "Windows"],
    setupSubtitle:
      "The CLI runs on your machine and streams simulations back over a secure tunnel.",
    status: OPTION_STATUS.COMING_SOON,
  },
];

export const BRING_YOUR_AGENT_ORDER = [
  "source",
  "upload",
  "hosted",
  "mcp",
  "local",
];

export const HERO_CLONES = ["Slack", "Notion", "Gmail", "Salesforce", "Linear"];

export const HERO_COPY = {
  templates: {
    tag: "Fastest",
    description:
      "Ready-made environments the harness already generated and validated. Pick one, get your own copy, and run it in under a minute.",
  },
  web: {
    tag: "Live SaaS sandboxes",
    description:
      "Your agent calls the real SDKs (Slack, Notion, Salesforce), but the calls land in a sandbox we own, seeded to your prompt and torn down between runs.",
    moreLabel: "+ 8 more",
  },
};

export const ENTRY_TAB = { BUILD: "build", MY: "my-environments" };
export const DEFAULT_ENTRY_TAB = ENTRY_TAB.BUILD;

// Matches the product tab rail (see HarnessDetail): the theme gives every tab a
// 40px right margin, which spreads short labels apart and breaks the row into
// separate boxes. Override it at the Tabs level so tabs sit flush and space
// themselves with padding — the indicator then spans a whole tab as one rail.
// The builder chat header shares ENV_TAB_RAIL_HEIGHT so the two line up.
export const ENV_TAB_RAIL_HEIGHT = 38;

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
