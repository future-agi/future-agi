import React, {
  useCallback,
  useContext,
  useEffect,
  useId,
  useMemo,
  useRef,
  useState,
} from "react";
import PropTypes from "prop-types";
import { useLocation, useNavigate } from "react-router-dom";
import {
  Box,
  Dialog,
  InputAdornment,
  InputBase,
  Stack,
  Typography,
} from "@mui/material";
import { useTheme } from "@mui/material/styles";
import Iconify from "src/components/iconify";
import { useSettingsContext } from "src/components/settings";
import { AuthContext } from "src/auth/context/jwt/auth-context";
import { useFeatureAllowed } from "src/hooks/useCapabilities";
import useFalconStore from "src/sections/falcon-ai/store/useFalconStore";
import useFalconDeferredAction from "src/sections/falcon-ai/hooks/useFalconDeferredAction";
import { KeyCap } from "src/sections/common/RowPanel/ShortcutSheet";
import { capForKey, keymapEntry, localiseCap } from "src/utils/keymap";
import {
  showTipsOnThisPage,
  useHintStore,
} from "src/sections/common/ux/oneview/Hint";
import { useShortcutStore } from "src/sections/common/ux/useShortcuts";
import { notify } from "src/sections/common/ux/toast";
import { searchGlossary } from "src/sections/common/ux/glossary";
import { isGlossaryShown, isVerbShown } from "src/launch/launchSwitch";
import {
  COMMAND_GROUPS,
  COPY_VIEW_LABEL,
  commandScore,
  paletteScopeText,
  pushRecent,
  rankCommands,
  readRecent,
  registeredCommands,
  useCommandStore,
} from "./commandRegistry";
import { currentProtoStage } from "src/sections/experiment-detail/results/shareLink";
import { activePaletteHost, registerPaletteHost } from "./shortcutOwner";
import { AREA_LABEL, areaOf, goToRank, inArea } from "./paletteAreas";
import {
  datasetIdOfPath,
  useDatasetCommands,
  useDatasetKind,
  useGlobalCommands,
  useRowCommands,
  useRunCommands,
} from "./providers";

// One fixed order: the current area first, then Go to, then actions, then
// what the search finds (glossary terms, datasets, runs, rows).
const SEARCH_RESULT_GROUPS = ["Glossary", "Datasets", "Runs", "Rows"];
const ACTION_GROUPS = COMMAND_GROUPS.filter(
  (g) => !["Recent", "Go to", ...SEARCH_RESULT_GROUPS].includes(g),
);
const groupOrder = (area) => [
  AREA_LABEL[area],
  "Recent",
  "Go to",
  ...ACTION_GROUPS,
  ...SEARCH_RESULT_GROUPS,
];
const PER_GROUP = {
  Datasets: 6,
  Runs: 8,
  Rows: 5,
  "Go to": 8,
  Simulate: 12,
  [AREA_LABEL.gateway]: 12,
  [AREA_LABEL.console]: 12,
  [AREA_LABEL.page]: 10,
};
// Rows whose panel only the dashboard layout renders (the ? sheet, tips,
// the Falcon sidebar): not offered where that panel is absent.
const DASHBOARD_ONLY = new Set([
  "help.tips",
  "help.shortcuts",
  "help.glossary",
  "falcon.ask",
]);

/** Commands by id, first one kept (two sources may offer the same link). */
const uniqueById = (commands) => {
  const seen = new Set();
  return commands.filter((c) => {
    if (!c?.id || seen.has(c.id)) return false;
    seen.add(c.id);
    return true;
  });
};

const capsOf = (shortcut) => {
  if (!shortcut) return [];
  if (Array.isArray(shortcut)) return shortcut.map(capForKey);
  const entry = keymapEntry(shortcut);
  return entry ? [localiseCap(entry.display[0])] : [];
};

/** Open Falcon with an optional prefilled message. */
export function useAskFalcon() {
  const navigate = useNavigate();
  const { allowed } = useFeatureAllowed("falcon_ai");
  const openSidebar = useFalconStore((s) => s.openSidebar);
  const setPendingPrompt = useFalconStore((s) => s.setPendingPrompt);
  return useCallback(
    (text) => {
      if (!allowed) {
        navigate("/dashboard/falcon-ai");
        return;
      }
      if (text) setPendingPrompt(text);
      openSidebar();
    },
    [allowed, navigate, openSidebar, setPendingPrompt],
  );
}

