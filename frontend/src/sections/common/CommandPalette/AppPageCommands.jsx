// Go to, for the whole product: every page of the left menu a person may
// open, the AI Gateway's pages and the Omega Console's pages, registered in
// the palette from every page. Mounted next to the palette (once per layout).
//
// The menu's own hooks decide edition, feature and role (the same data the
// sidebar draws); here a row is listed only where the menu would draw it as
// a link for this person: not withheld (inert), not switched off, its roles
// (when it names any) include the person's role, and it opens a page of this
// app (outside links such as Docs stay in the menu). Rows are flattened with
// the top bar search's own getAllItems, so the palette finds what that search
// box found, by title or by address.
import { useEffect, useMemo, useRef } from "react";
import { useAuthContext } from "src/auth/hooks";
import {
  useNavDashBoardData,
  useNavGatewayData,
  useNavSettingsData,
  useNavUpgradeData,
} from "src/layouts/dashboard/config-navigation";
import { useShellNavData } from "src/layouts/dashboard/useShellNavData";
import { getAllItems } from "src/layouts/common/searchbar/utils";
import useGatewayCommands from "src/sections/gateway/hooks/useGatewayCommands";
import { isOmegaConsoleEnabled } from "src/sections/omega/shell/flags";
import { useOmegaProducts } from "src/sections/omega/shell/useOmegaProducts";
import { OMEGA_PRODUCT } from "src/sections/omega/shell/rollout";
import {
  CONSOLE_ACCESS,
  useConsoleAccess,
} from "src/sections/omega/shell/omega-access";
import { useCommandStore } from "./commandRegistry";
import { consoleLinks } from "./consoleBridge";

export const APP_PAGES_SOURCE = "app-pages";

const sentenceCase = (text) =>
  typeof text === "string" && text
    ? text.charAt(0).toUpperCase() + text.slice(1).toLowerCase()
    : "";

const isAppPath = (path) =>
  typeof path === "string" && path.startsWith("/") && !path.startsWith("//");

// A workspace row of Settings opens that workspace's settings page.
const WORKSPACE_SETTINGS = /^\/dashboard\/settings\/workspace\/[^/]+\/?$/;

const shownTo = (role) => (item) =>
  !item?.inert &&
  !item?.disabled &&
  (!item?.roles || item.roles.includes(`${role}`));

function visibleRows(items, shown) {
  return (items ?? [])
    .filter(shown)
    .map((item) =>
      item.children
        ? { ...item, children: visibleRows(item.children, shown) }
        : item,
    );
}

/**
 * Go to commands for menu sections, as the menu shows them to `role`.
 * @param {{subheader?: string, items: object[]}[]} sections
 * @param {{role?: string, area?: string}} [options]  `area` prefixes the hint
 */
export function pageCommandsOf(sections, { role, area } = {}) {
  const shown = shownTo(role);
  const data = (sections ?? []).map((section) => ({
    ...section,
    items: visibleRows(section.items, shown),
  }));
  const seen = new Set();
  return getAllItems({ data }).flatMap(({ group, title, path }) => {
    if (!isAppPath(path) || typeof title !== "string" || seen.has(path))
      return [];
    seen.add(path);
    const where = sentenceCase(group);
    return [
      {
        id: `go.page:${path}`,
        group: "Go to",
        label: WORKSPACE_SETTINGS.test(path)
          ? `Open ${title} settings`
          : `Go to ${title}`,
        keywords: [title, where, path, "open", "navigate"].filter(Boolean),
        hint: [area, where].filter(Boolean).join(" · ") || undefined,
        href: path,
        // Listed in Recent only while the menu still offers it to this person.
        requiresLiveRegistration: true,
        run: ({ navigate }) => navigate(path),
      },
    ];
  });
}

/** What a person sees of a command list: a change here registers again. */
const signatureOf = (commands) =>
  commands
    .map((c) => [c.id, c.label, c.hint ?? "", c.href ?? ""].join("\u0001"))
    .join("\u0002");

export default function AppPageCommands() {
  const { user } = useAuthContext();
  const role = user?.role;
  const main = useShellNavData();
  const home = useNavDashBoardData();
  const resources = useNavUpgradeData();
  const settings = useNavSettingsData();
  const gateway = useNavGatewayData();
  const omegaProducts = useOmegaProducts();
  const consoleOffered =
    isOmegaConsoleEnabled() && omegaProducts.listed(OMEGA_PRODUCT.CONSOLE);
  const consoleViewer = useConsoleAccess().access === CONSOLE_ACCESS.VIEWER;

  const commands = useMemo(() => {
    const seen = new Set();
    const once = (list) =>
      list.filter((cmd) => {
        if (seen.has(cmd.href)) return false;
        seen.add(cmd.href);
        return true;
      });
    return once([
      ...pageCommandsOf([...home, ...main, ...resources], { role }),
      ...pageCommandsOf(settings, { role, area: "Settings" }),
      ...(consoleOffered ? consoleLinks({ viewer: consoleViewer }) : []),
    ]);
  }, [main, home, resources, settings, role, consoleOffered, consoleViewer]);

  // The menu hooks hand back new arrays on every render, so the list is
  // registered again only when what it shows changes, and in place: the
  // source keeps its spot in the registry (the palette orders Go to by area,
  // not by registration).
  const signature = signatureOf(commands);
  const latest = useRef(commands);
  latest.current = commands;
  useEffect(() => {
    useCommandStore.getState().register(APP_PAGES_SOURCE, latest.current);
  }, [signature]);
  useEffect(
    () => () => useCommandStore.getState().unregister(APP_PAGES_SOURCE),
    [],
  );
  // The AI Gateway's pages and actions, with the gateway's own ids and gates.
  useGatewayCommands(gateway);
  return null;
}
