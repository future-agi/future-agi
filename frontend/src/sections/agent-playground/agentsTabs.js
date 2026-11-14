import { paths } from "src/routes/paths";

export const AGENTS_TABS = ["connected", "playground"];

export const agentsTabPath = (tab) => `${paths.dashboard.agents}/${tab}`;

export const CONNECTED_AGENTS_PATH = agentsTabPath("connected");

export const PLAYGROUND_AGENTS_PATH = agentsTabPath("playground");

export const CONNECT_AGENT_PATH = `${paths.dashboard.simulate.environments.root}?tab=build`;

export const resolveAgentsTab = (raw) =>
  AGENTS_TABS.includes(raw) ? raw : "connected";

// Where the sidebar's Agents link points: the tab you are on (or whose
// detail page you are in), else Connected agents. Linking to the current
// tab makes a re-click a no-op instead of switching tabs.
export const agentsSidebarPath = (pathname) => {
  const tab = AGENTS_TABS.find(
    (t) =>
      pathname === agentsTabPath(t) ||
      pathname.startsWith(`${agentsTabPath(t)}/`),
  );
  return agentsTabPath(tab || "connected");
};
