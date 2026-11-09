import React from "react";
import PropTypes from "prop-types";
import Box from "@mui/material/Box";
import Chip from "@mui/material/Chip";
import IconButton from "@mui/material/IconButton";
import Link from "@mui/material/Link";
import MenuItem from "@mui/material/MenuItem";
import TextField from "@mui/material/TextField";
import Tooltip from "@mui/material/Tooltip";
import Typography from "@mui/material/Typography";

import { RouterLink } from "src/routes/components";

import { KIND_LABEL, hubPaths } from "../constants";
import { formatDate, splitRef } from "../lib/format";

import { copyText } from "./CopyCommand";
import EntryCover from "./EntryCover";
import HubIcon from "./HubIcon";
import LikeButton from "./LikeButton";
import TierBadge from "./TierBadge";
import ToneChip from "./ToneChip";

// The entry header (SPEC §7.2): publisher / name with a copy of the hub:
// reference, tier, version selector and Like, then the chips.
export default function EntryHeader({
  entry,
  version,
  versions,
  onVersion,
  signedIn,
  moderate,
}) {
  const [copied, setCopied] = React.useState(false);
  const publisher = entry.publisher || {};
  const manifest = version?.manifest || {};
  const derived = entry.remixOf ? splitRef(entry.remixOf) : null;
  const chips = [
    KIND_LABEL[entry.kind] || entry.kind,
    ...(entry.tags || []),
    manifest.license,
  ].filter(Boolean);
  const copyRef = async () => {
    if (await copyText(`hub:${entry.ref}`)) {
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    }
  };
  return (
    <Box
      data-testid="hub-entry-header"
      sx={{ display: "flex", flexDirection: "column", gap: 1.5 }}
    >
      <Box
        sx={{ display: "flex", gap: 2, alignItems: "flex-start", minWidth: 0 }}
      >
        <EntryCover cover={entry.cover} size={56} />
        <Box
          sx={{
            minWidth: 0,
            flex: 1,
            display: "flex",
            flexDirection: "column",
            gap: 0.5,
          }}
        >
          <Box
            sx={{
              display: "flex",
              alignItems: "center",
              gap: 0.5,
              flexWrap: "wrap",
              minWidth: 0,
            }}
          >
            <Typography
              component="h1"
              typography="m2"
              fontWeight={600}
              sx={{ overflowWrap: "anywhere" }}
            >
              <Link
                component={RouterLink}
                href={hubPaths.publisher(publisher.handle)}
                color="text.secondary"
                underline="hover"
              >
                {publisher.handle}
              </Link>
              <Box component="span" sx={{ color: "text.disabled", mx: 0.5 }}>
                /
              </Box>
              <Box component="span" data-testid="hub-entry-name">
                {entry.name}
              </Box>
            </Typography>
            <Tooltip title={copied ? "Copied" : `Copy hub:${entry.ref}`}>
              <IconButton
                size="small"
                onClick={copyRef}
                aria-label={`Copy hub:${entry.ref}`}
              >
                <HubIcon name={copied ? "check" : "copy"} size={14} />
              </IconButton>
            </Tooltip>
            <TierBadge tier={publisher.tier} />
          </Box>
          <Typography typography="s1" color="text.primary" fontWeight={500}>
            {entry.title}
          </Typography>
          <Typography typography="s2" color="text.secondary">
            {entry.tagline}
          </Typography>
          <Typography typography="s2" color="text.tertiary">
            {`by ${publisher.displayName || publisher.handle} · first published ${formatDate(entry.firstPublishedAt)} · updated ${formatDate(version?.approvedAt)}`}
          </Typography>
          {derived && (
            <Typography
              typography="s2"
              color="text.secondary"
              data-testid="hub-derived-from"
            >
              {"Derived from "}
              <Link
                component={RouterLink}
                href={hubPaths.entry(derived.publisher, derived.name)}
                underline="hover"
              >
                {`${derived.publisher}/${derived.name}`}
              </Link>
            </Typography>
          )}
        </Box>
      </Box>
      <Box
        sx={{ display: "flex", alignItems: "center", gap: 1, flexWrap: "wrap" }}
      >
        {(versions || []).length > 0 && (
          <TextField
            select
            size="small"
            value={version?.version || ""}
            onChange={(event) => onVersion(event.target.value)}
            label="Version"
            sx={{ minWidth: 140 }}
            SelectProps={{ MenuProps: { disablePortal: false } }}
            inputProps={{ "data-testid": "hub-version-select" }}
          >
            {versions.map((row) => (
              <MenuItem key={row.version} value={row.version}>
                {row.version}
                {row.status === "yanked" ? " (yanked)" : ""}
              </MenuItem>
            ))}
          </TextField>
        )}
        <LikeButton
          publisher={publisher.handle}
          name={entry.name}
          likes={entry.likes}
          likedByMe={entry.likedByMe}
          signedIn={signedIn}
        />
        {moderate}
      </Box>
      <Box sx={{ display: "flex", gap: 0.75, flexWrap: "wrap" }}>
        {chips.map((chip) => (
          <Chip key={chip} label={chip} size="small" variant="outlined" />
        ))}
        {entry.noKeyNeeded ? (
          <Chip
            label="No key needed"
            size="small"
            color="success"
            variant="outlined"
          />
        ) : null}
        {manifest.requires?.chromium ? (
          <Chip label="Needs Chromium" size="small" variant="outlined" />
        ) : null}
        {entry.pick && (
          <ToneChip
            tone="attention"
            icon={<HubIcon name="star" size={12} />}
            label={`Maintainer pick · week of ${formatDate(entry.pick.week)}`}
          />
        )}
      </Box>
    </Box>
  );
}

EntryHeader.propTypes = {
  entry: PropTypes.object.isRequired,
  version: PropTypes.object,
  versions: PropTypes.array,
  onVersion: PropTypes.func,
  signedIn: PropTypes.bool,
  moderate: PropTypes.node,
};
