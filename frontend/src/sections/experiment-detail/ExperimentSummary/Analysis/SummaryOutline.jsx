import React, { useEffect, useRef, useState } from "react";
import PropTypes from "prop-types";
import { Box, ButtonBase, Stack, Typography } from "@mui/material";
import { useRegisterCommands } from "src/sections/common/CommandPalette/commandRegistry";

/**
 * Scroll an anchor of the Summary into view. A collapsed Section is opened
 * first (`open(sectionId)`), then the anchor is scrolled to once it has
 * mounted (the Section's Collapse takes 150 ms).
 */
export function revealAnchor({ anchorId, sectionId, open }) {
  const go = () =>
    document
      .getElementById(anchorId)
      ?.scrollIntoView({ behavior: "smooth", block: "start" });
  if (sectionId && open) {
    open(sectionId);
    window.setTimeout(go, 200);
  } else go();
}

/**
 * The anchors of `items` that are on the page now (a card that has nothing
 * to say renders nothing); re-checked when the page changes size.
 */
function usePresent(items, rootRef) {
  const key = items.map((i) => i.anchorId).join("|");
  const [present, setPresent] = useState(() => items.map((i) => i.anchorId));
  useEffect(() => {
    const check = () => {
      const next = items
        .map((i) => i.anchorId)
        .filter((id) => {
          const el = document.getElementById(id);
          return el && el.getBoundingClientRect().height > 0;
        });
      setPresent((cur) => (cur.join("|") === next.join("|") ? cur : next));
    };
    check();
    const root = rootRef?.current;
    if (!root || typeof ResizeObserver === "undefined") return undefined;
    const ro = new ResizeObserver(check);
    ro.observe(root);
    return () => ro.disconnect();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key, rootRef]);
  return present;
}

/**
 * The anchor nearest the top of the viewport (scroll spy). `pick(id)` marks
 * a clicked item for a moment, so a short section at the end of the page
 * (which can never reach the top) still reads as the current one.
 */
function useActive(ids) {
  const key = ids.join("|");
  const [active, setActive] = useState(ids[0] ?? null);
  const lockUntil = useRef(0);
  useEffect(() => {
    if (typeof IntersectionObserver === "undefined" || !ids.length)
      return undefined;
    const seen = new Map();
    const io = new IntersectionObserver(
      (entries) => {
        entries.forEach((e) => seen.set(e.target.id, e.isIntersecting));
        if (Date.now() < lockUntil.current) return;
        const first = ids.find((id) => seen.get(id));
        if (first) setActive(first);
      },
      { rootMargin: "0px 0px -55% 0px" },
    );
    ids.forEach((id) => {
      const el = document.getElementById(id);
      if (el) io.observe(el);
    });
    return () => io.disconnect();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key]);
  const pick = (id) => {
    lockUntil.current = Date.now() + 1500;
    setActive(id);
  };
  return [active, pick];
}

/**
 * `On this page` (one view A5-03): the Summary's outline. ⌘K `Jump to
 * <section>` at every width; with `rail` (≥ 1600 px) also a small sticky
 * list on the right that follows the scroll. Clicking scrolls there and
 * opens a collapsed Section.
 *
 * @param {{id: string, label: string, anchorId: string, sectionId?: string}[]} props.items
 * @param {(sectionId: string) => void} props.onOpenSection
 * @param {boolean} [props.rail]
 * @param {object} [props.rootRef]  the page element (re-check on resize)
 */
export default function SummaryOutline({
  items,
  onOpenSection,
  rail = false,
  rootRef,
}) {
  const present = usePresent(items, rootRef);
  const shown = items.filter((i) => present.includes(i.anchorId));
  const [active, pick] = useActive(rail ? shown.map((i) => i.anchorId) : []);
  const go = (item) => {
    pick(item.anchorId);
    revealAnchor({
      anchorId: item.anchorId,
      sectionId: item.sectionId,
      open: onOpenSection,
    });
  };

  useRegisterCommands(
    "summary-outline",
    shown.map((item) => ({
      id: `summary.jump.${item.id}`,
      group: "Go to",
      label: `Jump to ${item.label}`,
      keywords: ["jump", "go", "section", "scroll", item.label.toLowerCase()],
      icon: "mdi:format-list-bulleted",
      run: () => go(item),
    })),
    [shown.map((i) => `${i.id}:${i.label}`).join("|")],
  );

  if (!rail) return null;
  return (
    <Box
      component="nav"
      aria-label="On this page"
      sx={{
        position: "sticky",
        top: 12,
        alignSelf: "start",
        pt: 0.5,
      }}
      data-testid="summary-outline"
    >
      <Typography
        typography="s3"
        color="text.secondary"
        fontWeight="fontWeightSemiBold"
        sx={{ px: 1, pb: 0.75 }}
      >
        On this page
      </Typography>
      <Stack
        component="ul"
        sx={{
          m: 0,
          p: 0,
          listStyle: "none",
          borderLeft: "1px solid",
          borderColor: "divider",
        }}
      >
        {shown.map((item) => {
          const on = item.anchorId === active;
          return (
            <li key={item.id}>
              <ButtonBase
                onClick={() => go(item)}
                aria-current={on ? "location" : undefined}
                sx={{
                  width: "100%",
                  justifyContent: "flex-start",
                  textAlign: "left",
                  px: 1,
                  py: 0.5,
                  ml: "-1px",
                  borderLeft: "2px solid",
                  borderColor: on ? "primary.main" : "transparent",
                  typography: "s2",
                  color: on ? "text.primary" : "text.secondary",
                  fontWeight: on ? 600 : 400,
                  "&:hover": { color: "text.primary" },
                  "&:focus-visible": {
                    outline: "2px solid",
                    outlineColor: "primary.main",
                    outlineOffset: -2,
                  },
                }}
              >
                {item.label}
              </ButtonBase>
            </li>
          );
        })}
      </Stack>
    </Box>
  );
}

SummaryOutline.propTypes = {
  items: PropTypes.arrayOf(
    PropTypes.shape({
      id: PropTypes.string.isRequired,
      label: PropTypes.string.isRequired,
      anchorId: PropTypes.string.isRequired,
      sectionId: PropTypes.string,
    }),
  ).isRequired,
  onOpenSection: PropTypes.func,
  rail: PropTypes.bool,
  rootRef: PropTypes.object,
};
