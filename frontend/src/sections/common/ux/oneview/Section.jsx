import React, { useCallback, useEffect, useId, useMemo } from "react";
import PropTypes from "prop-types";
import {
  Box,
  ButtonBase,
  Chip,
  Collapse,
  Stack,
  Typography,
} from "@mui/material";
import { create } from "zustand";
import Iconify from "src/components/iconify";
import { useRegisterCommands } from "src/sections/common/CommandPalette/commandRegistry";
import Truncate from "../Truncate";
import { useLayoutPrefs } from "./layoutPrefs";
import useViewportDensity from "./useViewportDensity";

/** Mounted sections per surface (+ scope): `{[surfaceKey]: {[id]: {title,
 * defaultOpen}}}` in mount order. Drives useSections and the View menu. */
export const useSectionRegistry = create((set) => ({
  bySurface: {},
  register: (key, id, entry) =>
    set((s) => ({
      bySurface: {
        ...s.bySurface,
        [key]: { ...(s.bySurface[key] ?? {}), [id]: entry },
      },
    })),
  unregister: (key, id) =>
    set((s) => {
      const next = { ...(s.bySurface[key] ?? {}) };
      delete next[id];
      return { bySurface: { ...s.bySurface, [key]: next } };
    }),
}));

const surfaceKey = (surface, scopeId) => `${surface}|${scopeId ?? ""}`;

/** `defaultOpen` (true | false | "roomy") for the current window. Pure. */
export const sectionDefaultOpen = (defaultOpen, density) =>
  defaultOpen === "roomy" ? density !== "short" : defaultOpen !== false;

/**
 * Page-level control of a surface's sections.
 * @returns {{sections: {id, title, open: boolean}[], expandAll(),
 *   collapseAll(), setOpen(id, open), toggle(id)}}
 */
export function useSections(surface, scopeId) {
  const key = surfaceKey(surface, scopeId);
  const registered = useSectionRegistry((s) => s.bySurface[key]);
  const [prefs, patch] = useLayoutPrefs(surface, scopeId);
  const sections = useMemo(
    () =>
      Object.entries(registered ?? {}).map(([id, e]) => ({
        id,
        title: e.title,
        open: prefs.sections?.[id] ?? e.defaultOpen,
      })),
    [registered, prefs.sections],
  );
  const setAll = useCallback(
    (open) =>
      patch({
        sections: Object.fromEntries(
          Object.keys(registered ?? {}).map((id) => [id, open]),
        ),
      }),
    [patch, registered],
  );
  const setOpen = useCallback(
    (id, open) => patch({ sections: { [id]: Boolean(open) } }),
    [patch],
  );
  const toggle = useCallback(
    (id) => {
      const cur = sections.find((s) => s.id === id);
      if (cur) setOpen(id, !cur.open);
    },
    [sections, setOpen],
  );
  return {
    sections,
    expandAll: () => setAll(true),
    collapseAll: () => setAll(false),
    setOpen,
    toggle,
  };
}

/**
 * Section — a collapsible region whose state is remembered (ONE-VIEW-SPEC
 * §3.3). Header 36 px: chevron + title (+ count) on the left, `actions`
 * always visible on the right; collapsed, the header still says its answer
 * (`summary`, one line). `defaultOpen="roomy"` = open unless the window is
 * short (1280×720). The user's toggle is remembered (`sections.<id>`) and
 * beats the default. Registers ⌘K `Show <title>` / `Hide <title>`.
 *
 * Props: `{id, surface, scopeId?, title, count?, summary?, actions?,
 * defaultOpen = true | false | "roomy", headingLevel = 3, keepMounted?,
 * sx?, children}`
 */
