import React, { useState } from "react";
import PropTypes from "prop-types";
import {
  Box,
  Checkbox,
  IconButton,
  Link,
  ListItemIcon,
  Menu,
  MenuItem,
  Stack,
  Typography,
} from "@mui/material";
import { alpha } from "@mui/material/styles";
import Iconify from "src/components/iconify";
import CustomTooltip from "src/components/tooltip";
import RelativeTime from "src/sections/common/ux/RelativeTime";
import { fmtCount } from "src/sections/common/format/fmt";
import {
  DefaultChip,
  VersionCardWrapper,
} from "src/sections/workbench/createPrompt/VersionHistory/VersionStyledComponents";
import { VersionNumber } from "src/sections/common/Datasets/VersionChip";
import VersionTagChip from "src/sections/common/Datasets/VersionTagChip";
import ExperimentLinks from "./ExperimentLinks";
import {
  countsParts,
  deltaParts,
  deltaTooltip,
  labelMoveText,
} from "./versionText";

const MENU = [
  { key: "view", label: "View this version", icon: "mdi:eye-outline" },
  {
    key: "changes",
    label: "Show changes in this version",
    icon: "mdi:file-compare",
  },
  { key: "tag", label: "Tag version…", icon: "mdi:tag-outline" },
  {
    key: "experiment",
    label: "New experiment on this version…",
    icon: "mdi:flask-outline",
  },
  { key: "restore", label: "Restore as new version…", icon: "mdi:restore" },
  { key: "copy", label: "Copy version id", icon: "mdi:content-copy" },
];

const DELTA_COLOR = {
  added: "green.600",
  modified: "blue.600",
  deleted: "red.600",
};

/**
 * One entry of the dataset Version history, in the Prompts version-history
 * grammar (VersionCardWrapper, grey `v6` tag, DefaultChip `Latest`, tag
 * pills):
 *   v6  Latest  golden-v4                               ⋯
 *   Added 10 rows from support-prod traces; …
 *   Ravi Menon · 3d ago · +10 ~2 −0
 *   Used by #4 #5 #6 #7 #8
 */