function Row({ cmd, active, onHover, onRun, id }) {
  const caps = capsOf(cmd.shortcut);
  return (
    <Stack
      id={id}
      role="option"
      aria-selected={active}
      direction="row"
      alignItems="center"
      gap={1.25}
      onMouseMove={onHover}
      onClick={onRun}
      data-command={cmd.id}
      sx={{
        px: 1.5,
        py: 0.875,
        mx: 0.75,
        borderRadius: 1,
        cursor: "pointer",
        bgcolor: active ? "action.selected" : "transparent",
      }}
    >
      <Iconify
        icon={cmd.icon ?? "mdi:chevron-right"}
        width={18}
        sx={{ color: "text.secondary", flexShrink: 0 }}
      />
      <Box sx={{ minWidth: 0, flex: 1 }}>
        <Typography
          typography="s1"
          color="text.primary"
          noWrap
          title={cmd.label}
        >
          {cmd.label}
        </Typography>
        {cmd.hint && (
          <Typography typography="s3" color="text.secondary" noWrap>
            {cmd.hint}
          </Typography>
        )}
      </Box>
      {caps.length > 0 && (
        <Stack direction="row" gap={0.5} sx={{ flexShrink: 0 }}>
          {caps.map((c) => (
            <KeyCap key={c} sx={{ height: 20, minWidth: 20, typography: "s3" }}>
              {c}
            </KeyCap>
          ))}
        </Stack>
      )}
      {active && cmd.href && caps.length === 0 && (
        <Typography
          typography="s3"
          color="text.disabled"
          sx={{ flexShrink: 0 }}
        >
          Open ↵
        </Typography>
      )}
    </Stack>
  );
}

Row.propTypes = {
  cmd: PropTypes.shape({
    id: PropTypes.string,
    label: PropTypes.string,
    hint: PropTypes.string,
    icon: PropTypes.string,
    href: PropTypes.string,
    shortcut: PropTypes.oneOfType([
      PropTypes.string,
      PropTypes.arrayOf(PropTypes.string),
    ]),
  }).isRequired,
  active: PropTypes.bool,
  onHover: PropTypes.func,
  onRun: PropTypes.func,
  id: PropTypes.string,
};

/**
 * CommandPalette — ⌘K anywhere (R3 G0 §B6), the one palette of the whole
 * product. Mounted once in the dashboard layout, and on the Console tab
 * (which has no dashboard layout) by ConsolePaletteHost; only the first
 * mounted host renders and takes the key. Groups: the current area (AI
 * Gateway, Omega Console or This page) · Recent · Go to · Actions ·
 * Settings · Glossary · Datasets · Runs · Rows. Arrow keys + Enter; no
 * match offers `Ask Falcon: "<text>"`.
 * @param {{host?: "app"|"console"}} props  `console`: the Console tab
 */
