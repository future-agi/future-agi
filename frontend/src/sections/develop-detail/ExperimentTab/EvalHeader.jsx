import React, { useEffect, useState } from "react";
import PropTypes from "prop-types";
import { Box, Stack, Typography } from "@mui/material";
import Iconify from "src/components/iconify";
import CustomTooltip from "src/components/tooltip";
import { fmtScore } from "src/sections/common/format/fmt";
import { HeaderMenuButton } from "src/sections/common/ux/oneview";
import OpenInEvalsLink from "src/sections/common/EvalHandoffs/OpenInEvalsLink";

const SORT_ICON = { asc: "mdi:arrow-up", desc: "mdi:arrow-down" };

/**
 * EL#10: direction is a trend glyph, never an arrow, so it cannot be read as
 * the sort; arrows are reserved for the active sort (primary, bold name).
 */
const BetterGlyph = ({ higherIsBetter }) => {
  if (higherIsBetter == null) return null;
  const label = higherIsBetter ? "Higher is better" : "Lower is better";
  return (
    <CustomTooltip show size="small" placement="top" arrow title={label}>
      <Box
        component="span"
        aria-label={label}
        sx={{ display: "inline-flex", flexShrink: 0, color: "text.disabled" }}
      >
        <Iconify
          icon={higherIsBetter ? "mdi:trending-up" : "mdi:trending-down"}
          width={14}
        />
      </Box>
    </CustomTooltip>
  );
};

BetterGlyph.propTypes = { higherIsBetter: PropTypes.bool };

/**
 * Eval column header (§3.7): `Correctness` + direction + eval version.
 * Click sorts (Shift adds a sort), like the default header.
 */
const EvalHeader = ({
  column,
  displayName,
  progressSort,
  enableSorting,
  enableMenu,
  showColumnMenu,
  def,
}) => {
  const [sort, setSort] = useState(column.getSort());
  useEffect(() => {
    const onSort = () => setSort(column.getSort());
    column.addEventListener("sortChanged", onSort);
    return () => column.removeEventListener("sortChanged", onSort);
  }, [column]);
  const tip = [
    def?.description,
    def?.output_type === "pass_fail"
      ? "Pass rate over scored tries (n= in each cell). Errored tries are not scored here, so an eval's pass rate can be higher than the run's Success rate."
      : def?.pass_threshold != null
        ? `Average score, 0–1. Passes at ${fmtScore(def.pass_threshold)}.`
        : "Average score.",
    "Hover a cell for the full breakdown.",
  ]
    .filter(Boolean)
    .join(" ");
  return (
    <CustomTooltip
      show
      title={tip}
      size="small"
      placement="top"
      arrow
      enterDelay={500}
    >
      <Stack
        direction="row"
        alignItems="center"
        gap={0.5}
        className="fa-header-cell"
        onClick={(e) => enableSorting && progressSort(e.shiftKey)}
        sx={{
          width: "100%",
          height: "100%",
          cursor: enableSorting ? "pointer" : "default",
          minWidth: 0,
          position: "relative",
        }}
      >
        <Typography
          typography="s1"
          fontWeight={sort ? "fontWeightBold" : "fontWeightMedium"}
          color="text.primary"
          noWrap
          sx={{ minWidth: 0 }}
        >
          {displayName}
        </Typography>
        <BetterGlyph higherIsBetter={def?.higher_is_better} />
        {def?.version && (
          <OpenInEvalsLink
            variant="chip"
            def={def}
            metricKey={def.key}
            name={def.name}
            version={def.version}
          >
            <Box
              component="span"
              sx={{
                typography: "s3",
                color: "text.secondary",
                bgcolor: "action.hover",
                borderRadius: "2px",
                px: 0.5,
                lineHeight: "16px",
                flexShrink: 0,
              }}
            >
              {def.version}
            </Box>
          </OpenInEvalsLink>
        )}
        {sort && (
          <Iconify
            icon={SORT_ICON[sort]}
            width={14}
            aria-label={
              sort === "asc" ? "Sorted ascending" : "Sorted descending"
            }
            sx={{ color: "primary.main", flexShrink: 0 }}
          />
        )}
        {enableMenu && showColumnMenu && (
          <HeaderMenuButton
            label={displayName}
            onOpen={(el) => showColumnMenu(el)}
            sx={{
              position: "absolute",
              right: -6,
              top: "50%",
              transform: "translateY(-50%)",
              m: 0,
              bgcolor: "background.paper",
            }}
          />
        )}
      </Stack>
    </CustomTooltip>
  );
};

EvalHeader.propTypes = {
  column: PropTypes.object.isRequired,
  displayName: PropTypes.string,
  progressSort: PropTypes.func,
  enableSorting: PropTypes.bool,
  enableMenu: PropTypes.bool,
  showColumnMenu: PropTypes.func,
  def: PropTypes.object,
};

export default EvalHeader;
