import PropTypes from "prop-types";
import { slugify } from "./helpers/slugify";

// A prebuilt template is a world that already exists — seeded state, tools,
// rules, scenarios and a baseline agent. There's nothing to derive; the only
// decision is where to build it. These constants back the "where to build it"
// panel shown after a template card is clicked.

export const TEMPLATE_ADOPT_LABEL = "Build environment";

export const BUILD_MODES = { CLOUD: "cloud", LOCAL: "local" };

export const BUILD_MODE_TABS = [
  { value: BUILD_MODES.CLOUD, label: "Build here", icon: "solar:cloud-linear" },
  { value: BUILD_MODES.LOCAL, label: "Build locally", icon: "solar:laptop-2-linear" },
];

export const CLOUD_CARD = {
  title: "Build in the cloud",
  subtitle: "Spins up in an isolated sandbox — ready to run in seconds.",
};

export const CLOUD_BULLETS = [
  {
    icon: "solar:box-minimalistic-linear",
    text: "The seeded world, tools and hard rules come up exactly as designed.",
  },
  {
    icon: "solar:user-speak-rounded-linear",
    text: "The template's baseline agent is wired in, so you can run it right away.",
  },
  {
    icon: "solar:magic-stick-3-linear",
    text: "Add your own agent version afterwards from the environment's Agents tab.",
  },
];

export const STATS_CARD = {
  title: "What this template gives you",
  subtitle: "Already built — you are not deriving it",
};

export const AGENT_STAT_VALUE = "Seeded baseline — swap in yours after";

export const NOTHING_TOUCHES_PRODUCTION = {
  icon: "solar:shield-keyhole-linear",
  heading: "Nothing touches production.",
  body:
    "Seeded data and test credentials in an isolated sandbox — your deployed systems are never called.",
};

export const LOCAL_CARD = {
  title: "Develop locally",
  subtitle: "Scaffold this template into your own repo and iterate from your terminal.",
};

export const LOCAL_INSTALL_HINT = "pip install futureagi";

// Static copy for the three CLI steps; the command is computed per template.
const LOCAL_STEP_META = [
  {
    n: 1,
    title: "Initialize",
    body: "Scaffold the environment, its scenario packs and the seeded agent into your repo.",
    cmd: (slug, id) => `fai env init ${slug} --template ${id}`,
  },
  {
    n: 2,
    title: "Run a simulation",
    body: "Run the core pack locally against the seeded agent — nothing leaves your laptop.",
    cmd: (slug) => `fai sim run --env ${slug} --pack core`,
  },
  {
    n: 3,
    title: "Deploy",
    body: "Publish when you're ready. Runs execute on our infrastructure and traces land back in the dashboard.",
    cmd: (slug) => `fai env deploy ${slug}`,
  },
];

export const localScaffoldSteps = (template) => {
  const slug = slugify(template?.name);
  return LOCAL_STEP_META.map((step) => ({
    n: step.n,
    title: step.title,
    body: step.body,
    cmd: step.cmd(slug, template?.id),
  }));
};

// Surface → glyph for the panel header. Falls back to a neutral box.
export const TEMPLATE_SURFACE_ICON = {
  voice: "solar:phone-calling-rounded-linear",
  chat: "solar:chat-round-linear",
};
export const TEMPLATE_SURFACE_ICON_FALLBACK = "solar:box-minimalistic-linear";

export const surfaceIconFor = (surface) =>
  TEMPLATE_SURFACE_ICON[surface] || TEMPLATE_SURFACE_ICON_FALLBACK;

// Empty state when the :templateId in the URL matches no library entry.
export const TEMPLATE_NOT_FOUND_COPY =
  "That template isn't available. It may have been renamed or removed.";

// Shared prop shape for the template a build panel renders.
export const TEMPLATE_SHAPE = PropTypes.shape({
  id: PropTypes.string,
  name: PropTypes.string,
  surface: PropTypes.string,
  tagline: PropTypes.string,
  difficulty: PropTypes.string,
  tools: PropTypes.array,
  rules: PropTypes.array,
  evalPreset: PropTypes.array,
  seed: PropTypes.shape({
    tables: PropTypes.arrayOf(
      PropTypes.shape({ rows: PropTypes.number, note: PropTypes.string }),
    ),
  }),
});