export default function CommandPalette({ host = "app" }) {
  const hostId = useId();
  const [, setRegistered] = useState(false);
  useEffect(() => {
    const off = registerPaletteHost(hostId);
    setRegistered(true);
    return off;
  }, [hostId]);
  const isActiveHost = activePaletteHost() === hostId;
  const onConsole = host === "console";
  const storeOpen = useShortcutStore((s) => s.paletteOpen);
  const open = storeOpen && isActiveHost;
  const setOpen = useShortcutStore((s) => s.setPaletteOpen);
  const openSheet = useShortcutStore((s) => s.openSheet);
  const sources = useCommandStore((s) => s.sources);
  const navigate = useNavigate();
  const location = useLocation();
  const theme = useTheme();
  const settings = useSettingsContext();
  const auth = useContext(AuthContext);
  const userId = auth?.user?.id ?? null;
  const hasTips = useHintStore((s) => s.mounted.length > 0);
  const askFalcon = useAskFalcon();
  // Falcon is offered only where the deployment allows it (as the menu does).
  const { allowed: falconAllowed } = useFeatureAllowed("falcon_ai");
  const schedule = useFalconDeferredAction();
  const [query, setQuery] = useState("");
  const [active, setActive] = useState(0);
  const [recent, setRecent] = useState([]);
  const listRef = useRef(null);

  // ⌘K / Ctrl+K on every page and for every focused element: capture
  // phase, so nothing below can take it (shortcutOwner.js).
  useEffect(() => {
    const onKey = (e) => {
      if (!(e.metaKey || e.ctrlKey) || e.altKey || e.shiftKey) return;
      if (String(e.key).toLowerCase() !== "k") return;
      if (activePaletteHost() !== hostId) return;
      e.preventDefault();
      e.stopPropagation();
      if (e.repeat) return;
      setOpen(!useShortcutStore.getState().paletteOpen);
    };
    window.addEventListener("keydown", onKey, true);
    return () => window.removeEventListener("keydown", onKey, true);
  }, [setOpen, hostId]);

  useEffect(() => {
    if (open) {
      setQuery("");
      setActive(0);
      setRecent(readRecent(userId, currentProtoStage()));
    }
  }, [open, userId]);

  const datasetId = datasetIdOfPath(location.pathname);
  const isDark = theme.palette.mode === "dark";
  const close = useCallback(() => setOpen(false), [setOpen]);

  const copy = useCallback(async (text, message) => {
    try {
      await navigator.clipboard.writeText(text);
      notify(message, { variant: "success", key: "palette-copy" });
    } catch {
      notify("Couldn't copy. Select the address bar and copy it.", {
        variant: "error",
        key: "palette-copy",
      });
    }
  }, []);

  const globals = useGlobalCommands({
    isDark,
    hasTips,
    showTips: () => showTipsOnThisPage(userId),
    openSheet,
    copy,
    askFalcon,
    toggleDark: () => settings.onUpdate("themeMode", isDark ? "light" : "dark"),
  });
  const datasets = useDatasetCommands(query, { enabled: open });
  const datasetKind = useDatasetKind(datasetId, { enabled: open });
  const runs = useRunCommands(datasetId, { enabled: open });
  const rows = useRowCommands(datasetId, query, {
    enabled: open,
    kind: datasetKind,
  });

  // L7c g1 (cmd:view.copyLink): the global `Copy link to this view` (keeps
  // the prototype stage) serves every page; a page's own copy is a duplicate.
  const page = uniqueById(registeredCommands(sources)).filter(
    (c) => c.label !== COPY_VIEW_LABEL,
  );
  const offered = globals.filter(
    (c) =>
      !(onConsole && DASHBOARD_ONLY.has(c.id)) &&
      (falconAllowed || c.id !== "falcon.ask"),
  );
  const area = areaOf(location.pathname);

  const groups = useMemo(() => {
    const q = query.trim();
    const registered = new Set(page);
    const consoleCommands = page.some((c) => c.id.startsWith("console."));
    const all = [...page, ...offered];
    const ranked = rankCommands(all, q);
    const out = new Map();
    const add = (group, cmd) => {
      if (!out.has(group)) out.set(group, []);
      out.get(group).push(cmd);
    };
    // A destination is listed once. A fixed Go to link also gives way to a
    // registered page with its label (two areas may each have an "Alerts").
    const listedHref = new Set();
    const listedLabel = new Set();
    const firstListing = (cmd, byLabel = false) => {
      if (cmd.href && listedHref.has(cmd.href)) return false;
      if (byLabel && listedLabel.has(cmd.label)) return false;
      if (cmd.href) listedHref.add(cmd.href);
      listedLabel.add(cmd.label);
      return true;
    };
    if (!q) {
      const byId = new Map(all.map((c) => [c.id, c]));
      recent.forEach((r) => {
        const live = byId.get(r.id);
        if (live)
          add("Recent", {
            ...live,
            group: "Recent",
            requiresLiveRegistration:
              r.requiresLiveRegistration || live.requiresLiveRegistration,
          });
        else if (!r.requiresLiveRegistration && r.href)
          add("Recent", { ...r, group: "Recent" });
      });
    }
    ranked.forEach((c) => {
      if (registered.has(c) && inArea(c, area, { consoleCommands })) {
        firstListing(c);
        add(AREA_LABEL[area], c);
        return;
      }
      const group = c.group ?? "Actions";
      if (group === "Go to" && !firstListing(c, !registered.has(c))) return;
      add(group, c);
    });
    if (q.length >= 3 && !onConsole) {
      // L6 launch switch: no glossary term of a hidden surface.
      const terms = searchGlossary(q)
        .filter(({ key }) => isGlossaryShown(key))
        .slice(0, 2);
      terms.forEach(({ key, entry }) =>
        add("Glossary", {
          id: `glossary.${key}`,
          group: "Glossary",
          label: `What does “${entry.label}” mean?`,
          hint: entry.short,
          icon: "mdi:book-open-variant",
          run: () => openSheet({ tab: "glossary", query: entry.label }),
        }),
      );
    }
    // Go to: best match first; on a tie the current area's pages, then the
    // rest of the product, then the other areas, whatever order the sources
    // registered in.
    if (out.has("Go to")) {
      const at = new Map(out.get("Go to").map((c, i) => [c, i]));
      const score = new Map(
        out.get("Go to").map((c) => [c, commandScore(c, q)]),
      );
      out
        .get("Go to")
        .sort(
          (a, b) =>
            score.get(b) - score.get(a) ||
            goToRank(a, area) - goToRank(b, area) ||
            at.get(a) - at.get(b),
        );
    }
    rankCommands(datasets, q.length >= 2 ? "" : q).forEach((c) =>
      add("Datasets", c),
    );
    rankCommands(runs, q).forEach((c) => add("Runs", c));
    rows.forEach((c) => add("Rows", c));
    const list = groupOrder(area)
      .filter((g) => out.has(g))
      .map((g) => ({
        group: g,
        items: out.get(g).slice(0, PER_GROUP[g] ?? 8),
      }));
    if (
      q &&
      !onConsole &&
      falconAllowed &&
      !list.some((g) => g.group !== "Glossary" && g.items.length)
    ) {
      list.push({
        group: "Falcon",
        items: [
          {
            id: "falcon.askText",
            group: "Falcon",
            label: `Ask Falcon: “${q}”`,
            icon: "mdi:creation-outline",
            run: () => askFalcon(q),
          },
        ],
      });
    }
    return list;
  }, [
    query,
    page,
    offered,
    area,
    onConsole,
    falconAllowed,
    datasets,
    runs,
    rows,
    recent,
    askFalcon,
    openSheet,
  ]);

  const flat = useMemo(() => groups.flatMap((g) => g.items), [groups]);
  useEffect(() => {
    if (active >= flat.length) setActive(Math.max(0, flat.length - 1));
  }, [flat.length, active]);

  const run = useCallback(
    (cmd) => {
      if (!cmd) return;
      close();
      if (cmd.id !== "falcon.askText")
        setRecent(pushRecent(userId, cmd, currentProtoStage()));
      // Let the dialog close first so focus returns before navigating.
      schedule(() => {
        // Visibility can change while the dialog closes; do not run a stale
        // destination, even if it was still registered when selected.
        if (
          cmd.requiresLiveRegistration &&
          !registeredCommands(useCommandStore.getState().sources).some(
            (live) => live.id === cmd.id,
          )
        )
          return;
        if (cmd.run) cmd.run({ navigate });
        else if (cmd.href) navigate(cmd.href);
      });
    },
    [close, navigate, userId, schedule],
  );

  useEffect(() => {
    const el = listRef.current?.querySelector(`[data-index="${active}"]`);
    el?.scrollIntoView?.({ block: "nearest" });
  }, [active]);

  const onKeyDown = (e) => {
    if (e.key === "ArrowDown") {
      e.preventDefault();
      setActive((i) => Math.min(flat.length - 1, i + 1));
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      setActive((i) => Math.max(0, i - 1));
    } else if (e.key === "Home" && !query) {
      e.preventDefault();
      setActive(0);
    } else if (e.key === "End" && !query) {
      e.preventDefault();
      setActive(flat.length - 1);
    } else if (e.key === "Enter") {
      e.preventDefault();
      run(flat[active]);
    }
  };

  let index = -1;
  return (
    <Dialog
      data-dsx-command-palette=""
      open={open}
      onClose={close}
      maxWidth={false}
      sx={{ "& .MuiDialog-container": { alignItems: "flex-start" } }}
      PaperProps={{
        "aria-label": "Command palette",
        sx: {
          width: 640,
          maxWidth: "calc(100vw - 32px)",
          mt: "15vh",
          mx: 2,
          borderRadius: 1.5,
          overflow: "hidden",
        },
      }}
    >
      <Box sx={{ borderBottom: "1px solid", borderColor: "divider" }}>
        <InputBase
          autoFocus
          fullWidth
          value={query}
          onChange={(e) => {
            setQuery(e.target.value);
            setActive(0);
          }}
          onKeyDown={onKeyDown}
          placeholder={
            // L6 launch switch: no runs list in a launch build.
            isVerbShown("run.x")
              ? "Search pages, datasets, runs, rows or type a command…"
              : "Search pages, datasets, rows or type a command…"
          }
          inputProps={{
            "aria-label": "Command",
            role: "combobox",
            "aria-expanded": true,
            "aria-controls": "fa-palette-list",
            "aria-activedescendant": flat[active]
              ? `fa-cmd-${active}`
              : undefined,
          }}
          startAdornment={
            <InputAdornment position="start" sx={{ ml: 2 }}>
              <Iconify icon="mdi:magnify" width={20} />
            </InputAdornment>
          }
          endAdornment={
            <KeyCap sx={{ mr: 2, typography: "s3", height: 20 }}>Esc</KeyCap>
          }
          sx={{ height: 52, typography: "m3" }}
        />
      </Box>
      <Box
        id="fa-palette-list"
        role="listbox"
        ref={listRef}
        sx={{ maxHeight: "min(440px, 55vh)", overflowY: "auto", py: 0.75 }}
      >
        {groups.map((g) => (
          <Box key={g.group} data-palette-group={g.group} sx={{ pb: 0.5 }}>
            <Typography
              typography="s3"
              color="text.disabled"
              sx={{
                px: 2.25,
                pt: 1,
                pb: 0.5,
                textTransform: "uppercase",
                letterSpacing: 0.4,
              }}
            >
              {g.group}
            </Typography>
            {g.items.map((cmd) => {
              index += 1;
              const i = index;
              return (
                <Box key={`${g.group}-${cmd.id}`} data-index={i}>
                  <Row
                    id={`fa-cmd-${i}`}
                    cmd={cmd}
                    active={i === active}
                    onHover={() => setActive(i)}
                    onRun={() => run(cmd)}
                  />
                </Box>
              );
            })}
          </Box>
        ))}
        {!flat.length && (
          <Typography
            typography="s1"
            color="text.secondary"
            sx={{ px: 2.25, py: 2 }}
          >
            {query.trim()
              ? `No matches for “${query.trim()}”`
              : "Type to search datasets, runs and rows, or a command like “Hide column” or “Show charts”."}
          </Typography>
        )}
      </Box>
      <Stack
        direction="row"
        alignItems="center"
        gap={1.5}
        sx={{
          px: 2,
          py: 1,
          borderTop: "1px solid",
          borderColor: "divider",
          color: "text.secondary",
        }}
      >
        {[
          [["↑", "↓"], "Move"],
          [["↵"], "Run"],
          [["Esc"], "Close"],
        ].map(([caps, label]) => (
          <Stack key={label} direction="row" alignItems="center" gap={0.5}>
            {caps.map((c) => (
              <KeyCap
                key={c}
                sx={{ height: 20, minWidth: 20, typography: "s3" }}
              >
                {c}
              </KeyCap>
            ))}
            <Typography typography="s3">{label}</Typography>
          </Stack>
        ))}
        <Box sx={{ flex: 1 }} />
        <Typography typography="s3" data-testid="palette-scope">
          {paletteScopeText(Boolean(datasetId))}
        </Typography>
      </Stack>
    </Dialog>
  );
}

CommandPalette.propTypes = {
  host: PropTypes.oneOf(["app", "console"]),
};