export default function Section({
  id,
  surface,
  scopeId,
  title,
  count,
  summary,
  actions,
  defaultOpen = true,
  headingLevel = 3,
  keepMounted = false,
  sx,
  children,
}) {
  const density = useViewportDensity();
  const def = sectionDefaultOpen(defaultOpen, density);
  const [prefs, patch] = useLayoutPrefs(surface, scopeId);
  const stored = prefs.sections?.[id];
  const open = typeof stored === "boolean" ? stored : def;
  const regionId = useId();
  const key = surfaceKey(surface, scopeId);
  const label = typeof title === "string" ? title : id;

  useEffect(() => {
    useSectionRegistry.getState().register(key, id, {
      title: label,
      defaultOpen: def,
    });
  }, [key, id, label, def]);
  useEffect(
    () => () => useSectionRegistry.getState().unregister(key, id),
    [key, id],
  );

  const toggle = useCallback(
    () => patch({ sections: { [id]: !open } }),
    [patch, id, open],
  );

  useRegisterCommands(
    `section:${key}:${id}`,
    [
      {
        id: `section.${key}.${id}`,
        label: open ? `Hide ${label}` : `Show ${label}`,
        keywords: ["section", "collapse", "expand", label.toLowerCase()],
        icon: open ? "mdi:chevron-up" : "mdi:chevron-down",
        run: toggle,
      },
    ],
    [open, label],
  );

  return (
    <Box component="section" aria-labelledby={`${regionId}-h`} sx={sx}>
      <Stack
        direction="row"
        alignItems="center"
        gap={1}
        sx={{ minHeight: 36, minWidth: 0 }}
      >
        <ButtonBase
          aria-expanded={open}
          aria-controls={regionId}
          onClick={toggle}
          sx={{
            gap: 0.5,
            borderRadius: 0.5,
            pr: 0.75,
            py: 0.25,
            minWidth: 0,
            flexShrink: open ? 1 : 0,
            "&:hover .fa-section-chevron": { color: "text.primary" },
            "&:focus-visible": {
              outline: "2px solid",
              outlineColor: "primary.main",
              outlineOffset: 1,
            },
          }}
        >
          <Iconify
            className="fa-section-chevron"
            icon={open ? "mdi:chevron-down" : "mdi:chevron-right"}
            width={18}
            sx={{ color: "text.secondary", flexShrink: 0 }}
          />
          <Typography
            id={`${regionId}-h`}
            component={`h${headingLevel}`}
            typography="s1"
            fontWeight="fontWeightSemiBold"
            noWrap
            sx={{ m: 0, color: "text.primary" }}
          >
            {title}
          </Typography>
          {count != null && (
            <Chip
              size="small"
              label={count}
              sx={{ height: 20, typography: "s3", ml: 0.25 }}
            />
          )}
        </ButtonBase>
        {!open && summary ? (
          <Box sx={{ flex: 1, minWidth: 0, color: "text.secondary" }}>
            {typeof summary === "string" ? (
              <Truncate
                text={summary}
                sx={{ typography: "s2", display: "block" }}
              />
            ) : (
              <Typography typography="s2" color="text.secondary" noWrap>
                {summary}
              </Typography>
            )}
          </Box>
        ) : (
          <Box sx={{ flex: 1 }} />
        )}
        {actions && (
          <Stack
            direction="row"
            alignItems="center"
            gap={0.5}
            sx={{ flexShrink: 0 }}
          >
            {actions}
          </Stack>
        )}
      </Stack>
      <Collapse in={open} unmountOnExit={!keepMounted} timeout={150}>
        <Box id={regionId} role="region" aria-labelledby={`${regionId}-h`}>
          {children}
        </Box>
      </Collapse>
    </Box>
  );
}

Section.propTypes = {
  id: PropTypes.string.isRequired,
  surface: PropTypes.string.isRequired,
  scopeId: PropTypes.string,
  title: PropTypes.node.isRequired,
  count: PropTypes.oneOfType([PropTypes.number, PropTypes.string]),
  summary: PropTypes.node,
  actions: PropTypes.node,
  defaultOpen: PropTypes.oneOf([true, false, "roomy"]),
  headingLevel: PropTypes.oneOf([2, 3, 4, 5, 6]),
  keepMounted: PropTypes.bool,
  sx: PropTypes.object,
  children: PropTypes.node,
};

export { Section };
