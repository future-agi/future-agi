// The host side of the palette bridge with the embedded Omega Console.
//
// It rides the embed's existing postMessage channel (omega-embed/1, see
// sections/omega/embed/protocol.js) with three added messages:
//   child  -> parent  {type: "embed.palette.open"}
//                     open the app palette (the Console's own Cmd-K)
//   child  -> parent  {type: "embed.palette.commands", commands: [
//                       {id, title, group?, keywords?}, ...]}
//                     the Console's commands; each list replaces the last
//   parent -> child   {type: "embed.palette.run", id}
//                     a person picked that command in the app palette
// A child message counts only when it comes from the Console frame's own
// window AND from the frame's expected origin, and only once the frame is
// connected (its gate said ready), the same rules use-host-navigate.js
// applies. A list that breaks any bound below is ignored whole.
import { useEffect, useRef, useState } from "react";
import { useShortcutStore } from "src/sections/common/ux/useShortcuts";
import { VIEWER_SWITCHER_DETAIL } from "src/sections/omega/shell/omega-access";
import { useCommandStore } from "./commandRegistry";

export const PALETTE_MESSAGE = Object.freeze({
  open: "embed.palette.open",
  commands: "embed.palette.commands",
  run: "embed.palette.run",
});

/** The palette group of the Console's own commands and links. */
export const CONSOLE_AREA = "Omega Console";
export const CONSOLE_SOURCE = "omega-console";
const LINKS_SOURCE = "omega-console-links";

export const CONSOLE_COMMANDS_MAX = 100;
const ID = /^[A-Za-z0-9._:-]{1,80}$/;
const TITLE_MAX = 120;
const GROUP_MAX = 40;
const KEYWORDS_MAX = 8;
const KEYWORD_MAX = 40;
// eslint-disable-next-line no-control-regex
const CONTROL_CHARS = /[\u0000-\u001f\u007f]/;

const isText = (value, max) =>
  typeof value === "string" &&
  value.trim().length > 0 &&
  value.length <= max &&
  !CONTROL_CHARS.test(value);

const isRecord = (value) =>
  value !== null && typeof value === "object" && !Array.isArray(value);

/**
 * The Console's commands as clean copies, or null when the list (or any
 * entry in it) is malformed.
 */
export function parseConsoleCommands(list) {
  if (!Array.isArray(list) || list.length > CONSOLE_COMMANDS_MAX) return null;
  const seen = new Set();
  const out = [];
  for (const entry of list) {
    if (!isRecord(entry)) return null;
    const { id, title, group, keywords } = entry;
    if (typeof id !== "string" || !ID.test(id) || seen.has(id)) return null;
    if (!isText(title, TITLE_MAX)) return null;
    if (group !== undefined && !isText(group, GROUP_MAX)) return null;
    if (
      keywords !== undefined &&
      (!Array.isArray(keywords) ||
        keywords.length > KEYWORDS_MAX ||
        !keywords.every((k) => isText(k, KEYWORD_MAX)))
    )
      return null;
    seen.add(id);
    out.push({
      id,
      title,
      ...(group === undefined ? {} : { group }),
      ...(keywords === undefined ? {} : { keywords: [...keywords] }),
    });
  }
  return out;
}

/**
 * Links into the Console's pages that exist as routes on this build: the
 * Console home and Console chat (the post-login landing route). For a viewer
 * they carry the product switcher's own hint (the tab shows the viewer panel).
 * @param {{viewer?: boolean}} [options]
 */
export const consoleLinks = ({ viewer = false } = {}) =>
  [
    ["go.console", "Go to Omega Console", "/dashboard/console"],
    ["go.console.chat", "Go to Console chat", "/dashboard/console/chat"],
  ].map(([id, label, href]) => ({
    id,
    group: "Go to",
    label,
    keywords: ["console", "omega", "chat", "agents", href],
    ...(viewer ? { hint: VIEWER_SWITCHER_DETAIL } : {}),
    icon: "mdi:robot-outline",
    href,
    // Listed in Recent only while the Console is still offered.
    requiresLiveRegistration: true,
    run: ({ navigate }) => navigate(href),
  }));

const FRAME_SELECTOR = '[data-testid="omega-embed-frame"]';

/**
 * Listen for the Console's palette messages and keep its commands in the
 * palette while the frame is connected.
 * @param {object} options
 * @param {{ current: HTMLElement | null }} options.containerRef  wraps the frame
 * @param {string | null} options.expectedOrigin  the only origin the frame may speak from
 * @param {boolean} options.enabled  true once the frame is connected
 * @param {Window} [options.hostWindow]
 */
export function useConsolePaletteBridge({
  containerRef,
  expectedOrigin,
  enabled,
  hostWindow = window,
}) {
  const latest = useRef(enabled);
  latest.current = enabled;
  const [received, setReceived] = useState(false);

  // Until the Console sends its own commands, its pages are listed as links.
  useEffect(() => {
    if (received) return undefined;
    useCommandStore.getState().register(LINKS_SOURCE, consoleLinks());
    return () => useCommandStore.getState().unregister(LINKS_SOURCE);
  }, [received]);

  useEffect(() => {
    if (enabled) return;
    useCommandStore.getState().unregister(CONSOLE_SOURCE);
    setReceived(false);
  }, [enabled]);

  useEffect(() => {
    if (!expectedOrigin) return undefined;
    const frameWindow = () =>
      containerRef.current?.querySelector(FRAME_SELECTOR)?.contentWindow ??
      null;
    const runInConsole = (id) => {
      const target = frameWindow();
      if (!latest.current || !target) return;
      target.postMessage({ type: PALETTE_MESSAGE.run, id }, expectedOrigin);
    };
    const onMessage = (event) => {
      if (!latest.current) return;
      const data = event.data;
      if (!isRecord(data)) return;
      if (
        data.type !== PALETTE_MESSAGE.open &&
        data.type !== PALETTE_MESSAGE.commands
      )
        return;
      if (event.origin !== expectedOrigin) return;
      const target = frameWindow();
      if (!target || event.source !== target) return;
      if (data.type === PALETTE_MESSAGE.open) {
        useShortcutStore.getState().setPaletteOpen(true);
        return;
      }
      const commands = parseConsoleCommands(data.commands);
      if (!commands) return;
      setReceived(commands.length > 0);
      useCommandStore.getState().register(
        CONSOLE_SOURCE,
        commands.map((c) => ({
          id: `console.${c.id}`,
          group: CONSOLE_AREA,
          label: c.title,
          keywords: c.keywords ?? [],
          hint: c.group,
          icon: "mdi:robot-outline",
          requiresLiveRegistration: true,
          run: () => runInConsole(c.id),
        })),
      );
    };
    hostWindow.addEventListener("message", onMessage);
    return () => {
      hostWindow.removeEventListener("message", onMessage);
      useCommandStore.getState().unregister(CONSOLE_SOURCE);
    };
  }, [containerRef, expectedOrigin, hostWindow]);
}
