import React from "react";
import PropTypes from "prop-types";
import { Box, IconButton, Typography } from "@mui/material";
import SvgColor from "src/components/svg-color";
import CustomTooltip from "src/components/tooltip";
import RunLetterBadge from "./RunLetterBadge";
import { useExperimentForm } from "./ExperimentFormContext";

/**
 * Frame of one run card on the Configuration step: letter(s), target icon,
 * name, type, version picker, actions and remove; the body below. The
 * border turns red when the card has an error to fix.
 */
const RunCardShell = ({
  icon,
  letters = [],
  title,
  typeLabel,
  headerExtra,
  actions,
  onRemove,
  error,
  children,
}) => {
  const { nextOrdinal } = useExperimentForm();
  return (
    <Box
      sx={{
        border: "1px solid",
        borderColor: error ? "error.main" : "divider",
        borderRadius: 0.5,
        bgcolor: error ? "red.o5" : "background.default",
        px: 1.5,
        py: 1.25,
        display: "flex",
        flexDirection: "column",
        gap: 1,
      }}
    >
      <Box
        sx={{ display: "flex", alignItems: "center", gap: 1, minHeight: 26 }}
      >
        {letters.map((l) => (
          <RunLetterBadge
            key={l}
            letter={l}
            ordinal={nextOrdinal ?? undefined}
          />
        ))}
        {icon}
        {/* IT-R5 G1 P7: the name takes the room the row has (its tail is
            what tells runs apart); the full name is in the tooltip. */}
        <CustomTooltip
          show={String(title ?? "").length > 28}
          title={`${letters.length && nextOrdinal != null ? `#${nextOrdinal}${letters[0]} ` : ""}${title ?? ""}`}
          size="small"
          type="black"
        >
          <Typography
            typography="s1"
            fontWeight="fontWeightMedium"
            noWrap
            data-testid="run-card-title"
            sx={{ flex: "0 1 auto", minWidth: 40 }}
          >
            {title}
          </Typography>
        </CustomTooltip>
        {typeLabel ? (
          <Typography
            typography="s3"
            color="text.disabled"
            noWrap
            sx={{ flexShrink: 0 }}
          >
            {typeLabel}
          </Typography>
        ) : null}
        {headerExtra}
        <Box sx={{ flex: 1 }} />
        {actions}
        {onRemove ? (
          <CustomTooltip show title="Remove run" size="small" type="black">
            <IconButton
              size="small"
              aria-label="Remove run"
              onClick={onRemove}
              sx={{
                width: 22,
                height: 22,
                p: "3px",
                borderRadius: "2px",
                border: "1px solid",
                borderColor: "divider",
                bgcolor: "background.paper",
              }}
            >
              <SvgColor
                src="/assets/icons/ic_delete.svg"
                sx={{ width: 16, height: 16, color: "error.main" }}
              />
            </IconButton>
          </CustomTooltip>
        ) : null}
      </Box>
      {children}
      {error ? (
        <Typography typography="s2" color="error.main">
          {error}
        </Typography>
      ) : null}
    </Box>
  );
};

RunCardShell.propTypes = {
  icon: PropTypes.node,
  letters: PropTypes.arrayOf(PropTypes.string),
  title: PropTypes.string,
  typeLabel: PropTypes.string,
  headerExtra: PropTypes.node,
  actions: PropTypes.node,
  onRemove: PropTypes.func,
  error: PropTypes.string,
  children: PropTypes.node,
};

export default RunCardShell;
