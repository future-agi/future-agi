import React, { useState } from "react";
import PropTypes from "prop-types";
import {
  Box,
  CircularProgress,
  Divider,
  ListItemIcon,
  ListItemText,
  Menu,
  MenuItem,
  Stack,
  Typography,
} from "@mui/material";
import Iconify from "src/components/iconify";
import CustomTooltip from "src/components/tooltip";
import { runColorAt } from "./runColors";
import { runLabel, runShortName } from "./runLabel";
import { etaText, getRunStatus, runProgressOf } from "./runStatus";
import RunSourceBadge from "./RunSourceBadge";
import PlaybackTag from "./PlaybackTag";
import { STAND_IN_EVENTS } from "./runLedger";
import { SimulatedTag } from "src/sections/common/RunSources/TodayStatus";
import { copyText, useCopiedFlash } from "src/sections/common/ux/CopyButton";

// R3 G0 (B11): a 6 px verdict dot; the shape differs per tone so colour is
// never the only signal (pass ● · fail ■ · flaky ◐ ring · error ○ red ring).
export const VERDICT_DOT = Object.freeze({
  pass: { color: "green.600", radius: "50%", ring: false, label: "Passed" },
  fail: { color: "red.600", radius: "1px", ring: false, label: "Failed" },
  flaky: { color: "amber.500", radius: "50%", ring: true, label: "Flaky" },
  error: { color: "red.600", radius: "50%", ring: true, label: "Errored" },
  none: { color: "text.disabled", radius: "50%", ring: true, label: "Not run" },
});

function VerdictDot({ verdict }) {
  const v = VERDICT_DOT[verdict?.tone] ?? VERDICT_DOT.none;
  return (
    <Box
      component="span"
      role="img"
      aria-label={verdict?.label ?? v.label}
      data-verdict={verdict?.tone ?? "none"}
      sx={{
        width: 6,
        height: 6,
        flexShrink: 0,
        borderRadius: v.radius,
        bgcolor: v.ring ? "transparent" : v.color,
        border: v.ring ? "1.5px solid" : "none",
        borderColor: v.color,
        boxSizing: "border-box",
      }}
    />
  );
}
VerdictDot.propTypes = { verdict: PropTypes.object };

/** Floating copy-id button shown on chip hover (no layout shift). */
function CopyRunId({ runId, label, placement = "top" }) {
  const [copied, flash] = useCopiedFlash();
  return (
    <CustomTooltip
      show
      size="small"
      placement={placement}
      title={copied ? "Copied" : `Copy run id ${runId}`}
    >
      <Box
        component="button"
        type="button"
        className="fa-runchip-copy"
        aria-label={`Copy ${label} run id`}
        onClick={async (e) => {
          e.stopPropagation();
          if (await copyText(runId)) flash();
        }}
        sx={{
          position: "absolute",
          top: -7,
          right: -7,
          width: 18,
          height: 18,
          p: 0,
          display: "grid",
          placeItems: "center",
          borderRadius: "50%",
          border: "1px solid",
          borderColor: "divider",
          bgcolor: "background.paper",
          color: copied ? "green.600" : "text.secondary",
          cursor: "pointer",
          opacity: copied ? 1 : 0,
          pointerEvents: copied ? "auto" : "none",
          transition: "opacity 120ms",
          zIndex: 1,
          "&:hover": { color: copied ? "green.600" : "text.primary" },
          "&:focus-visible": { opacity: 1, pointerEvents: "auto" },
        }}
      >
        <Iconify icon={copied ? "mdi:check" : "mdi:content-copy"} width={11} />
      </Box>
    </CustomTooltip>
  );
}
CopyRunId.propTypes = {
  runId: PropTypes.string,
  label: PropTypes.string,
  placement: PropTypes.string,
};

const SIZES = {
  small: { height: 24, font: "s2", gap: 0.75, px: 0.75, nameMax: 160 },
  medium: { height: 30, font: "s1", gap: 1, px: 1, nameMax: 220 },
};

