import React, { useEffect, useRef, useState } from "react";
import PropTypes from "prop-types";
import { Box, IconButton, Popover, Stack, Typography } from "@mui/material";
import { create } from "zustand";
import Iconify from "src/components/iconify";
import CustomTooltip from "src/components/tooltip";
import { PROTO_USER_ID, useLayoutUserId } from "./layoutPrefs";

// ------------------------------------------------------------ pure + store

/** `fa.hints.<userId>`: the ids of dismissed tips (array). */
export const hintsKey = (userId) => `fa.hints.${userId || PROTO_USER_ID}`;

export function readDismissed(userId) {
  try {
    const raw = JSON.parse(window.localStorage.getItem(hintsKey(userId)));
    return Array.isArray(raw) ? raw.filter((x) => typeof x === "string") : [];
  } catch {
    return [];
  }
}

export function writeDismissed(userId, ids) {
  try {
    if (ids.length)
      window.localStorage.setItem(hintsKey(userId), JSON.stringify(ids));
    else window.localStorage.removeItem(hintsKey(userId));
    return true;
  } catch {
    return false;
  }
}

/** The one hint to show: the first mounted one not dismissed. Pure. */
export function visibleHintId(mounted, dismissed) {
  const off = new Set(dismissed ?? []);
  return (mounted ?? []).find((m) => !off.has(m.id))?.id ?? null;
}

let seq = 0;

/**
 * Tips state: mounted hints in registration order (with their content, so
 * HintRecall can show it after dismissal) and dismissed ids per user.
 */
export const useHintStore = create((set, get) => ({
  mounted: [],
  dismissed: {}, // userId -> string[]
  register: (id, entry) => {
    seq += 1;
    const token = seq;
    set((s) => ({ mounted: [...s.mounted, { id, token, ...entry }] }));
    return token;
  },
  update: (token, entry) =>
    set((s) => ({
      mounted: s.mounted.map((m) =>
        m.token === token ? { ...m, ...entry } : m,
      ),
    })),
  unregister: (token) =>
    set((s) => ({ mounted: s.mounted.filter((m) => m.token !== token) })),
  dismissedFor: (userId) => {
    const key = userId || PROTO_USER_ID;
    const cur = get().dismissed[key];
    if (cur) return cur;
    const fromStorage = readDismissed(userId);
    set((s) => ({ dismissed: { ...s.dismissed, [key]: fromStorage } }));
    return fromStorage;
  },
  dismiss: (userId, id) => {
    const key = userId || PROTO_USER_ID;
    const cur = get().dismissedFor(userId);
    if (cur.includes(id)) return;
    const next = [...cur, id];
    writeDismissed(userId, next);
    set((s) => ({ dismissed: { ...s.dismissed, [key]: next } }));
  },
  restore: (userId, ids) => {
    const key = userId || PROTO_USER_ID;
    const drop = new Set(ids);
    const next = get()
      .dismissedFor(userId)
      .filter((x) => !drop.has(x));
    writeDismissed(userId, next);
    set((s) => ({ dismissed: { ...s.dismissed, [key]: next } }));
  },
}));

/** ⌘K `Show tips on this page`: clear this page's dismissals. */
export function showTipsOnThisPage(userId) {
  const { mounted, restore } = useHintStore.getState();
  restore(
    userId,
    mounted.map((m) => m.id),
  );
}

const useDismissed = (userId) => {
  const key = userId || PROTO_USER_ID;
  const list = useHintStore((s) => s.dismissed[key]);
  useEffect(() => {
    if (!list) useHintStore.getState().dismissedFor(userId);
  }, [list, userId]);
  return list ?? readDismissed(userId);
};

// ------------------------------------------------------------ components

/**
 * Hint — the Simple-mode teaching, folded in (ONE-VIEW-SPEC §3.9).
 * `strip`: one line on `primary.lighter` (dark: `background.neutral`) with a
 * lightbulb and `×` (`Got it · hide this tip`); `footer`: the same content
 * as a quiet bar at the bottom of a panel. At most one Hint shows per screen
 * (first mounted wins). Hidden for good after `×` or once `doneWhen` is
 * true; `HintRecall` (next to the title) and ⌘K `Show tips on this page`
 * bring it back. Storage `fa.hints.<userId>`.
 *
 * Props: `{id, variant = "strip" | "footer", doneWhen?, icon?, sx?,
 * children}`
 */
