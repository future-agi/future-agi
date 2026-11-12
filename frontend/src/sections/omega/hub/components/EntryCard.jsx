import React from "react";
import PropTypes from "prop-types";
import Box from "@mui/material/Box";
import Link from "@mui/material/Link";
import Skeleton from "@mui/material/Skeleton";
import Tooltip from "@mui/material/Tooltip";
import Typography from "@mui/material/Typography";

import { RouterLink } from "src/routes/components";

import { KIND_LABEL, hubPaths } from "../constants";
import { countIfAny, splitRef } from "../lib/format";
import { showsInstalls, useInstallPath } from "../lib/installs";

import { CopyButton } from "./CopyCommand";
import { UseEntryButton } from "./EntryCommands";
import EntryCover from "./EntryCover";
import { hubPageQuery } from "./HubFrame";
import HubIcon from "./HubIcon";
import TierBadge from "./TierBadge";
import { toneSx } from "./ToneChip";

const MONO_FONT =
  "'JetBrains Mono', ui-monospace, SFMono-Regular, Menlo, monospace";

const CLAMP_2 = {
  display: "-webkit-box",
  WebkitLineClamp: 2,
  WebkitBoxOrient: "vertical",
  overflow: "hidden",
};

// Columns by the Hub page's own width (HubFrame is the container): 4 from
// 1120 px (a 1440 window with the sidebar, about 1150 px, still 4 with a
// classic scrollbar), 3 from 880 px (1280), 2 from 560 px (1024 with the
// sidebar), 1 below (a phone). A grid of 3 (Start here) stops at 3, so its
// cards are wider than the ones below. The window breakpoints first are for
// a browser without container queries.
const GRID_3_SX = {
  listStyle: "none",
  m: 0,
  p: 0,
  minWidth: 0,
  display: "grid",
  gap: 2,
  gridTemplateColumns: {
    xs: "minmax(0, 1fr)",
    sm: "repeat(2, minmax(0, 1fr))",
    lg: "repeat(3, minmax(0, 1fr))",
  },
  [hubPageQuery("(max-width: 559.98px)")]: {
    gridTemplateColumns: "minmax(0, 1fr)",
  },
  [hubPageQuery("(min-width: 560px)")]: {
    gridTemplateColumns: "repeat(2, minmax(0, 1fr))",
  },
  [hubPageQuery("(min-width: 880px)")]: {
    gridTemplateColumns: "repeat(3, minmax(0, 1fr))",
  },
};

const GRID_SX = {
  3: GRID_3_SX,
  4: {
    ...GRID_3_SX,
    [hubPageQuery("(min-width: 1120px)")]: {
      gridTemplateColumns: "repeat(4, minmax(0, 1fr))",
    },
  },
};

const CARD_SX = {
  position: "relative",
  flex: 1,
  minWidth: 0,
  overflowWrap: "anywhere",
  display: "flex",
  flexDirection: "column",
  gap: 1.25,
  p: 2,
  borderRadius: 1,
  border: "1px solid",
  borderColor: "divider",
  bgcolor: "background.paper",
  transition: "border-color 120ms",
  "&:hover, &:focus-within": { borderColor: "border.hover" },
};

// The title link covers the whole card (its ::after), so a click anywhere on
// the card opens the entry; the controls on the card, and the tier and the
// counts (for their tooltips), sit above it. Focus draws one ring around the
// card, not around the title text.
const STRETCHED_LINK_SX = {
  "&::after": {
    content: '""',
    position: "absolute",
    inset: 0,
    borderRadius: 1,
  },
  "&:focus-visible": { outline: "none" },
  "&:focus-visible::after": {
    outline: "2px solid",
    outlineColor: (theme) => theme.palette.primary.main,
    outlineOffset: 2,
  },
};

const ABOVE_LINK = { position: "relative", zIndex: 1 };