const VersionHistoryEntry = ({
  version,
  viewing,
  selectable,
  checked,
  checkDisabled,
  onToggleCheck,
  onOpen,
  onAction,
  pickLabel,
  nextNumber,
  canRestore = true,
  canWrite = true,
}) => {
  const [anchor, setAnchor] = useState(null);
  const c = countsParts(version.counts);
  const deltas = deltaParts(version.counts);
  const experiments = version.experiments ?? [];
  const countsTip = `${deltaTooltip(version.counts)} · ${fmtCount(
    version.row_count,
  )} rows in v${version.number}`;

  return (
    <VersionCardWrapper
      role="button"
      tabIndex={0}
      aria-current={viewing ? "true" : undefined}
      aria-label={`Version ${version.number}`}
      onClick={() =>
        selectable ? !checkDisabled && onToggleCheck() : onOpen()
      }
      onKeyDown={(e) => {
        if (e.key === "Enter" && e.target === e.currentTarget) onOpen();
      }}
      sx={{
        p: 1.5,
        gap: 0.5,
        ...((viewing || (selectable && checked)) && {
          borderColor: selectable ? "primary.light" : "primary.main",
          bgcolor: (t) => alpha(t.palette.primary.main, 0.04),
        }),
      }}
    >
      <Stack
        direction="row"
        alignItems="center"
        gap={0.75}
        sx={{ minWidth: 0 }}
      >
        {selectable && pickLabel && (
          <Box
            component="span"
            aria-label={`Picked as ${pickLabel}`}
            sx={{
              width: 18,
              height: 18,
              borderRadius: "50%",
              bgcolor: "primary.main",
              color: "primary.contrastText",
              typography: "s3",
              fontWeight: 700,
              lineHeight: "18px",
              textAlign: "center",
              flexShrink: 0,
            }}
          >
            {pickLabel}
          </Box>
        )}
        {selectable && (
          <CustomTooltip
            show={checkDisabled}
            title="Two versions are selected. Clear one to pick this one."
            placement="bottom"
            arrow
            size="small"
          >
            <Box component="span" sx={{ display: "flex" }}>
              <Checkbox
                size="small"
                checked={checked}
                disabled={checkDisabled}
                onClick={(e) => e.stopPropagation()}
                onChange={onToggleCheck}
                inputProps={{ "aria-label": `Select v${version.number}` }}
                sx={{ p: 0, mr: 0.25 }}
              />
            </Box>
          </CustomTooltip>
        )}
        <VersionNumber number={version.number} />
        {version.is_latest && <DefaultChip label="Latest" />}
        {(version.tags ?? []).map((t) => {
          const env = (version.labels ?? []).some(
            (l) => l.name === t && l.kind === "environment",
          );
          return (
            <Box
              key={t}
              component="span"
              sx={{ display: "inline-flex", alignItems: "center", gap: 0.4 }}
              title={env ? "Environment" : undefined}
            >
              {env && (
                <Box
                  component="span"
                  aria-label="Environment"
                  sx={{
                    width: 6,
                    height: 6,
                    borderRadius: "50%",
                    bgcolor: t === "production" ? "success.main" : "info.main",
                  }}
                />
              )}
              <VersionTagChip name={t} />
            </Box>
          );
        })}
        <Box sx={{ flex: 1 }} />
        {viewing && (
          <Typography
            typography="s3"
            color="primary.main"
            fontWeight={500}
            noWrap
          >
            Viewing
          </Typography>
        )}
        <IconButton
          size="small"
          aria-label={`Actions for v${version.number}`}
          onClick={(e) => {
            e.stopPropagation();
            setAnchor(e.currentTarget);
          }}
          sx={{ p: 0.25, color: "text.secondary" }}
        >
          <Iconify icon="mdi:dots-horizontal" width={18} />
        </IconButton>
      </Stack>

      <Typography
        typography="s1"
        color="text.primary"
        title={version.description}
        sx={{
          display: "-webkit-box",
          WebkitLineClamp: 2,
          WebkitBoxOrient: "vertical",
          overflow: "hidden",
        }}
      >
        {version.description}
      </Typography>
      {(version.label_moves ?? []).map((m) => (
        <Typography
          key={`${m.label}-${m.at}`}
          typography="s2"
          color="text.secondary"
          data-testid="label-move"
        >
          {labelMoveText(m)}
        </Typography>
      ))}
      {(version.retests ?? []).map((r) => {
        const inNote = (version.description ?? "").includes(r.line);
        return (
          <Stack
            key={r.run_id}
            direction="row"
            alignItems="center"
            gap={0.75}
            flexWrap="wrap"
            data-testid="version-retest"
          >
            <Typography
              typography="s2"
              color={r.failed ? "text.primary" : "green.700"}
            >
              {r.line}
            </Typography>
            {r.finished && !inNote && canWrite ? (
              <Link
                component="button"
                type="button"
                typography="s2"
                data-testid="retest-add-note"
                onClick={(e) => {
                  e.stopPropagation();
                  onAction?.("retest-note", version, r.line);
                }}
              >
                Add to this version&apos;s note
              </Link>
            ) : null}
          </Stack>
        );
      })}

      <Stack
        direction="row"
        alignItems="center"
        gap={0.5}
        sx={{ typography: "s2", color: "text.secondary" }}
      >
        <span>{version.created_by?.name ?? "Unknown"}</span>
        <span>·</span>
        <RelativeTime iso={version.created_at} by={version.created_by?.name} />
        {deltas.length > 0 && <span>·</span>}
        <CustomTooltip
          show
          title={countsTip}
          placement="bottom"
          arrow
          size="small"
        >
          <Stack
            direction="row"
            gap={0.5}
            component="span"
            sx={{ fontVariantNumeric: "tabular-nums" }}
            data-testid="version-deltas"
          >
            {deltas.map((d) => (
              <Box
                component="span"
                key={d.key}
                sx={{ color: DELTA_COLOR[d.key] }}
              >
                {d.glyph}
                {d.n}
              </Box>
            ))}
            {deltas.length === 0 && c.splitChanges > 0 && (
              <Box component="span">
                {c.splitChanges} split change{c.splitChanges === 1 ? "" : "s"}
              </Box>
            )}
          </Stack>
        </CustomTooltip>
      </Stack>

      <ExperimentLinks experiments={experiments} />

      <Menu
        anchorEl={anchor}
        open={Boolean(anchor)}
        onClose={() => setAnchor(null)}
        onClick={(e) => e.stopPropagation()}
        anchorOrigin={{ vertical: "bottom", horizontal: "right" }}
        transformOrigin={{ vertical: "top", horizontal: "right" }}
      >
        {MENU.filter(
          (m) =>
            (m.key !== "restore" || (!version.is_latest && canRestore)) &&
            // Viewers only read: no tagging or new experiments from here.
            (canWrite || !["tag", "experiment"].includes(m.key)),
        ).map((m) => (
          <MenuItem
            key={m.key}
            disabled={
              m.key === "changes" &&
              version.number === 1 &&
              !version.counts?.added
            }
            onClick={() => {
              setAnchor(null);
              onAction(m.key, version);
            }}
            sx={{ typography: "s1" }}
          >
            <ListItemIcon sx={{ minWidth: 28 }}>
              <Iconify icon={m.icon} width={18} />
            </ListItemIcon>
            {m.key === "view" && version.is_latest
              ? "View latest"
              : m.key === "restore" && nextNumber
                ? `Restore as v${nextNumber}…`
                : m.label}
          </MenuItem>
        ))}
      </Menu>
    </VersionCardWrapper>
  );
};

VersionHistoryEntry.propTypes = {
  version: PropTypes.object.isRequired,
  viewing: PropTypes.bool,
  selectable: PropTypes.bool,
  checked: PropTypes.bool,
  checkDisabled: PropTypes.bool,
  onToggleCheck: PropTypes.func,
  onOpen: PropTypes.func,
  onAction: PropTypes.func,
  now: PropTypes.number,
  /** R3: `A` / `B` while picking two versions to compare. */
  pickLabel: PropTypes.string,
  /** R3: the number `Restore as v7…` creates (latest + 1). */
  nextNumber: PropTypes.number,
  /** R3: `Restore as v7…` is an Advanced action (⌘K in Simple). */
  canRestore: PropTypes.bool,
  /** False for viewers: Tag and New experiment leave the menu. */
  canWrite: PropTypes.bool,
};

export default VersionHistoryEntry;