export default function Hint({
  id,
  variant = "strip",
  doneWhen = false,
  icon = "mdi:lightbulb-on-outline",
  sx,
  children,
}) {
  const userId = useLayoutUserId();
  const dismissed = useDismissed(userId);
  const mounted = useHintStore((s) => s.mounted);
  const token = useRef(null);

  useEffect(() => {
    token.current = useHintStore
      .getState()
      .register(id, { content: children, icon });
    return () => useHintStore.getState().unregister(token.current);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [id]);
  useEffect(() => {
    if (token.current)
      useHintStore
        .getState()
        .update(token.current, { content: children, icon });
  }, [children, icon]);
  useEffect(() => {
    if (doneWhen) useHintStore.getState().dismiss(userId, id);
  }, [doneWhen, userId, id]);

  if (visibleHintId(mounted, dismissed) !== id || doneWhen) return null;

  const footer = variant === "footer";
  return (
    <Stack
      role="note"
      data-hint={id}
      direction="row"
      alignItems="center"
      gap={1}
      sx={{
        minHeight: footer ? 32 : 36,
        px: 1.5,
        py: 0.5,
        typography: footer ? "s3" : "s2",
        color: footer ? "text.secondary" : "text.primary",
        bgcolor: footer
          ? "background.paper"
          : (theme) =>
              theme.palette.mode === "dark"
                ? theme.palette.background.neutral
                : theme.palette.primary.lighter,
        borderRadius: footer ? 0 : 1,
        ...(footer ? { borderTop: "1px solid", borderColor: "divider" } : {}),
        ...sx,
      }}
    >
      <Iconify
        icon={icon}
        width={footer ? 14 : 16}
        sx={{ color: footer ? "text.disabled" : "primary.main", flexShrink: 0 }}
      />
      <Box sx={{ flex: 1, minWidth: 0 }}>{children}</Box>
      <CustomTooltip
        show
        size="small"
        title="Got it · hide this tip"
        disableInteractive
      >
        <IconButton
          size="small"
          aria-label="Got it · hide this tip"
          onClick={() => useHintStore.getState().dismiss(userId, id)}
          sx={{ p: 0.25, color: "text.secondary", flexShrink: 0 }}
        >
          <Iconify icon="mdi:close" width={16} />
        </IconButton>
      </CustomTooltip>
    </Stack>
  );
}

Hint.propTypes = {
  id: PropTypes.string.isRequired,
  variant: PropTypes.oneOf(["strip", "footer"]),
  doneWhen: PropTypes.bool,
  icon: PropTypes.string,
  sx: PropTypes.object,
  children: PropTypes.node,
};

/**
 * HintRecall — a 16 px `ⓘ` next to the screen title, shown only after the
 * page's tip was dismissed; tooltip `Show the tip for this page`; opens the
 * tip text in a popover.
 *
 * Props: `{id, sx?}` (the Hint's id).
 */
export function HintRecall({ id, sx }) {
  const userId = useLayoutUserId();
  const dismissed = useDismissed(userId);
  const entry = useHintStore((s) => s.mounted.find((m) => m.id === id));
  const [anchor, setAnchor] = useState(null);
  if (!entry || !dismissed.includes(id)) return null;
  return (
    <>
      <CustomTooltip
        show
        size="small"
        title="Show the tip for this page"
        disableInteractive
      >
        <IconButton
          size="small"
          aria-label="Show the tip for this page"
          onClick={(e) => setAnchor(e.currentTarget)}
          sx={{ p: 0.25, color: "text.disabled", ...sx }}
        >
          <Iconify icon="mdi:information-outline" width={16} />
        </IconButton>
      </CustomTooltip>
      <Popover
        open={Boolean(anchor)}
        anchorEl={anchor}
        onClose={() => setAnchor(null)}
        anchorOrigin={{ vertical: "bottom", horizontal: "left" }}
        slotProps={{ paper: { sx: { p: 1.5, maxWidth: 360, mt: 0.5 } } }}
      >
        <Stack direction="row" gap={1} alignItems="flex-start">
          <Iconify
            icon={entry.icon ?? "mdi:lightbulb-on-outline"}
            width={16}
            sx={{ color: "primary.main", mt: 0.25, flexShrink: 0 }}
          />
          <Typography component="div" typography="s2">
            {entry.content}
          </Typography>
        </Stack>
      </Popover>
    </>
  );
}

HintRecall.propTypes = {
  id: PropTypes.string.isRequired,
  sx: PropTypes.object,
};

export { Hint };
