// Command registry for the ⌘K palette (R3 G0 §B6, SVP-P1-5). Pages
// register their verbs while mounted; the palette lists them under
// `Actions`. Pure helpers (ranking, recents) are exported for tests.
//
//   useRegisterCommands("matrix", [
//     { id: "matrix.baseline", label: "Make #2D the baseline",
//       keywords: ["pin"], shortcut: "makeBaseline", icon: "mdi:pin",
//       run: () => pin(d), when: () => canPin },
//   ], [canPin, d]);
import { useEffect, useRef } from "react";
import { create } from "zustand";
import {
  isSurfaceShown,
  isLaunchBuild,
  isVerbShown,
} from "src/launch/launchSwitch";
import { ON_DATASET_PAGE, SURFACE_BY_ID } from "src/launch/launchSurfaces";
import { launchRedirect } from "src/launch/launchRoutes";

/** Group order in the palette. */
export const COMMAND_GROUPS = Object.freeze([
  "Recent",
  "Actions",
  "Go to",
  "Datasets",
  "Runs",
  "Rows",
  "Settings",
  // Prototype mode only (simulateCommands.js).
  "Simulate",
  "Demo stage",
]);

/**
 * @typedef {object} Command
 * @property {string} id         stable, unique across the app
 * @property {string} [group]    one of COMMAND_GROUPS (default `Actions`)
 * @property {string} label      sentence case (`Make #2D the baseline`)
 * @property {string[]} [keywords]  extra words the search matches
 * @property {string|string[]} [shortcut]  a KEYMAP action (`makeBaseline`)
 *   or key strings (`["mod+k"]`) shown as caps on the right
 * @property {string} [icon]     Iconify name (`mdi:pin`)
 * @property {string} [href]     navigate here (recents remember it)
 * @property {string} [hint]     a quiet second line
 * @property {(ctx) => void} [run]  called on Enter / click
 * @property {() => boolean} [when]  hide unless true
 */

export const useCommandStore = create((set) => ({
  sources: {},
  register: (id, commands) =>
    set((s) => ({ sources: { ...s.sources, [id]: commands } })),
  unregister: (id) =>
    set((s) => {
      const next = { ...s.sources };
      delete next[id];
      return { sources: next };
    }),
}));

/**
 * Register page commands while the component is mounted. `run` and `when`
 * always call the latest closures; pass `deps` when labels or the list
 * itself change.
 * @param {string} id  source id (one per page / component)
 * @param {Command[]} commands
 * @param {any[]} [deps]
 */
