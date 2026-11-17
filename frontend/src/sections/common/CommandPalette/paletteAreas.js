// The palette's "current area": what the person is in now, listed first.
//   AI Gateway     on /dashboard/gateway/*: the gateway's pages and actions
//   Omega Console  on /dashboard/console/*: the Console's own commands, or
//                  links into its pages until the Console sends them
//   This page      elsewhere: the verbs the open page registered
import {
  isConsolePath,
  isGatewayPath,
} from "src/sections/omega/shell/products";
import { CONSOLE_AREA } from "./consoleBridge";

export const AREA_LABEL = Object.freeze({
  gateway: "AI Gateway",
  console: CONSOLE_AREA,
  page: "This page",
});

export function areaOf(pathname) {
  if (isGatewayPath(pathname)) return "gateway";
  if (isConsolePath(pathname)) return "console";
  return "page";
}

const isGatewayCommand = (cmd) =>
  cmd.id.startsWith("go.gateway:") || cmd.id.startsWith("gateway.");

const isConsoleCommand = (cmd) =>
  cmd.id === "go.console" ||
  cmd.id.startsWith("go.console.") ||
  cmd.id.startsWith("console.");

const isSettingsPage = (cmd) =>
  typeof cmd.href === "string" && cmd.href.startsWith("/dashboard/settings");

/** The area a Go to row leads into. */
function goToAreaOf(cmd) {
  if (!cmd?.id) return "page";
  if (cmd.id.startsWith("go.gateway:")) return "gateway";
  if (isConsoleCommand(cmd)) return "console";
  return isSettingsPage(cmd) ? "settings" : "page";
}

// Inside Go to: the current area's pages, then the rest of the product, then
// the other areas.
const GO_TO_ORDER = Object.freeze({
  page: ["page", "settings", "gateway", "console"],
  gateway: ["gateway", "page", "settings", "console"],
  console: ["console", "page", "settings", "gateway"],
});

/**
 * Where a Go to row sorts on a tie, for the current area (lower first).
 * @param {object} cmd  a Go to row
 * @param {"gateway"|"console"|"page"} area
 */
export function goToRank(cmd, area) {
  const order = GO_TO_ORDER[area] ?? GO_TO_ORDER.page;
  return order.indexOf(goToAreaOf(cmd));
}

/**
 * Whether a registered command belongs to the current area's group.
 * @param {object} cmd  a registered command
 * @param {"gateway"|"console"|"page"} area
 * @param {{consoleCommands?: boolean}} [context]  the Console sent commands
 */
export function inArea(cmd, area, { consoleCommands = false } = {}) {
  if (!cmd?.id) return false;
  if (area === "gateway") return isGatewayCommand(cmd);
  if (area === "console")
    return consoleCommands
      ? cmd.id.startsWith("console.")
      : cmd.id === "go.console" || cmd.id.startsWith("go.console.");
  return (cmd.group ?? "Actions") === "Actions" && !isGatewayCommand(cmd);
}
