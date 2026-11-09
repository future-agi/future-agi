import React, { useRef, useState } from "react";
import PropTypes from "prop-types";
import { Link as RouterLink } from "react-router-dom";
import {
  Box,
  Button,
  Divider,
  Popover,
  Stack,
  Typography,
} from "@mui/material";
import CustomTooltip from "src/components/tooltip";
import Iconify from "src/components/iconify";
import VerdictLine from "src/sections/common/ux/VerdictLine";
import { sortVerdicts } from "src/sections/common/ux/verdictModel";
import { useLayoutPrefs } from "src/sections/common/ux/oneview/layoutPrefs";

/** `+3 more` / `Show fewer` (pure; exported for tests). */
export const verdictFoldLabel = (open, total) =>
  open ? "Show fewer" : `+${Math.max(0, total - 1)} more`;

// One 24 px line: the verdict's text actions keep their hit area but not
// their 30 px height.
const LINE_SX = {
  minHeight: 24,
  "& .MuiButton-root": { height: 24, py: 0 },
};

const toggleSx = {
  typography: "s2",
  color: "text.secondary",
  whiteSpace: "nowrap",
  flexShrink: 0,
  px: 0.75,
  minWidth: 0,
};

/**
 * Verdict first (ONE-VIEW-SPEC §3.11), the same on run Data, Compare Data
 * and Matrix: the most important line (best first) with up to two actions,
 * then `+N more` that opens every line in place; `Show fewer` folds them.
 * Open/closed is remembered per user and surface (`extras.verdictsOpen`).
 *
 * @param {{key?: string, verdict: object, actions?: object[]}[]} props.items
 * @param {string} props.surface  layout surface (`results`, `matrix`)
 * @param {string} [props.testId]
 * @param {string} [props.leadKey]  the item shown first (the page candidate)
 */
// IT-R1 G2 P6: a verdict wraps to at most 2 lines (never clipped at 1280).
const WRAP_SX = { "& p": { WebkitLineClamp: 2 } };

const TONE_ICON = {
  worse: { icon: "mdi:alert-circle", color: "error.main" },
  better: { icon: "mdi:check-circle-outline", color: "success.main" },
  mixed: { icon: "mdi:alert-outline", color: "warning.main" },
  tie: { icon: "mdi:approximately-equal", color: "text.secondary" },
};

/** The one sentence with its own link (`… it fails [T25], which …`). */
function SentenceWithLink({ item }) {
  const text = item.verdict?.sentence ?? "";
  const link = item.link;
  const at = link?.text ? text.indexOf(link.text) : -1;
  const tone = TONE_ICON[item.verdict?.tone] ?? TONE_ICON.tie;
  const anchor =
    at >= 0 ? (
      <CustomTooltip
        show={Boolean(link.tooltip)}
        size="small"
        placement="bottom"
        title={link.tooltip ?? ""}
      >
        <Box
          component={RouterLink}
          to={link.href}
          data-testid="verdict-sentence-link"
          sx={{
            color: "primary.main",
            fontWeight: "fontWeightMedium",
            textDecoration: "underline",
            textUnderlineOffset: "2px",
          }}
        >
          {link.text}
        </Box>
      </CustomTooltip>
    ) : null;
  return (
    <Stack direction="row" alignItems="center" gap={0.75} sx={{ minWidth: 0 }}>
      <Iconify
        icon={tone.icon}
        width={16}
        sx={{ color: tone.color, flexShrink: 0 }}
      />
      <Typography
        typography="s1"
        color="text.primary"
        data-testid="verdict-sentence"
        sx={{
          minWidth: 0,
          display: "-webkit-box",
          WebkitLineClamp: 2,
          WebkitBoxOrient: "vertical",
          overflow: "hidden",
        }}
      >
        {at >= 0 ? (
          <>
            {text.slice(0, at)}
            {anchor}
            {text.slice(at + link.text.length)}
          </>
        ) : (
          text
        )}
      </Typography>
    </Stack>
  );
}
SentenceWithLink.propTypes = { item: PropTypes.object.isRequired };