export function useRegisterCommands(id, commands, deps = []) {
  const ref = useRef(commands);
  ref.current = commands;
  useEffect(() => {
    const live = (ref.current ?? []).map((c) => ({
      ...c,
      group: c.group ?? "Actions",
      run: (...args) =>
        (ref.current ?? []).find((x) => x.id === c.id)?.run?.(...args),
      when: c.when
        ? () => {
            const cur = (ref.current ?? []).find((x) => x.id === c.id);
            return cur?.when ? Boolean(cur.when()) : true;
          }
        : undefined,
    }));
    useCommandStore.getState().register(id, live);
    return () => useCommandStore.getState().unregister(id);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [id, ...deps]);
}

/**
 * L6 launch switch (cmdk-page:*): whether a command may show in this build.
 * A launch build drops the verbs of hidden surfaces (by id) and every
 * command whose link lands on a hidden page or deep-link key, so no page
 * can bring a hidden surface back through the palette. Prototype builds
 * and unit tests keep every command.
 */
export function launchCommandShown(cmd) {
  if (!cmd || !isLaunchBuild()) return true;
  if (!isVerbShown(cmd.id)) return false;
  if (typeof cmd.href === "string" && cmd.href.startsWith("/")) {
    const u = new URL(cmd.href, "http://x");
    // J09: classic destinations are visible, but their palette entry is NEW.
    const classicRoute = SURFACE_BY_ID["exp.classic"].entries.routes.find(
      (route) => route.re.test(u.pathname),
    );
    if (classicRoute && !isSurfaceShown(classicRoute.paletteGate)) return false;
    if (ON_DATASET_PAGE.test(u.pathname)) {
      if (
        u.searchParams.get("tab") === "experiments" &&
        !isSurfaceShown("dataset.tab.experiments")
      )
        return false;
      if (u.searchParams.has("new_experiment") && !isSurfaceShown("exp.new"))
        return false;
    }
    if (launchRedirect({ pathname: u.pathname, search: u.search }))
      return false;
  }
  return true;
}

/** Every registered command whose `when()` holds (and the launch shows). */
export function registeredCommands(sources) {
  return Object.values(sources ?? {})
    .flat()
    .filter((c) => {
      try {
        return (!c.when || c.when()) && launchCommandShown(c);
      } catch {
        return false;
      }
    });
}

const norm = (s) =>
  String(s ?? "")
    .toLowerCase()
    .replace(/[#·×]/g, " ")
    .replace(/\s+/g, " ")
    .trim();

/**
 * Match score of a command for a query: 0 = no match. Every query word
 * must appear in the label or keywords; label prefix hits rank first.
 */
export function commandScore(cmd, query) {
  const q = norm(query);
  if (!q) return 1;
  const label = norm(cmd.label);
  const hay = `${label} ${norm((cmd.keywords ?? []).join(" "))} ${norm(cmd.hint)}`;
  const words = q.split(" ");
  if (!words.every((w) => hay.includes(w))) return 0;
  let score = 1;
  if (label === q) score += 4;
  if (label.startsWith(q)) score += 3;
  if (label.split(" ").some((w) => w.startsWith(words[0]))) score += 2;
  if ((cmd.keywords ?? []).some((k) => norm(k) === q)) score += 3;
  return score;
}

/** Commands matching a query, best first (stable within a score). */
export function rankCommands(commands, query) {
  return (commands ?? [])
    .map((c, i) => ({ c, i, s: commandScore(c, query) }))
    .filter((x) => x.s > 0)
    .sort((a, b) => b.s - a.s || a.i - b.i)
    .map((x) => x.c);
}

// ------------------------------------------------------------- scope copy

/** The one `Copy link to this view` label (the global command owns it). */
export const COPY_VIEW_LABEL = "Copy link to this view";

/**
 * L7c g1 (palette:open): what the palette searches, exactly: datasets in
 * this workspace (by name), commands and glossary terms, plus this dataset's
 * runs and rows when a dataset is open.
 */
export const paletteScopeText = (inDataset) =>
  inDataset
    ? isVerbShown("run.x")
      ? "Searching datasets, commands and this dataset's runs and rows"
      : // L6 launch switch: no runs list in a launch build.
        "Searching datasets, commands and this dataset's rows"
    : "Searching datasets and commands";

// ------------------------------------------------------------- recents

export const RECENT_MAX = 8;
export const recentKey = (userId) =>
  `fa.palette.recent.${userId || "proto-user"}`;

/**
 * L7c g1 (palette:recent): a recent with a link belongs to the prototype
 * stage it was opened on (`stage`, null outside the prototype). Links from
 * another stage are dropped (their dataset / experiment / row may not exist
 * there); commands without a link stay.
 */
export const recentFitsStage = (r, stage = null) =>
  !r?.href || (r.stage ?? null) === (stage ?? null);

function readAll(userId) {
  try {
    const raw = JSON.parse(window.localStorage.getItem(recentKey(userId)));
    return Array.isArray(raw) ? raw : [];
  } catch {
    return [];
  }
}

export function readRecent(userId, stage = null) {
  return readAll(userId)
    .filter((r) => recentFitsStage(r, stage) && launchCommandShown(r))
    .slice(0, RECENT_MAX);
}

/** Remember an executed command (id, label, group, icon, href, stage). */
export function pushRecent(userId, cmd, stage = null) {
  if (!cmd?.id) return [];
  const entry = {
    id: cmd.id,
    label: cmd.label,
    group: cmd.group ?? "Actions",
    icon: cmd.icon ?? null,
    href: cmd.href ?? null,
    stage: stage ?? null,
  };
  const next = [entry, ...readAll(userId).filter((r) => r.id !== cmd.id)].slice(
    0,
    RECENT_MAX * 3,
  );
  try {
    window.localStorage.setItem(recentKey(userId), JSON.stringify(next));
  } catch {
    /* storage blocked */
  }
  return next
    .filter((r) => recentFitsStage(r, stage) && launchCommandShown(r))
    .slice(0, RECENT_MAX);
}
