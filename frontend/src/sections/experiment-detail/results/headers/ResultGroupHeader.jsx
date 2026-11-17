import React, { useEffect, useRef, useState } from "react";
import PropTypes from "prop-types";
import {
  Box,
  Divider,
  IconButton,
  Link,
  ListItemIcon,
  ListItemText,
  Menu,
  MenuItem,
  Stack,
  Typography,
} from "@mui/material";
import Iconify from "src/components/iconify";
import CustomTooltip from "src/components/tooltip";
import { scoredWithText } from "./scoredWithText";
import { driftLabel, olderRunsOf } from "./evalVersionText";
import DirectionIcon from "src/sections/common/Metrics/DirectionIcon";
import { isMenuKey } from "src/sections/common/ux/oneview/HeaderMenu";

const ICON = {
  output: { icon: "material-symbols:notes", color: "text.secondary" },
  eval: {
    icon: "material-symbols:check-circle-outline",
    color: "info.success",
  },
  human: { icon: "jam:write", color: "text.secondary" },
  preference: { icon: "mdi:scale-balance", color: "text.secondary" },
  system: { icon: "mdi:timer-outline", color: "text.secondary" },
};

const SYSTEM_ICON = {
  "ops:cost": "mdi:currency-usd",
  "ops:tokens": "mdi:counter",
  "agent:steps": "mdi:source-branch",
};

/** "Eval versions differ: v2 (#1A) vs v3 (#9A, #9B)". */
export function versionsDifferText(versions, runLabels) {
  const byVersion = new Map();
  Object.entries(versions ?? {}).forEach(([runId, v]) => {
    const key = v ?? "unknown";
    if (!byVersion.has(key)) byVersion.set(key, []);
    byVersion.get(key).push(runLabels?.[runId] ?? "run");
  });
  return `Eval versions differ: ${[...byVersion.entries()]
    .map(([v, labels]) => `${v} (${labels.join(", ")})`)
    .join(" vs ")}`;
}

/**
 * Results grid group header (one metric, or Output / Preference):
 * icon · name · direction · eval version · amber dot when the runs used
 * different eval versions · ⋮ menu. Rendered by CustomExperimentGroupHeader.
 */