/** The run's state as a compact trailing badge (ring, `2 err`, …), or null. */
function StateBadge({ run }) {
  const info = getRunStatus(run);
  const prog = runProgressOf(run);
  if (info.active) {
    const eta = etaText(prog.eta_s);
    return (
      <CustomTooltip
        show
        size="small"
        placement="top"
        arrow
        title={`${info.label} · ${prog.done} of ${prog.total} rows${eta ? ` · ${eta}` : ""}`}
      >
        <Stack direction="row" alignItems="center" gap={0.5}>
          <Box sx={{ position: "relative", display: "inline-flex" }}>
            <CircularProgress
              variant="determinate"
              value={100}
              size={12}
              thickness={6}
              aria-hidden
              sx={{ color: "action.hover", position: "absolute" }}
            />
            {/* L2 G2 (a11y #8b): a named progressbar. */}
            <CircularProgress
              variant="determinate"
              value={Math.max(4, prog.pct)}
              size={12}
              thickness={6}
              aria-label={`Run ${run?.label ?? ""} progress`.replace(
                /\s+/g,
                " ",
              )}
              aria-valuetext={`${prog.done} of ${prog.total} rows`}
              sx={{ color: "primary.main" }}
            />
          </Box>
          <Typography
            typography="s3"
            color="text.secondary"
            sx={{ fontVariantNumeric: "tabular-nums", whiteSpace: "nowrap" }}
          >
            {prog.done}/{prog.total}
          </Typography>
          <PlaybackTag run={run} />
        </Stack>
      </CustomTooltip>
    );
  }
  if (info.waiting) {
    return (
      <CustomTooltip
        show
        size="small"
        placement="top"
        arrow
        title={`Waiting for results · ${prog.done}/${prog.total} uploaded`}
      >
        <Stack direction="row" alignItems="center" gap={0.25}>
          <Iconify
            icon="material-symbols:hourglass-empty-rounded"
            width={12}
            sx={{ color: "amber.700" }}
          />
          <Typography typography="s3" color="amber.700" noWrap>
            Waiting
          </Typography>
        </Stack>
      </CustomTooltip>
    );
  }
  if (info.key === "Failed") {
    return (
      <CustomTooltip
        show={!!run?.first_error}
        size="small"
        placement="top"
        arrow
        title={run?.first_error ? `First error: ${run.first_error}` : ""}
      >
        <Typography typography="s3" color="error.main" fontWeight={600} noWrap>
          Failed
        </Typography>
      </CustomTooltip>
    );
  }
  if (prog.errors > 0) {
    return (
      <CustomTooltip
        show
        size="small"
        placement="top"
        arrow
        title={`${prog.errors} ${prog.errors === 1 ? "row" : "rows"} errored${run?.first_error ? ` · ${run.first_error}` : ""}`}
      >
        <Typography typography="s3" color="error.main" fontWeight={600} noWrap>
          {prog.errors} err
        </Typography>
      </CustomTooltip>
    );
  }
  if (info.key === "Cancelled") {
    return (
      <CustomTooltip
        show
        size="small"
        placement="top"
        arrow
        title={`Stopped at ${prog.done} of ${prog.total} rows`}
      >
        <Typography typography="s3" color="text.secondary" noWrap>
          Stopped
        </Typography>
      </CustomTooltip>
    );
  }
  return null;
}

StateBadge.propTypes = { run: PropTypes.object };

/**
 * RunChip — one run, everywhere a run is named (PRODUCT-SPEC §4, §3.10.3).
 * 3 px colour bar · `#4A` · short name · `Baseline` badge · pin ·
 * progress ring / `2 err` / `Waiting` · optional menu.
 *
 * Props (frozen):
 * @param {object} props.run  canonical Run
 * @param {import("./runColors").RunColor} [props.color]  from useRunColors;
 *   defaults to the run's own `color_index` (variant order, as in the
 *   experiment grid)
 * @param {boolean} [props.isBaseline]  shows the `Baseline` badge
 * @param {boolean} [props.isPinned]  pin icon (the pinned dataset baseline)
 * @param {boolean} [props.showName=true]  short name after the label
 * @param {boolean} [props.showStatus=true]  trailing state badge
 * @param {boolean} [props.showSource=true]  the run's kind after the label
 *   (RunSourceBadge; nothing for runs without a config)
 * @param {"small"|"medium"} [props.size="small"]
 * @param {Function} [props.onClick]  click on the chip body
 * @param {Array<{label: string, icon?: string, onClick: Function,
 *   disabled?: boolean, danger?: boolean, divider?: boolean}>} [props.menu]
 *   items for the chevron menu (the chip body opens it when there is no
 *   onClick)
 * @param {{tone: "pass"|"fail"|"flaky"|"error"|"none", label: string}}
 *   [props.verdict]  R3: a 6 px dot before the label; `label` goes in the
 *   tooltip (`Failed 3 of 3 tries`)
 * @param {boolean} [props.copyId=true]  R3: hovering shows a copy-id button
 *   (full uuid) at the top-right corner, without moving anything
 * @param {"top"|"bottom"} [props.tooltipPlacement="top"]  where the chip's
 *   hover tooltips open (`bottom` when a line sits right above the chip)
 */