function MetaDot() {
  return (
    <Box component="span" aria-hidden="true" sx={{ typography: "s2" }}>
      ·
    </Box>
  );
}

const NO_KEY = "Runs without a key";

const HIDDEN_TEXT = {
  position: "absolute",
  width: "1px",
  height: "1px",
  overflow: "hidden",
  clip: "rect(0 0 0 0)",
  whiteSpace: "nowrap",
};

function Meta({ icon, value, label }) {
  return (
    <Tooltip title={label}>
      <Box
        component="span"
        aria-label={`${label}: ${value}`}
        sx={{
          display: "inline-flex",
          alignItems: "center",
          gap: 0.5,
          color: "text.secondary",
        }}
      >
        <HubIcon name={icon} size={13} />
        <Typography component="span" typography="s2">
          {value}
        </Typography>
      </Box>
    </Tooltip>
  );
}

Meta.propTypes = {
  icon: PropTypes.string,
  value: PropTypes.node,
  label: PropTypes.string,
};

// The copy button beside the reference is a 25 px target (WCAG 2.2 AA asks
// for 24), pulled back by its own margin so the reference keeps its line
// height.
const COPY_REF_SX = {
  ...ABOVE_LINK,
  alignSelf: "flex-start",
  p: 0.75,
  my: -0.5,
  mr: -0.5,
  color: "text.secondary",
};