const ResultGroupHeader = ({ group }) => {
  const [anchor, setAnchor] = useState(null);
  const [point, setPoint] = useState(null);
  const btnRef = useRef(null);
  const rootRef = useRef(null);
  const hasItems = (group.menuItems ?? []).length > 0;
  // One view: right-click opens THIS menu. A native listener, so AG Grid's
  // own header context menu (it listens on the header cell, natively)
  // never opens a second one.
  useEffect(() => {
    const el = rootRef.current;
    if (!el || !hasItems) return undefined;
    const onCtx = (e) => {
      e.preventDefault();
      e.stopPropagation();
      setPoint({ top: e.clientY, left: e.clientX });
    };
    el.addEventListener("contextmenu", onCtx);
    return () => el.removeEventListener("contextmenu", onCtx);
  }, [hasItems]);
  const icon =
    group.kind === "system"
      ? {
          icon: SYSTEM_ICON[group.key] ?? ICON.system.icon,
          color: "text.secondary",
        }
      : ICON[group.kind] ?? ICON.output;
  const def = group.def;
  const version =
    group.kind === "eval" && !group.versionsDiffer
      ? Object.values(group.versions ?? {})[0] ?? def?.version
      : null;
  const items = group.menuItems ?? [];
  // W1-3: a run in view scored with an older version than the template's.
  const older =
    group.kind === "eval" && group.latestVersion
      ? olderRunsOf(
          {
            versions: group.versions,
            latest_version: group.latestVersion,
            outdated_runs: group.def?.outdated_runs,
          },
          group.runLabels,
        )
      : [];

  return (
    <Stack
      direction="row"
      alignItems="center"
      gap={0.75}
      sx={{ width: "100%", minWidth: 0 }}
      ref={rootRef}
      onClick={(e) => e.stopPropagation()}
      onKeyDown={(e) => {
        if (!items.length || !isMenuKey(e)) return;
        e.preventDefault();
        e.stopPropagation();
        setAnchor(btnRef.current);
      }}
    >
      <Iconify
        icon={icon.icon}
        width={18}
        sx={{ color: icon.color, flexShrink: 0 }}
      />
      {/* iterate r2 G3 (P9): a narrow header breaks between words (two
          lines at most), the full name on hover. */}
      <CustomTooltip
        show
        size="small"
        placement="top"
        arrow
        title={
          group.kind === "eval" && group.scoredWith
            ? scoredWithText(
                group.name,
                group.versions,
                group.scoredWith,
                group.runLabels,
              )
            : group.name
        }
      >
        <Typography
          fontWeight={700}
          fontSize="13px"
          color="text.secondary"
          sx={{
            // IT-R4 G3 (P4): never cut inside a word (`Resolutic`): the
            // label keeps its longest word; the version and icons give way.
            minWidth: "min-content",
            lineHeight: "16px",
            display: "-webkit-box",
            WebkitLineClamp: 2,
            WebkitBoxOrient: "vertical",
            overflow: "hidden",
            wordBreak: "normal",
            overflowWrap: "normal",
          }}
        >
          {group.name}
        </Typography>
      </CustomTooltip>
      {def && group.kind !== "preference" && (
        <DirectionIcon higherIsBetter={def.higher_is_better ?? null} />
      )}
      {version && !older.length && (
        <Typography
          typography="s3"
          color="text.disabled"
          sx={{ flexShrink: 0, fontVariantNumeric: "tabular-nums" }}
        >
          {version}
        </Typography>
      )}
      {older.length > 0 && (
        <CustomTooltip
          show
          size="small"
          placement="top"
          arrow
          title={`${group.name} ${group.latestVersion} available · See changes`}
        >
          <Link
            component="button"
            type="button"
            typography="s3"
            underline="hover"
            data-testid="eval-version-drift"
            disabled={!group.onSeeChanges}
            onClick={(e) => {
              e.stopPropagation();
              group.onSeeChanges?.();
            }}
            sx={{
              flexShrink: 0,
              whiteSpace: "nowrap",
              color: "amber.700",
              fontVariantNumeric: "tabular-nums",
            }}
          >
            {version
              ? driftLabel(version, group.latestVersion)
              : `${group.latestVersion} available`}
          </Link>
        </CustomTooltip>
      )}
      {group.mixedVersions && (
        <Typography
          typography="s3"
          color="text.secondary"
          data-testid="eval-mixed-versions"
          sx={{ flexShrink: 0 }}
        >
          mixed versions
        </Typography>
      )}
      {group.versionsDiffer && (
        <CustomTooltip
          show
          size="small"
          placement="top"
          arrow
          title={versionsDifferText(group.versions, group.runLabels)}
        >
          <Box
            aria-label="Eval versions differ"
            sx={{
              width: 7,
              height: 7,
              borderRadius: "50%",
              bgcolor: "amber.500",
              flexShrink: 0,
            }}
          />
        </CustomTooltip>
      )}
      <Box sx={{ flex: 1 }} />
      {items.length > 0 && (
        <IconButton
          ref={btnRef}
          size="small"
          aria-label={`${group.name} menu`}
          aria-haspopup="menu"
          onClick={(e) => setAnchor(e.currentTarget)}
          sx={{ p: 0.25, color: "text.secondary" }}
        >
          <Iconify icon="mdi:dots-vertical" width={18} />
        </IconButton>
      )}
      <Menu
        anchorEl={point ? undefined : anchor}
        anchorReference={point ? "anchorPosition" : "anchorEl"}
        anchorPosition={point ?? undefined}
        open={!!anchor || !!point}
        onClose={() => {
          setAnchor(null);
          setPoint(null);
        }}
        slotProps={{ paper: { sx: { minWidth: 240 } } }}
      >
        {items.map((item, i) => [
          item.divider && i > 0 ? <Divider key={`d-${i}`} /> : null,
          item.heading ? (
            <Typography
              key={`h-${item.label}`}
              typography="s3"
              color="text.disabled"
              sx={{
                px: 2,
                pt: 1,
                pb: 0.5,
                display: "block",
                textTransform: "uppercase",
                letterSpacing: "0.5px",
              }}
            >
              {item.label}
            </Typography>
          ) : (
            <MenuItem
              key={item.label}
              disabled={item.disabled}
              selected={item.selected}
              onClick={() => {
                if (item.keepOpen) {
                  item.onClick?.();
                  return;
                }
                setAnchor(null);
                setPoint(null);
                item.onClick?.();
              }}
            >
              <ListItemIcon sx={{ minWidth: 28 }}>
                {item.icon && <Iconify icon={item.icon} width={16} />}
              </ListItemIcon>
              <ListItemText
                primary={item.label}
                secondary={item.secondary}
                sx={{
                  "& .MuiListItemText-secondary": { whiteSpace: "normal" },
                }}
                primaryTypographyProps={{ typography: "s1" }}
                secondaryTypographyProps={{ typography: "s3" }}
              />
              {item.selected && (
                <Iconify
                  icon="mdi:check"
                  width={16}
                  sx={{ color: "primary.main", ml: 1 }}
                />
              )}
            </MenuItem>
          ),
        ])}
      </Menu>
    </Stack>
  );
};

ResultGroupHeader.propTypes = {
  group: PropTypes.shape({
    key: PropTypes.string,
    kind: PropTypes.string,
    name: PropTypes.string,
    def: PropTypes.object,
    versions: PropTypes.object,
    versionsDiffer: PropTypes.bool,
    latestVersion: PropTypes.string,
    onSeeChanges: PropTypes.func,
    mixedVersions: PropTypes.bool,
    scoredWith: PropTypes.object,
    runLabels: PropTypes.object,
    menuItems: PropTypes.array,
  }).isRequired,
};

export default ResultGroupHeader;