/**
 * IT-R4 G6 P3 (one-view budget): ONE sentence — the ship line when there
 * is one (its row is the sentence's own link), else the lead verdict — then
 * the `after` controls (Why, Cost vs quality) and `+N more`, a popover
 * titled `N more findings` with every other line and `moreActions`
 * (`Copy as text`).
 */
function OneLineFold({ sorted, testId, sx, after, moreActions }) {
  const ref = useRef(null);
  const [open, setOpen] = useState(false);
  const pin = sorted.find((i) => i?.pin);
  const head = pin ?? sorted[0];
  const rest = sorted.filter((i) => i !== head);
  return (
    <Stack
      direction="row"
      alignItems="center"
      gap={1}
      sx={{ minWidth: 0, minHeight: 28, ...sx }}
      data-testid={testId}
    >
      <Box sx={{ minWidth: 0, flex: "0 1 auto" }}>
        {head.link || pin ? (
          <SentenceWithLink item={head} />
        ) : (
          <VerdictLine
            verdict={head.verdict}
            actions={[]}
            compact
            sx={WRAP_SX}
          />
        )}
      </Box>
      {after}
      {rest.length || moreActions?.length ? (
        <Button
          ref={ref}
          size="small"
          color="inherit"
          aria-expanded={open}
          aria-haspopup="dialog"
          onClick={() => setOpen(true)}
          sx={toggleSx}
          data-testid="verdict-more"
        >
          {rest.length ? `+${rest.length} more` : "More"}
        </Button>
      ) : null}
      <Popover
        open={open}
        anchorEl={ref.current}
        // IT-R5 G6 S4: focus goes back to `+N more` without the focus fill,
        // so the button never looks pressed after the popover closes.
        disableRestoreFocus
        onClose={() => {
          setOpen(false);
          requestAnimationFrame(() =>
            ref.current?.focus?.({ focusVisible: false, preventScroll: true }),
          );
        }}
        anchorOrigin={{ vertical: "bottom", horizontal: "right" }}
        transformOrigin={{ vertical: "top", horizontal: "right" }}
        slotProps={{
          paper: {
            sx: { mt: 0.5, p: 1.5, width: 640, maxWidth: "calc(100vw - 32px)" },
            "data-testid": "verdict-more-popover",
            role: "dialog",
            "aria-label": `${rest.length} more ${rest.length === 1 ? "finding" : "findings"}`,
          },
        }}
      >
        <Typography
          typography="s1"
          fontWeight="fontWeightSemiBold"
          sx={{ mb: 1 }}
        >
          {`${rest.length} more ${rest.length === 1 ? "finding" : "findings"}`}
        </Typography>
        <Stack gap={1} sx={LINE_SX}>
          {rest.map((item, i) => (
            <VerdictLine
              key={item.key ?? i}
              verdict={item.verdict}
              actions={item.actions ?? []}
              compact
            />
          ))}
        </Stack>
        {moreActions?.length ? (
          <>
            <Divider sx={{ my: 1 }} />
            <Stack direction="row" gap={1}>
              {moreActions.map((a) => (
                <Button
                  key={a.label}
                  size="small"
                  variant="text"
                  startIcon={
                    a.icon ? <Iconify icon={a.icon} width={16} /> : undefined
                  }
                  onClick={(e) => {
                    a.onClick?.(e);
                    setOpen(false);
                  }}
                  sx={{ typography: "s2" }}
                >
                  {a.label}
                </Button>
              ))}
            </Stack>
          </>
        ) : null}
      </Popover>
    </Stack>
  );
}
OneLineFold.propTypes = {
  sorted: PropTypes.array.isRequired,
  testId: PropTypes.string,
  sx: PropTypes.object,
  after: PropTypes.node,
  moreActions: PropTypes.array,
};