// One entry in a grid. The cover tile sits beside the title, which is the
// link (a heading, so a screen reader can move from card to card), with the
// publisher/name reference in mono under it, whole (a long one wraps), and a
// copy button. Then two lines of what it does, and kind, tier and the latest
// version as quiet metadata. The foot says "Runs without a key" when it does
// and holds "Use" (both commands), which shows only once the commands can
// run. A count shows only once there is one: a new Hub shows no "♡ 0" or
// "⬇ —". Installs only where they can be counted (lib/installs.js).
// `variant="pick"` is the Start here card: the maintainer's why leads in
// primary text, with the tagline kept under it.
export default function EntryCard({ entry, variant = "default" }) {
  const installPath = useInstallPath();
  const href = hubPaths.entry(entry.publisher?.handle, entry.name);
  const derived = entry.remixOf ? splitRef(entry.remixOf) : null;
  const likeCount = countIfAny(entry.likes);
  const installCount = showsInstalls(installPath, entry.installs30d)
    ? countIfAny(entry.installs30d)
    : null;
  const title = entry.title || entry.name;
  const why = entry.pick?.why;
  const pickCard = variant === "pick" && Boolean(why);
  const canUse = Boolean(installPath && entry.latestVersion);
  return (
    <Box
      component="article"
      data-testid="hub-entry-card"
      data-ref={entry.ref}
      sx={CARD_SX}
    >
      <Box
        sx={{
          display: "flex",
          gap: variant === "pick" ? 1.5 : 1.25,
          alignItems: "flex-start",
          minWidth: 0,
        }}
      >
        <EntryCover cover={entry.cover} size={variant === "pick" ? 40 : 32} />
        <Box
          sx={{
            minWidth: 0,
            flex: 1,
            display: "flex",
            flexDirection: "column",
            gap: 0.25,
          }}
        >
          <Typography
            component="h3"
            typography="m3"
            fontWeight="fontWeightSemiBold"
            color="text.primary"
            data-testid="hub-entry-card-title"
            sx={{ m: 0, ...CLAMP_2 }}
          >
            <Link
              component={RouterLink}
              href={href}
              color="inherit"
              underline="hover"
              sx={STRETCHED_LINK_SX}
            >
              {title}
            </Link>
          </Typography>
          <Box
            sx={{
              display: "flex",
              alignItems: "flex-start",
              gap: 0.25,
              minWidth: 0,
            }}
          >
            <Typography
              component="span"
              typography="s2"
              color="text.secondary"
              data-testid="hub-entry-card-ref"
              sx={{
                fontFamily: MONO_FONT,
                minWidth: 0,
                overflowWrap: "anywhere",
              }}
            >
              {entry.ref}
            </Typography>
            <CopyButton
              text={entry.ref}
              label={`Copy reference ${entry.ref}`}
              tooltip="Copy reference"
              size={13}
              testId="hub-entry-card-copy-ref"
              sx={COPY_REF_SX}
            />
          </Box>
        </Box>
      </Box>
      {pickCard && (
        <Typography
          typography="s1_2"
          color="text.primary"
          data-testid="hub-entry-card-why"
        >
          {why}
        </Typography>
      )}
      <Typography
        typography="s1"
        color="text.secondary"
        data-testid="hub-entry-card-tagline"
        sx={{ flex: 1, ...CLAMP_2 }}
      >
        {entry.tagline}
      </Typography>
      {why && !pickCard && (
        <Typography
          typography="s2"
          color="text.secondary"
          data-testid="hub-entry-card-why"
        >
          {`Maintainer pick: ${why}`}
        </Typography>
      )}
      {derived && (
        <Typography typography="s3" color="text.tertiary">
          {`Derived from ${derived.publisher}/${derived.name}`}
        </Typography>
      )}
      {entry.ranked === false && (
        <Typography typography="s3" color="text.tertiary">
          Not ranked
        </Typography>
      )}
      <Box
        data-testid="hub-entry-card-meta"
        sx={{
          alignSelf: "stretch",
          display: "flex",
          alignItems: "center",
          gap: 0.75,
          minWidth: 0,
          color: "text.secondary",
        }}
      >
        <Typography
          component="span"
          typography="s2"
          color="text.secondary"
          noWrap
          sx={{ flexShrink: 0 }}
        >
          {KIND_LABEL[entry.kind] || entry.kind}
        </Typography>
        <MetaDot />
        <Box sx={{ ...ABOVE_LINK, flexShrink: 0, display: "inline-flex" }}>
          <TierBadge tier={entry.publisher?.tier} short quiet />
        </Box>
        {entry.latestVersion && (
          <>
            <MetaDot />
            <Typography
              component="span"
              typography="s2"
              color="text.secondary"
              noWrap
              data-testid="hub-entry-card-version"
              sx={{ fontFamily: MONO_FONT, minWidth: 0 }}
            >
              {`v${entry.latestVersion}`}
            </Typography>
          </>
        )}
        <Box
          sx={{
            ...ABOVE_LINK,
            ml: "auto",
            display: "flex",
            alignItems: "center",
            gap: 1.25,
            flexShrink: 0,
          }}
        >
          {likeCount && <Meta icon="heart" value={likeCount} label="Likes" />}
          {installCount && (
            <Meta
              icon="download"
              value={installCount}
              label="Installs, last 30 days"
            />
          )}
        </Box>
      </Box>
      {(entry.noKeyNeeded || canUse) && (
        <Box
          data-testid="hub-entry-card-foot"
          sx={{
            display: "flex",
            alignItems: "center",
            gap: 1,
            minHeight: 30,
            pt: 1.25,
            borderTop: "1px solid",
            borderColor: "divider",
          }}
        >
          {entry.noKeyNeeded && (
            <Typography
              component="span"
              typography="s2"
              color="text.secondary"
              sx={{ display: "inline-flex", alignItems: "center", gap: 0.5 }}
            >
              <Box
                component="span"
                sx={{ display: "inline-flex", ...toneSx("success") }}
              >
                <HubIcon name="check" size={13} />
              </Box>
              {NO_KEY}
            </Typography>
          )}
          <Box sx={{ ml: "auto", display: "inline-flex" }}>
            <UseEntryButton entry={entry} />
          </Box>
        </Box>
      )}
    </Box>
  );
}

