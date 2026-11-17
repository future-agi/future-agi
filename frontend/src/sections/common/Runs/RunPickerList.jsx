import React from "react";
import PropTypes from "prop-types";
import { Box, ButtonBase, Checkbox, Stack, Typography } from "@mui/material";
import Iconify from "src/components/iconify";
import CustomTooltip from "src/components/tooltip";
import { fToNow } from "src/utils/format-time";
import { VersionNumber } from "../Datasets/VersionChip";
import RunChip from "./RunChip";
import RunProvenance from "./RunProvenance";

/** Uppercase group caption, as in DisplayMenu. */
export const PickerGroupTitle = ({ children }) => (
  <Typography
    typography="s3"
    sx={{
      fontWeight: "fontWeightRegular",
      textTransform: "uppercase",
      color: "text.disabled",
      letterSpacing: "0.5px",
      px: 1,
      pt: 1,
      pb: 0.5,
    }}
  >
    {children}
  </Typography>
);

PickerGroupTitle.propTypes = { children: PropTypes.node };

/**
 * One selectable run row shared by BaselinePicker and RunPickerPopover:
 * check / tick · RunChip · version · age.
 */
export const RunPickerRow = ({
  run,
  color,
  selected,
  multi = false,
  disabled = false,
  disabledReason,
  isPinned,
  referenceVersionNumber,
  onToggle,
  keyMetric,
  versionTag,
}) => {
  const version = run?.dataset_version;
  const versionDiffers =
    referenceVersionNumber != null &&
    version?.number != null &&
    version.number !== referenceVersionNumber;
  const row = (
    <ButtonBase
      disabled={disabled}
      onClick={() => onToggle?.(run)}
      aria-pressed={selected}
      sx={{
        width: "100%",
        display: "flex",
        alignItems: "center",
        justifyContent: "flex-start",
        gap: 1,
        px: 1,
        py: 0.5,
        borderRadius: 0.5,
        textAlign: "left",
        bgcolor: selected && !multi ? "action.selected" : "transparent",
        "&:hover": { bgcolor: "action.hover" },
        "&.Mui-disabled": { opacity: 0.5 },
      }}
    >
      {multi ? (
        <Checkbox
          size="small"
          checked={!!selected}
          tabIndex={-1}
          disableRipple
          sx={{ p: 0 }}
        />
      ) : (
        <Box sx={{ width: 16, display: "flex", flexShrink: 0 }}>
          {selected && (
            <Iconify
              icon="mdi:check"
              width={16}
              sx={{ color: "primary.main" }}
            />
          )}
        </Box>
      )}
      <Box
        sx={{
          minWidth: 0,
          flex: 1,
          display: "flex",
          flexDirection: "column",
          alignItems: "flex-start",
          gap: 0.25,
        }}
      >
        <Stack
          direction="row"
          alignItems="center"
          gap={0.75}
          sx={{ minWidth: 0, maxWidth: "100%" }}
        >
          <RunChip run={run} color={color} isPinned={isPinned} size="small" />
          {versionTag ? (
            <Typography
              typography="s3"
              color={versionTag.role ? "text.primary" : "text.secondary"}
              noWrap
            >
              {versionTag.role
                ? `${versionTag.text} · ${versionTag.role}`
                : versionTag.text}
            </Typography>
          ) : null}
        </Stack>
        {/* MUST 7: what the run used, one line. */}
        {run?.config ? (
          <Box
            sx={{ minWidth: 0, maxWidth: "100%", display: "flex", pl: 0.25 }}
          >
            <RunProvenance run={run} variant="line" interactive={false} />
          </Box>
        ) : null}
      </Box>
      {version?.number != null && (
        <CustomTooltip
          show={versionDiffers}
          size="small"
          placement="top"
          arrow
          title="Different dataset version: some rows are not comparable"
        >
          <Stack
            direction="row"
            alignItems="center"
            gap={0.5}
            sx={{ flexShrink: 0 }}
          >
            <VersionNumber number={version.number} />
            {versionDiffers && (
              <Box
                sx={{
                  width: 6,
                  height: 6,
                  borderRadius: "50%",
                  bgcolor: "amber.500",
                }}
              />
            )}
          </Stack>
        </CustomTooltip>
      )}
      {keyMetric && (
        <Typography
          typography="s3"
          color="text.secondary"
          noWrap
          sx={{ flexShrink: 0, fontVariantNumeric: "tabular-nums" }}
        >
          {keyMetric}
        </Typography>
      )}
      {run?.created_at && (
        <Typography
          typography="s3"
          color="text.disabled"
          noWrap
          sx={{ flexShrink: 0, minWidth: 56, textAlign: "right" }}
        >
          {fToNow(run.created_at)}
        </Typography>
      )}
    </ButtonBase>
  );
  return disabledReason && disabled ? (
    <CustomTooltip
      show
      size="small"
      placement="left"
      arrow
      title={disabledReason}
    >
      <Box>{row}</Box>
    </CustomTooltip>
  ) : (
    row
  );
};

RunPickerRow.propTypes = {
  run: PropTypes.object.isRequired,
  color: PropTypes.object,
  selected: PropTypes.bool,
  multi: PropTypes.bool,
  disabled: PropTypes.bool,
  disabledReason: PropTypes.string,
  isPinned: PropTypes.bool,
  referenceVersionNumber: PropTypes.number,
  onToggle: PropTypes.func,
  // optional key metric text (`Correctness 82%`), RunPickerPopover
  keyMetric: PropTypes.string,
  // `{text, role}` (`v3.1 (draft) · Latest version`).
  versionTag: PropTypes.object,
};