const RunChip = ({
  run,
  color,
  isBaseline = false,
  isPinned = false,
  showName = true,
  showStatus = true,
  showSource = true,
  size = "small",
  onClick,
  menu,
  verdict,
  copyId = true,
  tooltipPlacement = "top",
  roleText,
}) => {
  const [anchor, setAnchor] = useState(null);
  const [hover, setHover] = useState(false);
  const s = SIZES[size] ?? SIZES.small;
  const c = color ?? runColorAt(run?.color_index ?? 0);
  const label = runLabel(run);
  const name = runShortName(run);
  const hasMenu = Array.isArray(menu) && menu.length > 0;
  const clickable = !!onClick || hasMenu;

  const openMenu = (e) => {
    e.stopPropagation();
    setAnchor(e.currentTarget);
  };

  const handleBody = (e) => {
    if (onClick) onClick(e);
    else if (hasMenu) openMenu(e);
  };

  const notFound = run?.not_found;

  const body = (
    <Box
      role={clickable ? "button" : undefined}
      tabIndex={clickable ? 0 : undefined}
      onClick={clickable ? handleBody : undefined}
      onKeyDown={(e) => {
        if (clickable && (e.key === "Enter" || e.key === " ")) {
          e.preventDefault();
          handleBody(e);
        }
      }}
      data-run-id={run?.run_id}
      sx={{
        display: "inline-flex",
        alignItems: "stretch",
        height: s.height,
        maxWidth: "100%",
        minWidth: 0,
        border: "1px solid",
        borderColor: notFound ? "error.main" : "divider",
        borderRadius: 0.5,
        bgcolor: "background.paper",
        overflow: "hidden",
        cursor: clickable ? "pointer" : "default",
        transition: "border-color 120ms, background-color 120ms",
        "&:hover": clickable
          ? { borderColor: "border.hover", bgcolor: "action.hover" }
          : undefined,
        "&:focus-visible": {
          outline: (t) => `2px solid ${t.palette.primary.main}`,
          outlineOffset: 1,
        },
      }}
    >
      <Box sx={{ width: 3, flexShrink: 0, bgcolor: c.solid }} />
      <Stack
        direction="row"
        alignItems="center"
        gap={s.gap}
        sx={{ px: s.px, minWidth: 0 }}
      >
        {verdict && <VerdictDot verdict={verdict} />}
        <Typography
          typography={s.font}
          fontWeight="fontWeightSemiBold"
          color="text.primary"
          noWrap
          sx={{ flexShrink: 0, fontVariantNumeric: "tabular-nums" }}
        >
          {label}
        </Typography>
        {showSource && (
          <RunSourceBadge
            run={run}
            size={size}
            nameShown={Boolean(showName && name)}
          />
        )}
        {showName && name && (
          <Typography
            typography={s.font}
            color="text.secondary"
            noWrap
            sx={{ minWidth: 0, maxWidth: s.nameMax }}
          >
            {name}
          </Typography>
        )}
        {notFound && (
          <Typography typography="s3" color="error.main" noWrap>
            Run not found
          </Typography>
        )}
        {(isBaseline || roleText) && (
          <Box
            component="span"
            data-testid="run-role-badge"
            sx={{
              flexShrink: 0,
              px: 0.75,
              borderRadius: "2px",
              bgcolor: "action.hover",
              color: "text.primary",
              typography: "s3",
              fontWeight: 500,
              lineHeight: "16px",
            }}
          >
            {roleText ?? "Baseline"}
          </Box>
        )}
        {isPinned && (
          <CustomTooltip
            show
            size="small"
            placement="top"
            arrow
            title="Pinned dataset baseline"
          >
            <Box
              component="span"
              aria-label="Pinned dataset baseline"
              sx={{
                display: "inline-flex",
                color: "text.secondary",
                flexShrink: 0,
              }}
            >
              <Iconify icon="mdi:pin" width={12} />
            </Box>
          </CustomTooltip>
        )}
        {showStatus && !notFound && <StateBadge run={run} />}
        {hasMenu && (
          <Box
            component="span"
            onClick={openMenu}
            aria-label={`${label} menu`}
            sx={{
              display: "inline-flex",
              ml: -0.25,
              mr: -0.5,
              color: "text.secondary",
              "&:hover": { color: "text.primary" },
            }}
          >
            <Iconify icon="mdi:chevron-down" width={16} />
          </Box>
        )}
      </Stack>
    </Box>
  );

  const baseTip =
    !showName && name ? `${label} ${name}` : run?.experiment_name ?? "";
  const tooltip = [baseTip, verdict?.label].filter(Boolean).join(" · ");
  const withCopy = copyId && run?.run_id;

  return (
    <>
      <CustomTooltip
        show={!!tooltip && !anchor}
        title={tooltip}
        placement={tooltipPlacement}
        size="small"
        arrow
        enterDelay={400}
      >
        {withCopy ? (
          <Box
            component="span"
            onMouseEnter={() => setHover(true)}
            onMouseLeave={() => setHover(false)}
            onFocus={() => setHover(true)}
            onBlur={(e) => {
              if (!e.currentTarget.contains(e.relatedTarget)) setHover(false);
            }}
            sx={{
              position: "relative",
              display: "inline-flex",
              maxWidth: "100%",
              minWidth: 0,
              "& .fa-runchip-copy": { opacity: 1, pointerEvents: "auto" },
            }}
          >
            {body}
            {hover && (
              <CopyRunId
                runId={run.run_id}
                label={label}
                placement={tooltipPlacement}
              />
            )}
          </Box>
        ) : (
          body
        )}
      </CustomTooltip>
      {hasMenu && (
        <Menu
          anchorEl={anchor}
          open={!!anchor}
          onClose={() => setAnchor(null)}
          onClick={(e) => e.stopPropagation()}
          anchorOrigin={{ vertical: "bottom", horizontal: "left" }}
          transformOrigin={{ vertical: "top", horizontal: "left" }}
          slotProps={{ paper: { sx: { minWidth: 220, mt: 0.5 } } }}
        >
          {menu.map((item, i) => [
            item.divider && i > 0 ? <Divider key={`d-${i}`} /> : null,
            <MenuItem
              key={item.label}
              disabled={item.disabled}
              onClick={() => {
                setAnchor(null);
                item.onClick?.();
              }}
              sx={{
                typography: "s1",
                color: item.danger ? "error.main" : undefined,
              }}
            >
              {item.icon && (
                <ListItemIcon sx={{ minWidth: 28, color: "inherit" }}>
                  <Iconify icon={item.icon} width={16} />
                </ListItemIcon>
              )}
              <ListItemText
                primary={item.label}
                primaryTypographyProps={{ typography: "s1" }}
              />
              {/* L7c g5: a Simulate stand-in carries its Simulated tag on
                  every door (here: the RunBar chip menu). */}
              {item.tag ??
                (STAND_IN_EVENTS[item.label] ? (
                  <Box component="span" sx={{ ml: 1 }}>
                    <SimulatedTag event={STAND_IN_EVENTS[item.label]} />
                  </Box>
                ) : null)}
            </MenuItem>,
          ])}
        </Menu>
      )}
    </>
  );
};

