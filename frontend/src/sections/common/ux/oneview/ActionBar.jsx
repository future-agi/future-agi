import React, {
  useEffect,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import PropTypes from "prop-types";
import { useNavigate } from "react-router-dom";
import { Box, Button, IconButton, Stack } from "@mui/material";
import Iconify from "src/components/iconify";
import CustomTooltip from "src/components/tooltip";
import RouterLink from "src/routes/components/router-link";
import { useRegisterCommands } from "src/sections/common/CommandPalette/commandRegistry";
import KeyHintTooltip from "../KeyHintTooltip";
import { ItemsMenu } from "./MenuItems";
import { ACTION_GAP, fitActions, primaryCount } from "./fitActions";

// Latest actions per surface, so a ContextMenu can reuse the same list.
const registry = new Map();

/** The ActionBar actions currently mounted for a surface (flattened). */
export const actionsFor = (surface) => registry.get(surface) ?? [];

const flatten = (actions) =>
  (actions ?? []).flatMap((a) =>
    a.menu?.length
      ? a.menu.map((m) => ({
          ...m,
          label: `${a.label}: ${m.label}`,
          parent: a.id,
        }))
      : [a],
  );

function ActionButton({ action, compact, onOpenMenu, measure = false }) {
  const a = action;
  const iconOnly = compact || a.variant === "icon";
  const variant =
    a.variant === "primary"
      ? "contained"
      : a.variant === "outlined"
        ? "outlined"
        : "text";
  const pressed = a.toggle ? Boolean(a.toggle.on) : undefined;
  const common = {
    disabled: Boolean(a.disabled),
    "aria-pressed": pressed,
    "aria-haspopup": a.menu?.length ? "menu" : undefined,
    "data-action": a.id,
    tabIndex: measure ? -1 : undefined,
    ...(a.href && !a.disabled && !a.menu?.length
      ? { component: RouterLink, href: a.href }
      : {}),
    onClick: (e) => {
      if (measure) return;
      if (a.menu?.length) onOpenMenu?.(a, e.currentTarget);
      else a.run?.(e);
    },
  };
  const toggleSx = pressed
    ? { bgcolor: "action.selected", color: "text.primary" }
    : {};
  let node;
  if (iconOnly && a.icon) {
    node = (
      <IconButton
        size="small"
        aria-label={a.label}
        {...common}
        sx={{ color: "text.secondary", ...toggleSx }}
      >
        <Iconify icon={a.icon} width={18} />
      </IconButton>
    );
  } else {
    node = (
      <Button
        size="small"
        variant={variant}
        color={a.variant === "primary" ? "primary" : "inherit"}
        startIcon={a.icon ? <Iconify icon={a.icon} width={16} /> : undefined}
        endIcon={
          a.menu?.length ? (
            <Iconify icon="mdi:chevron-down" width={16} />
          ) : undefined
        }
        {...common}
        sx={{
          typography: "s2",
          whiteSpace: "nowrap",
          minWidth: 0,
          flexShrink: 0,
          ...(a.variant === "primary"
            ? {}
            : { color: "text.secondary", borderColor: "divider" }),
          ...toggleSx,
        }}
      >
        {a.label}
      </Button>
    );
  }
  if (measure) return node;
  if (a.disabled && a.reason)
    return (
      <CustomTooltip show size="small" title={a.reason}>
        <span style={{ display: "inline-flex" }}>{node}</span>
      </CustomTooltip>
    );
  if (a.shortcut || iconOnly)
    return (
      <KeyHintTooltip title={a.label} action={a.shortcut ?? undefined}>
        {node}
      </KeyHintTooltip>
    );
  return node;
}

ActionButton.propTypes = {
  action: PropTypes.object.isRequired,
  compact: PropTypes.bool,
  onOpenMenu: PropTypes.func,
  measure: PropTypes.bool,
};

/**
 * ActionBar — the priority-plus toolbar (ONE-VIEW-SPEC §3.6): calm at 1280,
 * complete everywhere. Measures itself; shows actions in their declared
 * order while they fit; the lowest priority first drops its label (when
 * `hideLabelBelow` allows) and then folds into `More ▾`; priority 1 never
 * overflows. Every action (shown or folded, menu items included) is in ⌘K
 * by its label (group `Actions`).
 *
 * Props: `{surface, actions: Action[], moreLabel = "More", sx?}`
 * Action: `{id, label, icon?, priority: 1|2|3|4, variant?: "primary" |
 * "outlined" | "text" | "icon", shortcut? (keymap action), run?, href?,
 * disabled?, reason?, menu?: Action[], toggle?: {on}, render?: () => node,
 * hideLabelBelow?: px}`
 */
export default function ActionBar({
  surface,
  actions,
  moreLabel = "More",
  sx,
}) {
  const navigate = useNavigate();
  const list = useMemo(() => (actions ?? []).filter(Boolean), [actions]);
  const boxRef = useRef(null);
  const measureRef = useRef(null);
  const [available, setAvailable] = useState(null);
  const [widths, setWidths] = useState({});
  const [moreWidth, setMoreWidth] = useState(72);
  const [menu, setMenu] = useState(null); // {anchor, actionId?}; no id = More

  if (import.meta.env?.DEV && primaryCount(list) > 1)
    // eslint-disable-next-line no-console
    console.warn(
      `ActionBar "${surface}": more than one primary action (P3: one primary per region).`,
    );

  // Available width.
  useLayoutEffect(() => {
    const el = boxRef.current;
    if (!el) return undefined;
    const read = () =>
      setAvailable(Math.floor(el.getBoundingClientRect().width));
    read();
    if (typeof ResizeObserver === "undefined") return undefined;
    const ro = new ResizeObserver(read);
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  // Natural widths (label and icon-only) of every action, and of `More`.
  const measureKey = list
    .map(
      (a) =>
        `${a.id}:${a.label}:${a.icon ?? ""}:${a.variant ?? ""}:${Boolean(a.menu?.length)}:${Boolean(a.render)}`,
    )
    .join("|");
  useLayoutEffect(() => {
    const root = measureRef.current;
    if (!root) return;
    const next = {};
    root.querySelectorAll("[data-measure-id]").forEach((el) => {
      const id = el.getAttribute("data-measure-id");
      const kind = el.getAttribute("data-measure-kind");
      next[id] = {
        ...(next[id] ?? {}),
        [kind]: Math.ceil(el.getBoundingClientRect().width),
      };
    });
    setWidths((cur) => {
      const merged = { ...next };
      // Custom `render` actions are measured where they show (never mounted
      // twice: a ViewMenu must not register its commands twice).
      boxRef.current?.querySelectorAll("[data-render-id]").forEach((el) => {
        const id = el.getAttribute("data-render-id");
        merged[id] = { full: Math.ceil(el.getBoundingClientRect().width) };
      });
      list.forEach((a) => {
        if (a.render && !merged[a.id] && cur[a.id]) merged[a.id] = cur[a.id];
      });
      return JSON.stringify(merged) === JSON.stringify(cur) ? cur : merged;
    });
    const more = root.querySelector("[data-measure-more]");
    if (more) setMoreWidth(Math.ceil(more.getBoundingClientRect().width));
  });

  const fit = useMemo(
    () =>
      available == null
        ? { shown: list.map((a) => a.id), compact: [], overflow: [] }
        : fitActions(list, widths, available, { moreWidth }),
    [list, widths, available, moreWidth],
  );

  const run = (a, e) => {
    if (a.disabled) return;
    if (a.run) a.run(e);
    else if (a.href) navigate(a.href);
  };

  useEffect(() => {
    registry.set(surface, flatten(list));
    return () => {
      if (registry.get(surface)) registry.delete(surface);
    };
  }, [surface, list]);

  useRegisterCommands(
    `${surface}.actions`,
    flatten(list)
      .filter((a) => a.run || a.href)
      .map((a) => ({
        id: `${surface}.action.${a.parent ? `${a.parent}.` : ""}${a.id}`,
        group: "Actions",
        label: a.label,
        keywords: a.keywords,
        icon: a.icon,
        shortcut: a.shortcut,
        run: () => run(a),
        when: () => !a.disabled,
      })),
    [measureKey, list.map((a) => `${a.disabled}`).join("")],
  );

  const byId = new Map(list.map((a) => [a.id, a]));
  const compact = new Set(fit.compact);
  const overflow = fit.overflow.map((id) => byId.get(id)).filter(Boolean);
  const moreItems = overflow.flatMap((a) =>
    a.menu?.length
      ? [
          { divider: true },
          ...a.menu.map((m) => ({
            id: `${a.id}.${m.id}`,
            label: `${a.label}: ${m.label}`,
            icon: m.icon ?? a.icon,
            shortcut: m.shortcut,
            disabled: m.disabled,
            secondary: m.disabled ? m.reason : undefined,
            run: (e) => run(m, e),
          })),
          { divider: true },
        ]
      : [
          {
            id: a.id,
            label: a.label,
            icon: a.icon,
            shortcut: a.shortcut,
            checked: a.toggle ? Boolean(a.toggle.on) : undefined,
            disabled: a.disabled,
            secondary: a.disabled ? a.reason : undefined,
            run: (e) => run(a, e),
          },
        ],
  );

  // Store only which menu is open. Rows, permissions and handlers can change
  // while it stays open; keeping a snapshot of items leaves old disabled
  // reasons (or old enabled actions) behind after the toolbar has updated.
  const menuItems =
    menu?.actionId != null
      ? (byId.get(menu.actionId)?.menu ?? []).map(({ reason, ...m }) => ({
          ...m,
          // As in More, the reason is the row's second line. Passing it on as
          // `reason` too would add a tooltip that renames the row to the
          // reason alone ("Add rows first"), hiding which action it is.
          secondary: m.disabled ? reason : m.secondary,
          run: (e) => run(m, e),
        }))
      : moreItems;

  return (
    <Box
      ref={boxRef}
      data-action-bar={surface}
      sx={{ flex: 1, minWidth: 0, position: "relative", ...sx }}
    >
      <Stack
        direction="row"
        alignItems="center"
        justifyContent="flex-end"
        gap={`${ACTION_GAP}px`}
        sx={{ minWidth: 0, overflow: "hidden" }}
      >
        {list
          .filter((a) => fit.shown.includes(a.id))
          .map((a) =>
            a.render ? (
              <Box
                key={a.id}
                data-render-id={a.id}
                sx={{ display: "inline-flex", flexShrink: 0 }}
              >
                {a.render()}
              </Box>
            ) : (
              <ActionButton
                key={a.id}
                action={a}
                compact={compact.has(a.id)}
                onOpenMenu={(act, anchor) =>
                  setMenu({
                    anchor,
                    actionId: act.id,
                  })
                }
              />
            ),
          )}
        {overflow.length > 0 && (
          <CustomTooltip
            show
            size="small"
            title="More actions"
            disableInteractive
          >
            <Button
              size="small"
              color="inherit"
              aria-haspopup="menu"
              aria-label="More actions"
              onClick={(e) =>
                setMenu({ anchor: e.currentTarget })
              }
              endIcon={<Iconify icon="mdi:chevron-down" width={16} />}
              sx={{
                typography: "s2",
                color: "text.secondary",
                minWidth: 0,
                flexShrink: 0,
              }}
            >
              {moreLabel}
            </Button>
          </CustomTooltip>
        )}
      </Stack>
      {/* Off-screen measuring row: every action as label and as icon. */}
      <Stack
        ref={measureRef}
        aria-hidden
        direction="row"
        gap={1}
        sx={{
          position: "absolute",
          visibility: "hidden",
          pointerEvents: "none",
          top: 0,
          left: 0,
          height: 0,
          overflow: "hidden",
          whiteSpace: "nowrap",
        }}
      >
        {list.map((a) => (
          <React.Fragment key={a.id}>
            {!a.render && (
              <Box
                data-measure-id={a.id}
                data-measure-kind="full"
                sx={{ display: "inline-flex" }}
              >
                <ActionButton action={a} measure />
              </Box>
            )}
            {a.icon && !a.render && (
              <Box
                data-measure-id={a.id}
                data-measure-kind="icon"
                sx={{ display: "inline-flex" }}
              >
                <ActionButton action={a} compact measure />
              </Box>
            )}
          </React.Fragment>
        ))}
        <Box data-measure-more sx={{ display: "inline-flex" }}>
          <Button
            size="small"
            tabIndex={-1}
            endIcon={<Iconify icon="mdi:chevron-down" width={16} />}
            sx={{ typography: "s2", minWidth: 0 }}
          >
            {moreLabel}
          </Button>
        </Box>
      </Stack>
      <ItemsMenu
        open={Boolean(menu)}
        anchorEl={menu?.anchor}
        onClose={() => setMenu(null)}
        items={menuItems}
        anchorOrigin={{ vertical: "bottom", horizontal: "right" }}
        transformOrigin={{ vertical: "top", horizontal: "right" }}
      />
    </Box>
  );
}

ActionBar.propTypes = {
  surface: PropTypes.string.isRequired,
  actions: PropTypes.arrayOf(PropTypes.object).isRequired,
  moreLabel: PropTypes.string,
  sx: PropTypes.object,
};

export { ActionBar };