export default function VerdictFold({
  items,
  surface,
  testId,
  sx,
  leadKey,
  oneLine = false,
  after,
  moreActions,
}) {
  const [prefs, patch] = useLayoutPrefs(surface);
  // R4 lane X: items marked `last` (Best value, next step) keep their place
  // after the sorted verdicts. IT-R1 G2 P1: the page's selected candidate
  // (`leadKey`, the URL `cand`) leads, so the banner talks about the run in
  // the header.
  const ranked = sortVerdicts((items ?? []).filter((i) => !i?.last));
  const lead = leadKey ? ranked.find((i) => i?.key === leadKey) : null;
  const sorted = [
    ...(lead ? [lead, ...ranked.filter((i) => i !== lead)] : ranked),
    ...(items ?? []).filter((i) => i?.last),
  ];
  if (!sorted.length) return null;
  if (oneLine)
    return (
      <OneLineFold
        sorted={sorted}
        testId={testId}
        sx={sx}
        after={after}
        moreActions={moreActions}
      />
    );
  // IT-R3 G6 P1: a `pin` item (the ship / don't-ship line) is part of the
  // verdict: it shows under the lead line even when folded, never behind
  // `+N more`; the fold counts only the lines it hides.
  const pinned = sorted.slice(1).filter((i) => i?.pin);
  const hidden = sorted.length - 1 - pinned.length;
  const open = Boolean(prefs.extras?.verdictsOpen) && hidden > 0;
  const setOpen = (v) =>
    patch({ extras: { verdictsOpen: v ? true : undefined } });
  const toggle =
    hidden > 0 ? (
      <Button
        size="small"
        color="inherit"
        aria-expanded={open}
        onClick={() => setOpen(!open)}
        sx={toggleSx}
        data-testid="verdict-more"
      >
        {verdictFoldLabel(open, hidden + 1)}
      </Button>
    ) : null;
  if (open)
    return (
      <Stack
        gap={0.5}
        sx={{ minWidth: 0, ...LINE_SX, ...sx }}
        data-testid={testId}
      >
        {sorted.map((item, i) => (
          <VerdictLine
            key={item.key ?? i}
            verdict={item.verdict}
            actions={item.actions ?? []}
            compact
            sx={WRAP_SX}
          />
        ))}
        <Box sx={{ ml: 2.5 }}>{toggle}</Box>
      </Stack>
    );
  const top = sorted[0];
  const leadLine = (
    <Stack
      direction="row"
      alignItems="center"
      gap={0.5}
      sx={{ minWidth: 0, ...LINE_SX, ...(pinned.length ? {} : sx) }}
      data-testid={pinned.length ? undefined : testId}
    >
      <Box sx={{ minWidth: 0, flex: "0 1 auto" }}>
        <VerdictLine
          verdict={top.verdict}
          actions={top.actions}
          compact
          sx={WRAP_SX}
        />
      </Box>
      {toggle}
    </Stack>
  );
  if (!pinned.length) return leadLine;
  return (
    <Stack
      gap={0.25}
      sx={{ minWidth: 0, ...LINE_SX, ...sx }}
      data-testid={testId}
    >
      {leadLine}
      {pinned.map((item, i) => (
        <Box key={item.key ?? i} data-testid="verdict-pinned">
          <VerdictLine
            verdict={item.verdict}
            actions={item.actions ?? []}
            compact
            sx={WRAP_SX}
          />
        </Box>
      ))}
    </Stack>
  );
}

VerdictFold.propTypes = {
  leadKey: PropTypes.string,
  // IT-R4 G6 P3: one sentence + `+N more` popover (Matrix); `after` sits
  // right after the sentence, `moreActions` close the popover.
  oneLine: PropTypes.bool,
  after: PropTypes.node,
  moreActions: PropTypes.array,
  items: PropTypes.array,
  surface: PropTypes.string.isRequired,
  testId: PropTypes.string,
  sx: PropTypes.object,
};
