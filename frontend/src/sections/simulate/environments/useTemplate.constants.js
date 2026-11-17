import PropTypes from "prop-types";
import { slugify } from "./helpers/slugify";

// APPROVED — all 21 strings; site SIMBUILD owner-resolution-001, 22458062.
// This is a local template preview, not a cloud build. Keep all new or changed
// user-visible strings for that action here; the existing CLI copy is below.
export const TEMPLATE_ADOPT_LABEL = "Open local draft";

export const BUILD_MODES = { PREVIEW: "preview", LOCAL: "local" };

export const BUILD_MODE_TABS = [
  {
    value: BUILD_MODES.PREVIEW,
    label: "Preview here",
    icon: "solar:document-text-linear",
  },
  {
    value: BUILD_MODES.LOCAL,
    label: "Build locally",
    icon: "solar:laptop-2-linear",
  },
];

export const DRAFT_CARD = {
  title: "Preview a template draft",
  subtitle:
    "Inspect sample definitions locally. No cloud environment is created.",
};

export const DRAFT_BULLETS = [
  {
    icon: "solar:box-minimalistic-linear",
    text: "Review summary counts for the template's data, tools and hard rules in this browser tab.",
  },
  {
    icon: "solar:user-speak-rounded-linear",
    text: "Inspect a sample baseline agent and generated scenario drafts; no agent is connected.",
  },
  {
    icon: "solar:magic-stick-3-linear",
    text: "To build a cloud environment, open the Build environment tab in Simulate and connect your own agent or source.",
  },
];

export const STATS_CARD = {
  title: "Template contents",
  subtitle: "Sample definitions for a local draft, not a deployed environment.",
};

export const TEMPLATE_STAT_COPY = {
  world: (count) => `${count.toLocaleString()} sample rows`,
  tools: (count) => `${count} tool definitions`,
  rules: (count) => `${count} rule definitions`,
  scenarios: (count) => `${count} draft scenarios`,
};

export const AGENT_STAT_VALUE = "Sample baseline (not connected)";

export const LOCAL_DRAFT_NOTICE = {
  icon: "solar:info-circle-linear",
  heading: "Local preview only.",
  body: "Opening a draft creates no cloud environment, sandbox or simulation run.",
};

export const DRAFT_PREVIEW_COPY = {
  title: "Local draft preview",
  subtitle:
    "This draft is only held on this page. Leaving or refreshing discards it.",
  agent: "Sample agent",
  scenarios: "Scenario drafts",
  close: "Close draft",
};

export const LOCAL_CARD = {
  title: "Develop locally",
  subtitle:
    "Scaffold this template into your own repo and iterate from your terminal.",
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
    body: "Run the core pack locally against the seeded agent. Nothing leaves your laptop.",
    cmd: (slug) => `fai sim run --env ${slug} --pack core`,
  },
  {
    n: 3,
    title: "Deploy",
    body: "Runs execute on our infrastructure. Simulation results remain in the simulation views; spans from agents run in hosted sandboxes aren't stored in Observe in this release.",
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