RunChip.propTypes = {
  run: PropTypes.shape({
    run_id: PropTypes.string,
    label: PropTypes.string,
    name: PropTypes.string,
    status: PropTypes.string,
    progress: PropTypes.object,
    experiment_name: PropTypes.string,
    first_error: PropTypes.string,
    not_found: PropTypes.bool,
    color_index: PropTypes.number,
  }).isRequired,
  color: PropTypes.shape({
    solid: PropTypes.string,
    tint: PropTypes.string,
    fg: PropTypes.string,
    index: PropTypes.number,
  }),
  isBaseline: PropTypes.bool,
  // IT-R4 G6 P1 (rule 30): the role words in the badge (`Baseline ·
  // current`, `New`); default `Baseline` when isBaseline.
  roleText: PropTypes.node,
  isPinned: PropTypes.bool,
  showName: PropTypes.bool,
  showStatus: PropTypes.bool,
  showSource: PropTypes.bool,
  size: PropTypes.oneOf(["small", "medium"]),
  onClick: PropTypes.func,
  verdict: PropTypes.shape({
    tone: PropTypes.oneOf(["pass", "fail", "flaky", "error", "none"]),
    label: PropTypes.string,
  }),
  copyId: PropTypes.bool,
  tooltipPlacement: PropTypes.oneOf(["top", "bottom"]),
  menu: PropTypes.arrayOf(
    PropTypes.shape({
      label: PropTypes.string.isRequired,
      icon: PropTypes.string,
      onClick: PropTypes.func,
      disabled: PropTypes.bool,
      danger: PropTypes.bool,
      divider: PropTypes.bool,
      // L7c g5 (additive): a tag after the label (a Simulate stand-in gets
      // its Simulated tag by default).
      tag: PropTypes.node,
    }),
  ),
};

export default RunChip;