EntryCard.propTypes = {
  entry: PropTypes.shape({
    ref: PropTypes.string,
    name: PropTypes.string,
    title: PropTypes.string,
    tagline: PropTypes.string,
    kind: PropTypes.string,
    cover: PropTypes.object,
    publisher: PropTypes.object,
    latestVersion: PropTypes.string,
    likes: PropTypes.number,
    installs30d: PropTypes.number,
    noKeyNeeded: PropTypes.bool,
    pick: PropTypes.object,
    remixOf: PropTypes.string,
    ranked: PropTypes.bool,
  }).isRequired,
  variant: PropTypes.oneOf(["default", "pick"]),
};

// A list of entries. Every card in a row has the row's height, so the feet
// (and their Use buttons) line up. `columns` (3 or 4) is the most a row
// holds.
export function EntryGrid({
  entries,
  variant = "default",
  columns = 4,
  testId = "hub-entry-grid",
}) {
  return (
    <Box
      component="ul"
      role="list"
      data-testid={testId}
      sx={GRID_SX[columns] || GRID_SX[4]}
    >
      {entries.map((entry) => (
        <Box
          component="li"
          key={entry.ref}
          sx={{ minWidth: 0, display: "flex", flexDirection: "column" }}
        >
          <EntryCard entry={entry} variant={variant} />
        </Box>
      ))}
    </Box>
  );
}

EntryGrid.propTypes = {
  entries: PropTypes.array.isRequired,
  variant: PropTypes.oneOf(["default", "pick"]),
  columns: PropTypes.oneOf([3, 4]),
  testId: PropTypes.string,
};

// The grid while it loads: placeholders in the card's own shape (cover,
// title, reference, two lines, foot), never a spinner. `label` is what a
// screen reader hears while it waits. A grid of 3 is Start here, whose
// cards have the larger cover.
export function EntryGridSkeleton({
  count = 8,
  columns = 4,
  label = "Loading entries",
  testId = "hub-loading",
}) {
  const cover = columns === 3 ? 40 : 32;
  return (
    <Box
      data-testid={testId}
      role="status"
      aria-busy="true"
      sx={GRID_SX[columns] || GRID_SX[4]}
    >
      <Box component="span" sx={HIDDEN_TEXT}>
        {label}
      </Box>
      {Array.from({ length: count }, (_, i) => (
        <Box
          key={i}
          data-testid="hub-entry-card-skeleton"
          sx={{
            display: "flex",
            flexDirection: "column",
            gap: 1.25,
            p: 2,
            borderRadius: 1,
            border: "1px solid",
            borderColor: "divider",
            bgcolor: "background.paper",
          }}
        >
          <Box
            sx={{
              display: "flex",
              gap: columns === 3 ? 1.5 : 1.25,
              alignItems: "flex-start",
            }}
          >
            <Skeleton variant="rounded" width={cover} height={cover} />
            <Box sx={{ flex: 1, minWidth: 0 }}>
              <Skeleton variant="text" width="70%" sx={{ fontSize: 13 }} />
              <Skeleton variant="text" width="50%" sx={{ fontSize: 11 }} />
            </Box>
          </Box>
          <Box>
            <Skeleton variant="text" width="100%" sx={{ fontSize: 12 }} />
            <Skeleton variant="text" width="80%" sx={{ fontSize: 12 }} />
          </Box>
          <Skeleton variant="text" width="40%" sx={{ fontSize: 11 }} />
          <Box
            sx={{
              display: "flex",
              alignItems: "center",
              justifyContent: "space-between",
              pt: 1.25,
              borderTop: "1px solid",
              borderColor: "divider",
            }}
          >
            <Skeleton variant="text" width={96} sx={{ fontSize: 11 }} />
            <Skeleton variant="rounded" width={60} height={26} />
          </Box>
        </Box>
      ))}
    </Box>
  );
}

EntryGridSkeleton.propTypes = {
  count: PropTypes.number,
  columns: PropTypes.oneOf([3, 4]),
  label: PropTypes.string,
  testId: PropTypes.string,
};
