import React, { useEffect, useState } from "react";
import PropTypes from "prop-types";
import { Box, Button, Checkbox, Stack, Typography } from "@mui/material";
import CustomTooltip from "src/components/tooltip";
import { runLabel, runTitle } from "src/sections/common/Runs/runLabel";
import { RailDot } from "./TaskRail";
import { MAX_LANES, dotOf } from "./workspaceModel";

/**
 * IT-R5 G4 P4: the runs of a link that has more runs than lanes, as one
 * thin strip above the lanes (it replaces the card picker). The lane runs
 * are ticked; a hover shows a checkbox on the others; picking 2 to
 * MAX_LANES and `Compare` swaps the lanes. Pure presentation.
 */
export default function RunStrip({ runs, laneIds, cells, blind, onCompare }) {
  const key = laneIds.join(",");
  const [picked, setPicked] = useState(laneIds);
  useEffect(() => setPicked(laneIds), [key]); // eslint-disable-line react-hooks/exhaustive-deps
  const changed =
    picked.length !== laneIds.length ||
    picked.some((id) => !laneIds.includes(id));
  const ok = picked.length >= 2 && picked.length <= MAX_LANES;
  const toggle = (id) =>
    setPicked((p) =>
      p.includes(id)
        ? p.filter((x) => x !== id)
        : p.length < MAX_LANES
          ? [...p, id]
          : p,
    );
  return (
    <Stack
      direction="row"
      alignItems="center"
      data-testid="tc-run-strip"
      sx={{
        px: 1.5,
        height: 30,
        flexShrink: 0,
        gap: 0.5,
        minWidth: 0,
        borderBottom: "1px solid",
        borderColor: "divider",
        bgcolor: "background.default",
      }}
    >
      <Typography
        typography="s3"
        color="text.secondary"
        sx={{ flexShrink: 0, mr: 0.5, whiteSpace: "nowrap" }}
      >
        {runs.length} runs in this link
      </Typography>
      <Stack
        direction="row"
        alignItems="center"
        sx={{ gap: 0.5, minWidth: 0, overflowX: "auto", flex: 1 }}
      >
        {runs.map((r, i) => {
          const id = r.run_id;
          const on = picked.includes(id);
          const full = !on && picked.length >= MAX_LANES;
          const label = blind ? `Run ${i + 1}` : runLabel(r);
          const dot = dotOf(cells?.[id], label);
          return (
            <CustomTooltip
              key={id}
              describeChild
              show
              size="small"
              title={`${blind ? label : runTitle(r)} · ${dot.label}${full ? ` · ${MAX_LANES} lanes at most` : ""}`}
            >
              <Box
                component="label"
                data-strip-run={id}
                sx={{
                  display: "inline-flex",
                  alignItems: "center",
                  gap: 0.5,
                  pl: 0.25,
                  pr: 0.75,
                  height: 22,
                  flexShrink: 0,
                  borderRadius: 0.75,
                  border: "1px solid",
                  borderColor: on
                    ? r.color?.solid ?? "primary.main"
                    : "divider",
                  bgcolor: "background.paper",
                  cursor: full ? "default" : "pointer",
                  opacity: full ? 0.6 : 1,
                  "& .tc-strip-check": { opacity: on ? 1 : 0 },
                  "&:hover .tc-strip-check, &:focus-within .tc-strip-check": {
                    opacity: 1,
                  },
                }}
              >
                <Checkbox
                  size="small"
                  className="tc-strip-check"
                  checked={on}
                  disabled={full}
                  onChange={() => toggle(id)}
                  inputProps={{ "aria-label": `Compare ${label}` }}
                  sx={{ p: 0, "& svg": { width: 16, height: 16 } }}
                />
                <RailDot cell={cells?.[id]} run={{ ...r, label }} />
                <Typography
                  component="span"
                  typography="s3"
                  sx={{
                    whiteSpace: "nowrap",
                    fontVariantNumeric: "tabular-nums",
                  }}
                >
                  {label}
                </Typography>
              </Box>
            </CustomTooltip>
          );
        })}
      </Stack>
      {changed ? (
        ok ? (
          <Button
            size="small"
            variant="contained"
            color="primary"
            data-testid="tc-strip-compare"
            onClick={() => onCompare(picked)}
            sx={{ flexShrink: 0, height: 24 }}
          >
            Compare {picked.length}
          </Button>
        ) : (
          <Typography
            typography="s3"
            color="text.secondary"
            data-testid="tc-strip-help"
            sx={{ flexShrink: 0, whiteSpace: "nowrap" }}
          >
            Select 2 to {MAX_LANES}
          </Typography>
        )
      ) : null}
    </Stack>
  );
}

RunStrip.propTypes = {
  runs: PropTypes.arrayOf(PropTypes.object).isRequired,
  laneIds: PropTypes.arrayOf(PropTypes.string).isRequired,
  cells: PropTypes.object,
  blind: PropTypes.bool,
  onCompare: PropTypes.func.isRequired,
};
