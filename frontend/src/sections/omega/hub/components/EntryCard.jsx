import React from "react";
import PropTypes from "prop-types";
import Box from "@mui/material/Box";
import CardActionArea from "@mui/material/CardActionArea";
import Tooltip from "@mui/material/Tooltip";
import Typography from "@mui/material/Typography";

import { RouterLink } from "src/routes/components";

import { KIND_LABEL, hubPaths } from "../constants";
import { countOrDash, exactCount, splitRef } from "../lib/format";
import { showsInstalls, useInstallPath } from "../lib/installs";

import EntryCover from "./EntryCover";
import HubIcon from "./HubIcon";
import TierBadge from "./TierBadge";
import { toneSx } from "./ToneChip";

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

// One entry in a grid: cover, title, publisher/name, tagline, tier, and the
// counts the server measured (or "—"). Installs only where they can be
// counted (lib/installs.js).
export default function EntryCard({ entry }) {
  const installPath = useInstallPath();
  const href = hubPaths.entry(entry.publisher?.handle, entry.name);
  const derived = entry.remixOf ? splitRef(entry.remixOf) : null;
  return (
    <CardActionArea
      component={RouterLink}
      href={href}
      data-testid="hub-entry-card"
      data-ref={entry.ref}
      sx={{
        height: "100%",
        minWidth: 0,
        overflowWrap: "anywhere",
        display: "flex",
        flexDirection: "column",
        alignItems: "stretch",
        gap: 1.25,
        p: 2,
        borderRadius: 1,
        border: "1px solid",
        borderColor: "divider",
        bgcolor: "background.paper",
        transition: "border-color 120ms",
        "&:hover": { borderColor: "text.disabled" },
      }}
    >
      <Box
        sx={{
          display: "flex",
          gap: 1.5,
          alignItems: "flex-start",
          minWidth: 0,
        }}
      >
        <EntryCover cover={entry.cover} size={40} />
        <Box sx={{ minWidth: 0, flex: 1 }}>
          <Typography
            typography="s1"
            fontWeight="fontWeightSemiBold"
            color="text.primary"
            sx={{
              overflow: "hidden",
              textOverflow: "ellipsis",
              whiteSpace: "nowrap",
            }}
          >
            {entry.title || entry.name}
          </Typography>
          <Typography
            typography="s2"
            color="text.secondary"
            sx={{
              overflow: "hidden",
              textOverflow: "ellipsis",
              whiteSpace: "nowrap",
            }}
          >
            {entry.ref}
          </Typography>
        </Box>
      </Box>
      <Typography
        typography="s2"
        color="text.secondary"
        sx={{
          flex: 1,
          display: "-webkit-box",
          WebkitLineClamp: 2,
          WebkitBoxOrient: "vertical",
          overflow: "hidden",
          minHeight: 36,
        }}
      >
        {entry.tagline}
      </Typography>
      {entry.pick?.why && (
        <Box
          sx={{
            display: "flex",
            gap: 0.75,
            alignItems: "flex-start",
            ...toneSx("attention"),
          }}
        >
          <HubIcon name="star" size={13} />
          <Typography typography="s2" sx={{ color: "text.secondary" }}>
            {entry.pick.why}
          </Typography>
        </Box>
      )}
      {/* One line at every width (HUB-23): the labels give way, the counts never do. */}
      <Box
        sx={{ display: "flex", alignItems: "center", gap: 1.25, minWidth: 0 }}
        data-testid="hub-entry-card-meta"
      >
        <Box
          sx={{
            display: "flex",
            alignItems: "center",
            gap: 1.25,
            minWidth: 0,
            flex: 1,
            overflow: "hidden",
          }}
        >
          <Box sx={{ flexShrink: 0, display: "inline-flex" }}>
            <TierBadge tier={entry.publisher?.tier} short />
          </Box>
          <Typography
            typography="s2"
            color="text.secondary"
            noWrap
            sx={{ flexShrink: 0 }}
          >
            {KIND_LABEL[entry.kind] || entry.kind}
          </Typography>
          {entry.noKeyNeeded && (
            <Typography
              typography="s2"
              color="success.dark"
              noWrap
              sx={{ minWidth: 0 }}
            >
              No key needed
            </Typography>
          )}
        </Box>
        <Box
          sx={{
            display: "flex",
            alignItems: "center",
            gap: 1.25,
            flexShrink: 0,
          }}
        >
          <Meta icon="heart" value={exactCount(entry.likes)} label="Likes" />
          {showsInstalls(installPath, entry.installs30d) && (
            <Meta
              icon="download"
              value={countOrDash(entry.installs30d)}
              label="Installs, last 30 days"
            />
          )}
        </Box>
      </Box>
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
    </CardActionArea>
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
    likes: PropTypes.number,
    installs30d: PropTypes.number,
    noKeyNeeded: PropTypes.bool,
    pick: PropTypes.object,
    remixOf: PropTypes.string,
    ranked: PropTypes.bool,
  }).isRequired,
};

export function EntryGrid({ entries }) {
  return (
    <Box
      data-testid="hub-entry-grid"
      sx={{
        minWidth: 0,
        display: "grid",
        gap: 2,
        gridTemplateColumns: {
          xs: "1fr",
          sm: "repeat(2, minmax(0, 1fr))",
          lg: "repeat(3, minmax(0, 1fr))",
        },
      }}
    >
      {entries.map((entry) => (
        <EntryCard key={entry.ref} entry={entry} />
      ))}
    </Box>
  );
}

EntryGrid.propTypes = { entries: PropTypes.array.isRequired };
